import os
import sys
import json
import time
import zipfile
import urllib.request
import csv
import audioop
import ctypes
import subprocess

import vosk
import pyaudio

class MockRecognizer:
    def __init__(self, is_wake=False):
        self.is_wake = is_wake
        self.call_count = 0

    def AcceptWaveform(self, data):
        # Simulate recognition after a few calls
        self.call_count += 1
        if self.call_count >= 5:  # Trigger after 5 calls (about 1 second at 4000 samples)
            self.call_count = 0
            return True
        return False

    def Result(self):
        if self.is_wake:
            return '{"text": "hey aidy"}'
        else:
            return '{"text": "open browser"}'

    def PartialResult(self):
        return '{"partial": ""}'

    def FinalResult(self):
        return '{"text": ""}'

from .config import (
    API_URL,
    WAKE_KEYWORDS,
    is_wake_phrase,
    SAMPLE_RATE,
    CHUNK_SAMPLES,
    FRAME_MS,
    VAD_START_THRESHOLD,
    VAD_SILENCE_MS,
    CONFIRM_GRAMMAR_PHRASES,
    WINDOW_SWITCH_GRAMMAR,
    WINDOW_SWITCH_LEFT,
    WINDOW_SWITCH_RIGHT,
    WINDOW_SWITCH_DONE,
    WINDOW_SWITCH_CANCEL,
    VOICE_RESPONSES,
)
from .logui import ui_state, ui_command, debug, info, warn, error, UI_MODE, LOG_LEVEL
from .voice import Voice
from .apps import (
    load_apps_config,
    extract_app_name,
    extract_close_app_name,
    find_app,
    launch_app,
    close_app,
)
from .system import (
    run_powershell_hidden,
    open_cmd_new_console,
    show_desktop,
    take_screenshot,
    open_task_manager,
    parse_first_int,
    set_volume_percent,
    volume_steps,
)
from .intent_api import start_local_intent_api, IntentAPI


COMMANDS = {
    "brightness up": lambda: run_powershell_hidden(
        "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, 80)"
    ),
    "brightness down": lambda: run_powershell_hidden(
        "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, 30)"
    ),
    "shutdown": lambda: os.system("shutdown /s /t 5"),
    "restart": lambda: os.system("shutdown /r /t 5"),
    "lock": lambda: ctypes.windll.user32.LockWorkStation(),
    "open cmd": lambda: open_cmd_new_console(keep_open=True, cmdline=None),
    "show desktop": lambda: show_desktop(),
    "screenshot": lambda: take_screenshot(),
    "task manager": lambda: open_task_manager(),
}


def load_command_phrases(base_dir: str):
    candidates = [
        os.path.join(base_dir, "commands.csv"),
        os.path.join(base_dir, "intents.csv"),
        os.path.join(base_dir, "dataset.csv"),
    ]

    phrases = set()
    used_file = None

    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row:
                        continue
                    if len(row) >= 2 and row[0].strip().lower() == "command" and row[1].strip().lower() == "intent":
                        continue
                    cmd = (row[0] if len(row) >= 1 else "").strip().strip('"').strip("'").lower()
                    if cmd:
                        phrases.add(cmd)

            if phrases:
                used_file = os.path.basename(path)
                break
        except Exception as e:
            warn(f"CSV read failed ({os.path.basename(path)}): {e}")

    if not phrases:
        phrases = set(COMMANDS.keys()) | {"volume up", "volume down"}
        warn(f"No CSV dataset рядом с Aidy.py. Using {len(phrases)} phrases from built-ins.")
    else:
        info(f"Command phrases loaded: {len(phrases)} (from {used_file})")

    return sorted(phrases)


