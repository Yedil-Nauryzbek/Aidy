import os
import glob
import random
import ctypes
import winsound
from ctypes import wintypes

import pyttsx3

winmm = ctypes.WinDLL("winmm")
mciSendStringW = winmm.mciSendStringW
mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HWND]
mciSendStringW.restype = wintypes.UINT


def mci(cmd: str) -> int:
    return mciSendStringW(cmd, None, 0, None)


def play_mp3_async(path: str, alias: str = "aidyvoice") -> bool:
    # close previous track
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


def play_wav_async(path: str) -> bool:
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except Exception:
        return False


def play_audio_async(path: str, alias: str = "aidyvoice") -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        return play_wav_async(path)
    if ext == ".mp3":
        return play_mp3_async(path, alias=alias)
    return False


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
        # 1) exact: WAV priority
        exact_wav = os.path.join(self.voice_dir, f"{key}.wav")
        if os.path.exists(exact_wav):
            return exact_wav

        exact_mp3 = os.path.join(self.voice_dir, f"{key}.mp3")
        if os.path.exists(exact_mp3):
            return exact_mp3

        # 2) variants
        wav_pattern = os.path.join(self.voice_dir, f"{key}_*.wav")
        wavs = [p for p in glob.glob(wav_pattern) if os.path.isfile(p)]
        if wavs:
            return random.choice(wavs)

        mp3_pattern = os.path.join(self.voice_dir, f"{key}_*.mp3")
        mp3s = [p for p in glob.glob(mp3_pattern) if os.path.isfile(p)]
        if mp3s:
            return random.choice(mp3s)

        return None

    def play_or_tts(self, key: str, fallback_text: str):
        audio_path = self._pick_audio(key)

        print(
            "VOICE KEY:", key,
            "file:", audio_path,
            "exists:", bool(audio_path and os.path.exists(audio_path)),
            flush=True
        )

        # 1) Try WAV/MP3
        if audio_path and os.path.exists(audio_path):
            ok = play_audio_async(audio_path, alias="aidyvoice")
            if ok:
                return

        # 2) TTS fallback (blocking)
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
