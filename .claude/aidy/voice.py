from __future__ import annotations
import os
import glob
import random
import time
import re
import struct
import math
import threading

import pyttsx3

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
try:
    import pygame
    pygame.mixer.init()
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False
    print("WARNING: pygame not installed. Audio playback disabled.")


class Voice:
    # Pre-compiled regexes for TTS text cleanup (avoid recompilation per call)
    _RE_PUNCT = re.compile(r"[,.;:!?]+\s*")
    _RE_MULTI_SPACE = re.compile(r"\s{2,}")

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.muted = False
        self.current_volume = 100
        self._thread_engine = None  # Reusable pyttsx3 engine for TTS worker thread
        self._tts_broken = False   # Set True after first runAndWait hang; skips all future pyttsx3 calls
        self._audio_cache: dict[str, str | None] = {}  # key -> resolved file path cache

        candidates = [
            os.path.join(base_dir, "Assets", "voice"),
            os.path.join(base_dir, "assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "Assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "assets", "voice"),
        ]
        self.voice_dir = next((p for p in candidates if os.path.isdir(p)), candidates[0])

        # ── pyttsx3 ───────────────────────────────────────────────────────
        self.engine = None
        try:
            self.engine = pyttsx3.init()
            self.engine.setProperty("rate", 188)
            self.engine.setProperty("volume", 1.0)

            voices = self.engine.getProperty("voices")
            chosen = None
            female_id = None
            for v in voices:
                name = (getattr(v, "name", "") or "").lower()
                vid = (getattr(v, "id", "") or "").lower()
                if "zira" in name or "zira" in vid:
                    chosen = v.id
                    break
                if female_id is None and "female" in name:
                    female_id = v.id
            if chosen:
                self.engine.setProperty("voice", chosen)
            elif female_id:
                self.engine.setProperty("voice", female_id)

            print(f"[VOICE] pyttsx3 OK, voices={len(voices)}, chosen={chosen or female_id}", flush=True)
        except Exception as e:
            print(f"[VOICE] !!! pyttsx3 INIT FAILED: {e}", flush=True)
            self.engine = None

    # ── Pre-generate TTS clips ──────────────────────────────────────────
    def generate_tts_clip(self, text: str, key: str, overwrite: bool = False) -> str | None:
        """Render *text* to a .wav file in the voice directory via pyttsx3.

        The file is saved as ``<key>.wav`` and cached so ``play_key(key)``
        can play it instantly via pygame without touching pyttsx3 at runtime.
        If *overwrite* is True, existing files are replaced.
        Returns the file path on success, None on failure.
        """
        if self.engine is None:
            return None
        out_path = os.path.join(self.voice_dir, f"{key}.wav")
        if os.path.exists(out_path) and not overwrite:
            # Already generated — just register in cache
            self._audio_cache[key] = out_path
            return out_path
        try:
            os.makedirs(self.voice_dir, exist_ok=True)
            # Remove old mp3 version if overwriting to avoid stale clips
            if overwrite:
                mp3_path = os.path.join(self.voice_dir, f"{key}.mp3")
                if os.path.exists(mp3_path):
                    try:
                        os.remove(mp3_path)
                    except Exception:
                        pass
            self.engine.save_to_file(text, out_path)
            self.engine.runAndWait()
            if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                self._audio_cache[key] = out_path
                print(f"[VOICE] Generated TTS clip: {key}.wav", flush=True)
                return out_path
        except Exception as e:
            print(f"[VOICE] Failed to generate TTS clip '{key}': {e}", flush=True)
        return None

    # ── Volume ─────────────────────────────────────────────────────────────
    def set_volume(self, vol: int):
        vol = max(0, min(100, int(vol)))
        self.current_volume = vol
        if self.engine:
            try:
                self.engine.setProperty("volume", vol / 100.0)
            except Exception:
                pass
        print(f"[VOICE] Volume set to {vol}%", flush=True)

    # ── Text cleanup (from repo) ──────────────────────────────────────────
    def _prepare_tts_text(self, text: str) -> str:
        cleaned = self._RE_PUNCT.sub(" ", (text or "").strip())
        return self._RE_MULTI_SPACE.sub(" ", cleaned).strip()

    # ── Audio file helpers ─────────────────────────────────────────────────
    def _pick_audio(self, key: str) -> str | None:
        # Check cache first — avoids repeated filesystem I/O
        cached = self._audio_cache.get(key)
        if cached is not None:
            return cached

        exts = [".wav", ".mp3"]
        for ext in exts:
            exact = os.path.join(self.voice_dir, f"{key}{ext}")
            if os.path.exists(exact):
                self._audio_cache[key] = exact
                return exact

        key_escaped = re.escape(key)
        for ext in exts:
            pattern = os.path.join(self.voice_dir, f"{key}_*{ext}")
            matched = [p for p in glob.glob(pattern) if os.path.isfile(p)]
            numbered = []
            fallback = []
            for p in matched:
                base = os.path.splitext(os.path.basename(p))[0]
                if re.fullmatch(rf"{key_escaped}_\d+", base):
                    numbered.append(p)
                else:
                    fallback.append(p)
            candidates = numbered if numbered else fallback
            if candidates:
                choice = random.choice(candidates)
                # Cache the candidates list for random keys; for exact keys cache directly
                if len(candidates) == 1:
                    self._audio_cache[key] = choice
                return choice

        self._audio_cache[key] = None  # Cache miss to avoid repeated lookups
        return None

    def _play_audio_file(self, path: str, wait: bool, max_ms: int | None = None) -> bool:
        if not HAS_PYGAME:
            return False
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(self.current_volume / 100.0)
            pygame.mixer.music.play()
            if wait:
                t0 = time.time()
                while pygame.mixer.music.get_busy():
                    if max_ms and (time.time() - t0) * 1000 > max_ms:
                        pygame.mixer.music.stop()
                        break
                    time.sleep(0.008)  # ~8ms polling for fast end detection
            return True
        except Exception as e:
            print(f"[VOICE] Audio error: {e}", flush=True)
            return False

    def stop_playback(self):
        """Immediately stop any audio currently playing."""
        if HAS_PYGAME:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass

    def play_key(self, key: str, max_ms: int | None = None) -> bool:
        if self.muted:
            return False
        audio = self._pick_audio(key)
        if not audio or not os.path.exists(audio):
            return False
        return self._play_audio_file(audio, wait=True, max_ms=max_ms)

    def play_key_async(self, key: str) -> bool:
        """Start playing an audio clip without waiting for it to finish.

        Returns True if playback was started successfully.
        """
        if self.muted:
            return False
        audio = self._pick_audio(key)
        if not audio or not os.path.exists(audio):
            return False
        return self._play_audio_file(audio, wait=False)

    def is_tts_available(self) -> bool:
        """Return True if the TTS lock is free (no other call in progress)."""
        if self._tts_lock.acquire(blocking=False):
            self._tts_lock.release()
            return True
        return False

    def play_or_tts(self, key: str, fallback_text: str) -> bool:
        if self.muted:
            return False
        audio = self._pick_audio(key)
        if audio and os.path.exists(audio):
            ok = self._play_audio_file(audio, wait=True)
            if ok:
                return True
        # Skip TTS if another call is in progress (e.g., startup "Aidy is ready")
        # to avoid blocking the command execution flow for up to 10s.
        if not self.is_tts_available():
            print(f"AIDY SPEAKING (tts busy, skipped): {fallback_text}", flush=True)
            return False
        return self.tts_blocking(fallback_text)

    def play_or_tts_async(self, key: str, fallback_text: str) -> bool:
        """Play audio clip async (non-blocking) or fire-and-forget TTS.

        Returns True if playback was started. The caller can proceed
        immediately — useful for short confirmations like "Opening X"
        where we don't need to wait for the clip to finish.
        """
        if self.muted:
            return False
        audio = self._pick_audio(key)
        if audio and os.path.exists(audio):
            ok = self._play_audio_file(audio, wait=False)
            if ok:
                return True
        # Fall back to fire-and-forget TTS (non-blocking)
        self.tts_fire_and_forget(fallback_text)
        return True

    # ── TTS ────────────────────────────────────────────────────────────────
    # Lock serialises TTS calls so overlapping fire-and-forget requests
    # don't fight over COM / SAPI resources.
    _tts_lock = threading.Lock()
    _TTS_LOCK_TIMEOUT = 8.0  # max seconds to wait for lock before giving up

    def _ensure_tts_engine(self):
        """Create or reuse a pyttsx3 engine for the current thread."""
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except (ImportError, Exception):
            pass
        if self._thread_engine is None:
            self._thread_engine = pyttsx3.init()
            self._thread_engine.setProperty("rate", 188)
            voices = self._thread_engine.getProperty("voices")
            for v in voices:
                name = (getattr(v, "name", "") or "").lower()
                if "zira" in name:
                    self._thread_engine.setProperty("voice", v.id)
                    break
        self._thread_engine.setProperty("volume", self.current_volume / 100.0)
        return self._thread_engine

    def _tts_worker(self, text: str, result_box: list | None):
        """Run pyttsx3 say+runAndWait in a dedicated thread."""
        acquired = False
        try:
            acquired = self._tts_lock.acquire(timeout=self._TTS_LOCK_TIMEOUT)
            if not acquired:
                # Lock held too long — previous TTS likely hung.
                # Do NOT release a lock we don't own; just skip this call.
                # tts_blocking() will mark _tts_broken if the owning thread
                # is still alive after its own timeout, preventing future hangs.
                print(f"[TTS-WORKER] SKIP '{text}' — lock held (previous TTS hung)", flush=True)
                if result_box is not None:
                    result_box.append(False)
                return

            print(f"[TTS-WORKER] START '{text}'", flush=True)
            engine = self._ensure_tts_engine()
            engine.say(text)
            engine.runAndWait()
            print(f"[TTS-WORKER] DONE '{text}'", flush=True)
            if result_box is not None:
                result_box.append(True)
        except Exception as e:
            print(f"[TTS-WORKER] ERROR: {e}", flush=True)
            self._thread_engine = None  # Reset on failure so next call recreates
            if result_box is not None:
                result_box.append(False)
        finally:
            if acquired:
                try:
                    self._tts_lock.release()
                except RuntimeError:
                    pass

    def tts_blocking(self, text: str, timeout_sec: float = 10.0) -> bool:
        """Speak text using pyttsx3 with a timeout guard.

        Runs the TTS engine in a separate daemon thread so that a hung
        ``runAndWait()`` cannot freeze the caller forever.
        """
        if self.muted:
            print(f"AIDY SPEAKING (muted, skipped): {text}", flush=True)
            return False

        if self._tts_broken:
            print(f"AIDY SPEAKING (tts broken, skipped): {text}", flush=True)
            return False

        print(f"AIDY SPEAKING: {text}", flush=True)
        result_box: list = []
        t = threading.Thread(target=self._tts_worker, args=(text, result_box), daemon=True)
        t.start()
        t.join(timeout=timeout_sec)

        if t.is_alive():
            print(f"[TTS] TIMEOUT after {timeout_sec}s — runAndWait hung; disabling TTS permanently", flush=True)
            # Mark TTS as permanently broken so we never block again.
            # The hung daemon thread will die when the process exits.
            # Do NOT replace the class lock — it creates split-brain where
            # the hung thread still holds the old lock object.
            self._tts_broken = True
            self._thread_engine = None
            return False

        ok = bool(result_box and result_box[0])
        print(f"AIDY DONE SPEAKING. ok={ok}", flush=True)
        return ok

    def warmup_tts(self) -> None:
        """Pre-initialise the TTS worker-thread engine in the background.

        Calling this early (e.g. during startup) eliminates the ~1-3 s
        cold-start penalty on the first real TTS call (COM init, voice
        enumeration, pyttsx3.init).
        """
        def _warmup():
            try:
                self._ensure_tts_engine()
                print("[TTS-WARMUP] engine ready", flush=True)
            except Exception as e:
                print(f"[TTS-WARMUP] failed: {e}", flush=True)
        t = threading.Thread(target=_warmup, daemon=True)
        t.start()

    def tts_fire_and_forget(self, text: str) -> None:
        """Launch TTS in a background daemon thread without waiting.

        The caller returns immediately.  Used during enrollment so TTS
        can never block the microphone-listening flow.
        """
        if self.muted:
            print(f"AIDY TTS-FF (muted, skipped): {text}", flush=True)
            return
        if self._tts_broken:
            print(f"AIDY TTS-FF (tts broken, skipped): {text}", flush=True)
            return
        print(f"AIDY TTS-FF: launching '{text}'", flush=True)
        t = threading.Thread(target=self._tts_worker, args=(text, None), daemon=True)
        t.start()

    # ── Access-denied beep ─────────────────────────────────────────────
    def play_access_denied_beep(self) -> bool:
        """Play a short dual-tone 'access denied' beep via pygame.

        Generates two descending tones (880 Hz → 440 Hz) in memory —
        no external WAV file required.  Returns True on success.
        """
        if self.muted:
            return False
        if not HAS_PYGAME:
            return False
        try:
            sample_rate = 22050
            # First tone: 880 Hz for 120 ms
            t1_dur = 0.12
            n1 = int(sample_rate * t1_dur)
            # Second tone: 440 Hz for 180 ms
            t2_dur = 0.18
            n2 = int(sample_rate * t2_dur)
            # 60 ms silence gap
            gap = int(sample_rate * 0.06)

            total = n1 + gap + n2
            buf = bytearray(total * 2)  # 16-bit mono

            vol = self.current_volume / 100.0
            amplitude = int(24000 * vol)

            # Tone 1 — 880 Hz
            for i in range(n1):
                fade = 1.0 - (i / n1) * 0.3  # slight fade
                val = int(amplitude * fade * math.sin(2 * math.pi * 880 * i / sample_rate))
                struct.pack_into('<h', buf, i * 2, max(-32768, min(32767, val)))

            # Gap — silence (already zero)

            # Tone 2 — 440 Hz
            offset = (n1 + gap) * 2
            for i in range(n2):
                fade = 1.0 - (i / n2) * 0.5
                val = int(amplitude * fade * math.sin(2 * math.pi * 440 * i / sample_rate))
                struct.pack_into('<h', buf, offset + i * 2, max(-32768, min(32767, val)))

            sound = pygame.mixer.Sound(buffer=bytes(buf))
            sound.set_volume(vol)
            sound.play()
            # Wait for it to finish (~360 ms)
            pygame.time.wait(int((t1_dur + 0.06 + t2_dur) * 1000) + 50)
            return True
        except Exception as e:
            print(f"[VOICE] access-denied beep error: {e}", flush=True)
            return False
