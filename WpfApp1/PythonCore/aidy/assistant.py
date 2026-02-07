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
    CONFIRM_YES,
    CONFIRM_NO,
    DANGEROUS_INTENTS,
    REPEAT_PHRASES,
    CLOSE_ACTIVE_PHRASES,
    MUTE_PHRASES,
    UNMUTE_PHRASES,
    UNDO_LAST_PHRASES,
    UNDO_ALL_PHRASES,
    WINDOW_SWITCH_GRAMMAR,
    WINDOW_SWITCH_LEFT,
    WINDOW_SWITCH_RIGHT,
    WINDOW_SWITCH_DONE,
    WINDOW_SWITCH_CANCEL,
    VOICE_RESPONSES,
    NUMERIC_FOLLOWUP_GRAMMAR_PHRASES,
    MORE_ACTION_PHRASES,
    LESS_ACTION_PHRASES,
    REPEAT_LAST_STEPS,
    FOLLOW_MODE_ENABLED,
    FOLLOW_MODE_TTL_SECONDS,
    FOLLOW_MODE_REPEAT_LAST_STEPS,
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
    close_app_by_process,
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
    brightness_steps,
    get_active_window_info,
)
from .intent_api import start_local_intent_api, IntentAPI
from .context import ContextManager, should_merge_context
from .scheduler import TaskScheduler, Task
from .delay import parse_delay_request
from .action_history import ActionHistory, ActionRecord
from .followup import (
    FollowUpManager,
    PendingAction,
    PENDING_NEED_STEPS,
    PENDING_NEED_CHOICE,
)
from .decision_core import (
    STEP_REQUIRED,
    STEP_INTENT_TO_LEGACY,
    detect_step_intent_from_text,
    api_intent_to_step_intent,
    parse_numeric_input,
    extract_steps_value,
)
from .last_step_action import LastStepActionManager
from .follow_mode import FollowModeManager, classify_follow_input, resolve_follow_mode_gate


