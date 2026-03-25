from __future__ import annotations
import os
import glob
import queue
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
        self._thread_engine = None  # pyttsx3 engine owned by the dedicated TTS thread

        candidates = [
            os.path.join(base_dir, "Assets", "voice"),
            os.path.join(base_dir, "assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "Assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "assets", "voice"),
        ]
        self.voice_dir = next((p for p in candidates if os.path.isdir(p)), candidates[0])

        # Probe pyttsx3 availability once (no engine kept — all TTS goes
        # through the dedicated worker thread to avoid COM apartment conflicts).
        try:
            probe = pyttsx3.init()
            voices = probe.getProperty("voices")
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
            print(f"[VOICE] pyttsx3 OK, voices={len(voices)}, chosen={chosen or female_id}", flush=True)
            del probe  # Release immediately — never keep a main-thread engine
        except Exception as e:
            print(f"[VOICE] !!! pyttsx3 INIT FAILED: {e}", flush=True)

    # ── Volume ─────────────────────────────────────────────────────────────
    def set_volume(self, vol: int):
        vol = max(0, min(100, int(vol)))
        self.current_volume = vol
        # Volume is applied to _thread_engine on next TTS call via _ensure_tts_engine
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
    # A single persistent daemon thread owns the pyttsx3 COM engine.
    # All TTS requests go through a queue so COM apartment threading is
    # never violated (the engine is created, used, and lives on ONE thread).
    _tts_queue: queue.Queue | None = None
    _tts_thread: threading.Thread | None = None

    def _start_tts_thread(self) -> None:
        """Spin up the persistent TTS worker thread (once)."""
        if self._tts_thread is not None and self._tts_thread.is_alive():
            return
        self._tts_queue = queue.Queue()
        self._tts_thread = threading.Thread(target=self._tts_loop, daemon=True)
        self._tts_thread.start()

    def _tts_loop(self) -> None:
        """Persistent loop: owns the pyttsx3 engine for its entire lifetime."""
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except (ImportError, Exception):
            pass

        engine = None
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 188)
            voices = engine.getProperty("voices")
            for v in voices:
                name = (getattr(v, "name", "") or "").lower()
                if "zira" in name:
                    engine.setProperty("voice", v.id)
                    break
            print("[TTS-THREAD] Engine ready", flush=True)
        except Exception as e:
            print(f"[TTS-THREAD] Engine init failed: {e}", flush=True)

        while True:
            item = self._tts_queue.get()
            if item is None:
                break  # poison pill
            text, result_event, result_box = item
            try:
                if engine is None:
                    engine = pyttsx3.init()
                    engine.setProperty("rate", 188)
                engine.setProperty("volume", self.current_volume / 100.0)
                print(f"[TTS-WORKER] START '{text}'", flush=True)
                engine.say(text)
                engine.runAndWait()
                print(f"[TTS-WORKER] DONE '{text}'", flush=True)
                if result_box is not None:
                    result_box.append(True)
            except Exception as e:
                print(f"[TTS-WORKER] ERROR: {e}", flush=True)
                engine = None  # recreate on next call
                if result_box is not None:
                    result_box.append(False)
            finally:
                if result_event is not None:
                    result_event.set()

    def tts_blocking(self, text: str, timeout_sec: float = 5.0) -> bool:
        """Speak text using pyttsx3 with a timeout guard."""
        if self.muted:
            print(f"AIDY SPEAKING (muted, skipped): {text}", flush=True)
            return False

        self._start_tts_thread()
        print(f"AIDY SPEAKING: {text}", flush=True)

        result_box: list = []
        done_event = threading.Event()
        self._tts_queue.put((text, done_event, result_box))

        if not done_event.wait(timeout=timeout_sec):
            print(f"[TTS] TIMEOUT after {timeout_sec}s — runAndWait hung, proceeding anyway", flush=True)
            return False

        ok = bool(result_box and result_box[0])
        print(f"AIDY DONE SPEAKING. ok={ok}", flush=True)
        return ok

    def tts_fire_and_forget(self, text: str) -> None:
        """Queue TTS without waiting. Returns immediately."""
        if self.muted:
            print(f"AIDY TTS-FF (muted, skipped): {text}", flush=True)
            return
        self._start_tts_thread()
        print(f"AIDY TTS-FF: launching '{text}'", flush=True)
        self._tts_queue.put((text, None, None))
