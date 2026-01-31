import os
import glob
import random
import ctypes
from ctypes import wintypes

import pyttsx3

winmm = ctypes.WinDLL("winmm")
mciSendStringW = winmm.mciSendStringW
mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HWND]
mciSendStringW.restype = wintypes.UINT


def mci(cmd: str) -> int:
    return mciSendStringW(cmd, None, 0, None)


def play_mp3_async(path: str, alias: str = "aidyvoice") -> bool:
    mci(f"close {alias}")

    p = path.replace('"', '\\"')
    rc = mci(f'open "{p}" type mpegvideo alias {alias}')
    if rc != 0:
        return False

    rc = mci(f"play {alias}")
    if rc != 0:
        mci(f"close {alias}")
        return False

    return True


class Voice:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir

        candidates = [
            os.path.join(base_dir, "Assets", "voice"),
            os.path.join(base_dir, "assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "Assets", "voice"),
            os.path.join(os.path.dirname(base_dir), "assets", "voice"),
        ]
        self.voice_dir = next((p for p in candidates if os.path.isdir(p)), candidates[0])

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", 180)
        self.engine.setProperty("volume", 0.9)

        voices = self.engine.getProperty("voices")
        for v in voices:
            name = (getattr(v, "name", "") or "").lower()
            if "zira" in name or "female" in name:
                self.engine.setProperty("voice", v.id)
                break

    def _pick_mp3(self, key: str) -> str | None:
        exact = os.path.join(self.voice_dir, f"{key}.mp3")
        if os.path.exists(exact):
            return exact

        pattern = os.path.join(self.voice_dir, f"{key}_*.mp3")
        candidates = [p for p in glob.glob(pattern) if os.path.isfile(p)]
        if not candidates:
            return None

        return random.choice(candidates)

    def play_or_tts(self, key: str, fallback_text: str):
        mp3 = self._pick_mp3(key)
        print(
            "VOICE KEY:", key,
            "mp3:", mp3,
            "exists:", bool(mp3 and os.path.exists(mp3)),
            flush=True
        )

        if mp3 and os.path.exists(mp3):
            ok = play_mp3_async(mp3, alias="aidyvoice")
            if ok:
                return

        try:
            self.engine.say(fallback_text)
            self.engine.runAndWait()
        except Exception as e:
            print("TTS ERROR:", e, flush=True)

    def tts_blocking(self, text: str):
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception:
            pass
