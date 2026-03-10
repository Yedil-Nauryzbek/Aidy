from __future__ import annotations
import os
import glob
import random
import time
import re

import pyttsx3

# ??????????? pygame ??? ??????????? ?????????? ??????
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
try:
    import pygame
    pygame.mixer.init()
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False
    print("WARNING: pygame not installed. Audio playback disabled. Run 'pip install pygame'")


class Voice:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.muted = False
        self.current_volume = 100

        candidates = [
            os.path.join(base_dir, "Assets", "voice"),
            os.path.join(base_dir, "assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "Assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "assets", "voice"),
        ]
        self.voice_dir = next((p for p in candidates if os.path.isdir(p)), candidates[0])

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", 188)
        self.engine.setProperty("volume", self.current_volume / 100.0)

        voices = self.engine.getProperty("voices")
        zira_id = None
        female_id = None
        for v in voices:
            name = (getattr(v, "name", "") or "").lower()
            vid = (getattr(v, "id", "") or "").lower()
            if "zira" in name or "zira" in vid:
                zira_id = v.id
                break
            if female_id is None and "female" in name:
                female_id = v.id
        if zira_id:
            self.engine.setProperty("voice", zira_id)
        elif female_id:
            self.engine.setProperty("voice", female_id)

    def set_volume(self, vol: int):
        vol = max(0, min(100, int(vol)))
        self.current_volume = vol
        # ????????? ????????? ??? ?????? TTS
        self.engine.setProperty("volume", vol / 100.0)
        print(f"[VOICE] Volume set to {vol}%")

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

    def _prepare_tts_text(self, text: str) -> str:
        cleaned = re.sub(r"[,.;:!?]+\s*", " ", (text or "").strip())
        return re.sub(r"\s{2,}", " ", cleaned).strip()

    def _play_audio_file(self, path: str, wait: bool, max_ms: int | None = None) -> bool:
        if not HAS_PYGAME:
            return False
        
        try:
            pygame.mixer.music.load(path)
            # ????????? ??????? ????????? (pygame ????????? ?? 0.0 ?? 1.0)
            pygame.mixer.music.set_volume(self.current_volume / 100.0)
            pygame.mixer.music.play()

            if wait:
                start_time = time.time()
                # ????, ???? ?????? ??????
                while pygame.mixer.music.get_busy():
                    if max_ms and (time.time() - start_time) * 1000 > max_ms:
                        pygame.mixer.music.stop()
                        break
                    time.sleep(0.01)
            return True
        except Exception as e:
            print(f"[VOICE] Error playing audio: {e}")
            return False

    def play_key(self, key: str, max_ms: int | None = None) -> bool:
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
            
        return self._play_audio_file(audio, wait=True, max_ms=max_ms)

    def play_or_tts(self, key: str, fallback_text: str) -> bool:
        if self.muted:
            time.sleep(0.12)
            return False
        audio = self._pick_audio(key)
        print(
            "VOICE KEY:", key,
            "audio:", audio,
            "exists:", bool(audio and os.path.exists(audio)),
            flush=True
        )

        if audio and os.path.exists(audio):
            ok = self._play_audio_file(audio, wait=True)
            if ok:
                return True

        # ???? ????? ???, ?????????? TTS (? ???? ????????? ??? ??????????? ? set_volume)
        try:
            self.engine.say(self._prepare_tts_text(fallback_text))
            self.engine.runAndWait()
            return True
        except Exception as e:
            print("TTS ERROR:", e, flush=True)
            return False

    def tts_blocking(self, text: str) -> bool:
        if self.muted:
            return False
        try:
            self.engine.say(self._prepare_tts_text(text))
            self.engine.runAndWait()
            return True
        except Exception:
            return False