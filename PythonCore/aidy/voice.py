from __future__ import annotations
import os
import glob
import random
import time
import re
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
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.muted = False
        self.current_volume = 100
        self._thread_engine = None  # Reusable pyttsx3 engine for TTS worker thread

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
        cleaned = re.sub(r"[,.;:!?]+\s*", " ", (text or "").strip())
        return re.sub(r"\s{2,}", " ", cleaned).strip()

    # ── Audio file helpers ─────────────────────────────────────────────────
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
                    time.sleep(0.01)
            return True
        except Exception as e:
            print(f"[VOICE] Audio error: {e}", flush=True)
            return False

    def play_key(self, key: str, max_ms: int | None = None) -> bool:
        if self.muted:
            return False
        audio = self._pick_audio(key)
        if not audio or not os.path.exists(audio):
            return False
        return self._play_audio_file(audio, wait=True, max_ms=max_ms)

    def play_or_tts(self, key: str, fallback_text: str) -> bool:
        if self.muted:
            time.sleep(0.12)
            return False
        audio = self._pick_audio(key)
        if audio and os.path.exists(audio):
            ok = self._play_audio_file(audio, wait=True)
            if ok:
                return True
        return self.tts_blocking(fallback_text)

    # ── TTS ────────────────────────────────────────────────────────────────
    # Lock serialises TTS calls so overlapping fire-and-forget requests
    # don't fight over COM / SAPI resources.
    _tts_lock = threading.Lock()

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
        try:
            with self._tts_lock:
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

    def tts_blocking(self, text: str, timeout_sec: float = 5.0) -> bool:
        """Speak text using pyttsx3 with a timeout guard.

        Runs the TTS engine in a separate daemon thread so that a hung
        ``runAndWait()`` cannot freeze the caller forever.
        """
        if self.muted:
            print(f"AIDY SPEAKING (muted, skipped): {text}", flush=True)
            return False

        print(f"AIDY SPEAKING: {text}", flush=True)
        result_box: list = []
        t = threading.Thread(target=self._tts_worker, args=(text, result_box), daemon=True)
        t.start()
        t.join(timeout=timeout_sec)

        if t.is_alive():
            print(f"[TTS] TIMEOUT after {timeout_sec}s — runAndWait hung, proceeding anyway", flush=True)
            return False

        ok = bool(result_box and result_box[0])
        print(f"AIDY DONE SPEAKING. ok={ok}", flush=True)
        return ok

    def tts_fire_and_forget(self, text: str) -> None:
        """Launch TTS in a background daemon thread without waiting.

        The caller returns immediately.  Used during enrollment so TTS
        can never block the microphone-listening flow.
        """
        if self.muted:
            print(f"AIDY TTS-FF (muted, skipped): {text}", flush=True)
            return
        print(f"AIDY TTS-FF: launching '{text}'", flush=True)
        t = threading.Thread(target=self._tts_worker, args=(text, None), daemon=True)
        t.start()