class Aidy:
    DEAFEN_MS_AFTER_TTS = 650
    FLUSH_MS = 250
    _SHORT_PATH_ENABLED = True

    def _short_path(self, path: str) -> str:
        if not self._SHORT_PATH_ENABLED:
            return path
        try:
            from ctypes import wintypes
            GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
            GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            GetShortPathNameW.restype = wintypes.DWORD

            buf = ctypes.create_unicode_buffer(260)
            n = GetShortPathNameW(path, buf, len(buf))
            if n > 0 and buf.value:
                return buf.value
        except Exception:
            pass
        return path

    def _flush_audio(self, ms: int):
        if not self.stream:
            return
        frames = int(ms / (CHUNK_SAMPLES / SAMPLE_RATE * 1000.0))
        for _ in range(max(1, frames)):
            self.stream.read(CHUNK_SAMPLES, exception_on_overflow=False)

    def _deafen_after_speak(self, ms: int | None = None):
        if not self.stream:
            return
        if ms is None:
            ms = self.DEAFEN_MS_AFTER_TTS

        self._flush_audio(self.FLUSH_MS)

        end = time.time() + (ms / 1000.0)
        while time.time() < end:
            self.stream.read(CHUNK_SAMPLES, exception_on_overflow=False)

    def __init__(self, base_dir: str | None = None):
        if base_dir:
            self.base_dir = os.path.abspath(base_dir)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))

        self.model_path = os.path.join(self.base_dir, "vosk-model-small-en-us-0.15")
        self.model_url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

        model_file = os.path.join(self.model_path, "am", "final.mdl")
        if not os.path.exists(model_file):
            info("Vosk model missing -> downloading...")
            zip_path = os.path.join(self.base_dir, "vosk_model.zip")
            try:
                urllib.request.urlretrieve(self.model_url, zip_path)
                with zipfile.ZipFile(zip_path, "r") as z:
                    z.extractall(self.base_dir)
                # os.remove(zip_path)
                info("Vosk model downloaded")
            except Exception as e:
                warn(f"Failed to download Vosk model: {e}")

        try:
            model_path = self._short_path(self.model_path)
            self.model = vosk.Model(model_path)
        except Exception as e:
            warn(f"Failed to load Vosk model: {e}")
            self.model = None

        self.command_phrases = load_command_phrases(self.base_dir)
        self.command_phrases = sorted(
            set(self.command_phrases) | set(CONFIRM_GRAMMAR_PHRASES) | set(WINDOW_SWITCH_GRAMMAR)
        )

        self.apps = load_apps_config(self.base_dir)

        self.audio = pyaudio.PyAudio()
        self.stream = None

        self.voice = Voice(self.base_dir)
        ok = start_local_intent_api(self.base_dir)
        if not ok:
            warn("Local Intent API not started. Will try anyway.")
        self.api = IntentAPI(API_URL)

        self.wake_recognizer = self._new_wake_recognizer() if self.model is not None else None

        self.window_switch_active = False
        self.window_switch_silence_hits = 0

    def start_stream(self):
        if self.stream is not None:
            return
        try:
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SAMPLES
            )
            self.stream.start_stream()
            debug("Audio stream started")
        except Exception as e:
            warn(f"Failed to start audio stream: {e}. Using mock mode.")
            self.stream = None  # Indicate mock mode

    def stop_stream(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
            debug("Audio stream stopped")

    def _new_wake_recognizer(self):
        if self.model is None or self.stream is None:
            return MockRecognizer(is_wake=True)
        rec = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
        rec.SetWords(False)
        return rec

    def _new_command_recognizer(self):
        if self.model is None or self.stream is None:
            return MockRecognizer(is_wake=False)
        grammar = json.dumps(self.command_phrases)
        rec = vosk.KaldiRecognizer(self.model, SAMPLE_RATE, grammar)
        rec.SetWords(True)
        return rec

    def _key_down(self, vk: int):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)

    def _key_up(self, vk: int):
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    def _press(self, vk: int):
        self._key_down(vk)
        self._key_up(vk)

    def _open_default_browser(self) -> bool:
        try:
            subprocess.Popen(
                ["cmd.exe", "/C", "start", "", "https://www.google.com"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            return False

    def start_window_switch(self):
        VK_ALT = 0x12
        VK_TAB = 0x09

        self.window_switch_active = True
        self.window_switch_silence_hits = 0

        self._key_down(VK_ALT)
        self._press(VK_TAB)

        ui_state("SPEAKING")
        self.voice.play_or_tts("window_switch_mode", "Say left or right. Say done to select.")
        self._deafen_after_speak()
        ui_state("IDLE")

    def window_switch_step(self, direction: str):
        VK_TAB = 0x09
        VK_SHIFT = 0x10

        if direction == "right":
            self._press(VK_TAB)
            return

        self._key_down(VK_SHIFT)
        self._press(VK_TAB)
        self._key_up(VK_SHIFT)

    def end_window_switch(self, cancel: bool = False):
        VK_ALT = 0x12
        self._key_up(VK_ALT)
        self.window_switch_active = False

        ui_state("SPEAKING")
        if cancel:
            self.voice.play_or_tts("window_switch_cancel", "Cancelled.")
            self._deafen_after_speak()
        else:
            self.voice.play_or_tts("window_switch_done", "Done.")
            self._deafen_after_speak()
        ui_state("IDLE")

    def wait_for_wake(self):
        ui_state("LISTENING")
        info("Wake: listening...")

        self.wake_recognizer = self._new_wake_recognizer()

        last_logged = ""
        last_log_t = 0.0

        while True:
            if self.stream:
                data = self.stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
            else:
                data = b'\x00' * (CHUNK_SAMPLES * 2)  # Mock silence data

            if self.wake_recognizer.AcceptWaveform(data):
                r = json.loads(self.wake_recognizer.Result())
                text = (r.get("text", "") or "").lower().strip()
                text = " ".join(text.split())
                if not text:
                    continue

                now = time.time()
                if text != last_logged or (now - last_log_t) > 1.0:
                    info(f'Wake Heard: "{text}"')
                    last_logged = text
                    last_log_t = now

                if is_wake_phrase(text):
                    ui_state("PROCESSING")
                    info(f'Wake detected: "{text}"')
                    self.voice.play_or_tts("wake", "I am here, sir")
                    self._deafen_after_speak()
                    return

    def listen_command_vosk(self, max_seconds=6, min_listen_ms=2000):
        ui_state("LISTENING")
        info("Command: listening...")

        rec = self._new_command_recognizer()

        started = False
        silence_ms = 0
        start_time = time.time()
        best_final = ""

        while time.time() - start_time < max_seconds:
            if self.stream is None:
                data = b'\x00' * (CHUNK_SAMPLES * 2)
            else:
                data = self.stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
            rms = audioop.rms(data, 2)

            elapsed_ms = int((time.time() - start_time) * 1000)

            if not started:
                if rms >= VAD_START_THRESHOLD:
                    started = True
                    silence_ms = 0
                    debug(f"VAD: start (rms={rms})")
                else:
                    if elapsed_ms < min_listen_ms:
                        continue
            else:
                if rms < VAD_START_THRESHOLD:
                    silence_ms += FRAME_MS
                else:
                    silence_ms = 0

            if rec.AcceptWaveform(data):
                r = json.loads(rec.Result())
                t = (r.get("text") or "").strip().lower()
                if t:
                    best_final = t

            if started and silence_ms >= VAD_SILENCE_MS:
                debug("VAD: stop (silence)")
                break

        if not best_final:
            r = json.loads(rec.FinalResult())
            best_final = (r.get("text") or "").strip().lower()

        if not best_final:
            ui_state("IDLE")
            warn("Command: empty")
            self.voice.play_or_tts("not_heard", "I didn't catch that")
            self._deafen_after_speak()
            return None

        ui_command(best_final)
        info(f"Heard: \"{best_final}\"")
        return best_final

    def process_command(self, text: str):
        if self.window_switch_active:
            t = (text or "").strip().lower()

            if t in WINDOW_SWITCH_RIGHT:
                ui_state("EXECUTING")
                self.window_switch_step("right")
                ui_state("IDLE")
                return True

            if t in WINDOW_SWITCH_LEFT:
                ui_state("EXECUTING")
                self.window_switch_step("left")
                ui_state("IDLE")
                return True

            if t in WINDOW_SWITCH_DONE:
                self.end_window_switch(cancel=False)
                return True

            if t in WINDOW_SWITCH_CANCEL:
                self.end_window_switch(cancel=True)
                return False

            ui_state("SPEAKING")
            self.voice.play_or_tts("window_switch_help", "Left or right, sir. Say done.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        t0 = (text or "").strip().lower()
        if t0.startswith(("close ", "quit ", "exit ", "kill ", "stop ")):
            app_name = extract_close_app_name(t0)
            app = find_app(self.apps, app_name)

            if not app:
                ui_state("WARNING")
                self.voice.play_or_tts("app_not_found", "I couldn't find that app")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False

            ui_state("SPEAKING")
            self.voice.play_or_tts("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            ok = close_app(app)

            if ok:
                ui_state("SUCCESS")
                time.sleep(3.18)
                ui_state("IDLE")
                return True

            ui_state("ERROR")
            self.voice.play_or_tts("close_app_fail", "Sorry, I couldn't close it")
            self._deafen_after_speak()
            time.sleep(0.18)
            ui_state("IDLE")
            return False

        t0 = " ".join((text or "").lower().split())

        # Offline direct commands without Intent API
        if t0 in COMMANDS:
            response = VOICE_RESPONSES.get(t0, f"Executing {t0}")
            ui_state("SPEAKING")
            self.voice.play_or_tts(t0.replace(" ", "_"), response)
            self._deafen_after_speak()

            ui_state("EXECUTING")
            info(f"Exec: {t0}")
            try:
                COMMANDS[t0]()
                ui_state("SUCCESS")
                time.sleep(0.18)
                ui_state("IDLE")
                return True
            except Exception as e:
                ui_state("ERROR")
                error(f"Exec failed: {e}")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                time.sleep(0.18)
                ui_state("IDLE")
                return False

        if t0 in ("volume up", "sound up", "increase volume", "louder", "make it louder"):
            ui_state("SPEAKING")
            self.voice.play_or_tts("volume_up", VOICE_RESPONSES.get("volume up", "Turning it up"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            volume_steps(up=True, steps=6)
            ui_state("SUCCESS")
            time.sleep(0.18)
            ui_state("IDLE")
            return True

        if t0 in ("volume down", "sound down", "decrease volume", "quieter", "make it quieter"):
            ui_state("SPEAKING")
            self.voice.play_or_tts("volume_down", VOICE_RESPONSES.get("volume down", "Turning it down"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            volume_steps(up=False, steps=6)
            ui_state("SUCCESS")
            time.sleep(0.18)
            ui_state("IDLE")
            return True

        if t0 in ("brightness up", "increase brightness", "brighten screen", "make screen brighter"):
            ui_state("SPEAKING")
            self.voice.play_or_tts("brightness_up", VOICE_RESPONSES.get("brightness up", "Making it brighter"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            COMMANDS["brightness up"]()
            ui_state("SUCCESS")
            time.sleep(0.18)
            ui_state("IDLE")
            return True

        if t0 in ("brightness down", "decrease brightness", "dim screen", "make screen darker"):
            ui_state("SPEAKING")
            self.voice.play_or_tts("brightness_down", VOICE_RESPONSES.get("brightness down", "Making it dimmer"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            COMMANDS["brightness down"]()
            ui_state("SUCCESS")
            time.sleep(0.18)
            ui_state("IDLE")
            return True

        # Direct app name without "open"/"launch" (e.g., "steam", "chrome")
        app = find_app(self.apps, t0)
        if app:
            ui_state("SPEAKING")
            self.voice.play_or_tts("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            ok = launch_app(app)

            if ok:
                ui_state("SUCCESS")
                time.sleep(0.18)
                ui_state("IDLE")
                return True
            else:
                if "browser" in app.get("aliases", []) or app.get("id") in ("chrome", "browser"):
                    if self._open_default_browser():
                        ui_state("SUCCESS")
                        time.sleep(0.18)
                        ui_state("IDLE")
                        return True

                ui_state("ERROR")
                self.voice.play_or_tts("open_app_fail", "Sorry, I couldn't open it")
                self._deafen_after_speak()
                time.sleep(0.18)
                ui_state("IDLE")
                return False

        if t0.startswith(("open ", "launch ", "start ", "run ")):
            app_name = extract_app_name(t0)
            app = find_app(self.apps, app_name)

            if app:
                ui_state("SPEAKING")
                self.voice.play_or_tts("open_app", f"Opening {app['id']}")
                self._deafen_after_speak()

                ui_state("EXECUTING")
                ok = launch_app(app)

                if ok:
                    ui_state("SUCCESS")
                    time.sleep(0.18)
                    ui_state("IDLE")
                    return True
                else:
                    # Fallback: open default browser if "browser" requested but app not found
                    if "browser" in app.get("aliases", []) or app.get("id") in ("chrome", "browser"):
                        if self._open_default_browser():
                            ui_state("SUCCESS")
                            time.sleep(0.18)
                            ui_state("IDLE")
                            return True

                    ui_state("ERROR")
                    self.voice.play_or_tts("open_app_fail", "Sorry, I couldn't open it")
                    self._deafen_after_speak()
                    time.sleep(0.18)
                    ui_state("IDLE")
                    return False

        if t0 == "switch" or t0.startswith("switch ") or t0 in ("switch app", "switch window"):
            ui_state("EXECUTING")
            self.start_window_switch()
            return True

        ui_state("PROCESSING")
        info("Intent: sending to API...")

        result = self.api.get_intent(text)
        if not result:
            ui_state("OFFLINE")
            self.voice.play_or_tts("offline", "Sorry, I couldn't connect to the server")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        intent = (result.get("intent") or "").strip().lower()
        confidence = float(result.get("confidence", 0) or 0)

        info(f"Intent: {intent}  conf={confidence:.2f}")

        if confidence < 0.4:
            ui_state("WARNING")
            self.voice.play_or_tts("not_sure", "I'm not sure what you mean")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        if intent in ("volume up", "volume down"):
            n = parse_first_int(text)
            up = (intent == "volume up")
            t = (text or "").lower()
            wants_absolute = (" to " in f" {t} ") or ("%" in t) or ("percent" in t)

            if n is None:
                steps = 6
                ui_state("SPEAKING")
                self.voice.play_or_tts(intent.replace(" ", "_"), VOICE_RESPONSES.get(intent, "Adjusting volume"))
                self._deafen_after_speak()
                ui_state("EXECUTING")
                volume_steps(up, steps)
                ui_state("SUCCESS")
                time.sleep(0.18)
                ui_state("IDLE")
                return True

            if wants_absolute:
                ui_state("SPEAKING")
                self.voice.play_or_tts("set_volume", f"Setting volume to {n} percent")
                self._deafen_after_speak()
                ui_state("EXECUTING")
                ok = set_volume_percent(n)
                if not ok:
                    volume_steps(up=True, steps=1)
                ui_state("SUCCESS")
                time.sleep(0.18)
                ui_state("IDLE")
                return True

            steps = max(1, n)
            ui_state("SPEAKING")
            self.voice.play_or_tts(intent.replace(" ", "_"), VOICE_RESPONSES.get(intent, "Adjusting volume"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            volume_steps(up, steps)
            ui_state("SUCCESS")
            time.sleep(0.18)
            ui_state("IDLE")
            return True

        if intent == "switch window":
            ui_state("EXECUTING")
            self.start_window_switch()
            return True

        if intent == "open app":
            app_name = extract_app_name(text)
            app = find_app(self.apps, app_name)

            if not app:
                ui_state("WARNING")
                self.voice.play_or_tts("app_not_found", "I couldn't find that app")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False

            ui_state("SPEAKING")
            self.voice.play_or_tts("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            ok = launch_app(app)

            if ok:
                ui_state("SUCCESS")
                time.sleep(0.18)
                ui_state("IDLE")
                return True
            else:
                if "browser" in app.get("aliases", []) or app.get("id") in ("chrome", "browser"):
                    if self._open_default_browser():
                        ui_state("SUCCESS")
                        time.sleep(0.18)
                        ui_state("IDLE")
                        return True
                ui_state("ERROR")
                self.voice.play_or_tts("open_app_fail", "Sorry, I couldn't open it")
                self._deafen_after_speak()
                time.sleep(0.18)
                ui_state("IDLE")
                return False

        if intent == "close app":
            app_name = extract_close_app_name(text)
            app = find_app(self.apps, app_name)

            if not app:
                ui_state("WARNING")
                self.voice.play_or_tts("app_not_found", "I couldn't find that app")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False

            ui_state("SPEAKING")
            self.voice.play_or_tts("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            ok = close_app(app)

            if ok:
                ui_state("SUCCESS")
                time.sleep(0.18)
                ui_state("IDLE")
                return True
            else:
                ui_state("ERROR")
                self.voice.play_or_tts("close_app_fail", "Sorry, I couldn't close it")
                self._deafen_after_speak()
                time.sleep(0.18)
                ui_state("IDLE")
                return False

        if intent in COMMANDS:
            response = VOICE_RESPONSES.get(intent, f"Executing {intent}")
            ui_state("SPEAKING")
            self.voice.play_or_tts(intent.replace(" ", "_"), response)
            self._deafen_after_speak()

            ui_state("EXECUTING")
            info(f"Exec: {intent}")

            try:
                COMMANDS[intent]()
                ui_state("SUCCESS")
                info("Exec: OK")
                time.sleep(0.18)
                ui_state("IDLE")
                return True
            except Exception as e:
                ui_state("ERROR")
                error(f"Exec failed: {e}")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                time.sleep(0.18)
                ui_state("IDLE")
                return False

        ui_state("WARNING")
        warn(f"Intent not implemented: {intent}")
        self.voice.play_or_tts("not_implemented", "I don't know how to do that yet")
        self._deafen_after_speak()
        ui_state("IDLE")
        return False

    def run(self):
        info("AIDY start")
        info(f"Mode: {'UI bridge' if UI_MODE else 'Console'} | log={LOG_LEVEL}")
        info(f"API: {API_URL}")
        info(f"Grammar phrases: {len(self.command_phrases)}")

        if self.model is None:
            ui_state("ERROR")
            error("Vosk model not loaded")
            ui_state("IDLE")
            # return  # Allow to continue without model for demo

        ui_state("STARTING")
        ui_state("SPEAKING")
        self.voice.tts_blocking("Aidy is ready")

        try:
            self.start_stream()
            ui_state("LISTENING")

            while True:
                if self.window_switch_active:
                    cmd_text = self.listen_command_vosk(max_seconds=3)
                    if cmd_text:
                        self.window_switch_silence_hits = 0
                        self.process_command(cmd_text)
                    else:
                        self.window_switch_silence_hits += 1
                        if self.window_switch_silence_hits >= 3:
                            self.end_window_switch(cancel=True)
                    continue

                self.wait_for_wake()
                cmd_text = self.listen_command_vosk(max_seconds=20)
                if cmd_text:
                    self.process_command(cmd_text)
                else:
                    ui_state("IDLE")

        except KeyboardInterrupt:
            info("Shutdown: Ctrl+C")
            ui_state("SPEAKING")
            self.voice.tts_blocking("Goodbye")
        except Exception as e:
            ui_state("ERROR")
            error(f"Fatal: {e}")
        finally:
            ui_state("IDLE")
            self.stop_stream()
            self.audio.terminate()
            info("AIDY stopped")
