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


def play_audio_async(path: str, alias: str = "aidyvoice") -> bool:
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

    def _pick_audio(self, key: str) -> str | None:
        exts = [".wav", ".mp3"]
        for ext in exts:
            exact = os.path.join(self.voice_dir, f"{key}{ext}")
            if os.path.exists(exact):
                return exact

        candidates = []
        for ext in exts:
            pattern = os.path.join(self.voice_dir, f"{key}_*{ext}")
            candidates.extend([p for p in glob.glob(pattern) if os.path.isfile(p)])

        if not candidates:
            return None
        return random.choice(candidates)

    def play_or_tts(self, key: str, fallback_text: str):
        audio = self._pick_audio(key)
        print(
            "VOICE KEY:", key,
            "audio:", audio,
            "exists:", bool(audio and os.path.exists(audio)),
            flush=True
        )

        if audio and os.path.exists(audio):
            ok = play_audio_async(audio, alias="aidyvoice")
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
