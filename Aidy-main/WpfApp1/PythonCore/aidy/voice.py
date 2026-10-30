import os
import glob
import random
import ctypes
import re
import threading
import queue
import wave
import io
import audioop
import winsound
from functools import lru_cache
from contextlib import contextmanager
from typing import Callable
from ctypes import wintypes

import pyttsx3
import numpy as np

winmm = ctypes.WinDLL("winmm")
mciSendStringW = winmm.mciSendStringW
mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HWND]
mciSendStringW.restype = wintypes.UINT


def mci(cmd: str) -> int:
    return mciSendStringW(cmd, None, 0, None)


@lru_cache(maxsize=256)
def _wav_speech_bounds(path: str, modified_ns: int, size: int):
    """Trim only outer silence; retain the original recordings and quiet endings."""
    try:
        with wave.open(path, "rb") as source:
            if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                return None
            rate, channels = source.getframerate(), source.getnchannels()
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16)
        if not samples.size:
            return None
        samples = np.abs(samples.astype(np.float32).reshape(-1, channels))
        block = max(1, rate // 100)
        peaks = np.array([samples[i:i + block].max() for i in range(0, len(samples), block)])
        active = np.flatnonzero(peaks > max(50.0, float(peaks.max()) * 0.006))
        if not active.size:
            return None
        start = max(0, int(active[0] * block * 1000 / rate) - 30)
        end = min(int(len(samples) * 1000 / rate),
                  int((active[-1] + 1) * block * 1000 / rate) + 60)
        return start, end
    except (OSError, ValueError, wave.Error):
        return None


def play_audio(path: str, alias: str = "aidyvoice", wait: bool = False, max_ms: int | None = None, volume: int = 100) -> bool:
    if wait and os.path.splitext(path)[1].lower() == '.wav':
        try:
            stat = os.stat(path)
            data = _cached_wave(path, stat.st_mtime_ns, stat.st_size, max_ms)
            if data is not None:
                if volume != 100:
                    data = _scale_wave(data, volume)
                # Avoid MCI open/set/seek/close and repeated file reads on every
                # wake. Synchronous memory playback ends at the actual clip end.
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                return True
        except (OSError, RuntimeError, ValueError, wave.Error):
            pass  # Keep MCI available for unsupported WAVs/devices and MP3s.
    mci(f"close {alias}")

    p = path.replace('"', '\\"')
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        media_type = "waveaudio"
    else:
        media_type = "mpegvideo"

    rc = mci(f'open "{p}" type {media_type} alias {alias}')
    if rc != 0:
        return False

    if volume != 100:
        mci(f"setaudio {alias} volume to {max(0, min(100, volume)) * 10}")

    bounds = None
    if ext == ".wav":
        try:
            stat = os.stat(path)
            bounds = _wav_speech_bounds(path, stat.st_mtime_ns, stat.st_size)
        except OSError:
            pass
    if bounds is not None and mci(f"set {alias} time format milliseconds") == 0:
        start, end = bounds
        if max_ms is not None and int(max_ms) > 0:
            end = min(end, start + int(max_ms))
        play_cmd = f"play {alias} from {start} to {end}"
    else:
        play_cmd = f"play {alias}"
        if max_ms is not None and int(max_ms) > 0 and mci(f"set {alias} time format milliseconds") == 0:
            play_cmd += f" to {int(max_ms)}"
    play_cmd += " wait" if wait else ""
    rc = mci(play_cmd)
    if rc != 0:
        mci(f"close {alias}")
        return False

    if wait:
        mci(f"close {alias}")

    return True


def _scale_wave(data: bytes, volume: int) -> bytes:
    """Change only this clip's gain; do not alter the system output volume."""
    with wave.open(io.BytesIO(data), 'rb') as source:
        params = source.getparams()
        pcm = source.readframes(source.getnframes())
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setparams(params)
        target.writeframes(audioop.mul(pcm, params.sampwidth, max(0, min(100, volume)) / 100.0))
    return output.getvalue()


@lru_cache(maxsize=256)
def _cached_wave(path, modified_ns, size, max_ms=None):
    bounds = _wav_speech_bounds(path, modified_ns, size)
    with wave.open(path, 'rb') as source:
        if source.getsampwidth() != 2 or source.getcomptype() != 'NONE':
            return None
        rate = source.getframerate()
        start, end = bounds or (0, int(source.getnframes() * 1000 / rate))
        if max_ms is not None and max_ms > 0:
            end = min(end, start + int(max_ms))
        source.setpos(int(start * rate / 1000))
        frames = source.readframes(int((end - start) * rate / 1000))
        output = io.BytesIO()
        with wave.open(output, 'wb') as target:
            target.setparams(source.getparams())
            target.writeframes(frames)
        return output.getvalue()


class Voice:
    def __init__(
        self,
        base_dir: str,
        before_playback: Callable[[], None] | None = None,
        after_playback: Callable[[], None] | None = None,
    ):
        self.base_dir = base_dir
        self.muted = False
        self.current_volume = 100
        self.before_playback = before_playback
        self.after_playback = after_playback
        self._playback_lock = threading.RLock()
        self._playback_depth = 0

        candidates = [
            os.path.join(base_dir, "Assets", "voice"),
            os.path.join(base_dir, "assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "Assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "assets", "voice"),
        ]
        self.voice_dir = next((p for p in candidates if os.path.isdir(p)), candidates[0])

        for key in ('wake', 'listening', 'ready', 'are_you_sure_clean'):
            for path in glob.glob(os.path.join(self.voice_dir, key + '*.wav')):
                try:
                    stat = os.stat(path)
                    _cached_wave(path, stat.st_mtime_ns, stat.st_size)
                except (OSError, ValueError, wave.Error):
                    pass

        self._tts_queue = queue.Queue()
        self._tts_thread = threading.Thread(target=self._tts_loop, name="aidy-tts", daemon=True)
        self._tts_thread.start()

    def _tts_loop(self):
        import pythoncom
        pythoncom.CoInitialize()
        engine = None
        try:
            while True:
                item = self._tts_queue.get()
                if item is None:
                    break
                text, done, result = item
                try:
                    if engine is None:
                        engine = pyttsx3.init()
                        engine.setProperty("rate", 188)
                        for candidate in engine.getProperty("voices"):
                            if "zira" in candidate.name.lower():
                                engine.setProperty("voice", candidate.id)
                                break
                    engine.setProperty("volume", self.current_volume / 100.0)
                    engine.say(text)
                    engine.runAndWait()
                    result.append(True)
                except Exception as exc:
                    print("TTS ERROR:", exc, flush=True)
                    engine = None
                    result.append(False)
                finally:
                    done.set()
        finally:
            if engine is not None:
                engine.stop()
            pythoncom.CoUninitialize()

    def close(self):
        self._tts_queue.put(None)
        if self._tts_thread is not threading.current_thread():
            self._tts_thread.join(timeout=2)

    def _pick_audio(self, key: str) -> str | None:
        exts = [".wav", ".mp3"]
        for ext in exts:
            exact = os.path.join(self.voice_dir, f"{key}{ext}")
            if os.path.exists(exact):
                return exact

        for ext in exts:
            pattern = os.path.join(self.voice_dir, f"{key}_*{ext}")
            matched = [p for p in glob.glob(pattern) if os.path.isfile(p)]
            numbered = []
            fallback = []
            for p in matched:
                base = os.path.splitext(os.path.basename(p))[0]
                if re.fullmatch(rf"{re.escape(key)}_\d+", base):
                    numbered.append(p)
                else:
                    fallback.append(p)
            candidates = numbered if numbered else fallback
            if candidates:
                return random.choice(candidates)
        return None

    def set_volume(self, value: int):
        self.current_volume = max(0, min(100, int(value)))

    def _prepare_tts_text(self, text: str) -> str:
        # SAPI voices can pause too long on punctuation in short assistant replies.
        # Collapse punctuation boundaries to keep responses snappy.
        # Keep question marks: removing them flattens confirmation intonation.
        cleaned = re.sub(r"[,.;:!]+\s*", " ", (text or "").strip())
        return re.sub(r"\s{2,}", " ", cleaned).strip()

    @contextmanager
    def _playback(self):
        # All clips and TTS share one output device. Keep capture suspended for
        # the whole operation, including clip-to-TTS fallback and failures.
        with self._playback_lock:
            outermost = self._playback_depth == 0
            self._playback_depth += 1
            try:
                if outermost and self.before_playback is not None:
                    self.before_playback()
                yield
            finally:
                self._playback_depth -= 1
                if outermost and self.after_playback is not None:
                    self.after_playback()

    def play_key(self, key: str, max_ms: int | None = None) -> bool:
        if self.muted:
            return False
        with self._playback():
            return self._play_key(key, max_ms)

    def _play_key(self, key: str, max_ms: int | None = None) -> bool:
        if self.muted:
            return False
        audio = self._pick_audio(key)
        print(
            "VOICE KEY ONLY:", key,
            "audio:", audio,
            "exists:", bool(audio and os.path.exists(audio)),
            flush=True,
        )
        if not audio or not os.path.exists(audio):
            return False
        return play_audio(audio, alias="aidyvoice", wait=True, max_ms=max_ms,
                          volume=getattr(self, 'current_volume', 100))

    def play_or_tts(self, key: str, fallback_text: str) -> bool:
        if self.muted:
            return False
        with self._playback():
            return self._play_or_tts(key, fallback_text)

    def _play_or_tts(self, key: str, fallback_text: str) -> bool:
        if self.muted:
            return False
        audio = self._pick_audio(key)
        print(
            "VOICE KEY:", key,
            "audio:", audio,
            "exists:", bool(audio and os.path.exists(audio)),
            flush=True
        )

        if audio and os.path.exists(audio):
            ok = play_audio(audio, alias="aidyvoice", wait=True,
                            volume=getattr(self, 'current_volume', 100))
            if ok:
                return True

        return self._tts_blocking(fallback_text)

    def tts_blocking(self, text: str) -> bool:
        if self.muted:
            return False
        with self._playback():
            return self._tts_blocking(text)

    def _tts_blocking(self, text: str) -> bool:
        if self.muted:
            return False
        if hasattr(self, '_tts_queue'):
            done, result = threading.Event(), []
            self._tts_queue.put((self._prepare_tts_text(text), done, result))
            # Keep capture suspended until speech really ends, including long enrollment prompts.
            done.wait()
            return bool(result and result[0])
        # Device-free test doubles retain the synchronous engine interface.
        try:
            self.engine.say(self._prepare_tts_text(text))
            self.engine.runAndWait()
            return True
        except Exception as exc:
            print("TTS ERROR:", exc, flush=True)
            return False