COMMANDS = {
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
        warn(f"No CSV dataset near Aidy.py. Using {len(phrases)} phrases from built-ins.")
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
        if getattr(self.voice, "muted", False):
            return
        if not self.stream:
            return
        if ms is None:
            ms = self.DEAFEN_MS_AFTER_TTS

        self._flush_audio(self.FLUSH_MS)

        end = time.time() + (ms / 1000.0)
        while time.time() < end:
            self.stream.read(CHUNK_SAMPLES, exception_on_overflow=False)

    def _sleep_success(self):
        time.sleep(0.8 if self.voice.muted else 0.35)

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
            set(self.command_phrases)
            | set(WAKE_KEYWORDS)
            | set(CONFIRM_GRAMMAR_PHRASES)
            | set(WINDOW_SWITCH_GRAMMAR)
            | set(REPEAT_PHRASES)
            | set(CLOSE_ACTIVE_PHRASES)
            | set(MUTE_PHRASES)
            | set(UNMUTE_PHRASES)
            | set(UNDO_LAST_PHRASES)
            | set(UNDO_ALL_PHRASES)
            | set(NUMERIC_FOLLOWUP_GRAMMAR_PHRASES)
            | set(MORE_ACTION_PHRASES)
            | set(LESS_ACTION_PHRASES)
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
        self.last_command_text = None
        self._is_repeating = False
        self.short_memory = {
            "last_intent": None,
            "args": None,
            "status": None,
        }
        self.context_mgr = ContextManager(ttl_seconds=7.5, min_confidence=0.2, main_confidence=0.4)
        self.scheduler = TaskScheduler(max_tasks=5, max_delay_seconds=3600)
        self.history = ActionHistory(max_actions=20, chain_gap_seconds=5.0)
        self.follow_up = FollowUpManager(ttl_seconds=8.0)
        self.last_step_actions = LastStepActionManager(ttl_seconds=12.0)
        self.repeat_last_steps = FOLLOW_MODE_REPEAT_LAST_STEPS or REPEAT_LAST_STEPS
        self.follow_mode = FollowModeManager(ttl_seconds=float(FOLLOW_MODE_TTL_SECONDS), enabled=bool(FOLLOW_MODE_ENABLED))

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

        self.last_step_actions.clear()
        self.follow_mode.clear()
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

            self._handle_due_tasks()

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
                    info(f'Wake detected: "{text}"')
                    ui_state("SPEAKING")
                    self.voice.play_or_tts("wake", "I am here, sir")
                    self._deafen_after_speak()
                    ui_state("IDLE")
                    return

    def listen_command_vosk(self, max_seconds=6, min_listen_ms=2000, ui_state_label="LISTENING"):
        ui_state(ui_state_label)
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

    def _set_last_command(self, text: str):
        if self._is_repeating:
            return
        self.last_command_text = text

    def _set_memory(self, intent: str, args: dict | None = None, status: str = "success"):
        if self._is_repeating:
            return
        self.short_memory = {
            "last_intent": intent,
            "args": args or {},
            "status": status,
        }

    def _set_context(self, intent: str, entities: dict | None = None):
        if self._is_repeating:
            return
        self.context_mgr.set_context(intent, entities or {})

    def _is_followup_phrase(self, text: str) -> bool:
        t = (text or "").strip().lower()
        t = " ".join(t.split())
        if not t:
            return False
        words = t.split()
        if len(words) > 4:
            return False
        linkers = {"ГЁ", "ГІГҐГЇГҐГ°Гј", "Г¤Г Г«ГјГёГҐ", "ГҐГ№Вё", "ГҐГ№ГҐ"}
        return any(w in linkers for w in words)

    def _strip_linkers(self, text: str) -> str:
        t = (text or "").strip().lower()
        words = [w for w in t.split() if w not in {"ГЁ", "ГІГҐГЇГҐГ°Гј", "Г¤Г Г«ГјГёГҐ", "ГҐГ№Вё", "ГҐГ№ГҐ"}]
        return " ".join(words).strip()

    def _apply_followup(self, ctx: dict, text: str, api_intent: str) -> bool:
        ctx_intent = (ctx.get("last_intent") or "").strip().lower()
        if not ctx_intent:
            return False
        if not should_merge_context(ctx_intent, api_intent):
            return False

        t = self._strip_linkers(text)
        if not t:
            return False

        if ctx_intent == "open app":
            app = find_app(self.apps, t)
            if not app:
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = launch_app(app)
            if ok:
                self._set_context("open app", {"app": app["id"]})
                self._set_memory("open app", {"id": app["id"]})
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            return False

        if ctx_intent == "close app":
            app = find_app(self.apps, t)
            if not app:
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = close_app(app)
            if ok:
                self._set_context("close app", {"app": app["id"]})
                self._set_memory("close app", {"id": app["id"]})
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            return False

        return False

    def _cancel_pending(self, speak_cancelled: bool):
        self.follow_up.clear_pending()
        self.last_step_actions.clear()
        self.follow_mode.clear()
        if speak_cancelled:
            ui_state("SPEAKING")
            self.voice.play_or_tts("pending_cancelled", "Cancelled.")
            self._deafen_after_speak()
            ui_state("IDLE")

    def _execute_step_intent(self, step_intent: str, steps: int, original_text: str, extra_entities: dict | None = None) -> bool:
        cfg = STEP_REQUIRED.get(step_intent)
        if not cfg:
            return False
        exec_entities = {
            "direction": cfg["direction"],
            "magnitude_steps": max(1, min(10, int(steps))),
        }
        if extra_entities:
            exec_entities.update(extra_entities)
        ok = self._execute_intent(cfg["base"], exec_entities)
        if not ok:
            self.history.break_chain()
            return False
        action_intent = STEP_INTENT_TO_LEGACY.get(step_intent, step_intent.replace("_", " "))
        action_entities = {"steps": exec_entities["magnitude_steps"]}
        self._set_last_command(original_text)
        self._set_memory(action_intent, action_entities)
        self._set_context(action_intent, action_entities)
        self._record_action(action_intent, action_entities)
        return True

    def _legacy_step_intent_name(self, base_intent: str, direction: str) -> str:
        direction = (direction or "").upper()
        if base_intent == "volume_change":
            return "volume up" if direction == "UP" else "volume down"
        if base_intent == "brightness_change":
            return "brightness up" if direction == "UP" else "brightness down"
        return base_intent

    def _handle_more_less_action(self, is_less: bool, command_text: str) -> bool:
        last = self.follow_mode.get_last_step_action_if_active()
        if not last:
            last = self.last_step_actions.get_if_fresh(ttl_seconds=12)
        if not last:
            ui_state("SPEAKING")
            self.voice.play_or_tts("not_now", "Nothing to adjust." if is_less else "Nothing to repeat.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False
        steps_to_apply = last.last_steps if self.repeat_last_steps else 1
        direction = "DOWN" if (is_less and last.direction == "UP") else "UP" if is_less else last.direction
        ok = self._execute_intent(
            last.base_intent,
            {
                **(last.entities or {}),
                "direction": direction,
                "magnitude_steps": steps_to_apply,
            },
        )
        if not ok:
            self.history.break_chain()
            return False
        action_intent = self._legacy_step_intent_name(last.base_intent, direction)
        action_entities = {"steps": steps_to_apply}
        self._set_last_command(command_text)
        self._set_memory(action_intent, action_entities)
        self._set_context(action_intent, action_entities)
        self._record_action(action_intent, action_entities)
        last2 = self.last_step_actions.get_if_fresh(ttl_seconds=12)
        if last2:
            self.follow_mode.activate(last2)
        return True

    def _handle_pending_numeric_flow(self, text: str) -> bool | None:
        pending = self.follow_up.get_pending()
        if not pending:
            numeric = parse_numeric_input(text)
            if numeric is not None:
                ui_state("SPEAKING")
                self.voice.play_or_tts("not_now", "Not now.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            return None

        numeric = parse_numeric_input(text)
        if numeric is None:
            attempts = self.follow_up.register_invalid_attempt()
            if attempts >= 2:
                self._cancel_pending(speak_cancelled=True)
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("need_number", "Say a number from one to ten.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        if pending.pending_type == PENDING_NEED_STEPS:
            ok = self._execute_intent(
                pending.base_intent,
                {
                    "direction": pending.direction,
                    "magnitude_steps": numeric,
                    **(pending.entities or {}),
                },
            )
            self.follow_up.clear_pending()
            if not ok:
                self.history.break_chain()
                return False
            if pending.base_intent == "volume_change":
                action_intent = "volume up" if pending.direction == "UP" else "volume down"
            elif pending.base_intent == "brightness_change":
                action_intent = "brightness up" if pending.direction == "UP" else "brightness down"
            else:
                action_intent = pending.base_intent
            action_entities = {"steps": numeric}
            self._set_last_command(text)
            self._set_memory(action_intent, action_entities)
            self._set_context(action_intent, action_entities)
            self._record_action(action_intent, action_entities)
            return True

        if pending.pending_type == PENDING_NEED_CHOICE:
            max_choice = int(pending.max_choice or 0)
            if max_choice > 0 and 1 <= numeric <= max_choice:
                self.follow_up.clear_pending()
                ui_state("SPEAKING")
                self.voice.play_or_tts("choice_selected", "Selected.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return True
            attempts = self.follow_up.register_invalid_attempt()
            if attempts >= 2:
                self._cancel_pending(speak_cancelled=True)
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("need_number", "Say a number from one to ten.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        self.follow_up.clear_pending()
        return False

    def _infer_intent_and_entities(self, text: str) -> tuple[str | None, dict]:
        t0 = " ".join((text or "").lower().split())
        if not t0:
            return None, {}

        if t0 in COMMANDS:
            return t0, {}

        if t0 in ("switch", "switch app", "switch window"):
            return "switch window", {}

        if t0.startswith(("close ", "quit ", "exit ", "kill ", "stop ")):
            app_name = extract_close_app_name(t0)
            app = find_app(self.apps, app_name)
            if app:
                return "close app", {"app": app["id"]}
            return None, {}

        if t0.startswith(("open ", "launch ", "start ", "run ")):
            app_name = extract_app_name(t0)
            app = find_app(self.apps, app_name)
            if app:
                return "open app", {"app": app["id"]}
            return None, {}

        if t0 in ("volume up", "sound up", "increase volume", "louder", "make it louder"):
            return "volume up", {"steps": 6}
        if t0 in ("volume down", "sound down", "decrease volume", "quieter", "make it quieter"):
            return "volume down", {"steps": 6}

        if t0 in ("brightness up", "increase brightness", "brighten screen", "make screen brighter"):
            return "brightness up", {}
        if t0 in ("brightness down", "decrease brightness", "dim screen", "make screen darker"):
            return "brightness down", {}

        result = self.api.get_intent(text)
        if not result:
            return None, {}
        intent = (result.get("intent") or "").strip().lower()
        confidence = float(result.get("confidence", 0) or 0)
        if confidence < 0.4:
            return None, {}

        if intent == "open app":
            app_name = extract_app_name(text)
            app = find_app(self.apps, app_name)
            if app:
                return "open app", {"app": app["id"]}
            return None, {}

        if intent == "close app":
            app_name = extract_close_app_name(text)
            app = find_app(self.apps, app_name)
            if app:
                return "close app", {"app": app["id"]}
            return None, {}

        if intent in ("volume up", "volume down"):
            n = parse_first_int(text)
            if n is None:
                return intent, {"steps": 6}
            t = (text or "").lower()
            wants_absolute = (" to " in f" {t} ") or ("%" in t) or ("percent" in t)
            if wants_absolute:
                return "set_volume", {"value": n}
            return intent, {"steps": max(1, n)}

        if intent == "switch window":
            return "switch window", {}

        if intent in COMMANDS:
            return intent, {}

        return None, {}

    def _execute_intent(self, intent: str, entities: dict) -> bool:
        if intent == "volume_change":
            direction = (entities.get("direction") or "").upper()
            steps = max(1, min(10, int(entities.get("magnitude_steps") or entities.get("steps") or 1)))
            if direction not in ("UP", "DOWN"):
                return False
            ui_state("SPEAKING")
            if direction == "UP":
                self.voice.play_or_tts("volume_up", VOICE_RESPONSES.get("volume up", "Increasing volume"))
            else:
                self.voice.play_or_tts("volume_down", VOICE_RESPONSES.get("volume down", "Decreasing volume"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            volume_steps(up=(direction == "UP"), steps=steps)
            base_entities = dict(entities or {})
            base_entities.pop("direction", None)
            base_entities.pop("magnitude_steps", None)
            base_entities.pop("steps", None)
            self.last_step_actions.record("volume_change", direction, steps, base_entities)
            last = self.last_step_actions.get_if_fresh(ttl_seconds=12)
            if last:
                self.follow_mode.activate(last)
            ui_state("SUCCESS")
            self._sleep_success()
            ui_state("IDLE")
            return True

        if intent == "brightness_change":
            direction = (entities.get("direction") or "").upper()
            steps = max(1, min(10, int(entities.get("magnitude_steps") or entities.get("steps") or 1)))
            if direction not in ("UP", "DOWN"):
                return False
            ui_state("SPEAKING")
            if direction == "UP":
                self.voice.play_or_tts("brightness_up", VOICE_RESPONSES.get("brightness up", "Increasing brightness"))
            else:
                self.voice.play_or_tts("brightness_down", VOICE_RESPONSES.get("brightness down", "Decreasing brightness"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = brightness_steps(up=(direction == "UP"), steps=steps)
            if not ok:
                ui_state("ERROR")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                self._sleep_success()
                ui_state("IDLE")
                return False
            base_entities = dict(entities or {})
            base_entities.pop("direction", None)
            base_entities.pop("magnitude_steps", None)
            base_entities.pop("steps", None)
            self.last_step_actions.record("brightness_change", direction, steps, base_entities)
            last = self.last_step_actions.get_if_fresh(ttl_seconds=12)
            if last:
                self.follow_mode.activate(last)
            ui_state("SUCCESS")
            self._sleep_success()
            ui_state("IDLE")
            return True

        if intent == "set_volume":
            value = entities.get("value")
            if value is None:
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("set_volume", f"Setting volume to {value} percent")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = set_volume_percent(int(value))
            if not ok:
                volume_steps(up=True, steps=1)
            ui_state("SUCCESS")
            self._sleep_success()
            ui_state("IDLE")
            return True

        if intent == "volume up":
            steps = int(entities.get("steps") or 6)
            ui_state("SPEAKING")
            self.voice.play_or_tts("volume_up", VOICE_RESPONSES.get("volume up", "Adjusting volume"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            volume_steps(up=True, steps=steps)
            ui_state("SUCCESS")
            self._sleep_success()
            ui_state("IDLE")
            return True

        if intent == "volume down":
            steps = int(entities.get("steps") or 6)
            ui_state("SPEAKING")
            self.voice.play_or_tts("volume_down", VOICE_RESPONSES.get("volume down", "Adjusting volume"))
            self._deafen_after_speak()
            ui_state("EXECUTING")
            volume_steps(up=False, steps=steps)
            ui_state("SUCCESS")
            self._sleep_success()
            ui_state("IDLE")
            return True

        if intent == "open app":
            app_id = (entities.get("app") or entities.get("id") or "").strip().lower()
            app = find_app(self.apps, app_id)
            if not app:
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = launch_app(app)
            if ok:
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            return False

        if intent == "close app":
            app_id = (entities.get("app") or entities.get("id") or "").strip().lower()
            app = find_app(self.apps, app_id)
            if not app:
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = close_app(app)
            if ok:
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            return False

        if intent == "close active":
            proc = (entities.get("process") or "").strip()
            if not proc:
                return False
            proc_name = proc if proc.lower().endswith(".exe") else (proc + ".exe")
            ui_state("SPEAKING")
            self.voice.play_or_tts("close_active", "Closing current app")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = close_app_by_process(proc_name, force=False)
            time.sleep(0.15)
            ok2 = close_app_by_process(proc_name, force=True)
            if ok or ok2:
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            return False

        if intent == "switch window":
            ui_state("EXECUTING")
            self.start_window_switch()
            return True

        if intent in COMMANDS:
            response = VOICE_RESPONSES.get(intent, f"Executing {intent}")
            ui_state("SPEAKING")
            self.voice.play_or_tts(intent.replace(" ", "_"), response)
            self._deafen_after_speak()
            ui_state("EXECUTING")
            COMMANDS[intent]()
            ui_state("SUCCESS")
            self._sleep_success()
            ui_state("IDLE")
            return True

        return False

    def _inverse_for_action(self, intent: str, entities: dict) -> dict | None:
        if intent == "open app":
            app_id = (entities.get("app") or entities.get("id") or "").strip().lower()
            if app_id:
                return {"intent": "close app", "entities": {"app": app_id}}
            return None

        if intent == "close app":
            app_id = (entities.get("app") or entities.get("id") or "").strip().lower()
            if app_id:
                return {"intent": "open app", "entities": {"app": app_id}}
            return None

        if intent == "volume up":
            steps = int(entities.get("steps") or 6)
            return {"intent": "volume down", "entities": {"steps": steps}}

        if intent == "volume down":
            steps = int(entities.get("steps") or 6)
            return {"intent": "volume up", "entities": {"steps": steps}}

        if intent == "brightness up":
            return {"intent": "brightness down", "entities": {}}

        if intent == "brightness down":
            return {"intent": "brightness up", "entities": {}}

        if intent == "mute":
            return {"intent": "unmute", "entities": {}}

        if intent == "unmute":
            return {"intent": "mute", "entities": {}}

        return None

    def _record_action(self, intent: str, entities: dict):
        inverse = self._inverse_for_action(intent, entities or {})
        rec = ActionRecord(
            id=0,
            action_intent=intent,
            entities=entities or {},
            inverse_action=inverse,
            timestamp=0,
            chain_id=0,
        )
        self.history.push(rec)

    def _undo_last(self) -> bool:
        rec = self.history.get_last()
        if not rec:
            ui_state("WARNING")
            self.voice.play_or_tts("not_sure", "Nothing to undo")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False
        if not rec.inverse_action:
            ui_state("WARNING")
            self.voice.play_or_tts("not_sure", "I can't undo that")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False
        inv = rec.inverse_action
        ok = self._execute_intent(inv["intent"], inv.get("entities") or {})
        if ok:
            self.history.pop_last()
            self.history.break_chain()
        return ok

    def _undo_chain(self) -> bool:
        rec = self.history.get_last()
        if not rec:
            ui_state("WARNING")
            self.voice.play_or_tts("not_sure", "Nothing to undo")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False
        chain = self.history.get_chain(rec.chain_id)
        if not chain:
            ui_state("WARNING")
            self.voice.play_or_tts("not_sure", "Nothing to undo")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False
        for r in chain:
            if not r.inverse_action:
                ui_state("WARNING")
                self.voice.play_or_tts("not_sure", "I can't undo that")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
        for r in reversed(chain):
            inv = r.inverse_action
            if not inv:
                return False
            ok = self._execute_intent(inv["intent"], inv.get("entities") or {})
            if not ok:
                return False
        self.history.pop_chain(rec.chain_id)
        self.history.break_chain()
        return True

    def _confirm_and_schedule(self, intent: str, entities: dict, delay_seconds: int) -> bool:
        ui_state("SPEAKING")
        self.voice.play_or_tts("are_you_sure", "Are you sure?")
        self._deafen_after_speak()

        for attempt in range(2):
            reply = self.listen_command_vosk(max_seconds=6, min_listen_ms=1200, ui_state_label="CONFIRM")
            if not reply:
                self.context_mgr.clear_context()
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False

            t = " ".join(reply.lower().split())
            if t in CONFIRM_YES:
                task = Task(id=0, action_intent=intent, entities=entities, execute_at=0)
                task_id = self.scheduler.schedule(task, delay_seconds)
                if not task_id:
                    self.context_mgr.clear_context()
                    ui_state("WARNING")
                    self.voice.play_or_tts("not_sure", "I couldn't schedule that")
                    self._deafen_after_speak()
                    ui_state("IDLE")
                    return False
                mins = delay_seconds // 60
                if mins >= 1 and delay_seconds % 60 == 0:
                    msg = f"Okay. I'll do it in {mins} minutes."
                else:
                    msg = f"Okay. I'll do it in {delay_seconds} seconds."
                ui_state("SPEAKING")
                self.voice.play_or_tts("scheduled", msg)
                self._deafen_after_speak()
                ui_state("IDLE")
                return True

            if t in CONFIRM_NO:
                self.context_mgr.clear_context()
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False

            if attempt == 0:
                ui_state("SPEAKING")
                self.voice.play_or_tts("confirm_retry", "Please say confirm or cancel.")
                self._deafen_after_speak()

        self.context_mgr.clear_context()
        ui_state("WARNING")
        self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
        self._deafen_after_speak()
        ui_state("IDLE")
        return False

    def _handle_due_tasks(self):
        due = self.scheduler.tick()
        for task in due:
            ok = self._execute_intent(task.action_intent, task.entities)
            self.context_mgr.clear_context()
            if ok:
                self._record_action(task.action_intent, task.entities or {})
            else:
                ui_state("WARNING")
                self.voice.play_or_tts("not_sure", "I couldn't complete that")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
    def _confirm_and_execute(self, intent: str, exec_fn, original_text: str | None = None):
        ui_state("SPEAKING")
        self.voice.play_or_tts("are_you_sure", "Are you sure?")
        self._deafen_after_speak()

        for attempt in range(2):
            reply = self.listen_command_vosk(max_seconds=6, min_listen_ms=1200, ui_state_label="CONFIRM")
            if not reply:
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False

            t = " ".join(reply.lower().split())
            if t in CONFIRM_YES:
                if original_text:
                    self._set_last_command(original_text)
                else:
                    self._set_last_command(intent)

                response = VOICE_RESPONSES.get(intent, f"Executing {intent}")
                ui_state("SPEAKING")
                self.voice.play_or_tts(intent.replace(" ", "_"), response)
                self._deafen_after_speak()

                ui_state("EXECUTING")
                info(f"Exec: {intent}")
                try:
                    exec_fn()
                    self._set_memory(intent)
                    self._set_context(intent, {})
                    self._record_action(intent, {})
                    ui_state("SUCCESS")
                    info("Exec: OK")
                    self._sleep_success()
                    ui_state("IDLE")
                    return True
                except Exception as e:
                    ui_state("ERROR")
                    error(f"Exec failed: {e}")
                    self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                    self._deafen_after_speak()
                    self._sleep_success()
                    ui_state("IDLE")
                    self.history.break_chain()
                    return False

            if t in CONFIRM_NO:
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            if attempt == 0:
                ui_state("SPEAKING")
                self.voice.play_or_tts("confirm_retry", "Please say confirm or cancel.")
                self._deafen_after_speak()

        ui_state("WARNING")
        self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
        self._deafen_after_speak()
        ui_state("IDLE")
        self.history.break_chain()
        return False

    def _confirm_close_request(self) -> bool:
        ui_state("SPEAKING")
        self.voice.play_or_tts("are_you_sure", "Are you sure?")
        self._deafen_after_speak()

        for attempt in range(2):
            reply = self.listen_command_vosk(max_seconds=6, min_listen_ms=1200, ui_state_label="CONFIRM")
            if not reply:
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            t = " ".join(reply.lower().split())
            if t in CONFIRM_YES:
                return True

            if t in CONFIRM_NO:
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            if attempt == 0:
                ui_state("SPEAKING")
                self.voice.play_or_tts("confirm_retry", "Please say confirm or cancel.")
                self._deafen_after_speak()

        ui_state("WARNING")
        self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
        self._deafen_after_speak()
        ui_state("IDLE")
        self.history.break_chain()
        return False

    def _repeat_from_memory(self) -> bool:
        mem = self.short_memory or {}
        intent = mem.get("last_intent")
        args = mem.get("args") or {}
        if not intent:
            return False

        self._is_repeating = True
        try:
            if intent == "set_volume":
                value = args.get("value")
                if value is None:
                    return False
                ui_state("SPEAKING")
                self.voice.play_or_tts("set_volume", f"Setting volume to {value} percent")
                self._deafen_after_speak()
                ui_state("EXECUTING")
                ok = set_volume_percent(int(value))
                if not ok:
                    volume_steps(up=True, steps=1)
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True

            if intent == "volume up":
                steps = int(args.get("steps") or 6)
                ui_state("SPEAKING")
                self.voice.play_or_tts("volume_up", VOICE_RESPONSES.get("volume up", "Adjusting volume"))
                self._deafen_after_speak()
                ui_state("EXECUTING")
                volume_steps(up=True, steps=steps)
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True

            if intent == "volume down":
                steps = int(args.get("steps") or 6)
                ui_state("SPEAKING")
                self.voice.play_or_tts("volume_down", VOICE_RESPONSES.get("volume down", "Adjusting volume"))
                self._deafen_after_speak()
                ui_state("EXECUTING")
                volume_steps(up=False, steps=steps)
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True

            if intent == "open app":
                app_id = (args.get("id") or "").strip().lower()
                app = find_app(self.apps, app_id)
                if not app:
                    return False
                ui_state("SPEAKING")
                self.voice.play_or_tts("open_app", f"Opening {app['id']}")
                self._deafen_after_speak()
                ui_state("EXECUTING")
                ok = launch_app(app)
                if ok:
                    ui_state("SUCCESS")
                    self._sleep_success()
                    ui_state("IDLE")
                    return True
                return False

            if intent == "close app":
                app_id = (args.get("id") or "").strip().lower()
                app = find_app(self.apps, app_id)
                if not app:
                    return False
                if not self._confirm_close_request():
                    return False
                ui_state("SPEAKING")
                self.voice.play_or_tts("close_app", f"Closing {app['id']}")
                self._deafen_after_speak()
                ui_state("EXECUTING")
                ok = close_app(app)
                if ok:
                    ui_state("SUCCESS")
                    self._sleep_success()
                    ui_state("IDLE")
                    return True
                return False

            if intent == "close active":
                proc = (args.get("process") or "").strip()
                if not proc:
                    return False
                if not self._confirm_close_request():
                    return False
                proc_name = proc if proc.lower().endswith(".exe") else (proc + ".exe")
                ui_state("SPEAKING")
                self.voice.play_or_tts("close_active", "Closing current app")
                self._deafen_after_speak()
                ui_state("EXECUTING")
                ok = close_app_by_process(proc_name, force=False)
                time.sleep(0.15)
                ok2 = close_app_by_process(proc_name, force=True)
                if ok or ok2:
                    ui_state("SUCCESS")
                    self._sleep_success()
                    ui_state("IDLE")
                    return True
                return False

            if intent == "switch window":
                ui_state("EXECUTING")
                self.start_window_switch()
                return True

            if intent in COMMANDS:
                if intent in DANGEROUS_INTENTS:
                    return self._confirm_and_execute(intent, COMMANDS[intent], original_text=intent)

                response = VOICE_RESPONSES.get(intent, f"Executing {intent}")
                ui_state("SPEAKING")
                self.voice.play_or_tts(intent.replace(" ", "_"), response)
                self._deafen_after_speak()
                ui_state("EXECUTING")
                info(f"Exec: {intent}")
                COMMANDS[intent]()
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True

            return False
        finally:
            self._is_repeating = False

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
            self.history.break_chain()
            return False

        t0 = " ".join((text or "").strip().lower().split())
        pending_active = self.follow_up.get_pending() is not None

        if t0 in ("cancel", "stop") and pending_active:
            self._cancel_pending(speak_cancelled=True)
            self.history.break_chain()
            return False

        if pending_active and (t0 in MORE_ACTION_PHRASES or t0 in LESS_ACTION_PHRASES):
            ui_state("SPEAKING")
            self.voice.play_or_tts("need_number", "Say a number.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        pending_result = self._handle_pending_numeric_flow(text)
        if pending_result is not None:
            return pending_result

        if self.follow_mode.is_active():
            routed = classify_follow_input(
                text=text,
                wake_keywords=WAKE_KEYWORDS,
                more_phrases=MORE_ACTION_PHRASES,
                less_phrases=LESS_ACTION_PHRASES,
                pending_active=False,
            )
            kind = routed.get("kind")
            if kind == "other" and is_wake_phrase(t0):
                kind = "wake"
                routed = {"kind": "wake", "tail": ""}
            if kind == "more":
                return self._handle_more_less_action(is_less=False, command_text=text)
            if kind == "less":
                return self._handle_more_less_action(is_less=True, command_text=text)
            if kind == "cancel":
                self.follow_mode.clear()
                self.last_step_actions.clear()
                ui_state("SPEAKING")
                self.voice.play_or_tts("ok", "Okay.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            if kind == "wake":
                self.follow_mode.clear()
                tail = (routed.get("tail") or "").strip()
                if tail:
                    return self.process_command(tail)
                ui_state("IDLE")
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("follow_mode_hint", "Say 'more', 'less', or the wake word.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        if t0 in ("cancel", "stop"):
            self.follow_mode.clear()
            self.last_step_actions.clear()
            ui_state("SPEAKING")
            self.voice.play_or_tts("cancelled", "Cancelled.")
            self._deafen_after_speak()
            ui_state("IDLE")
            self.history.break_chain()
            return False

        if t0 in MORE_ACTION_PHRASES:
            ui_state("SPEAKING")
            self.voice.play_or_tts("say_wake_word", "Say the wake word.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False
        if t0 in LESS_ACTION_PHRASES:
            ui_state("SPEAKING")
            self.voice.play_or_tts("say_wake_word", "Say the wake word.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        if t0 in UNMUTE_PHRASES:
            self.voice.muted = False
            ui_state("SPEAKING")
            self.voice.play_or_tts("unmute", "Sound on.")
            self._deafen_after_speak()
            ui_state("SUCCESS")
            self._sleep_success()
            ui_state("IDLE")
            self._record_action("unmute", {})
            return True

        if t0 in MUTE_PHRASES:
            self.voice.muted = True
            ui_state("SUCCESS")
            self._sleep_success()
            ui_state("IDLE")
            self._record_action("mute", {})
            return True

        if t0 in UNDO_ALL_PHRASES:
            return self._undo_chain()

        if t0 in UNDO_LAST_PHRASES:
            return self._undo_last()

        delay_req = parse_delay_request(text)
        if delay_req:
            action_text, delay_seconds = delay_req
            if delay_seconds > self.scheduler.max_delay_seconds:
                self.context_mgr.clear_context()
                ui_state("WARNING")
                self.voice.play_or_tts("not_sure", "I couldn't schedule that")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False
            if self.scheduler.count() >= self.scheduler.max_tasks:
                self.context_mgr.clear_context()
                ui_state("WARNING")
                self.voice.play_or_tts("not_sure", "Queue is full")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            intent, entities = self._infer_intent_and_entities(action_text)
            if not intent:
                self.context_mgr.clear_context()
                ui_state("WARNING")
                self.voice.play_or_tts("not_sure", "I'm not sure what you mean")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            if intent in DANGEROUS_INTENTS or intent in ("close app", "close active"):
                return self._confirm_and_schedule(intent, entities, delay_seconds)

            task = Task(id=0, action_intent=intent, entities=entities, execute_at=0)
            task_id = self.scheduler.schedule(task, delay_seconds)
            if not task_id:
                self.context_mgr.clear_context()
                ui_state("WARNING")
                self.voice.play_or_tts("not_sure", "I couldn't schedule that")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            mins = delay_seconds // 60
            if mins >= 1 and delay_seconds % 60 == 0:
                msg = f"Okay. I'll do it in {mins} minutes."
            else:
                msg = f"Okay. I'll do it in {delay_seconds} seconds."
            ui_state("SPEAKING")
            self.voice.play_or_tts("scheduled", msg)
            self._deafen_after_speak()
            ui_state("IDLE")
            return True

        if t0 in REPEAT_PHRASES:
            if self._repeat_from_memory():
                return True

            if not self.last_command_text:
                ui_state("IDLE")
                return False

            if self.last_command_text.strip().lower() in REPEAT_PHRASES:
                ui_state("IDLE")
                return False

            self._is_repeating = True
            try:
                return self.process_command(self.last_command_text)
            finally:
                self._is_repeating = False

        if t0 in CLOSE_ACTIVE_PHRASES:
            info = get_active_window_info()
            if not info:
                ui_state("IDLE")
                return False

            proc = (info.get("process") or "").strip()
            if not proc:
                ui_state("IDLE")
                return False

            if not self._confirm_close_request():
                return False

            proc_name = proc if proc.lower().endswith(".exe") else (proc + ".exe")

            ui_state("SPEAKING")
            self.voice.play_or_tts("close_active", "Closing current app")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            self._set_last_command(text)
            ok = close_app_by_process(proc_name, force=False)
            time.sleep(0.15)
            ok2 = close_app_by_process(proc_name, force=True)
            if ok or ok2:
                self._set_memory("close active", {"process": proc})
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True

            ui_state("ERROR")
            self.voice.play_or_tts("close_app_fail", "Sorry, I couldn't close it")
            self._deafen_after_speak()
            self._sleep_success()
            ui_state("IDLE")
            return False

        if t0.startswith(("close ", "quit ", "exit ", "kill ", "stop ")):
            app_name = extract_close_app_name(t0)
            app = find_app(self.apps, app_name)

            if not app:
                ui_state("WARNING")
                self.voice.play_or_tts("app_not_found", "I couldn't find that app")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            if not self._confirm_close_request():
                return False

            ui_state("SPEAKING")
            self.voice.play_or_tts("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            self._set_last_command(text)
            ok = close_app(app)

            if ok:
                self._set_context("close app", {"app": app["id"]})
                self._set_memory("close app", {"id": app["id"]})
                self._record_action("close app", {"app": app["id"]})
                ui_state("SUCCESS")
                time.sleep(3.18)
                ui_state("IDLE")
                return True

            ui_state("ERROR")
            self.voice.play_or_tts("close_app_fail", "Sorry, I couldn't close it")
            self._deafen_after_speak()
            self._sleep_success()
            ui_state("IDLE")
            self.history.break_chain()
            return False

        t0 = " ".join((text or "").lower().split())

        # Offline direct commands without Intent API
        if t0 in COMMANDS:
            if t0 in DANGEROUS_INTENTS:
                return self._confirm_and_execute(t0, COMMANDS[t0], original_text=text)

            response = VOICE_RESPONSES.get(t0, f"Executing {t0}")
            ui_state("SPEAKING")
            self.voice.play_or_tts(t0.replace(" ", "_"), response)
            self._deafen_after_speak()

            ui_state("EXECUTING")
            info(f"Exec: {t0}")
            self._set_last_command(text)
            try:
                COMMANDS[t0]()
                self._set_memory(t0)
                self._set_context(t0, {})
                self._record_action(t0, {})
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            except Exception as e:
                ui_state("ERROR")
                error(f"Exec failed: {e}")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                self._sleep_success()
                ui_state("IDLE")
                self.history.break_chain()
                return False

        step_intent = detect_step_intent_from_text(t0)
        if step_intent:
            steps = extract_steps_value(text)
            if steps is None:
                cfg = STEP_REQUIRED[step_intent]
                self.follow_up.set_pending(
                    PendingAction(
                        pending_type=PENDING_NEED_STEPS,
                        base_intent=cfg["base"],
                        direction=cfg["direction"],
                        entities={},
                    )
                )
                ui_state("SPEAKING")
                self.voice.play_or_tts("how_much", "How much?")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            return self._execute_step_intent(step_intent, steps, text)

        # Direct app name without "open"/"launch" (e.g., "steam", "chrome")
        app = find_app(self.apps, t0)
        if app:
            ui_state("SPEAKING")
            self.voice.play_or_tts("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            ok = launch_app(app)

            if ok:
                self._set_context("open app", {"app": app["id"]})
                self._set_memory("open app", {"id": app["id"]})
                self._record_action("open app", {"app": app["id"]})
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            else:
                if "browser" in app.get("aliases", []) or app.get("id") in ("chrome", "browser"):
                    if self._open_default_browser():
                        ui_state("SUCCESS")
                        self._sleep_success()
                        ui_state("IDLE")
                        return True

                ui_state("ERROR")
                self.voice.play_or_tts("open_app_fail", "Sorry, I couldn't open it")
                self._deafen_after_speak()
                self._sleep_success()
                ui_state("IDLE")
                self.history.break_chain()
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
                    self._set_context("open app", {"app": app["id"]})
                    self._set_memory("open app", {"id": app["id"]})
                    self._record_action("open app", {"app": app["id"]})
                    ui_state("SUCCESS")
                    self._sleep_success()
                    ui_state("IDLE")
                    return True
                else:
                    # Fallback: open default browser if "browser" requested but app not found
                    if "browser" in app.get("aliases", []) or app.get("id") in ("chrome", "browser"):
                        if self._open_default_browser():
                            ui_state("SUCCESS")
                            self._sleep_success()
                            ui_state("IDLE")
                            return True

                    ui_state("ERROR")
                    self.voice.play_or_tts("open_app_fail", "Sorry, I couldn't open it")
                    self._deafen_after_speak()
                    self._sleep_success()
                    ui_state("IDLE")
                    self.history.break_chain()
                    return False

        if t0 == "switch" or t0.startswith("switch ") or t0 in ("switch app", "switch window"):
            ui_state("EXECUTING")
            self._set_last_command(text)
            self._set_memory("switch window")
            self._set_context("switch window", {})
            self._record_action("switch window", {})
            self.start_window_switch()
            return True

        ui_state("PROCESSING")
        info("Intent: sending to API...")

        result = self.api.get_intent(text)
        if not result:
            ui_state("WARNING")
            if not self.voice.muted:
                self.voice.play_or_tts("not_sure", "I'm not sure what you mean")
                self._deafen_after_speak()
            ui_state("IDLE")
            self.history.break_chain()
            return False

        intent = (result.get("intent") or "").strip().lower()
        confidence = float(result.get("confidence", 0) or 0)

        info(f"Intent: {intent}  conf={confidence:.2f}")

        if confidence < 0.4:
            if confidence >= self.context_mgr.min_confidence and self._is_followup_phrase(text):
                ctx = self.context_mgr.get_context()
                if ctx and self._apply_followup(ctx, text, intent):
                    self.context_mgr.clear_context()
                    return True
                self.context_mgr.clear_context()
            ui_state("WARNING")
            self.voice.play_or_tts("not_sure", "I'm not sure what you mean")
            self._deafen_after_speak()
            ui_state("IDLE")
            self.history.break_chain()
            return False

        step_intent = api_intent_to_step_intent(intent)
        if step_intent:
            steps = extract_steps_value(text)
            if steps is None:
                cfg = STEP_REQUIRED[step_intent]
                self.follow_up.set_pending(
                    PendingAction(
                        pending_type=PENDING_NEED_STEPS,
                        base_intent=cfg["base"],
                        direction=cfg["direction"],
                        entities={},
                    )
                )
                ui_state("SPEAKING")
                self.voice.play_or_tts("how_much", "How much?")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            return self._execute_step_intent(step_intent, steps, text)

        intent_key = intent.replace(" ", "_")
        if intent_key == "more_action":
            return self._handle_more_less_action(is_less=False, command_text=text)
        if intent_key == "less_action":
            return self._handle_more_less_action(is_less=True, command_text=text)

        if intent == "switch window":
            ui_state("EXECUTING")
            self._set_last_command(text)
            self._set_memory("switch window")
            self._set_context("switch window", {})
            self._record_action("switch window", {})
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
                self.history.break_chain()
                return False

            ui_state("SPEAKING")
            self.voice.play_or_tts("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            self._set_last_command(text)
            ok = launch_app(app)

            if ok:
                self._set_memory("open app", {"id": app["id"]})
                self._set_context("open app", {"app": app["id"]})
                self._record_action("open app", {"app": app["id"]})
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            else:
                if "browser" in app.get("aliases", []) or app.get("id") in ("chrome", "browser"):
                    if self._open_default_browser():
                        ui_state("SUCCESS")
                        self._sleep_success()
                        ui_state("IDLE")
                        return True
                ui_state("ERROR")
                self.voice.play_or_tts("open_app_fail", "Sorry, I couldn't open it")
                self._deafen_after_speak()
                self._sleep_success()
                ui_state("IDLE")
                self.history.break_chain()
                return False

        if intent == "close app":
            app_name = extract_close_app_name(text)
            app = find_app(self.apps, app_name)

            if not app:
                ui_state("WARNING")
                self.voice.play_or_tts("app_not_found", "I couldn't find that app")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            if not self._confirm_close_request():
                return False

            ui_state("SPEAKING")
            self.voice.play_or_tts("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()

            ui_state("EXECUTING")
            self._set_last_command(text)
            ok = close_app(app)

            if ok:
                self._set_memory("close app", {"id": app["id"]})
                self._set_context("close app", {"app": app["id"]})
                self._record_action("close app", {"app": app["id"]})
                ui_state("SUCCESS")
                self._sleep_success()
                ui_state("IDLE")
                return True
            else:
                ui_state("ERROR")
                self.voice.play_or_tts("close_app_fail", "Sorry, I couldn't close it")
                self._deafen_after_speak()
                self._sleep_success()
                ui_state("IDLE")
                self.history.break_chain()
                return False

        if intent in COMMANDS:
            if intent in DANGEROUS_INTENTS:
                return self._confirm_and_execute(intent, COMMANDS[intent], original_text=text)

            response = VOICE_RESPONSES.get(intent, f"Executing {intent}")
            ui_state("SPEAKING")
            self.voice.play_or_tts(intent.replace(" ", "_"), response)
            self._deafen_after_speak()

            ui_state("EXECUTING")
            info(f"Exec: {intent}")
            self._set_last_command(text)

            try:
                COMMANDS[intent]()
                self._set_memory(intent)
                self._set_context(intent, {})
                self._record_action(intent, {})
                ui_state("SUCCESS")
                info("Exec: OK")
                self._sleep_success()
                ui_state("IDLE")
                return True
            except Exception as e:
                ui_state("ERROR")
                error(f"Exec failed: {e}")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                self._sleep_success()
                ui_state("IDLE")
                self.history.break_chain()
                return False

        ui_state("WARNING")
        warn(f"Intent not implemented: {intent}")
        self.voice.play_or_tts("not_implemented", "I don't know how to do that yet")
        self._deafen_after_speak()
        ui_state("IDLE")
        self.history.break_chain()
        return False

    def _safe_process_command(self, text: str) -> bool:
        try:
            return self.process_command(text)
        except Exception as e:
            ui_state("ERROR")
            error(f"Command failed: {e}")
            if not self.voice.muted:
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
            ui_state("IDLE")
            self.history.break_chain()
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
                        self._safe_process_command(cmd_text)
                    else:
                        self.window_switch_silence_hits += 1
                        if self.window_switch_silence_hits >= 3:
                            self.end_window_switch(cancel=True)
                    continue

                if self.follow_up.get_pending():
                    self._handle_due_tasks()
                    cmd_text = self.listen_command_vosk(
                        max_seconds=8,
                        min_listen_ms=800,
                        ui_state_label="FOLLOWUP",
                    )
                    if cmd_text:
                        self._safe_process_command(cmd_text)
                    else:
                        self.follow_up.clear_pending()
                        ui_state("IDLE")
                    continue

                if self.follow_mode.is_active():
                    self._handle_due_tasks()
                    cmd_text = self.listen_command_vosk(
                        max_seconds=6,
                        min_listen_ms=700,
                        ui_state_label="LISTENING",
                    )
                    if cmd_text:
                        routed = classify_follow_input(
                            text=cmd_text,
                            wake_keywords=WAKE_KEYWORDS,
                            more_phrases=MORE_ACTION_PHRASES,
                            less_phrases=LESS_ACTION_PHRASES,
                            pending_active=False,
                        )
                        if routed.get("kind") == "other" and is_wake_phrase(cmd_text):
                            routed = {"kind": "wake", "tail": ""}
                        if routed.get("kind") == "wake":
                            self.follow_mode.clear()
                            tail = (routed.get("tail") or "").strip()
                            ui_state("SPEAKING")
                            self.voice.play_or_tts("wake", "I am here, sir")
                            self._deafen_after_speak()
                            if tail:
                                self._safe_process_command(tail)
                            else:
                                cmd2 = self.listen_command_vosk(max_seconds=20)
                                if cmd2:
                                    self._safe_process_command(cmd2)
                                else:
                                    ui_state("IDLE")
                            continue
                        self._safe_process_command(cmd_text)
                    else:
                        ui_state("IDLE")
                    continue

                self.wait_for_wake()
                self._handle_due_tasks()
                cmd_text = self.listen_command_vosk(max_seconds=20)
                if cmd_text:
                    self._safe_process_command(cmd_text)
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









