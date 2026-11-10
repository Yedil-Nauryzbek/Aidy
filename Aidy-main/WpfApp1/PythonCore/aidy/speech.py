"""English command transcription with one persistent, locally cached Whisper model.

Audio capture and wake-word authorization belong to the caller. A transcript is
only a recognition result; this module never executes commands.
"""

from __future__ import annotations

import gc
from concurrent.futures import ThreadPoolExecutor
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time

import numpy as np

from .logui import info, warn


MODEL_ID = "Systran/faster-whisper-tiny.en"
SAMPLE_RATE = 16000
LANGUAGE = "en"
COMMAND_VOCABULARY = "Aidy, Steam, Discord, Chrome, browser, Telegram, Spotify, YouTube, WhatsApp, ChatGPT, ChatGPT app, GPT app, Notepad, Microsoft Word, Excel, PowerPoint, browser tabs, websites, document, essay."
DOCUMENT_VOCABULARY = "Write an essay, a 250-word essay, a 500-word essay, create a new document."
WAKE_VOCABULARY = "Aidy."
WAKE_PROBE_WINDOW_SECONDS = 10
NAVIGATION_WINDOW_SECONDS = 8
NUMBER_WINDOW_SECONDS = 15


def normalize_number_reply(text):
    """Only canonicalize a complete reply while a numeric answer is expected.

    Never repair arbitrary command words or drop a negation. Audio confidence
    checks still run before this function; it cannot rescue rejected audio.
    """
    from .config import NUMERIC_FOLLOWUP_WORD_TO_VALUE
    value = NUMERIC_FOLLOWUP_WORD_TO_VALUE.get(text)
    if value is None:
        value = {'zen': 10, 'buff': 5, 'baf': 5}.get(text)
    return str(value) if value is not None else text


def _ambiguous_speech_score(logprob, no_speech):
    # Like faster-whisper, consider transcript confidence alongside no_speech.
    # Live 'yes' had logprob=-0.138 and no_speech=0.548: rejecting it solely
    # on the latter discarded a clear answer and forced another slow decode.
    return no_speech > .85 or (no_speech > .5 and logprob < -.35)


