import os
import glob
import random
import ctypes
import time
import re
from ctypes import wintypes

import pyttsx3

winmm = ctypes.WinDLL("winmm")
mciSendStringW = winmm.mciSendStringW
mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HWND]
mciSendStringW.restype = wintypes.UINT


def mci(cmd: str) -> int:
    return mciSendStringW(cmd, None, 0, None)


def play_audio(path: str, alias: str = "aidyvoice", wait: bool = False, max_ms: int | None = None) -> bool:
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

    if wait and max_ms is not None and int(max_ms) > 0:
        rc = mci(f"play {alias}")
        if rc != 0:
            mci(f"close {alias}")
            return False
        time.sleep(max(0, int(max_ms)) / 1000.0)
        mci(f"close {alias}")
        return True

    play_cmd = f"play {alias}" + (" wait" if wait else "")
    rc = mci(play_cmd)
    if rc != 0:
        mci(f"close {alias}")
        return False

    if wait:
        mci(f"close {alias}")

    return True


class Voice:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.muted = False

        candidates = [
            os.path.join(base_dir, "Assets", "voice"),
            os.path.join(base_dir, "assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "Assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "assets", "voice"),
        ]
        self.voice_dir = next((p for p in candidates if os.path.isdir(p)), candidates[0])

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", 188)
        self.engine.setProperty("volume", 0.9)

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
        # SAPI voices can pause too long on punctuation in short assistant replies.
        # Collapse punctuation boundaries to keep responses snappy.
        cleaned = re.sub(r"[,.;:!?]+\s*", " ", (text or "").strip())
        return re.sub(r"\s{2,}", " ", cleaned).strip()

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
        return play_audio(audio, alias="aidyvoice", wait=True, max_ms=max_ms)

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
            ok = play_audio(audio, alias="aidyvoice", wait=True)
            if ok:
                return True

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