def _canonical_short_repeat(text, purpose):
    """Remove exact decoder echoes only for a complete, known short phrase.

    Never deduplicate numbers or arbitrary text ("five five" can mean 55).
    A repeated command keeps the same action, target and any negation.
    """
    from .activation import match_wake
    from .config import WAKE_KEYWORDS
    words = text.split()
    for size in range(1, len(words) // 2 + 1):
        if len(words) % size or words != words[:size] * (len(words) // size):
            continue
        candidate = ' '.join(words[:size])
        if purpose == 'wake' and match_wake(candidate, WAKE_KEYWORDS) == (True, ''):
            return candidate
        if purpose == 'navigation':
            from .config import WINDOW_SWITCH_DONE, WINDOW_SWITCH_CANCEL
            # Ending/cancelling the selection is idempotent. Never collapse
            # repeated left/right steps, numbers or yes/no confirmations.
            if candidate in WINDOW_SWITCH_DONE | WINDOW_SWITCH_CANCEL:
                return candidate
        bare = re.sub(r"^(?:please )?(?:don't |do not )?", '', candidate)
        apps = {name.strip().lower().rstrip('.') for name in COMMAND_VOCABULARY.split(',')}
        if purpose != 'wake' and (bare in {'volume up', 'volume down'} or
                any(bare == f'{verb} {app}' for verb in ('open', 'close') for app in apps)):
            return candidate
    return text


class SpeechRecognitionError(RuntimeError):
    """Recognition is unavailable; callers must suspend command processing."""


def default_model_cache() -> Path:
    configured = os.environ.get("AIDY_MODEL_CACHE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise ValueError("AIDY_MODEL_CACHE must be an absolute path")
        return path
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) / "Aidy" / "models") if local else (Path.home() / ".cache" / "Aidy" / "models")


def _cache_is_complete(cache_dir: Path, model_id: str = MODEL_ID) -> bool:
    from huggingface_hub import try_to_load_from_cache

    # Converted models use either vocabulary.json or vocabulary.txt.
    for filename in ("config.json", "model.bin", "tokenizer.json"):
        cached = try_to_load_from_cache(model_id, filename, cache_dir=str(cache_dir))
        if not isinstance(cached, str) or not Path(cached).is_file():
            return False
    return any(isinstance(cached, str) and Path(cached).is_file()
               for cached in (try_to_load_from_cache(model_id, filename, cache_dir=str(cache_dir))
                              for filename in ("vocabulary.json", "vocabulary.txt")))


def normalize_transcript(text: str) -> str:
    text = str(text).lower().replace("\u2019", "'").strip()
    # Keep decimal points, apostrophes and hyphens for numeric answers, names,
    # and application targets. Remove ordinary sentence punctuation.
    text = re.sub(r"\.(?=\s|$)", " ", text)
    text = re.sub(r"[!?;,\"\u201c\u201d]", " ", text)
    return " ".join(text.split())


class SpeechRecognizer:
    """Load once; serialize inference and fail explicitly on runtime errors.

    Environment settings: AIDY_WHISPER_DEVICE=auto/cpu/cuda,
    AIDY_WHISPER_COMPUTE_TYPE=auto/<supported type>, AIDY_WHISPER_CPU_THREADS,
    AIDY_WHISPER_BEAM_SIZE, AIDY_WHISPER_WINDOW_SECONDS,
    and AIDY_MODEL_CACHE (absolute path).
    """

    supports_previews = True

    def __init__(self, *, device: str | None = None, compute_type: str | None = None,
                 cache_dir: str | Path | None = None, model_factory=None, runtime=None):
        if model_factory is None:
            from .whisper_backend import CommandWhisperModel
            model_factory = CommandWhisperModel
        if runtime is None:
            import ctranslate2
            runtime = ctranslate2

        self._model_factory = model_factory
        self._runtime = runtime
        self._lock = threading.Lock()
        self._closed = False
        self._cpu_fallback_used = False
        self._model = None
        self._preview_pool = None
        self._preview_future = None
        self.model_id = os.environ.get("AIDY_WHISPER_MODEL", MODEL_ID).strip() or MODEL_ID
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else default_model_cache()
        if not self.cache_dir.is_absolute():
            raise ValueError("Whisper model cache must be an absolute path")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=self.cache_dir):
            pass
        self.cpu_threads = self._positive_setting("AIDY_WHISPER_CPU_THREADS", min(4, os.cpu_count() or 1), 64)
        self.beam_size = self._positive_setting("AIDY_WHISPER_BEAM_SIZE", 1, 10)
        self.window_seconds = self._positive_setting("AIDY_WHISPER_WINDOW_SECONDS", 15, 30)
        requested_device = (device or os.environ.get("AIDY_WHISPER_DEVICE", "auto")).strip().lower()
        requested_compute = (compute_type or os.environ.get("AIDY_WHISPER_COMPUTE_TYPE", "auto")).strip().lower()
        if requested_device not in {"auto", "cpu", "cuda"}:
            raise ValueError("AIDY_WHISPER_DEVICE must be auto, cpu, or cuda")

        self.device = "cpu"
        if requested_device != "cpu":
            try:
                if runtime.get_cuda_device_count() > 0:
                    self.device = "cuda"
                elif requested_device == "cuda":
                    warn("Whisper: CUDA was requested but no supported CUDA device is available; using CPU")
            except Exception as exc:
                warn(f"Whisper: CUDA availability check failed; using CPU: {exc}")
        if requested_device == "cuda" and self.device == "cpu":
            # An explicit GPU precision such as float16 is not a CPU fallback
            # configuration; select a supported CPU precision independently.
            requested_compute = "auto"
        try:
            self.compute_type = self._choose_compute_type(self.device, requested_compute)
        except Exception as exc:
            if self.device != "cuda":
                raise
            warn(f"Whisper: CUDA compute configuration unavailable; using CPU: {exc}")
            self.device = "cpu"
            self._cpu_fallback_used = True
            self.compute_type = self._choose_compute_type("cpu", "auto")

        self._local_files_only = _cache_is_complete(self.cache_dir, self.model_id)
        info(f"Whisper: model={self.model_id} language={LANGUAGE} cache={self.cache_dir}")
        info("Whisper: using cached model (offline)" if self._local_files_only else "Whisper: downloading missing model files; first launch requires internet")
        started = time.perf_counter()
        try:
            self._model = self._load_model()
        except Exception as exc:
            if self.device == "cuda":
                self._fallback_to_cpu(exc)
            else:
                raise SpeechRecognitionError(f"Whisper model could not load: {exc}") from exc
        info(f"Whisper ready: device={self.device} compute_type={self.compute_type} load_s={time.perf_counter() - started:.2f}")

    @staticmethod
    def _positive_setting(name: str, default: int, maximum: int) -> int:
        value = int(os.environ.get(name, str(default)))
        if not 1 <= value <= maximum:
            raise ValueError(f"{name} must be between 1 and {maximum}")
        return value

    def _choose_compute_type(self, device: str, requested: str) -> str:
        supported = self._runtime.get_supported_compute_types(device)
        if requested != "auto":
            if requested not in supported:
                raise ValueError(f"Whisper compute type {requested!r} is not supported on {device}; supported: {sorted(supported)}")
            return requested
        preferences = ("float16", "int8_float16", "float32") if device == "cuda" else ("int8", "int8_float32", "float32")
        for candidate in preferences:
            if candidate in supported:
                return candidate
        raise SpeechRecognitionError(f"No supported Whisper compute type for {device}")

    def _load_model(self):
        info(f"Whisper: initializing {self.device}/{self.compute_type}")
        model = self._model_factory(
            self.model_id,
            device=self.device,
            compute_type=self.compute_type,
            download_root=str(self.cache_dir),
            local_files_only=self._local_files_only,
            cpu_threads=self.cpu_threads,
            num_workers=1,
        )
        if getattr(model, 'supports_short_windows', False) is True:
            info('Whisper: warming up speech detection and decoding')
            model.warm_up(self.window_seconds * 100 if self.model_id == MODEL_ID else 3000)
        return model

    def _fallback_to_cpu(self, cause: Exception):
        if self._cpu_fallback_used or self.device != "cuda":
            raise SpeechRecognitionError(f"Whisper recognition failed: {cause}") from cause
        self._cpu_fallback_used = True
        warn(f"Whisper: GPU failed; switching to CPU once: {cause}")
        self._model = None
        gc.collect()
        self.device = "cpu"
        self.compute_type = self._choose_compute_type("cpu", "auto")
        # GPU initialization may have completed the first download.
        self._local_files_only = _cache_is_complete(self.cache_dir, self.model_id)
        try:
            self._model = self._load_model()
        except Exception as exc:
            raise SpeechRecognitionError(f"Whisper CPU fallback failed: {exc}") from exc

    def _decode(self, audio: np.ndarray, purpose: str, full_context=False, preview=False,
                skip_wake_probe=False):
        short = (getattr(self._model, "supports_short_windows", False) is True
                 and self.model_id == MODEL_ID and not full_context)
        # Keep every audio sample plus one second of context; only remove padded
        # silence. Longer recordings automatically get a larger encoder window.
        wake_probe = (short and purpose == 'wake' and not skip_wake_probe
                      and self.window_seconds > WAKE_PROBE_WINDOW_SECONDS
                      and self.window_seconds < 30
                      and audio.size / SAMPLE_RATE <= 3)
        window = WAKE_PROBE_WINDOW_SECONDS if wake_probe else self.window_seconds
        if short and purpose == 'navigation' and self.window_seconds < 30:
            window = min(window, NAVIGATION_WINDOW_SECONDS)
        if short and purpose == 'number' and self.window_seconds < 30:
            window = min(window, NUMBER_WINDOW_SECONDS)
        frames = min(3000, max(window * 100,
                               math.ceil((audio.size / SAMPLE_RATE + 1) * 50) * 2)) if short else 3000
        if getattr(self._model, "supports_short_windows", False) is True:
            self._model.encoder_frames = frames
        # A short word must not spend dozens of decoder steps repeating itself.
        # Hitting this generous duration-based budget always requires verification.
        token_limit = min(64, max(16, math.ceil(audio.size / SAMPLE_RATE * 8) + 8))
        # The standalone probe needs quiet context on both sides, preserved by
        # VAD. A 10-second encoder window avoids the decoder loops seen at 5s.
        # These are synthetic samples, never extra microphone waiting. Retain
        # the original audio/settings for normal-context command verification.
        wake_padding = wake_probe
        number_padding = purpose == 'number'
        decode_audio = np.pad(audio, (SAMPLE_RATE // 5, SAMPLE_RATE * 3 // 10)) if wake_padding or number_padding else audio
        segments, metadata = self._model.transcribe(
            decode_audio,
            language=LANGUAGE,
            task="transcribe",
            beam_size=self.beam_size,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            hotwords=(WAKE_VOCABULARY if purpose == "wake" else
                      None if purpose in {"confirmation", "navigation", "number"} else
                      COMMAND_VOCABULARY + ' ' + DOCUMENT_VOCABULARY),
            vad_filter=True,
            vad_parameters={"min_speech_duration_ms": 150, "min_silence_duration_ms": 250,
                            "speech_pad_ms": 300 if wake_padding or number_padding else 100},
            no_speech_threshold=0.5,
            log_prob_threshold=-1.05,
            compression_ratio_threshold=2.4,
            initial_prompt='A short answer to how many steps.' if purpose == 'number' else None,
            max_new_tokens=token_limit if frames < 3000 else None,
        )
        # Inference (including CUDA errors) can happen here rather than above.
        segments = list(segments)
        if wake_probe:
            # Only a complete, confident standalone wake may use this fast
            # path. Exact decoder echoes of that same wake are harmless;
            # command tails and incomplete repetitions still need verification.
            from .activation import match_wake
            from .config import WAKE_KEYWORDS
            text = normalize_transcript(' '.join(s.text for s in segments))
            text = _canonical_short_repeat(text, purpose)
            if (not segments or match_wake(text, WAKE_KEYWORDS) != (True, '')
                    or self._needs_full_context(segments, metadata, purpose, token_limit)):
                if preview:
                    return None, metadata
                return self._decode(audio, purpose, preview=preview, skip_wake_probe=True)
        if frames < 3000 and self._needs_full_context(segments, metadata, purpose, token_limit):
            if preview:
                # A speculative pause may turn out to be inside a sentence.
                # Never occupy the worker with a second decode of that fragment.
                return None, metadata
            info("Whisper: verifying uncertain short-window result with full context")
            return self._decode(audio, purpose, full_context=True)
        return segments, metadata

    @staticmethod
    def _needs_full_context(segments, metadata, purpose, token_limit=64):
        if float(getattr(metadata, "duration_after_vad", 0)) < 0.15:
            return False
        if not segments:
            return True
        for segment in segments:
            metrics = [float(getattr(segment, key, float('nan'))) for key in
                       ('avg_logprob', 'no_speech_prob', 'compression_ratio')]
            if (not all(math.isfinite(v) for v in metrics)
                    or metrics[0] < (-0.95 if purpose == 'wake' else -1.05)
                    or _ambiguous_speech_score(metrics[0], metrics[1]) or metrics[2] > 2.4
                    or len(getattr(segment, 'tokens', [])) >= token_limit):
                return True
        text = normalize_transcript(' '.join(s.text for s in segments))
        canonical = _canonical_short_repeat(text, purpose)
        if canonical != text:
            return False
        words = text.split()
        # Verify unknown repeated phrases with the original window. Only the
        # complete known phrases handled above can be canonicalized directly.
        for size in range(1, len(words) // 2 + 1):
            if len(words) % size == 0 and words == words[:size] * (len(words) // size):
                return True
        return purpose == 'wake' and text in {
            'i', 'a', 'id', 'ad', 'okay a', 'ok a', 'hey a', 'hi a', 'hello a',
        }

    def start_preview(self, audio, purpose='command'):
        """At most one decode in flight; never queue obsolete partial phrases."""
        if self._closed or (self._preview_future is not None and not self._preview_future.done()):
            return None
        if self._preview_pool is None:
            self._preview_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='aidy-preview')
        self._preview_future = self._preview_pool.submit(self.transcribe, audio.copy(), purpose, preview=True)
        return self._preview_future

    def transcribe(self, audio: np.ndarray, purpose: str = "command", *,
                   preview=False, full_context=False, skip_wake_probe=False) -> str | None:
        audio = np.asarray(audio)
        if audio.ndim != 1 or audio.dtype != np.float32:
            raise ValueError("Whisper audio must be a mono float32 array sampled at 16000 Hz")
        if not np.isfinite(audio).all() or (audio.size and float(np.max(np.abs(audio))) > 1.01):
            raise ValueError("Whisper audio must contain finite samples scaled to [-1, 1]")
        duration = audio.size / SAMPLE_RATE
        if duration > 30:
            raise ValueError("Aidy command audio must be at most 30 seconds")
        if duration < 0.18:
            info(f"Whisper rejected {purpose}: audio too short ({duration:.3f}s)")
            return ""
        # Remove a DC offset for this energy check only. The capture gate and
        # neural VAD remain responsible for distinguishing speech from noise.
        centered = audio - np.mean(audio)
        rms = float(np.sqrt(np.mean(centered * centered)))
        if rms < 0.0015:
            info(f"Whisper rejected {purpose}: silence/insufficient energy (rms={rms:.5f})")
            return ""

        with self._lock:
            if self._closed:
                raise SpeechRecognitionError("Whisper recognizer is closed")
            started = time.perf_counter()
            try:
                segments, metadata = self._decode(np.ascontiguousarray(audio), purpose,
                                                  full_context=full_context, preview=preview,
                                                  skip_wake_probe=skip_wake_probe)
            except Exception as exc:
                if self.device == "cuda" and not self._cpu_fallback_used:
                    self._fallback_to_cpu(exc)
                    try:
                        segments, metadata = self._decode(np.ascontiguousarray(audio), purpose,
                                                          full_context=full_context, preview=preview,
                                                          skip_wake_probe=skip_wake_probe)
                    except Exception as cpu_exc:
                        raise SpeechRecognitionError(f"Whisper CPU transcription failed: {cpu_exc}") from cpu_exc
                else:
                    raise SpeechRecognitionError(f"Whisper transcription failed: {exc}") from exc
            elapsed = time.perf_counter() - started
            if segments is None:
                return None  # Verify only after the caller confirms the endpoint.
            speech_duration = float(getattr(metadata, "duration_after_vad", 0.0))
            if not math.isfinite(speech_duration) or speech_duration < 0.15 or not segments:
                info(f"Whisper rejected {purpose}: VAD found insufficient speech; inference_s={elapsed:.2f}")
                return ""
            # Short English utterances have lower average scores than long speech.
            # Checked against the local tiny.en command/wake/number corpus.
            min_logprob = -0.95 if purpose == "wake" else -1.05
            for segment in segments:
                if not str(segment.text).strip():
                    continue
                metrics = (
                    float(getattr(segment, "avg_logprob", float("nan"))),
                    float(getattr(segment, "no_speech_prob", float("nan"))),
                    float(getattr(segment, "compression_ratio", float("nan"))),
                )
                logprob, no_speech, compression = metrics
                if (not all(math.isfinite(value) for value in metrics)
                        or logprob < min_logprob or _ambiguous_speech_score(logprob, no_speech)
                        or compression > 2.4):
                    # Reject the complete utterance; dropping an uncertain word
                    # such as "don't" could turn a negation into an action.
                    info(f"Whisper rejected {purpose}: logprob={logprob:.3f} no_speech={no_speech:.3f} compression={compression:.3f}; inference_s={elapsed:.2f}")
                    return ""
            text = normalize_transcript(" ".join(segment.text for segment in segments))
            if (getattr(self._model, "supports_short_windows", False) is True
                    and self._model.encoder_frames < 3000):
                text = _canonical_short_repeat(text, purpose)
            if not any(character.isalnum() for character in text):
                info(f"Whisper rejected {purpose}: empty text; inference_s={elapsed:.2f}")
                return ""
            if purpose == 'number':
                text = normalize_number_reply(text)
            info(f"Whisper accepted {purpose}: audio_s={duration:.2f} speech_s={speech_duration:.2f} inference_s={elapsed:.2f} device={self.device}")
            return text

    def close(self):
        if self._preview_pool is not None:
            self._preview_pool.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._closed = True
            self._model = None

