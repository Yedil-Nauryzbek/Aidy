from __future__ import annotations
import os
import json
import time
import csv
import audioop
import ctypes
import subprocess
import threading
import io
import sys
import queue
import wave
import glob as _glob_mod
import pyttsx3
from collections import deque
from ctypes import wintypes


try:
    import vosk
except ImportError:
    vosk = None

try:
    import pyaudio
except ImportError:
    pyaudio = None

from .voice_auth import (
    VoiceAuthManager,
    GRANT_GRAMMAR_PHRASES,
    GRANT_ROLE_GRAMMAR,
    GRANT_DURATION_GRAMMAR,
    parse_grant_duration_minutes,
    _parse_grant_command,
    _parse_delete_user_command,
)

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

from . import config as _aidy_cfg
from .config import (
    API_URL,
    WAKE_KEYWORDS,
    WAKE_FUZZY_ALIASES,
    is_wake_phrase,
    SAMPLE_RATE,
    CHUNK_SAMPLES,
    WAKE_CHUNK_SAMPLES,
    WAKE_DETECT_PARTIAL,
    WAKE_REPLY_DEAFEN_MS,
    TIMER_START_PHRASES,
    TIMER_CANCEL_PHRASES,
    TIMER_MAX_SECONDS,
    STUDY_MODE_DIRECT_START_PHRASES,
    STUDY_MODE_CONFIRM_START_PHRASES,
    STUDY_MODE_START_PHRASES,
    STUDY_MODE_START_ALIASES,
    STUDY_MODE_STOP_PHRASES,
    STUDY_MODE_STATUS_PHRASES,
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
    CUSTOM_MODE_ON_PHRASES,
    CUSTOM_MODE_OFF_PHRASES,
    WINDOW_SWITCH_GRAMMAR,
    WINDOW_SWITCH_LEFT,
    WINDOW_SWITCH_RIGHT,
    WINDOW_SWITCH_DONE,
    WINDOW_SWITCH_CANCEL,
    VOICE_RESPONSES,
    NUMERIC_FOLLOWUP_GRAMMAR_PHRASES,
    NUMERIC_FOCUSED_GRAMMAR,
    MORE_ACTION_PHRASES,
    LESS_ACTION_PHRASES,
    REPEAT_LAST_STEPS,
    FOLLOW_MODE_ENABLED,
    FOLLOW_MODE_TTL_SECONDS,
    FOLLOW_MODE_REPEAT_LAST_STEPS,
    MOUSE_COMMAND_PHRASES,
)
from .logui import ui_state, ui_command, ui_timer, ui_study_mode, ui_custom_mode, debug, info, warn, error, UI_MODE, LOG_LEVEL
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
    execute_mouse_command,
)
from .intent_api import start_local_intent_api, IntentAPI
from .context import ContextManager, should_merge_context
from .scheduler import TaskScheduler, Task
from .delay import parse_delay_request, parse_timer_duration_seconds
from .action_history import ActionHistory, ActionRecord
from .debug_logger import get_debug_logger
from .followup import (
    FollowUpManager,
    PendingAction,
    PENDING_NEED_STEPS,
    PENDING_NEED_CHOICE,
    PENDING_NEED_TARGET,
    PENDING_NEED_TIMER_DURATION,
    PENDING_NEED_SONG,
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
    "close everything": lambda: sys.exit(0),
}

INTENT_STUDY_MODE_START = "study_mode_start"
INTENT_STUDY_MODE_STOP = "study_mode_stop"
INTENT_STUDY_MODE_STATUS = "study_mode_status"

STUDY_SESSION_SECONDS = 45 * 60
STUDY_OPEN_URLS = (
    "https://chatgpt.com",
    "https://gemini.google.com",
    "https://stepik.org",
)

DISTRACT_PROCESSES = (
    "discord.exe",
    "telegram.exe",
    "epicgameslauncher.exe",
    "spotify.exe",
    "riotclientservices.exe",
    "valorant.exe",
    "leagueclient.exe",
    "robloxplayerbeta.exe",
    "minecraftlauncher.exe",
)
DISTRACT_GUARD_INTERVAL_SEC = 0.9
DISTRACT_BLOCK_ANNOUNCE_COOLDOWN_SEC = 8.0
DISTRACT_SOFT_CLOSE_WAIT_SEC = 1.15

# ---------------------------------------------------------------------------
# STUDY_SAFE_PROCESSES — Single source of truth for processes that study mode
# must NEVER touch.  Edit this set to add/remove protected applications.
#
# Categories:
#   1. Aidy itself (WPF host + Python backend)
#   2. IDEs / dev tools (Visual Studio, VS Code)
#   3. Recording / streaming
#   4. Audio engines
#   5. Windows OS shell infrastructure
# ---------------------------------------------------------------------------
STUDY_SAFE_PROCESSES: frozenset[str] = frozenset({
    # ── 1. Aidy ──────────────────────────────────────────────────────────
    "wpfapp1.exe",           # Aidy WPF shell
    "python.exe",            # Aidy Python backend
    "pythonw.exe",           # Aidy Python backend (windowless)
    "python3.exe",           # Alternative Python entry
    "python311.exe",         # Embedded Python that ships with Aidy

    # ── 2. IDEs / dev tools ──────────────────────────────────────────────
    "devenv.exe",            # Visual Studio
    "code.exe",              # VS Code
    "code - insiders.exe",   # VS Code Insiders

    # ── 3. Recording / streaming ─────────────────────────────────────────
    "obs64.exe",
    "obs.exe",

    # ── 4. Audio ─────────────────────────────────────────────────────────
    "fxsound.exe",
    "dfx.exe",

    # ── 5. Windows OS infrastructure ─────────────────────────────────────
    "explorer.exe",
    "dwm.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "searchhost.exe",
    "runtimebroker.exe",
    "sihost.exe",
    "svchost.exe",
    "wininit.exe",
    "winlogon.exe",
    "csrss.exe",
    "services.exe",
    "lsass.exe",
    "conhost.exe",
    "applicationframehost.exe",
    "systemsettings.exe",
    "steam.exe",
})

FOLLOWUP_LINKERS = {
    "and",
    "then",
    "next",
    "more",
    "\u0438",        # и
    "\u0442\u0435\u043f\u0435\u0440\u044c",  # теперь
    "\u0434\u0430\u043b\u044c\u0448\u0435",  # дальше
    "\u0435\u0449\u0451",  # ещё
    "\u0435\u0449\u0435",  # еще
}

WM_CLOSE = 0x0010
SW_MINIMIZE = 6
SW_RESTORE = 9
SMTO_ABORTIFHUNG = 0x0002
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWM_CLOAKED = 14

_USER32 = ctypes.windll.user32
_DWMAPI = getattr(ctypes.windll, "dwmapi", None)
if _DWMAPI is not None:
    try:
        _DWMAPI.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        _DWMAPI.DwmGetWindowAttribute.restype = ctypes.c_long
    except Exception:
        _DWMAPI = None


def load_command_phrases(base_dir: str):
    """Load command phrases and build text→intent lookup from CSV.

    Returns:
        (sorted_phrases, text_to_intent) where text_to_intent maps
        normalised spoken text to its intent key (e.g. "shut down pc" → "shutdown").
    """
    candidates = [
        os.path.join(base_dir, "commands.csv"),
        os.path.join(base_dir, "intents.csv"),
        os.path.join(base_dir, "dataset.csv"),
    ]

    phrases = set()
    text_to_intent: dict[str, str] = {}
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
                        # Column 2 is the intent key — build the reverse lookup
                        if len(row) >= 2:
                            intent = row[1].strip().strip('"').strip("'").lower()
                            if intent:
                                text_to_intent[cmd] = intent

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

    # Also map COMMANDS dict keys to themselves (they are already intent keys)
    for key in COMMANDS:
        text_to_intent.setdefault(key, key)

    return sorted(phrases), text_to_intent


class Aidy:
    DEAFEN_MS_AFTER_TTS = 80
    FLUSH_MS = 10
    POST_COMMAND_FLUSH_MS = 30
    WAKE_ACK_GUARD_MS = 10
    WAKE_GREETING_COOLDOWN_S = 0.10
    WAKE_GREETING_MIN_RMS = 1
    COMMAND_SILENCE_STOP_MS = 60
    COMMAND_SILENCE_STOP_MS_LEGACY = 60
    FOLLOWUP_SILENCE_STOP_MS = 160
    CONFIRM_SILENCE_STOP_MS = 60
    PTT_PAUSED_TOKEN = "__PTT_PAUSED__"
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

    def _resolve_existing_dir(self, candidates: list[str]) -> str | None:
        for path in candidates:
            if path and os.path.isdir(path):
                return os.path.abspath(path)
        return None

    def _resolve_model_path(self) -> str | None:
        model_dir = "vosk-model-small-en-us-0.15"
        here = os.path.abspath(os.path.dirname(__file__))
        candidates = [
            os.path.join(self.base_dir, model_dir),
            os.path.join(self.base_dir, "PythonCore", model_dir),
            os.path.join(os.path.dirname(self.base_dir), model_dir),
            os.path.join(os.path.dirname(self.base_dir), "PythonCore", model_dir),
            os.path.join(here, model_dir),
            os.path.join(os.path.dirname(here), model_dir),
            os.path.join(os.path.dirname(os.path.dirname(here)), model_dir),
        ]

        resolved = self._resolve_existing_dir(candidates)
        if not resolved:
            return None

        model_file = os.path.join(resolved, "am", "final.mdl")
        if os.path.exists(model_file):
            return resolved
        return None

    def _wake_log(self, msg: str):
        info(f"[WAKE] {msg}")

    def _cmd_log(self, msg: str):
        info(f"[CMD] {msg}")

    def _debug_file_log(self, msg: str):
        """Write debug line to CUSTOM_MODE_DEBUG.txt for post-mortem analysis."""
        try:
            path = os.path.join(self.base_dir, "CUSTOM_MODE_DEBUG.txt")
            stamp = time.strftime("%H:%M:%S")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{stamp} {msg}\n")
        except Exception:
            pass

    def _study_log(self, msg: str):
        stamp = time.strftime("%H:%M:%S")
        entry = f"{stamp} {msg}"
        with self._study_lock:
            self.study_actions_log.append(entry)
            if len(self.study_actions_log) > 300:
                self.study_actions_log = self.study_actions_log[-300:]
        info(f"[STUDY] {msg}")

    def _study_default_allowed_processes(self) -> set[str]:
        """Return the baseline set of processes study mode must never touch.

        Delegates to the module-level STUDY_SAFE_PROCESSES constant so
        there is exactly one place to edit the safelist.
        """
        return set(STUDY_SAFE_PROCESSES)

    def _is_protected_process_for_close(self, proc_name: str) -> bool:
        """Return True if *proc_name* must never be terminated by any Aidy action."""
        p = (proc_name or "").strip().lower()
        if not p:
            return True
        if not p.endswith(".exe"):
            p += ".exe"
        return p in STUDY_SAFE_PROCESSES

    def _is_study_never_close_process(self, proc_name: str) -> bool:
        """Return True if study mode / distract guard must skip this process."""
        p = (proc_name or "").strip().lower()
        if not p:
            return False
        if not p.endswith(".exe"):
            p += ".exe"
        return p in STUDY_SAFE_PROCESSES

    def _tasklist_rows_for_pid(self, pid: int) -> list[list[str]]:
        safe_pid = int(pid)
        if safe_pid <= 0:
            return []
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {safe_pid}", "/FO", "CSV", "/NH"],
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return []

        txt = (out or "").strip()
        if not txt:
            return []
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        if not lines:
            return []
        if lines[0].lower().startswith("info:"):
            return []
        try:
            return [row for row in csv.reader(io.StringIO("\n".join(lines))) if row]
        except Exception:
            return []

    def _process_name_by_pid(self, pid: int) -> str:
        rows = self._tasklist_rows_for_pid(pid)
        if not rows:
            return ""
        name = (rows[0][0] if rows[0] else "").strip().strip('"').lower()
        if name and not name.endswith(".exe"):
            name += ".exe"
        return name

    def _is_pid_alive(self, pid: int) -> bool:
        return bool(self._tasklist_rows_for_pid(pid))

    def _process_exec_and_cmdline(self, pid: int) -> tuple[str, str]:
        safe_pid = int(pid)
        if safe_pid <= 0:
            return "", ""
        ps = (
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={safe_pid}\" -ErrorAction SilentlyContinue;"
            "if($null -ne $p){"
            "$e=$p.ExecutablePath;$c=$p.CommandLine;"
            "if($null -eq $e){$e=''};if($null -eq $c){$c=''};"
            "Write-Output ($e + '|||' + $c)"
            "}"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=0.45,
            )
        except Exception:
            return "", ""
        raw = (out or "").strip()
        if not raw:
            return "", ""
        if "|||" not in raw:
            return raw.strip(), ""
        exe_path, cmdline = raw.split("|||", 1)
        return exe_path.strip(), cmdline.strip()

    def _capture_study_workspace_snapshot(self):
        windows = self._enum_visible_real_windows()
        active_hwnd = int(_USER32.GetForegroundWindow() or 0)
        pid_name_cache: dict[int, str] = {}
        pid_details_cache: dict[int, tuple[str, str]] = {}
        snapshot: list[dict] = []
        for win in windows:
            pid = int(win.get("pid") or 0)
            hwnd = int(win.get("hwnd") or 0)
            if pid <= 0 or hwnd <= 0:
                continue
            if pid in self.study_safe_pids:
                continue
            if pid not in pid_name_cache:
                pid_name_cache[pid] = self._process_name_by_pid(pid)
            process_name = pid_name_cache.get(pid) or ""
            if not process_name:
                continue
            # Snapshot only windows that study mode may actually close.
            if process_name in self.study_allowed_processes:
                continue
            if process_name in self.study_browser_processes:
                continue
            if pid not in pid_details_cache:
                pid_details_cache[pid] = self._process_exec_and_cmdline(pid)
            exe_path, cmdline = pid_details_cache.get(pid, ("", ""))
            snapshot.append(
                {
                    "hwnd": hwnd,
                    "pid": pid,
                    "title": (win.get("title") or "").strip(),
                    "process": process_name,
                    "exe_path": exe_path,
                    "cmdline": cmdline,
                    "was_active": (hwnd == active_hwnd),
                }
            )
        active_entry = next((w for w in snapshot if w.get("was_active")), None)
        with self._study_lock:
            self.study_workspace_snapshot = snapshot
            self.study_snapshot_active = active_entry
        self._study_log(
            f"workspace snapshot captured windows={len(snapshot)} active='{(active_entry or {}).get('title', '')}'"
        )

    def _restore_workspace_snapshot(self) -> bool:
        with self._study_lock:
            snapshot = list(self.study_workspace_snapshot)
            closed_pids = set(self.study_closed_window_pids)
        if not snapshot:
            self._study_log("workspace restore skipped: no snapshot")
            return False

        candidates: list[dict] = []
        seen: set[str] = set()
        for item in snapshot:
            pid = int(item.get("pid") or 0)
            process_name = (item.get("process") or "").strip().lower()
            exe_path = (item.get("exe_path") or "").strip()
            cmdline = (item.get("cmdline") or "").strip()
            if pid <= 0 or not process_name:
                continue
            if pid in self.study_safe_pids:
                continue
            if process_name in self.study_allowed_processes:
                continue
            if closed_pids and pid not in closed_pids:
                continue
            key = (exe_path or process_name).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "pid": pid,
                    "process": process_name,
                    "exe_path": exe_path,
                    "cmdline": cmdline,
                }
            )

        restored = 0
        for item in candidates:
            exe_path = item["exe_path"]
            cmdline = item["cmdline"]
            process_name = item["process"]
            launched = False
            if cmdline:
                try:
                    subprocess.Popen(
                        cmdline,
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                except Exception:
                    launched = False
            if (not launched) and exe_path:
                try:
                    subprocess.Popen(
                        [exe_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                except Exception:
                    launched = False
            if launched:
                restored += 1
                self._study_log(f"workspace restore launch process={process_name}")
            else:
                self._study_log(f"workspace restore failed process={process_name}")

        self._study_log(f"workspace restore complete restored={restored} candidates={len(candidates)}")
        return restored > 0

    def _ask_restore_previous_workspace(self) -> bool:
        ui_state("SPEAKING")
        self.voice.play_or_tts("restore_workspace_prompt", "Restore previous workspace?")
        ui_state("CONFIRM")
        self._deafen_after_speak()
        extra_yes = {
            "restore",
            "restore it",
            "restore workspace",
            "yes restore",
            "bring it back",
            "return back",
        }
        for attempt in range(2):
            reply = self._listen_confirm_reply(extra_grammar_phrases=extra_yes)
            decision = self._classify_confirm_reply(reply or "", extra_yes_phrases=extra_yes)
            if decision == "yes":
                return True
            if decision == "no":
                return False
            if attempt == 0:
                ui_state("SPEAKING")
                self.voice.play_or_tts("say_yes_no", "Please say yes or no.")
                ui_state("CONFIRM")
                self._deafen_after_speak()
        return False

    def _maybe_offer_restore_workspace(self, reason: str = "") -> bool:
        with self._study_lock:
            has_snapshot = bool(self.study_workspace_snapshot)
            pending = bool(self.study_restore_prompt_pending)
        if not has_snapshot:
            with self._study_lock:
                self.study_restore_prompt_pending = False
                self.study_restore_prompt_reason = ""
                self.study_workspace_snapshot = []
                self.study_snapshot_active = None
                self.study_closed_window_pids.clear()
            return False
        if not pending and not reason:
            return False

        why = reason or self.study_restore_prompt_reason or "study_end"
        self._study_log(f"restore prompt reason={why}")
        do_restore = self._ask_restore_previous_workspace()
        if do_restore:
            ui_state("EXECUTING")
            restored = self._restore_workspace_snapshot()
            ui_state("SPEAKING")
            if restored:
                self.voice.play_or_tts("workspace_restored", "Workspace restored.")
            else:
                self.voice.play_or_tts("workspace_restore_failed", "I couldn't restore previous apps.")
            ui_state("IDLE")
            self._deafen_after_speak()
        else:
            ui_state("SPEAKING")
            self.voice.play_or_tts("workspace_restore_skip", "Okay.")
            ui_state("IDLE")
            self._deafen_after_speak()

        with self._study_lock:
            self.study_restore_prompt_pending = False
            self.study_restore_prompt_reason = ""
            self.study_workspace_snapshot = []
            self.study_snapshot_active = None
            self.study_closed_window_pids.clear()
        return do_restore

    def _listen_study_confirm_reply(self, extra_grammar_phrases: set[str] | None = None) -> str | None:
        grammar = set(CONFIRM_GRAMMAR_PHRASES)
        grammar.update({"yes yes", "no no", "yea", "ya", "da", "net", "yes please", "no thanks"})
        for phrase in (extra_grammar_phrases or set()):
            p = " ".join(str(phrase or "").strip().lower().split())
            if p:
                grammar.add(p)
        reply = self.listen_command_vosk(
            max_seconds=3,
            min_listen_ms=120,
            ui_state_label="CONFIRM",
            use_grammar=True,
            speak_on_empty=False,
            grammar_phrases=sorted(grammar),
        )
        if reply:
            return reply
        return self.listen_command_vosk(
            max_seconds=2,
            min_listen_ms=100,
            ui_state_label="CONFIRM",
            use_grammar=False,
            speak_on_empty=False,
        )

    def _play_confirm_prompt_clip(self) -> bool:
        # Use the same voice style as other Aidy clips (are_you_sure_yes_no, then are_you_sure).
        for key in ("are_you_sure_yes_no", "are_you_sure"):
            try:
                if bool(self.voice.play_key(key)):
                    self._cmd_log(f"confirm prompt source=clip key='{key}'")
                    return True
            except Exception:
                continue
        return False

    def _speak_study_confirm_prompt(self):
        ui_state("SPEAKING")
        spoken = self._play_confirm_prompt_clip()
        if not spoken:
            self.voice.play_or_tts("study_confirm", "Are you sure?")
            self._cmd_log("confirm prompt source=tts text='Are you sure?'")
        ui_state("CONFIRM")
        self._deafen_after_speak(40)
        self._cmd_log("study confirm expected")

    def _is_window_cloaked(self, hwnd: int) -> bool:
        if _DWMAPI is None:
            return False
        try:
            cloaked = ctypes.c_int(0)
            hr = _DWMAPI.DwmGetWindowAttribute(
                wintypes.HWND(int(hwnd)),
                ctypes.c_uint(DWM_CLOAKED),
                ctypes.byref(cloaked),
                ctypes.sizeof(cloaked),
            )
            if int(hr) != 0:
                return False
            return int(cloaked.value) != 0
        except Exception:
            return False

    def _enum_visible_real_windows(self) -> list[dict]:
        windows: list[dict] = []
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        pid_box = ctypes.c_ulong(0)

        def _cb(hwnd, _lparam):
            try:
                if not _USER32.IsWindowVisible(hwnd):
                    return True
                if self._is_window_cloaked(int(hwnd)):
                    return True
                title_len = _USER32.GetWindowTextLengthW(hwnd)
                if title_len <= 0:
                    return True
                title_buf = ctypes.create_unicode_buffer(title_len + 1)
                _USER32.GetWindowTextW(hwnd, title_buf, title_len + 1)
                title = (title_buf.value or "").strip()
                if not title:
                    return True
                ex_style = _USER32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if int(ex_style) & WS_EX_TOOLWINDOW:
                    return True
                cls_buf = ctypes.create_unicode_buffer(128)
                _USER32.GetClassNameW(hwnd, cls_buf, len(cls_buf))
                class_name = (cls_buf.value or "").strip()
                pid_box.value = 0
                _USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_box))
                pid = int(pid_box.value)
                if pid <= 0:
                    return True
                windows.append({"hwnd": int(hwnd), "pid": pid, "title": title, "class_name": class_name})
            except Exception:
                return True
            return True

        try:
            _USER32.EnumWindows(enum_proc(_cb), 0)
        except Exception:
            return []
        return windows

    def _send_wm_close(self, hwnd: int, timeout_ms: int = 180) -> bool:
        try:
            # PostMessage is non-blocking and keeps the assistant responsive.
            if _USER32.PostMessageW(wintypes.HWND(int(hwnd)), WM_CLOSE, 0, 0):
                return True
            result = ctypes.c_size_t(0)
            ok = _USER32.SendMessageTimeoutW(
                wintypes.HWND(int(hwnd)),
                WM_CLOSE,
                0,
                0,
                SMTO_ABORTIFHUNG,
                int(max(250, timeout_ms)),
                ctypes.byref(result),
            )
            return bool(ok)
        except Exception:
            return False

    def _show_window(self, hwnd: int, cmd: int) -> bool:
        try:
            return bool(_USER32.ShowWindow(wintypes.HWND(int(hwnd)), int(cmd)))
        except Exception:
            return False

    def _set_foreground(self, hwnd: int) -> bool:
        try:
            return bool(_USER32.SetForegroundWindow(wintypes.HWND(int(hwnd))))
        except Exception:
            return False

    def _browser_windows(self) -> list[dict]:
        windows = self._enum_visible_real_windows()
        if not windows:
            return []
        pid_cache: dict[int, str] = {}
        out: list[dict] = []
        for win in windows:
            pid = int(win.get("pid") or 0)
            if pid <= 0:
                continue
            if pid not in pid_cache:
                pid_cache[pid] = self._process_name_by_pid(pid)
            proc = pid_cache.get(pid) or ""
            if proc in self.study_browser_processes:
                out.append({**win, "process": proc})
        return out

    def _minimize_browser_windows(self) -> int:
        minimized = 0
        for win in self._browser_windows():
            hwnd = int(win["hwnd"])
            try:
                _USER32.ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZE)
                minimized += 1
            except Exception:
                continue
        self._study_log(f"minimized browser windows={minimized}")
        return minimized

    def _restore_foreground_browser_window(self) -> bool:
        windows = self._browser_windows()
        if not windows:
            self._study_log("browser foreground restore skipped: no visible browser windows")
            return False
        for win in reversed(windows):
            hwnd = int(win["hwnd"])
            try:
                _USER32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
                if _USER32.SetForegroundWindow(wintypes.HWND(hwnd)):
                    self._study_log(f"browser window restored hwnd={hwnd} pid={win['pid']}")
                    return True
            except Exception:
                continue
        self._study_log("browser foreground restore failed")
        return False

    def _close_study_launched_browsers(self):
        with self._study_lock:
            pids = set(self.study_launched_browser_pids)
            self.study_launched_browser_pids.clear()
        if not pids:
            return

        windows = self._enum_visible_real_windows()
        for win in windows:
            pid = int(win.get("pid") or 0)
            if pid in pids:
                self._send_wm_close(int(win.get("hwnd") or 0), timeout_ms=120)
        time.sleep(0.35)

        killed = 0
        for pid in sorted(pids):
            if not self._is_pid_alive(pid):
                continue
            try:
                res = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                if res.returncode == 0:
                    killed += 1
            except Exception:
                continue
        self._study_log(f"study browsers closed pids={len(pids)} killed={killed}")

    def _soft_close_study_windows(self):
        self.study_closed_window_pids.clear()
        windows = self._enum_visible_real_windows()
        pid_cache: dict[int, str] = {}
        close_count = 0
        skipped = 0
        for win in windows:
            hwnd = int(win["hwnd"])
            pid = int(win["pid"])
            title = win.get("title") or ""
            class_name = (win.get("class_name") or "").strip()
            if pid in self.study_safe_pids:
                skipped += 1
                continue
            if pid not in pid_cache:
                pid_cache[pid] = self._process_name_by_pid(pid)
            process_name = pid_cache.get(pid) or ""
            if not process_name:
                self._study_log(f"skip hwnd={hwnd} pid={pid} title='{title}' reason=unknown_process")
                skipped += 1
                continue
            # Close folder windows while keeping explorer process alive.
            if process_name == "explorer.exe" and class_name in {"CabinetWClass", "ExploreWClass"}:
                sent = self._send_wm_close(hwnd, timeout_ms=180)
                close_count += 1
                self._study_log(
                    f"wm_close folder hwnd={hwnd} pid={pid} process={process_name} sent={'yes' if sent else 'no'} title='{title}'"
                )
                continue
            if self._is_study_never_close_process(process_name):
                skipped += 1
                self._study_log(
                    f"skip hwnd={hwnd} pid={pid} process={process_name} reason=study_never_close"
                )
                continue
            if process_name in self.study_allowed_processes:
                skipped += 1
                continue
            if process_name in self.study_browser_processes:
                skipped += 1
                continue
            # ── Final safeguard: never close anything on the safelist ────
            if process_name in STUDY_SAFE_PROCESSES:
                skipped += 1
                continue
            sent = self._send_wm_close(hwnd, timeout_ms=180)
            self.study_closed_window_pids.add(pid)
            close_count += 1
            self._study_log(
                f"wm_close hwnd={hwnd} pid={pid} process={process_name} sent={'yes' if sent else 'no'} title='{title}'"
            )
        self._study_log(
            f"window sweep done total={len(windows)} soft_close={close_count} skipped={skipped}"
        )

    def _hard_kill_lingering_study_processes(self):
        kill_count = 0
        for pid in sorted(self.study_closed_window_pids):
            if pid in self.study_safe_pids:
                continue
            if not self._is_pid_alive(pid):
                continue
            process_name = self._process_name_by_pid(pid)
            if not process_name:
                continue
            if self._is_study_never_close_process(process_name):
                continue
            if process_name in self.study_allowed_processes:
                continue
            # ── Final safeguard: never kill anything on the safelist ──
            if process_name in STUDY_SAFE_PROCESSES:
                self._study_log(f"hard-kill BLOCKED by safelist: pid={pid} process={process_name}")
                continue
            try:
                res = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                if res.returncode == 0:
                    kill_count += 1
                    self._study_log(f"taskkill pid={pid} process={process_name}")
                else:
                    self._study_log(f"taskkill_failed pid={pid} process={process_name} rc={res.returncode}")
            except Exception as e:
                self._study_log(f"taskkill_exception pid={pid} process={process_name} error={e}")
        self._study_log(f"hard-kill pass completed kills={kill_count}")

    def _tasklist_process_names(self) -> set[str]:
        try:
            res = subprocess.run(
                ["tasklist", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
        except Exception as e:
            self._study_log(f"DistractGuard tasklist exception: {e}")
            return set()

        if int(getattr(res, "returncode", 1)) != 0:
            self._study_log(f"DistractGuard tasklist failed rc={res.returncode}")
            return set()

        names: set[str] = set()
        try:
            for row in csv.reader(io.StringIO(res.stdout or "")):
                if not row:
                    continue
                name = (row[0] or "").strip().strip('"').lower()
                if not name or name.startswith("info:"):
                    continue
                if not name.endswith(".exe"):
                    name += ".exe"
                names.add(name)
        except Exception as e:
            self._study_log(f"DistractGuard csv parse failed: {e}")
        return names

    def _announce_distract_blocked(self, proc_name: str):
        proc = (proc_name or "").strip().lower()
        if not proc:
            return
        now = time.time()
        with self._study_lock:
            last_ts = float(self.distract_guard_last_announce_ts.get(proc, 0.0))
            if (now - last_ts) < float(DISTRACT_BLOCK_ANNOUNCE_COOLDOWN_SEC):
                return
            self.distract_guard_last_announce_ts[proc] = now
        if bool(getattr(self.voice, "muted", False)):
            return
        try:
            # Prefer local clip for the fastest start; fallback to TTS only if clip is missing.
            played = bool(self.voice.play_key("distract_guard_closed"))
            if not played:
                self.voice.play_or_tts("distract_guard_closed", "Closed by distract guard.")
            self._deafen_after_speak(40)
        except Exception:
            pass

    def close_process_soft_then_hard(self, proc_name: str) -> bool:
        target = " ".join((proc_name or "").strip().lower().split())
        if not target:
            return False
        if not target.endswith(".exe"):
            target += ".exe"
        if self._is_study_never_close_process(target):
            self._study_log(f"DistractGuard skip protected process: {target}")
            return False
        if target not in self.study_distract_processes:
            return False

        pid_name_cache: dict[int, str] = {}
        sent_count = 0
        windows = self._enum_visible_real_windows()
        for win in windows:
            pid = int(win.get("pid") or 0)
            hwnd = int(win.get("hwnd") or 0)
            if pid <= 0 or hwnd <= 0:
                continue
            if pid not in pid_name_cache:
                pid_name_cache[pid] = self._process_name_by_pid(pid)
            process_name = pid_name_cache.get(pid) or ""
            if process_name != target:
                continue
            if self._send_wm_close(hwnd, timeout_ms=180):
                sent_count += 1

        self._study_log(f"DistractGuard {target}: WM_CLOSE sent to {sent_count} windows")
        if sent_count > 0:
            time.sleep(DISTRACT_SOFT_CLOSE_WAIT_SEC)

        remaining = self._tasklist_process_names()
        if target not in remaining:
            return sent_count > 0

        try:
            res = subprocess.run(
                ["taskkill", "/IM", target, "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            self._study_log(f"DistractGuard {target}: taskkill result code {res.returncode}")
            if int(getattr(res, "returncode", 1)) == 0:
                return True
        except Exception as e:
            self._study_log(f"DistractGuard {target}: taskkill exception {e}")

        return False

    def distract_guard_loop(self):
        self._study_log("DistractGuard started")
        try:
            while True:
                if self.distract_guard_stop_event.is_set():
                    break
                with self._study_lock:
                    enabled = bool(self.distract_guard_enabled)
                    active = bool(self.study_mode_active)
                if (not enabled) or (not active):
                    break

                running = self._tasklist_process_names()
                if running:
                    hits = sorted(set(self.study_distract_processes) & running)
                    for proc in hits:
                        self._study_log(f"Detected distract process: {proc}")
                        if self.close_process_soft_then_hard(proc):
                            self._announce_distract_blocked(proc)
                if self.distract_guard_stop_event.wait(float(DISTRACT_GUARD_INTERVAL_SEC)):
                    break
        finally:
            with self._study_lock:
                self.distract_guard_enabled = False
            self._study_log("DistractGuard stopped")

    def start_distract_guard(self):
        with self._study_lock:
            if self.distract_guard_enabled:
                return False
            self.distract_guard_enabled = True
            self.distract_guard_stop_event.clear()
            guard_thread = threading.Thread(target=self.distract_guard_loop, daemon=True)
            self.distract_guard_thread = guard_thread
        guard_thread.start()
        return True

    def stop_distract_guard(self):
        with self._study_lock:
            guard_thread = self.distract_guard_thread
            self.distract_guard_enabled = False
            self.distract_guard_stop_event.set()
            self.distract_guard_thread = None
        if (
            guard_thread
            and guard_thread.is_alive()
            and (guard_thread is not threading.current_thread())
        ):
            guard_thread.join(timeout=1.4)
        self._study_log("DistractGuard stop requested")
        return True

    def _open_with_start(self, target: str) -> bool:
        try:
            subprocess.Popen(
                ["cmd.exe", "/C", "start", "", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._study_log(f"opened target={target}")
            return True
        except Exception as e:
            self._study_log(f"open failed target={target} error={e}")
            return False

    def _run_powershell_capture(self, ps_command: str) -> str:
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            return (out or "").strip()
        except Exception:
            return ""

    def _get_notifications_enabled(self) -> int | None:
        ps = (
            "$v=(Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications' "
            "-Name ToastEnabled -ErrorAction SilentlyContinue).ToastEnabled;"
            "if($null -eq $v){'1'} else {[string]$v}"
        )
        raw = self._run_powershell_capture(ps)
        if raw == "":
            return None
        try:
            return 1 if int(raw) != 0 else 0
        except Exception:
            return None

    def _set_notifications_enabled(self, enabled: bool) -> bool:
        value = "1" if enabled else "0"
        ps = (
            "New-Item -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications' -Force | Out-Null;"
            f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\PushNotifications' -Name ToastEnabled -Type DWord -Value {value} -Force"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return True
        except Exception:
            return False

    def _disable_notifications_for_study(self):
        current = self._get_notifications_enabled()
        with self._study_lock:
            if self.study_notifications_prev_enabled is None:
                self.study_notifications_prev_enabled = current
        ok = self._set_notifications_enabled(False)
        self._study_log(
            f"notifications disabled={'yes' if ok else 'no'} previous={current if current is not None else 'unknown'}"
        )

    def _restore_notifications_after_study(self):
        with self._study_lock:
            prev = self.study_notifications_prev_enabled
            self.study_notifications_prev_enabled = None
        target_enabled = True if prev is None else bool(prev)
        ok = self._set_notifications_enabled(target_enabled)
        self._study_log(
            f"notifications restored={'yes' if ok else 'no'} target={'on' if target_enabled else 'off'}"
        )

    def _open_learning_browser_window(self) -> bool:
        urls = list(STUDY_OPEN_URLS)
        ts = int(time.time())
        tmp_root = os.path.join(os.environ.get("TEMP", os.getcwd()), "AidyStudyBrowser")
        try:
            os.makedirs(tmp_root, exist_ok=True)
        except Exception:
            pass

        opera_gx_candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Opera GX\opera.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Opera GX\opera.exe"),
        ]
        for exe in opera_gx_candidates:
            if not exe or not os.path.exists(exe):
                continue
            profile_dir = os.path.join(tmp_root, f"opera_gx_{ts}")
            try:
                proc = subprocess.Popen(
                    [exe, f"--user-data-dir={profile_dir}", "--new-window", *urls],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                if int(getattr(proc, "pid", 0) or 0) > 0:
                    with self._study_lock:
                        self.study_launched_browser_pids.add(int(proc.pid))
                self._study_log(f"opened learning browser via opera_gx profile={profile_dir}")
                return True
            except Exception:
                continue

        browser_variants: list[tuple[list[str], str]] = [
            (["msedge", f"--user-data-dir={os.path.join(tmp_root, f'edge_{ts}')}", "--new-window", *urls], "edge"),
            (["chrome", f"--user-data-dir={os.path.join(tmp_root, f'chrome_{ts}')}", "--new-window", *urls], "chrome"),
            (["brave", f"--user-data-dir={os.path.join(tmp_root, f'brave_{ts}')}", "--new-window", *urls], "brave"),
            (["opera", f"--user-data-dir={os.path.join(tmp_root, f'opera_{ts}')}", "--new-window", *urls], "opera"),
            (["firefox", "-new-instance", "-profile", os.path.join(tmp_root, f"firefox_{ts}"), "-new-window", *urls], "firefox"),
        ]
        for cmd, label in browser_variants:
            try:
                if label == "firefox":
                    os.makedirs(cmd[3], exist_ok=True)
                else:
                    for arg in cmd:
                        if arg.startswith("--user-data-dir="):
                            os.makedirs(arg.split("=", 1)[1], exist_ok=True)
                            break
            except Exception:
                pass
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                if int(getattr(proc, "pid", 0) or 0) > 0:
                    with self._study_lock:
                        self.study_launched_browser_pids.add(int(proc.pid))
                self._study_log(f"opened learning browser via {label} separate-instance")
                return True
            except Exception:
                continue

        self._study_log("failed to open separate learning browser instance; shell fallback skipped")
        return False

    def _open_study_targets(self):
        self._open_learning_browser_window()

    def _study_setup_worker(self):
        self._study_log("study setup worker started")
        try:
            self._capture_study_workspace_snapshot()
            if not self.study_mode_active or self.study_abort_flag:
                self._study_log("study setup aborted before opening targets")
                return
            self._soft_close_study_windows()
            time.sleep(0.25)
            self._hard_kill_lingering_study_processes()
            if not self.study_mode_active or self.study_abort_flag:
                self._study_log("study setup aborted before opening targets")
                return
            try:
                show_desktop()
            except Exception:
                pass
            self._open_study_targets()
            self._disable_notifications_for_study()
            self._study_log("study setup worker completed")
        except Exception as e:
            self._study_log(f"study setup worker error={e}")

    def _announce_study_finished(self):
        ui_state("SPEAKING")
        played = False
        try:
            played = bool(self.voice.play_key("study_finished"))
        except Exception:
            played = False
        if not played:
            try:
                played = bool(self.voice.tts_blocking("Forty five minutes are over."))
            except Exception:
                played = False
        if played:
            self._deafen_after_speak()
        ui_state("IDLE")

    def _study_timer_worker(self, expected_end_ts: float):
        self._study_log("timer thread started")
        try:
            while True:
                with self._study_lock:
                    active = bool(self.study_mode_active)
                    aborted = bool(self.study_abort_flag)
                    end_ts = self.study_timer_end_ts
                    total = int(self.study_timer_total_seconds or 0)
                    last_sent = int(self.study_timer_last_second_sent or 0)
                remaining = int(max(0, float(expected_end_ts) - time.time() + 0.999))
                if total > 0 and remaining != last_sent:
                    self.study_timer_last_second_sent = remaining
                    self._emit_timer_ui("tick", remaining, total)
                if not active:
                    self._study_log("timer thread exit: study mode inactive")
                    return
                if aborted:
                    self._study_log("timer thread exit: abort flag set")
                    return
                if end_ts is None or abs(float(end_ts) - float(expected_end_ts)) > 0.001:
                    self._study_log("timer thread exit: timer replaced")
                    return
                if time.time() >= expected_end_ts:
                    break
                time.sleep(0.35)

            with self._study_lock:
                if (not self.study_mode_active) or self.study_abort_flag:
                    self._study_log("timer completion ignored: mode inactive or aborted")
                    return
                total = int(self.study_timer_total_seconds or 0)
                self.study_timer_end_ts = None
                self.study_timer_total_seconds = 0
                self.study_timer_last_second_sent = -1
                self.study_mode_active = False
                self.study_abort_flag = False
                self.study_restore_prompt_pending = bool(self.study_workspace_snapshot)
                self.study_restore_prompt_reason = "timer_finished"
            self._study_log("45-minute timer finished")
            self.stop_distract_guard()
            ui_study_mode(False)
            self._close_study_launched_browsers()
            self._emit_timer_ui("done", 0, total)
            self._restore_notifications_after_study()
            self._announce_study_finished()
        except Exception as e:
            self._study_log(f"timer thread error={e}")

    def _start_study_timer(self, seconds: int) -> bool:
        safe_seconds = max(1, int(seconds))
        end_ts = time.time() + safe_seconds
        with self._study_lock:
            self.study_abort_flag = False
            self.study_timer_end_ts = end_ts
            self.study_timer_total_seconds = safe_seconds
            self.study_timer_last_second_sent = -1
            timer_thread = threading.Thread(
                target=self._study_timer_worker,
                args=(end_ts,),
                daemon=True,
            )
            self._study_timer_thread = timer_thread
        self._emit_timer_ui("start", safe_seconds, safe_seconds)
        self._study_log(f"TIMER:start seconds={safe_seconds} end_ts={int(end_ts)}")
        timer_thread.start()
        return True

    def _update_study_timer_watchdog(self):
        with self._study_lock:
            active = bool(self.study_mode_active)
            aborted = bool(self.study_abort_flag)
            end_ts = self.study_timer_end_ts
            total = int(self.study_timer_total_seconds or 0)
            last_sent = int(self.study_timer_last_second_sent or -1)
        if not active or aborted or end_ts is None:
            return

        remaining = int(max(0, float(end_ts) - time.time() + 0.999))
        if total > 0 and remaining != last_sent:
            self.study_timer_last_second_sent = remaining
            self._emit_timer_ui("tick", remaining, total)
        if remaining > 0:
            return

        with self._study_lock:
            if (not self.study_mode_active) or self.study_abort_flag or self.study_timer_end_ts is None:
                return
            total_done = int(self.study_timer_total_seconds or 0)
            self.study_timer_end_ts = None
            self.study_timer_total_seconds = 0
            self.study_timer_last_second_sent = -1
            self.study_mode_active = False
            self.study_abort_flag = False
            self.study_restore_prompt_pending = bool(self.study_workspace_snapshot)
            self.study_restore_prompt_reason = "timer_finished"
        self._study_log("45-minute timer finished (watchdog)")
        self.stop_distract_guard()
        ui_study_mode(False)
        self._close_study_launched_browsers()
        self._emit_timer_ui("done", 0, total_done)
        self._restore_notifications_after_study()
        self._announce_study_finished()

    def _study_time_left_seconds(self) -> int:
        with self._study_lock:
            end_ts = self.study_timer_end_ts
            active = self.study_mode_active
        if not active or end_ts is None:
            return 0
        return int(max(0, float(end_ts) - time.time()))

    def _speak_study_mode_status(self) -> bool:
        if not self.study_mode_active:
            ui_state("SPEAKING")
            self.voice.play_or_tts("study_mode_status", "Study mode is not active.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False
        remaining = self._study_time_left_seconds()
        if self.study_timer_end_ts is None:
            msg = "Study mode is active. Timer already finished."
        else:
            mins, secs = divmod(remaining, 60)
            if mins > 0 and secs > 0:
                msg = f"Study mode is active. {mins} minutes {secs} seconds left."
            elif mins > 0:
                msg = f"Study mode is active. {mins} minutes left."
            else:
                msg = f"Study mode is active. {secs} seconds left."
        ui_state("SPEAKING")
        self.voice.play_or_tts("study_mode_status", msg)
        self._deafen_after_speak()
        ui_state("IDLE")
        return True

    def _stop_study_mode(self, announce: bool = True, prompt_restore_now: bool = False) -> bool:
        with self._study_lock:
            if not self.study_mode_active:
                if announce:
                    ui_state("SPEAKING")
                    self.voice.play_or_tts("study_mode_not_active", "Study mode is not active.")
                    self._deafen_after_speak()
                    ui_state("IDLE")
                return False
            self.study_abort_flag = True
            self.study_mode_active = False
            remaining = int(max(0, self.study_timer_end_ts - time.time() + 0.999)) if self.study_timer_end_ts else 0
            total = int(self.study_timer_total_seconds or 0)
            self.study_timer_end_ts = None
            self.study_timer_total_seconds = 0
            self.study_timer_last_second_sent = -1
            self.study_restore_prompt_pending = bool(self.study_workspace_snapshot)
            self.study_restore_prompt_reason = "manual_stop"
        self._study_log("study mode stopped")
        self.stop_distract_guard()
        ui_study_mode(False)
        self._close_study_launched_browsers()
        if total > 0:
            self._emit_timer_ui("stop", remaining, total)
        self._restore_notifications_after_study()
        if announce:
            ui_state("SPEAKING")
            self.voice.play_or_tts("study_mode_stopped", "Stopped.")
            self._deafen_after_speak()
            ui_state("IDLE")
        if prompt_restore_now:
            self._maybe_offer_restore_workspace(reason="manual_stop")
        return True

    def _start_study_mode(self) -> bool:
        with self._study_lock:
            if self.study_mode_active:
                ui_state("SPEAKING")
                self.voice.play_or_tts("study_mode_already", "Already in study mode.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            self.study_mode_active = True
            self.study_abort_flag = False
            self.study_timer_end_ts = None
            self.study_closed_window_pids.clear()
            self.study_workspace_snapshot = []
            self.study_snapshot_active = None
            self.study_restore_prompt_pending = False
            self.study_restore_prompt_reason = ""
            self.study_actions_log = []
            self.study_notifications_prev_enabled = None
            self.study_launched_browser_pids.clear()
            self.distract_guard_last_announce_ts.clear()
        self._study_log("study mode start requested")

        # Start timer first so study session is always armed even if later setup steps fail.
        timer_started = self._start_study_timer(STUDY_SESSION_SECONDS)
        self.start_distract_guard()
        ui_study_mode(True)

        setup_thread = threading.Thread(target=self._study_setup_worker, daemon=True)
        self._study_setup_thread = setup_thread
        setup_thread.start()

        ui_state("SPEAKING")
        self.voice.play_or_tts(
            "study_mode_started",
            ("Study mode started. Forty five minute timer is running." if timer_started
             else "Study mode started. Timer fallback is armed."),
        )
        self._deafen_after_speak()
        ui_state("IDLE")
        return True

    def _study_start_requires_confirmation(self, text: str) -> bool:
        t = " ".join((text or "").strip().lower().split())
        if not t:
            return False
        if t in STUDY_MODE_DIRECT_START_PHRASES:
            return False
        if t in STUDY_MODE_CONFIRM_START_PHRASES:
            return True
        tokens = set(t.split())
        study_tokens = {"study", "stady", "studi", "stadi"}
        mode_tokens = {"mode", "mood", "mod"}
        if tokens & study_tokens:
            if tokens & mode_tokens:
                return False
            return True
        return False

    def _confirm_study_mode_request(self, heard_text: str) -> bool:
        self._study_log(f"study confirmation required for '{heard_text}'")
        self._speak_study_confirm_prompt()
        extra_yes = {
            "yes",
            "do it",
            "start study mode",
            "study mode",
            "start study",
            "start",
            "да",
            "da",
        }
        for attempt in range(1):
            reply = self._listen_study_confirm_reply(extra_grammar_phrases=extra_yes)
            decision = self._classify_confirm_reply(reply or "", extra_yes_phrases=extra_yes)
            if decision == "yes":
                self._study_log("study confirmation accepted")
                return True
            if decision == "no":
                self._study_log("study confirmation declined")
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            self._study_log("study confirmation unresolved")
        self._study_log("study confirmation unresolved -> cancelled")
        ui_state("WARNING")
        self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
        self._deafen_after_speak()
        ui_state("IDLE")
        return False

    def _confirm_study_stop_request(self, heard_text: str) -> bool:
        self._study_log(f"study stop confirmation required for '{heard_text}'")
        self._speak_confirm_prompt()
        extra_yes = {
            "yes",
            "yeah",
            "yep",
            "confirm",
            "да",
            "da",
        }
        for _ in range(1):
            reply = self._listen_confirm_reply(extra_grammar_phrases=extra_yes)
            decision = self._classify_confirm_reply(reply or "", extra_yes_phrases=extra_yes)
            if decision == "yes":
                self._study_log("study stop confirmation accepted")
                return True
            if decision == "no":
                self._study_log("study stop confirmation declined")
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
        self._study_log("study stop confirmation unresolved -> cancelled")
        ui_state("WARNING")
        self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
        self._deafen_after_speak()
        ui_state("IDLE")
        return False

    def _study_intent_from_text(self, text: str) -> str | None:
        t = " ".join((text or "").strip().lower().split())
        if not t:
            return None
        if t in STUDY_MODE_STOP_PHRASES:
            return INTENT_STUDY_MODE_STOP
        if t in STUDY_MODE_STATUS_PHRASES:
            return INTENT_STUDY_MODE_STATUS
        if t in STUDY_MODE_START_PHRASES or t in STUDY_MODE_START_ALIASES:
            return INTENT_STUDY_MODE_START
        tokens = set(t.split())
        if tokens & {"study", "stady", "studi", "stadi"}:
            return INTENT_STUDY_MODE_START
        return None

    def _generate_wake_clips_async(self):
        """Generate 'Yes sir' wake-ack clips in a background thread.

        Runs pyttsx3 save_to_file + runAndWait in a daemon thread so the
        main startup path is never blocked.  A marker file prevents
        redundant regeneration on subsequent launches.
        """
        marker = os.path.join(self.voice.voice_dir, ".yes_sir_generated")
        if os.path.exists(marker):
            # Already generated on a previous launch — just register in cache
            yes_sir_path = os.path.join(self.voice.voice_dir, "yes_sir.wav")
            if os.path.exists(yes_sir_path):
                self.voice._audio_cache["yes_sir"] = yes_sir_path
            # Still warm up TTS in the background for other calls
            self.voice.warmup_tts()
            return

        def _gen():
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            try:
                # Create a fresh engine for this thread (pyttsx3 is not
                # thread-safe when sharing engines across threads).
                engine = pyttsx3.init()
                engine.setProperty("rate", 188)
                voices = engine.getProperty("voices")
                for v in voices:
                    name = (getattr(v, "name", "") or "").lower()
                    if "zira" in name:
                        engine.setProperty("voice", v.id)
                        break

                voice_dir = self.voice.voice_dir
                os.makedirs(voice_dir, exist_ok=True)

                # Generate all clips in one batch
                clips = ["yes_sir", "wake_fast"]
                clips += [f"wake_{i:02d}" for i in range(1, 5)]
                for key in clips:
                    out_path = os.path.join(voice_dir, f"{key}.wav")
                    # Remove old mp3 version
                    mp3_path = os.path.join(voice_dir, f"{key}.mp3")
                    if os.path.exists(mp3_path):
                        try:
                            os.remove(mp3_path)
                        except Exception:
                            pass
                    engine.save_to_file("Yes sir.", out_path)
                engine.runAndWait()

                # Verify and cache
                for key in clips:
                    out_path = os.path.join(voice_dir, f"{key}.wav")
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                        self.voice._audio_cache[key] = out_path

                # Write marker so we skip regeneration next time
                try:
                    with open(marker, "w") as f:
                        f.write("yes_sir")
                except Exception:
                    pass
                print("[VOICE] Generated 'Yes sir' wake clips", flush=True)
            except Exception as e:
                print(f"[VOICE] Wake clip generation failed: {e}", flush=True)

            # Warm up the TTS worker engine AFTER clip generation finishes
            # (only one pyttsx3 engine should be active at a time).
            try:
                self.voice._ensure_tts_engine()
                print("[TTS-WARMUP] engine ready", flush=True)
            except Exception as e:
                print(f"[TTS-WARMUP] failed: {e}", flush=True)

        t = threading.Thread(target=_gen, daemon=True)
        t.start()

    def _play_wake_ack(self):
        if bool(getattr(self.voice, "muted", False)):
            # Respect explicit mute command: keep assistant silent until unmute.
            self._wake_log("ack skipped: voice muted")
            ui_state("IDLE")
            return
        ui_state("SPEAKING")
        self._wake_log("ack clip 'yes_sir'")
        played = False
        try:
            # Play the pre-generated "Yes sir" clip via pygame (non-TTS).
            # Capped at 800ms so it finishes the phrase but never blocks
            # longer than that (TTS-generated wavs can vary in length).
            played = bool(self.voice.play_key("yes_sir", max_ms=800))
            if played:
                self._wake_log("ack source=clip key='yes_sir'")
        except Exception as e:
            self._wake_log(f"ack yes_sir clip failed: {e}")
            played = False
        if not played:
            # Fallback: try the original wake clips
            try:
                played = bool(self.voice.play_key("wake_fast", max_ms=800))
                if played:
                    self._wake_log("ack fallback source=clip key='wake_fast'")
            except Exception:
                pass
        if not played:
            try:
                played = bool(self.voice.play_key("wake", max_ms=800))
                if played:
                    self._wake_log("ack fallback source=clip key='wake'")
            except Exception:
                pass
        if played:
            guard_ms = 30
            self._deafen_after_speak(guard_ms)
            if self.wake_recognizer is not None:
                try:
                    self.wake_recognizer.Reset()
                except Exception:
                    pass
            self._wake_log(f"ack guard_ms={guard_ms}")
        else:
            self._wake_log("ack silent: all clips failed")

    def _speak_not_heard(self):
        """Tell the user we didn't catch a command, then deafen to avoid echo."""
        if bool(getattr(self.voice, "muted", False)):
            return
        ui_state("SPEAKING")
        # Blocking playback so SPEAKING state persists for the clip duration
        played = bool(self.voice.play_key("not_heard", max_ms=1500))
        if not played:
            played = self.voice.play_or_tts(
                "not_heard", "Sorry, I didn't catch that.",
            )
        if played:
            self._deafen_after_speak(60)
            if self.wake_recognizer is not None:
                try:
                    self.wake_recognizer.Reset()
                except Exception:
                    pass

    def _normalize_wake_text(self, text: str) -> str:
        t = (text or "").lower().strip()
        if not t:
            return ""
        t = t.replace("'", "")
        cleaned = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in t)
        return " ".join(cleaned.split())

    def _is_greeting_only(self, text: str) -> bool:
        return text in {"hey", "hello", "ok", "okay", "hi", "хей", "хэй"}

    def _match_wake_prefix(self, text: str, prefixes: list[str]) -> tuple[bool, str]:
        for p in prefixes:
            if not p:
                continue
            if text == p:
                return True, ""
            px = p + " "
            if text.startswith(px):
                return True, text[len(px):].strip()
        return False, ""

    def _detect_wake_with_tail(self, normalized_text: str, greeting_latched: bool) -> tuple[bool, str, str]:
        if not normalized_text:
            return False, "", ""

        if is_wake_phrase(normalized_text):
            matched, tail = self._match_wake_prefix(normalized_text, self._wake_prefixes)
            if matched:
                return True, tail, "keyword"
            return True, "", "keyword"

        if greeting_latched:
            matched, tail = self._match_wake_prefix(normalized_text, self._wake_alias_prefixes)
            # Fuzzy aliases are noisy; require an actual tail command.
            if matched and tail:
                return True, tail, "fuzzy"

        return False, "", ""

    def _flush_audio(self, ms: int):
        if not self.stream:
            return
        frames = int(ms / (CHUNK_SAMPLES / SAMPLE_RATE * 1000.0))
        for _ in range(max(1, frames)):
            try:
                self.stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
            except Exception:
                break

    def _deafen_after_speak(self, ms: int | None = None):
        if getattr(self.voice, "muted", False):
            return
        if not self.stream:
            return
        if ms is None:
            ms = self.DEAFEN_MS_AFTER_TTS
        # Non-blocking deafen window:
        # keep UI state transitions responsive while still ignoring mic input
        # right after TTS playback to avoid self-recognition.
        hold_ms = max(0, int(ms)) + int(self.FLUSH_MS)
        now = time.time()
        self._deafen_until = max(self._deafen_until, now + (hold_ms / 1000.0))

    def _is_deafened(self) -> bool:
        if getattr(self.voice, "muted", False):
            return False
        return time.time() < self._deafen_until

    def _reset_recognition_after_command(self):
        if not self.stream:
            return
        flush_ms = int(self.POST_COMMAND_FLUSH_MS)
        now = time.time()
        self._deafen_until = max(self._deafen_until, now + (flush_ms / 1000.0))
        self._flush_audio(flush_ms)
        # Reset existing recognizer instead of creating a new one —
        # avoids expensive KaldiRecognizer construction (~10-30ms).
        if self.wake_recognizer is not None:
            try:
                self.wake_recognizer.Reset()
            except Exception:
                if self.model is not None:
                    self.wake_recognizer = self._new_wake_recognizer()
        elif self.model is not None:
            self.wake_recognizer = self._new_wake_recognizer()

    def _sync_state_before_listen(self, listen_state: str):
        # Reflect target state immediately; keep guard internal.
        ui_state(listen_state)
        # Only sleep through deafen if substantial time remains.
        # Short deafen windows (e.g. post-wake-ack 20ms) are handled by
        # _is_deafened() skipping frames inside the listen loop — no
        # blocking sleep needed.  Only sleep for longer deafen windows
        # (e.g. after full TTS responses).
        if self._is_deafened():
            remaining = self._deafen_until - time.time()
            if remaining > 0.06:
                # Cap the sleep so we never block command listening too long.
                time.sleep(min(remaining, 0.06))

    def _sleep_success(self):
        if not self.voice.muted:
            ui_state("SPEAKING")
            self.voice.play_or_tts("success", "Task finished.")
            self._deafen_after_speak()
            ui_state("SUCCESS")
        # Minimal pause — just enough for UI state to register visually.
        time.sleep(0.05)

    def _sleep_error(self):
        time.sleep(0.05)

    def _apply_volume_steps(self, up: bool, steps: int) -> bool:
        safe_steps = max(1, min(10, int(steps)))
        try:
            volume_steps(up=up, steps=safe_steps)
            return True
        except Exception as e:
            error(f"Volume change failed: {e}")
            return False

    def _format_timer_speech(self, seconds: int) -> str:
        seconds = max(1, int(seconds))
        mins, secs = divmod(seconds, 60)
        if mins > 0 and secs > 0:
            return f"{mins} minutes {secs} seconds"
        if mins > 0:
            return f"{mins} minutes"
        return f"{secs} seconds"

    def _emit_timer_ui(self, event: str, remaining_seconds: int, total_seconds: int):
        ui_timer(event, int(max(0, remaining_seconds)), int(max(0, total_seconds)))

    def _start_timer(self, seconds: int, source_text: str | None = None) -> bool:
        seconds = int(seconds)
        if seconds <= 0 or seconds > TIMER_MAX_SECONDS:
            ui_state("WARNING")
            self.voice.play_or_tts("not_sure", "I couldn't set that timer")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        replacing = self.timer_active
        if replacing:
            self._emit_timer_ui("stop", 0, self.timer_total_seconds)

        self.timer_active = True
        self.timer_total_seconds = seconds
        self.timer_end_at = time.time() + float(seconds)
        self.timer_last_second_sent = -1
        self._emit_timer_ui("start", seconds, seconds)

        ui_state("SPEAKING")
        if replacing:
            msg = f"Timer reset to {self._format_timer_speech(seconds)}."
        else:
            msg = f"Timer set for {self._format_timer_speech(seconds)}."
        self.voice.play_or_tts("timer_set", msg)
        self._deafen_after_speak()
        ui_state("IDLE")

        if source_text:
            self._set_last_command(source_text)
        self._set_memory("timer", {"seconds": seconds})
        self._set_context("timer", {"seconds": seconds})
        return True

    def _cancel_timer(self, announce: bool = True) -> bool:
        if not self.timer_active:
            if announce:
                ui_state("SPEAKING")
                self.voice.play_or_tts("not_now", "No active timer.")
                self._deafen_after_speak()
                ui_state("IDLE")
            return False

        total = self.timer_total_seconds
        self.timer_active = False
        self.timer_total_seconds = 0
        self.timer_end_at = 0.0
        self.timer_last_second_sent = -1
        self._emit_timer_ui("stop", 0, total)

        if announce:
            ui_state("SPEAKING")
            self.voice.play_or_tts("cancelled", "Timer cancelled.")
            self._deafen_after_speak()
            ui_state("IDLE")
        return True

    def _update_timer(self):
        if not self.timer_active:
            return

        remaining = int(max(0, self.timer_end_at - time.time() + 0.999))
        if remaining != self.timer_last_second_sent:
            self.timer_last_second_sent = remaining
            self._emit_timer_ui("tick", remaining, self.timer_total_seconds)

        if remaining > 0:
            return

        total = self.timer_total_seconds
        self.timer_active = False
        self.timer_total_seconds = 0
        self.timer_end_at = 0.0
        self.timer_last_second_sent = -1
        self._emit_timer_ui("done", 0, total)

        ui_state("SPEAKING")
        self.voice.play_or_tts("timer_done", "Timer is done.")
        self._deafen_after_speak()
        ui_state("IDLE")

    def __init__(self, base_dir: str | None = None):
        if base_dir:
            self.base_dir = os.path.abspath(base_dir)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))

        self.model_path = self._resolve_model_path()

        from aidy import config as _cfg
        _cfg.reload_from_config_json(self.base_dir)

        try:
            if vosk is None:
                raise ImportError("Package 'vosk' is not installed")
            if not self.model_path:
                raise FileNotFoundError(
                    "Local Vosk model not found. Expected folder: vosk-model-small-en-us-0.15"
                )
            model_path = self._short_path(self.model_path)
            info(f"Vosk model path: {self.model_path}")
            self.model = vosk.Model(model_path)
        except Exception as e:
            warn(f"Failed to load Vosk model: {e}")
            self.model = None

        loaded_phrases_list, self._text_to_intent_map = load_command_phrases(self.base_dir)
        loaded_phrases = set(loaded_phrases_list)
        legacy_accuracy_env = os.environ.get("AIDY_LEGACY_ACCURACY", "1").strip().lower()
        self.legacy_accuracy_mode = legacy_accuracy_env not in {"0", "false", "off", "no"}

        common_phrases = (
            set(CONFIRM_GRAMMAR_PHRASES)
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
            | set(TIMER_START_PHRASES)
            | set(TIMER_CANCEL_PHRASES)
            | set(CUSTOM_MODE_ON_PHRASES)
            | set(CUSTOM_MODE_OFF_PHRASES)
            | set(STUDY_MODE_STOP_PHRASES)
            | set(STUDY_MODE_STATUS_PHRASES)
            | set(MOUSE_COMMAND_PHRASES)
        )

        if self.legacy_accuracy_mode:
            # Accuracy-first grammar: keep command grammar focused and avoid noisy wake/study aliases.
            study_grammar = set(STUDY_MODE_DIRECT_START_PHRASES)
        else:
            # Wider grammar coverage for mixed/noisy phrases.
            study_grammar = set(STUDY_MODE_START_PHRASES) | set(STUDY_MODE_START_ALIASES)

        self.command_phrases = sorted(
            loaded_phrases | common_phrases | study_grammar | set(GRANT_GRAMMAR_PHRASES)
        )
        self._cmd_log(
            f"accuracy_profile={'legacy' if self.legacy_accuracy_mode else 'wide'} "
            f"grammar_phrases={len(self.command_phrases)}"
        )
        self._wake_prefixes = sorted(
            {self._normalize_wake_text(w) for w in WAKE_KEYWORDS if self._normalize_wake_text(w)},
            key=len,
            reverse=True,
        )
        self._wake_alias_prefixes = sorted(
            {
                self._normalize_wake_text(w)
                for w in WAKE_FUZZY_ALIASES
                if self._normalize_wake_text(w)
            },
            key=len,
            reverse=True,
        )

        self.apps = load_apps_config(self.base_dir)

        if pyaudio is None:
            warn("Package 'pyaudio' is not installed. Microphone capture disabled.")
            self.audio = None
        else:
            self.audio = pyaudio.PyAudio()
        self.stream = None

        self.voice = Voice(self.base_dir)
        # Pre-generate the "Yes sir" wake-ack clips in a background thread
        # so startup never blocks.  The thread also warms up the TTS engine
        # after clip generation, so warmup_tts() is NOT called separately
        # (running two pyttsx3 engines in parallel causes COM conflicts).
        self._generate_wake_clips_async()
        self.intent_api_ready = start_local_intent_api(self.base_dir)
        if not self.intent_api_ready:
            warn("Local Intent API not started. Offline keyword mode only.")
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
        self.timer_active = False
        self.timer_total_seconds = 0
        self.timer_end_at = 0.0
        self.timer_last_second_sent = -1
        self.study_mode_active = False
        self.study_timer_end_ts: float | None = None
        self.study_abort_flag = False
        self.study_allowed_processes = self._study_default_allowed_processes()
        self.study_safe_pids: set[int] = {os.getpid()}
        if os.getppid() > 0:
            self.study_safe_pids.add(os.getppid())
        self.study_browser_processes = {
            "chrome.exe",
            "msedge.exe",
            "firefox.exe",
            "opera.exe",
            "brave.exe",
        }
        self.study_closed_window_pids: set[int] = set()
        self.study_notifications_prev_enabled: int | None = None
        self.study_workspace_snapshot: list[dict] = []
        self.study_snapshot_active: dict | None = None
        self.study_restore_prompt_pending = False
        self.study_restore_prompt_reason = ""
        self.study_launched_browser_pids: set[int] = set()
        self.study_timer_total_seconds = 0
        self.study_timer_last_second_sent = -1
        self.study_actions_log: list[str] = []
        self.study_distract_processes: set[str] = set(DISTRACT_PROCESSES)
        self.distract_guard_enabled = False
        self.distract_guard_stop_event = threading.Event()
        self.distract_guard_thread: threading.Thread | None = None
        self.distract_guard_last_announce_ts: dict[str, float] = {}
        self._study_lock = threading.Lock()
        self._study_timer_thread: threading.Thread | None = None
        self._study_setup_thread: threading.Thread | None = None
        self._deafen_until = 0.0
        self.config_path = os.path.join(self.base_dir, "config.json")
        self.push_to_talk_enabled = False
        self.push_to_talk_key = "LeftCtrl"
        self._preferred_browser = ""
        self._preferred_music_app = ""
        self._ptt_listening_flag = True
        self._runtime_cfg_last_mtime: float | None = None
        self._runtime_cfg_last_check_at = 0.0
        self._control_queue: queue.Queue[str] = queue.Queue()
        self._control_thread: threading.Thread | None = None
        self._load_runtime_config(force=True)
        self._start_control_reader()
        # Voice biometric authentication / RBAC
        # Use async_encoder_init=True to avoid blocking startup with ~28s model warmup
        self.voice_auth = VoiceAuthManager(self.base_dir, async_encoder_init=True)
        # Raw PCM-16 bytes captured from the most recent speech segment.
        # Updated by listen_command_vosk() and wait_for_wake() so that
        # _safe_process_command() can extract a speaker embedding for auth.
        self._last_audio_buffer: bytes = b""
        self.voice_id_enabled = False
        self._pending_admin_enrollment = False
        self._enrollment_cancel_requested = False
        self._enrollment_in_progress = False
        self._enrollment_force_overwrite = False

    def start_stream(self):
        if self.stream is not None:
            return
        if self.audio is None or pyaudio is None:
            warn("Audio backend is unavailable (missing pyaudio). Using mock mode.")
            self.stream = None
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
            self._wake_log(
                f"audio stream started rate={SAMPLE_RATE} chunk={CHUNK_SAMPLES}"
            )
        except Exception as e:
            warn(f"Failed to start audio stream: {e}. Using mock mode.")
            self._wake_log(f"audio stream failed: {e}")
            self.stream = None  # Indicate mock mode

    def stop_stream(self):
        if self.stream:
            try:
                self.stream.stop_stream()
            except Exception:
                pass
            self.stream.close()
            self.stream = None
            debug("Audio stream stopped")

    def _pause_audio_capture(self):
        if not self.stream:
            return
        try:
            if self.stream.is_active():
                self.stream.stop_stream()
        except Exception as e:
            warn(f"Audio pause failed: {e}")

    def _resume_audio_capture(self):
        if not self.stream:
            return
        try:
            if not self.stream.is_active():
                self.stream.start_stream()
        except Exception as e:
            warn(f"Audio resume failed: {e}")

    def _normalize_ptt_key(self, raw: str | None) -> str:
        txt = (raw or "").strip()
        return txt or "LeftCtrl"

    def _apply_push_to_talk_settings(self, enabled: bool, key: str | None, source: str):
        normalized_key = self._normalize_ptt_key(key)
        changed = (
            bool(enabled) != bool(self.push_to_talk_enabled) or
            normalized_key != self.push_to_talk_key
        )

        self.push_to_talk_enabled = bool(enabled)
        self.push_to_talk_key = normalized_key

        if self.push_to_talk_enabled:
            self._ptt_listening_flag = False
            self._pause_audio_capture()
            ui_state("IDLE")
        else:
            self._ptt_listening_flag = True
            self._resume_audio_capture()

        if changed:
            info(
                f"PTT config source={source} enabled={self.push_to_talk_enabled} "
                f"key={self.push_to_talk_key}"
            )

    def _load_runtime_config(self, force: bool = False):
        cfg_enabled = False
        cfg_key = "LeftCtrl"
        current_mtime: float | None = None

        try:
            current_mtime = os.path.getmtime(self.config_path)
        except OSError:
            current_mtime = None

        if (not force) and (current_mtime == self._runtime_cfg_last_mtime):
            return

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh) or {}
                cfg_enabled = bool(payload.get("push_to_talk_enabled", False))
                cfg_key = self._normalize_ptt_key(payload.get("push_to_talk_key"))
                preferred = payload.get("preferred_apps") or {}
                self._preferred_browser = preferred.get("browser", "")
                self._preferred_music_app = preferred.get("music_app", "")
            except Exception as e:
                warn(f"config.json read failed: {e}")

        self._runtime_cfg_last_mtime = current_mtime
        self._apply_push_to_talk_settings(cfg_enabled, cfg_key, source="config")

    def _refresh_runtime_config_if_needed(self):
        if self._enrollment_in_progress:
            return
        now = time.time()
        if (now - self._runtime_cfg_last_check_at) < 1.0:
            return
        self._runtime_cfg_last_check_at = now
        self._load_runtime_config(force=False)

    def _start_control_reader(self):
        if not UI_MODE:
            info("[CustomMode] _start_control_reader skipped: UI_MODE is off")
            return

        info("[CustomMode] Starting control reader thread")

        def _reader():
            info("[CustomMode] Control reader thread running")
            while True:
                try:
                    line = sys.stdin.readline()
                except Exception as e:
                    warn(f"Control channel failed: {e}")
                    break

                if line == "":
                    info("[CustomMode] Control reader: stdin closed (EOF)")
                    break

                cmd = (line or "").strip()
                if cmd:
                    info(f"[CustomMode] Control reader queued: '{cmd}'")
                    self._control_queue.put(cmd)

        self._control_thread = threading.Thread(
            target=_reader,
            name="aidy-control",
            daemon=True,
        )
        self._control_thread.start()

    def _handle_control_command(self, command: str):
        cmd = (command or "").strip()
        if not cmd:
            return

        low = cmd.lower()
        if low == "start_listening":
            self.start_listening()
            return
        if low == "stop_listening":
            self.stop_listening()
            return
        if low == "reload_config":
            self._load_runtime_config(force=True)
            from aidy import config as _cfg
            _cfg.reload_from_config_json(self.base_dir)
            return
        if low.startswith("set_push_to_talk:"):
            parts = cmd.split(":", 2)
            enabled = (parts[1].strip().lower() if len(parts) > 1 else "0") in {"1", "true", "on", "yes"}
            key = parts[2].strip() if len(parts) > 2 else self.push_to_talk_key
            self._apply_push_to_talk_settings(enabled, key, source="bridge")
            return
        if low.startswith("set_volume:"):
            try:
                value = int(cmd.split(":", 1)[1].strip())
                self.voice.set_volume(value)
                self._cmd_log(f"set volume: {value}")
            except Exception as e:
                self._cmd_log(f"set volume failed: {e}")
            return
        if low.startswith("set_voice_id:"):
            flag = (cmd.split(":", 1)[1].strip().lower() if ":" in cmd else "0")
            self.voice_id_enabled = flag in {"1", "true", "on", "yes"}
            self._cmd_log(f"voice_id_enabled={self.voice_id_enabled}")
            return
        if low == "enroll_admin":
            self._pending_admin_enrollment = True
            self._enrollment_force_overwrite = False # Обычный запуск (спросит пароль)
            return
        if low == "enroll_admin_force":
            info("[ENROLL] Received enroll_admin_force command")
            self._pending_admin_enrollment = True
            self._enrollment_force_overwrite = True  # ПАРОЛЬ ВВЕДЕН! (Сразу пишет фразы)
            return
        if low == "cancel_enrollment":
            self._pending_admin_enrollment = False
            self._enrollment_cancel_requested = True
            return
        if low == "list_voice_users":
            self._emit_voice_users_list()
            return
        if low.startswith("execute_slot:"):
            payload = cmd.split(":", 1)[1].strip() if ":" in cmd else ""
            # Debounce: skip if Python already executed all slots recently
            elapsed = time.time() - self._last_custom_mode_exec_ts
            if elapsed < self._CUSTOM_MODE_DEBOUNCE_SEC:
                info(f"[CustomMode] Skipping execute_slot (debounce {elapsed:.1f}s < {self._CUSTOM_MODE_DEBOUNCE_SEC}s): '{payload}'")
                return
            info(f"[CustomMode] Received execute_slot command, payload='{payload}'")
            self._execute_custom_mode_slot(payload)
            return

    # ------------------------------------------------------------------
    # Custom mode slot execution
    # ------------------------------------------------------------------

    def _execute_custom_mode_slot(self, payload: str):
        """Execute a custom mode slot action.

        payload format: action_type|target|display_name
        e.g. "open_app|chrome|Chrome" or "function|volume_up_10|Volume +10%"
        """
        info(f"[CustomMode] _execute_custom_mode_slot called with payload='{payload}'")
        parts = payload.split("|", 2)
        if len(parts) < 2:
            warn(f"[CustomMode] bad slot payload (need at least 2 pipe-separated parts): '{payload}'")
            return

        action_type = parts[0].strip().lower()
        target = parts[1].strip()
        display = parts[2].strip() if len(parts) > 2 else target

        info(f"[CustomMode] executing: type='{action_type}' target='{target}' display='{display}'")

        try:
            if action_type == "open_app":
                app = find_app(self.apps, target)
                if app:
                    launch_app(app)
                else:
                    warn(f"[CustomMode] app NOT found: '{target}'")

            elif action_type == "close_app":
                app = find_app(self.apps, target)
                if app:
                    close_app(app)
                else:
                    warn(f"[CustomMode] app NOT found for close: '{target}'")

            elif action_type == "function":
                self._execute_custom_mode_function(target)

            elif action_type == "open_url":
                def _open_url_bg(u=target):
                    try:
                        CREATE_NO_WINDOW = 0x08000000
                        subprocess.Popen(
                            ["cmd", "/c", "start", "", u],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW,
                        )
                    except Exception as ex:
                        warn(f"[CustomMode] open_url bg error: {ex}")
                threading.Thread(target=_open_url_bg, daemon=True).start()

            else:
                warn(f"[CustomMode] unknown action type: '{action_type}'")

            self._debug_file_log(f"slot done: {action_type}|{target}")

        except Exception as e:
            import traceback
            self._debug_file_log(f"slot EXCEPTION: {e}\n{traceback.format_exc()}")
            warn(f"[CustomMode] EXCEPTION: {e}")

    def _execute_custom_mode_function(self, target: str):
        """Execute a custom mode function target like volume_up_10, timer_5, etc."""
        t = target.lower().strip()
        info(f"[CustomMode] _execute_custom_mode_function: target='{t}'")
        ui_state("EXECUTING")

        try:
            if t == "close_everything":
                info("[CustomMode] function: close_everything — exiting")
                sys.exit(0)
            elif t == "shutdown_computer":
                info("[CustomMode] function: shutdown_computer")
                os.system("shutdown /s /t 5")
            elif t == "restart_computer":
                info("[CustomMode] function: restart_computer")
                os.system("shutdown /r /t 5")
            elif t.startswith("volume_up_"):
                pct = parse_first_int(t) or 10
                info(f"[CustomMode] function: volume_up {pct}%")
                volume_steps(up=True, steps=pct)
            elif t.startswith("volume_down_"):
                pct = parse_first_int(t) or 10
                info(f"[CustomMode] function: volume_down {pct}%")
                volume_steps(up=False, steps=pct)
            elif t.startswith("brightness_up_"):
                pct = parse_first_int(t) or 10
                info(f"[CustomMode] function: brightness_up {pct}%")
                brightness_steps(up=True, steps=pct)
            elif t.startswith("brightness_down_"):
                pct = parse_first_int(t) or 10
                info(f"[CustomMode] function: brightness_down {pct}%")
                brightness_steps(up=False, steps=pct)
            elif t.startswith("timer_"):
                mins = parse_first_int(t) or 5
                info(f"[CustomMode] function: timer {mins} min")
                self._start_timer_seconds(mins * 60)
            elif t.startswith("website_"):
                site = t[len("website_"):]
                urls = {
                    "whatsapp": "https://web.whatsapp.com",
                    "chatgpt": "https://chatgpt.com",
                    "youtube": "https://youtube.com",
                }
                url = urls.get(site, f"https://{site}")
                info(f"[CustomMode] function: website '{url}'")
                def _open_site_bg(u=url):
                    try:
                        CREATE_NO_WINDOW = 0x08000000
                        subprocess.Popen(
                            ["cmd", "/c", "start", "", u],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW,
                        )
                    except Exception as ex:
                        warn(f"[CustomMode] website bg error: {ex}")
                threading.Thread(target=_open_site_bg, daemon=True).start()
            else:
                warn(f"[CustomMode] unknown function target: '{t}'")
                ui_state("IDLE")
                return

            info(f"[CustomMode] function '{t}' executed OK")
            ui_state("SUCCESS")
            time.sleep(0.3)
            ui_state("IDLE")

        except Exception as e:
            import traceback
            warn(f"[CustomMode] function EXCEPTION: {e}")
            warn(f"[CustomMode] traceback: {traceback.format_exc()}")
            ui_state("ERROR")
            time.sleep(0.5)
            ui_state("IDLE")

    # Debounce: prevent duplicate slot execution from C# round-trip
    _last_custom_mode_exec_ts: float = 0.0
    _CUSTOM_MODE_DEBOUNCE_SEC: float = 5.0

    def _execute_all_custom_mode_slots(self):
        """Read config.json and execute all non-empty custom mode slots directly."""
        self._last_custom_mode_exec_ts = time.time()
        import json as _json
        config_path = os.path.join(self.base_dir, "config.json")
        self._debug_file_log(f"_execute_all: config_path={config_path}")
        self._debug_file_log(f"_execute_all: file exists={os.path.exists(config_path)}")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = _json.load(f)
            cm = cfg.get("custom_mode", {})
            slots = cm.get("slots", [])
            self._debug_file_log(f"_execute_all: found {len(slots)} slots, cm={cm}")
            executed = 0
            for i, slot in enumerate(slots):
                action_type = (slot.get("action_type") or "").strip()
                target = (slot.get("target") or "").strip()
                display = (slot.get("display_name") or "").strip()
                if not target:
                    self._debug_file_log(f"_execute_all: slot {i} empty, skip")
                    continue
                payload = f"{action_type}|{target}|{display}"
                self._debug_file_log(f"_execute_all: slot {i} executing: {payload}")
                self._execute_custom_mode_slot(payload)
                self._debug_file_log(f"_execute_all: slot {i} done")
                executed += 1
            self._debug_file_log(f"_execute_all: executed {executed} slot(s)")
        except Exception as e:
            import traceback
            self._debug_file_log(f"_execute_all: EXCEPTION: {e}")
            self._debug_file_log(f"_execute_all: traceback: {traceback.format_exc()}")
            warn(f"[CustomMode] Failed to load/execute slots: {e}")
            warn(f"[CustomMode] traceback: {traceback.format_exc()}")

    def _drain_control_commands(self, max_items: int = 24):
        for _ in range(max(1, int(max_items))):
            try:
                cmd = self._control_queue.get_nowait()
            except queue.Empty:
                break

            try:
                self._handle_control_command(cmd)
            except Exception as e:
                warn(f"Control command failed '{cmd}': {e}")

    def _is_listening_allowed(self) -> bool:
        if self._enrollment_in_progress:
            return True
        return (not self.push_to_talk_enabled) or self._ptt_listening_flag

    def start_listening(self):
        if not self.push_to_talk_enabled:
            return
        if self._ptt_listening_flag:
            return
        self._ptt_listening_flag = True
        self._resume_audio_capture()
        self._cmd_log("ptt hold started")

    def stop_listening(self):
        if not self.push_to_talk_enabled:
            return
        if not self._ptt_listening_flag:
            return
        self._ptt_listening_flag = False
        self._pause_audio_capture()
        ui_state("IDLE")
        self._cmd_log("ptt hold released")

    def _new_wake_recognizer(self):
        if self.model is None or self.stream is None:
            return MockRecognizer(is_wake=True)
        # Free-form recognizer is more robust for wake word across noisy mics/debug runs.
        rec = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
        rec.SetWords(False)
        return rec

    def _new_command_recognizer(self, use_grammar: bool = True, grammar_phrases: list[str] | None = None):
        if self.model is None or self.stream is None:
            return MockRecognizer(is_wake=False)
        if use_grammar:
            phrases = grammar_phrases if grammar_phrases else self.command_phrases
            grammar = json.dumps(sorted(set(phrases)))
            rec = vosk.KaldiRecognizer(self.model, SAMPLE_RATE, grammar)
        else:
            rec = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
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
        if self._preferred_browser:
            app = find_app(self.apps, self._preferred_browser)
            if app and launch_app(app):
                return True
            warn(f"Preferred browser '{self._preferred_browser}' failed to launch, falling back to system default")
        # Fallback: system default
        try:
            subprocess.Popen(
                ["cmd.exe", "/C", "start", "", "https://www.google.com"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
        self._sync_state_before_listen("LISTENING")

        # Flush any audio frames that accumulated in PyAudio's internal
        # buffer while we were sleeping through the deafen window.  Without
        # this, the first stream.read() could return stale data containing
        # the TTS echo (e.g. "Aidy is ready"), causing a false wake trigger.
        self._flush_audio(50)

        info("Wake: listening...")

        # Reuse existing wake recognizer to avoid expensive KaldiRecognizer creation.
        # It is already reset by _reset_recognition_after_command() after each command.
        if self.wake_recognizer is None:
            self.wake_recognizer = self._new_wake_recognizer()
        wake_chunk = max(160, int(WAKE_CHUNK_SAMPLES))
        self._wake_log(
            f"listen start chunk={wake_chunk} stream={'on' if self.stream else 'off'} "
            f"partial={'on' if WAKE_DETECT_PARTIAL else 'off'} vad={_aidy_cfg.VAD_START_THRESHOLD}"
        )

        last_logged = ""
        last_log_t = 0.0
        last_status_t = 0.0
        last_partial = ""
        last_partial_t = 0.0
        greeting_latch_until = 0.0
        greeting_latch_energy = 0
        last_greeting_log_t = 0.0
        last_greeting_trigger_t = 0.0
        greeting_min_rms = int(self.WAKE_GREETING_MIN_RMS)
        greeting_partial_hits = 0
        last_greeting_partial = ""
        # Rolling audio deque for speaker verification on wake-tail commands.
        # 60 frames × 1600 samples × 2 bytes ≈ 6 s of audio at 16 kHz.
        _wake_audio_deque: deque[bytes] = deque(maxlen=60)

        # --- НАСТРОЙКА ШУМОПОДАВЛЕНИЯ ---
        # Порог энергии звука. Всё, что ниже этого значения — считается тишиной
        # или белым шумом кулеров/микрофона.  Linked to the user-tunable
        # VAD threshold in Settings → Voice Sensitivity so phantom wake
        # detections ("Go ahead" spam) can be suppressed by raising it.
        SPEECH_MIN_RMS = _aidy_cfg.VAD_START_THRESHOLD

        while True:
            self._refresh_runtime_config_if_needed()
            self._drain_control_commands()

            # Interrupt wake loop if enrollment was requested
            if getattr(self, "_pending_admin_enrollment", False):
                self._wake_log("interrupt: pending admin enrollment")
                return "__CONTROL_BREAK__"

            if not self._is_listening_allowed():
                ui_state("IDLE")
                return self.PTT_PAUSED_TOKEN

            with self._study_lock:
                if self.study_restore_prompt_pending:
                    return "__STUDY_RESTORE_PROMPT__"

            # 1. ЧТЕНИЕ АУДИОПОТОКА
            if self.stream:
                data = self.stream.read(wake_chunk, exception_on_overflow=False)
            else:
                data = b'\x00' * (wake_chunk * 2)  # Имитация тишины

            # Accumulate for speaker verification (voiced frames only later)
            _wake_audio_deque.append(data)

            self._handle_due_tasks()
            
            if self._is_deafened():
                continue

            # 2. ВЫЧИСЛЕНИЕ ГРОМКОСТИ
            try:
                rms = audioop.rms(data, 2)
            except Exception:
                rms = 0

            now = time.time()
            if (now - last_status_t) >= 2.0:
                self._wake_log(f"waiting rms={rms}")
                last_status_t = now

            # --- 3. ОБРАБОТКА ФИНАЛЬНОГО РЕЗУЛЬТАТА ---
            if self.wake_recognizer.AcceptWaveform(data):
                try:
                    r = json.loads(self.wake_recognizer.Result())
                except Exception:
                    continue
                
                text = (r.get("text", "") or "").lower().strip()
                if not text:
                    continue
                text = " ".join(text.split())

                # 🛑 ГЛОБАЛЬНЫЙ БЛОКИРАТОР ДЛЯ ФИНАЛЬНОГО РЕЗУЛЬТАТА 🛑
                # Если движок выдал текст, но звук был тише порога фонового шума — игнорируем!
                if rms < SPEECH_MIN_RMS:
                    self._wake_log(f"Фантом отсечен (Final): text='{text}', rms={rms}")
                    continue

                if text != last_logged or (now - last_log_t) > 1.0:
                    info(f'Wake Heard: "{text}"')
                    last_logged = text
                    last_log_t = now

                norm_text = self._normalize_wake_text(text)

                if self._is_greeting_only(norm_text):
                    self._wake_log(f"detected greeting-only final='{norm_text}' rms={rms}")
                    self._last_audio_buffer = b"".join(_wake_audio_deque)
                    self._play_wake_ack()
                    return None

                detected, tail, reason = self._detect_wake_with_tail(
                    norm_text,
                    greeting_latched=(now <= greeting_latch_until),
                )
                if detected:
                    info(f'Wake detected (final): "{text}"')
                    self._wake_log(f"detected {reason} final='{norm_text}' tail='{tail}' rms={rms}")
                    self._last_audio_buffer = b"".join(_wake_audio_deque)
                    self._play_wake_ack()
                    return tail or None

            # --- 4. ОБРАБОТКА ПРОМЕЖУТОЧНОГО РЕЗУЛЬТАТА ---
            elif WAKE_DETECT_PARTIAL:
                # 🛑 ГЛОБАЛЬНЫЙ БЛОКИРАТОР ДЛЯ ЧАСТИЧНОГО РЕЗУЛЬТАТА 🛑
                if rms < SPEECH_MIN_RMS:
                    greeting_partial_hits = 0
                    last_greeting_partial = ""
                    if now > greeting_latch_until:
                        greeting_latch_energy = 0
                    continue
                    
                # ... дальше try/except парсинг json и остальной код partial ...

                try:
                    pr = json.loads(self.wake_recognizer.PartialResult())
                    partial = (pr.get("partial", "") or "").lower().strip()
                    if not partial:
                        continue
                    partial = " ".join(partial.split())
                except Exception:
                    continue

                if partial != last_partial or (now - last_partial_t) > 1.0:
                    self._wake_log(f"partial='{partial}' rms={rms}")
                    last_partial = partial
                    last_partial_t = now

                norm_partial = self._normalize_wake_text(partial)
                
                if self._is_greeting_only(norm_partial):
                    if norm_partial == last_greeting_partial:
                        greeting_partial_hits += 1
                    else:
                        greeting_partial_hits = 1
                        
                    last_greeting_partial = norm_partial
                    greeting_latch_until = now + 2.4
                    greeting_latch_energy = max(greeting_latch_energy, int(rms))
                    
                    if (now - last_greeting_log_t) >= 0.8:
                        self._wake_log(f"greeting latch partial='{norm_partial}'")
                        last_greeting_log_t = now

                    # Быстрый триггер теперь работает только если звук достаточно громкий
                    if norm_partial in {"hey", "hi", "хей", "хэй"}:
                        fast_ready = rms > SPEECH_MIN_RMS 
                    else:
                        fast_ready = (rms >= greeting_min_rms) or (greeting_partial_hits >= 2)
                        
                    if fast_ready and (now - last_greeting_trigger_t) >= float(self.WAKE_GREETING_COOLDOWN_S):
                        self._wake_log(f"detected greeting-only partial='{norm_partial}' rms={rms} hits={greeting_partial_hits}")
                        last_greeting_trigger_t = now
                        self._last_audio_buffer = b"".join(_wake_audio_deque)
                        self._play_wake_ack()
                        return None
                    continue

                greeting_partial_hits = 0
                last_greeting_partial = ""

                detected, tail, reason = self._detect_wake_with_tail(
                    norm_partial,
                    greeting_latched=(now <= greeting_latch_until),
                )
                if detected:
                    if reason == "fuzzy":
                        self._wake_log(f"ignore fuzzy partial='{norm_partial}' tail='{tail}' rms={rms}")
                        continue
                    info(f'Wake detected (partial): "{partial}"')
                    self._wake_log(f"detected {reason} partial='{norm_partial}' tail='{tail}' rms={rms}")
                    self._last_audio_buffer = b"".join(_wake_audio_deque)
                    self._play_wake_ack()
                    return tail or None

    def listen_command_vosk(
        self,
        max_seconds=6,
        min_listen_ms=2000,
        ui_state_label="LISTENING",
        use_grammar=True,
        speak_on_empty=True,
        grammar_phrases: list[str] | None = None,
        vad_threshold: int | None = None,
        silence_stop_override_ms: int | None = None,
        force_listen: bool = False,
        stop_audio_on_voice: bool = False,
    ):
        self._sync_state_before_listen(ui_state_label)
        info(f"Command: listening... grammar={'on' if use_grammar else 'off'}")

        # Silence stop configuration per state
        if silence_stop_override_ms is not None:
            silence_stop_ms = silence_stop_override_ms
        elif ui_state_label == "CONFIRM":
            silence_stop_ms = min(_aidy_cfg.VAD_SILENCE_MS, int(self.CONFIRM_SILENCE_STOP_MS))
        elif ui_state_label == "COMMAND_LISTENING":
            silence_cap = (
                int(self.COMMAND_SILENCE_STOP_MS_LEGACY)
                if getattr(self, "legacy_accuracy_mode", True)
                else int(self.COMMAND_SILENCE_STOP_MS)
            )
            silence_stop_ms = min(_aidy_cfg.VAD_SILENCE_MS, silence_cap)
        elif ui_state_label == "FOLLOWUP":
            silence_stop_ms = min(_aidy_cfg.VAD_SILENCE_MS, int(self.FOLLOWUP_SILENCE_STOP_MS))
        else:
            silence_stop_ms = min(_aidy_cfg.VAD_SILENCE_MS, int(self.COMMAND_SILENCE_STOP_MS))

        effective_vad = vad_threshold if vad_threshold is not None else _aidy_cfg.VAD_START_THRESHOLD

        # For post-wake command listening, use a fast min-speech threshold
        # regardless of config.json voice_sensitivity (which is tuned for
        # wake-word detection and general listening, not command snappiness).
        if ui_state_label in ("COMMAND_LISTENING", "CONFIRM"):
            effective_min_speech_ms = 60
        else:
            effective_min_speech_ms = _aidy_cfg.VAD_MIN_SPEECH_MS

        self._cmd_log(
            f"start state={ui_state_label} grammar={'on' if use_grammar else 'off'} "
            f"max_s={max_seconds} min_listen_ms={min_listen_ms} vad={effective_vad} "
            f"silence_stop_ms={silence_stop_ms} min_speech_ms={effective_min_speech_ms}"
        )

        rec = self._new_command_recognizer(
            use_grammar=use_grammar,
            grammar_phrases=grammar_phrases,
        )
        frame_ms = max(1, int(round((CHUNK_SAMPLES / SAMPLE_RATE) * 1000.0)))

        started = False
        silence_ms = 0
        voiced_ms = 0
        last_rms = 0
        vad_active = False  # tracks real-time voice activity for UI emblem
        start_time = time.time()
        if self._deafen_until > start_time:
            start_time += (self._deafen_until - start_time)
        best_final = ""
        _vbuf: list[bytes] = []  # voiced audio accumulator for speaker verification

        while time.time() - start_time < max_seconds:
            self._refresh_runtime_config_if_needed()
            self._drain_control_commands()

            # Interrupt if enrollment was requested
            if getattr(self, "_pending_admin_enrollment", False):
                if vad_active:
                    print("VOICE_ACTIVITY:off", flush=True)
                return None

            # Push-to-talk check (skip during enrollment)
            if not force_listen and not self._is_listening_allowed():
                ui_state("IDLE")
                self._cmd_log("listen interrupted by ptt release")
                if vad_active:
                    print("VOICE_ACTIVITY:off", flush=True)
                return None

            if self.stream is None:
                data = b'\x00' * (CHUNK_SAMPLES * 2)
            else:
                try:
                    data = self.stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
                except Exception:
                    break
            self._handle_due_tasks()
            if self._is_deafened():
                continue
            rms = audioop.rms(data, 2)
            last_rms = rms

            # Real-time voice activity for UI emblem
            if rms >= effective_vad:
                if not vad_active:
                    vad_active = True
                    print("VOICE_ACTIVITY:on", flush=True)
            else:
                if vad_active:
                    vad_active = False
                    print("VOICE_ACTIVITY:off", flush=True)

            elapsed_ms = int((time.time() - start_time) * 1000)

            if not started:
                if rms >= effective_vad:
                    started = True
                    silence_ms = 0
                    voiced_ms = frame_ms
                    debug(f"VAD: start (rms={rms})")
                    if stop_audio_on_voice:
                        self.voice.stop_playback()
                else:
                    if elapsed_ms < min_listen_ms:
                        continue
            else:
                if rms < effective_vad:
                    silence_ms += frame_ms
                else:
                    silence_ms = 0
                    voiced_ms += frame_ms

            # Accumulate voiced frames for speaker verification
            if started:
                _vbuf.append(data)

            if rec.AcceptWaveform(data):
                try:
                    r = json.loads(rec.Result())
                    t = (r.get("text") or "").strip().lower()
                    if t:
                        best_final = t
                except Exception:
                    pass

            if started and silence_ms >= silence_stop_ms:
                if voiced_ms >= effective_min_speech_ms:
                    debug("VAD: stop (silence)")
                    break
                debug("VAD: false start (noise), continue listening")
                started = False
                silence_ms = 0
                voiced_ms = 0

        # Ensure voice activity indicator is turned off after loop exits
        if vad_active:
            print("VOICE_ACTIVITY:off", flush=True)

        # Store voiced audio for speaker verification
        if _vbuf:
            self._last_audio_buffer = b"".join(_vbuf)

        if not best_final:
            r = json.loads(rec.FinalResult())
            best_final = (r.get("text") or "").strip().lower()

        if not best_final:
            ui_state("IDLE")
            warn("Command: empty")
            self._cmd_log(
                f"empty started={started} voiced_ms={voiced_ms} silence_ms={silence_ms} "
                f"elapsed_ms={int((time.time() - start_time) * 1000)} rms={last_rms}"
            )
            if speak_on_empty:
                self.voice.play_or_tts("not_heard", "I didn't catch that")
                self._deafen_after_speak()
            return None

        ui_command(best_final)
        info(f"Heard: \"{best_final}\"")
        self._cmd_log(
            f"final='{best_final}' voiced_ms={voiced_ms} silence_ms={silence_ms} rms={last_rms}"
        )
        return best_final

    def listen_command_smart(
        self,
        max_seconds=6,
        min_listen_ms=2000,
        ui_state_label="LISTENING",
    ):
        text = self.listen_command_vosk(
            max_seconds=max_seconds,
            min_listen_ms=min_listen_ms,
            ui_state_label=ui_state_label,
            use_grammar=True,
            speak_on_empty=False,
        )
        if text:
            return text

        self._cmd_log("grammar empty -> retry free-form")
        return self.listen_command_vosk(
            max_seconds=max_seconds,
            min_listen_ms=min_listen_ms,
            ui_state_label=ui_state_label,
            use_grammar=False,
            speak_on_empty=True,
        )

    def _listen_post_wake_command(self, max_attempts: int = 2) -> str | None:
        non_action_hits = 0
        for attempt in range(max(1, int(max_attempts))):
            cmd_text = self.listen_command_vosk(
                max_seconds=(2 if attempt == 0 else 1.5),
                min_listen_ms=(50 if attempt == 0 else 40),
                ui_state_label="COMMAND_LISTENING",
                use_grammar=True,
                speak_on_empty=False,
            )
            if not cmd_text:
                # Only retry free-form on second attempt to avoid slow double-listen
                if attempt > 0:
                    self._cmd_log("post-wake grammar empty -> retry free-form")
                    cmd_text = self.listen_command_vosk(
                        max_seconds=1.5,
                        min_listen_ms=40,
                        ui_state_label="COMMAND_LISTENING",
                        use_grammar=False,
                        speak_on_empty=False,
                    )
            if not cmd_text:
                if attempt == 0:
                    # First attempt empty: retry once more with grammar
                    continue
                return None
            if not self._is_non_action_utterance(cmd_text):
                return cmd_text
            non_action_hits += 1
            self._cmd_log(
                f"post-wake ignore non-action='{cmd_text}' attempt={attempt + 1}/{max_attempts}"
            )
            if non_action_hits >= 2:
                return "__SKIP_WAKE_COMMAND__"
        return "__SKIP_WAKE_COMMAND__" if non_action_hits > 0 else None

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
        return any(w in FOLLOWUP_LINKERS for w in words)

    def _strip_linkers(self, text: str) -> str:
        t = (text or "").strip().lower()
        words = [w for w in t.split() if w not in FOLLOWUP_LINKERS]
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
            self.voice.play_or_tts_async("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = launch_app(app)
            if ok:
                self._set_context("open app", {"app": app["id"]})
                self._set_memory("open app", {"id": app["id"]})
                ui_state("SUCCESS")
                time.sleep(0.15)
                ui_state("IDLE")
                return True
            return False

        if ctx_intent == "close app":
            app = find_app(self.apps, t)
            if not app:
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts_async("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = close_app(app)
            if ok:
                self._set_context("close app", {"app": app["id"]})
                self._set_memory("close app", {"id": app["id"]})
                ui_state("SUCCESS")
                time.sleep(0.15)
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

    def _arm_rephrase_followup(self):
        # One-shot slot: listen for a rephrased command without requiring wake word.
        self.follow_up.set_pending(
            PendingAction(
                pending_type=PENDING_NEED_TARGET,
                base_intent="rephrase_command",
                entities={},
            )
        )

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

    def _speak_steps_prompt(self, base_intent: str):
        # Use a single explicit prompt so ASR catches "by how much" consistently.
        self.voice.play_or_tts("by_how_much", "By how much?")

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

        if pending.pending_type == PENDING_NEED_TIMER_DURATION:
            seconds = parse_timer_duration_seconds(text, default_unit="minutes", max_seconds=TIMER_MAX_SECONDS)
            if seconds is None:
                # STT often returns fuzzy numerics ("tree/free/for/ten").
                n = parse_numeric_input(text)
                if n is None:
                    n = extract_steps_value(text)
                if n is not None:
                    seconds = max(1, min(600, int(n))) * 60
            if seconds is None:
                attempts = self.follow_up.register_invalid_attempt()
                if attempts >= 3:
                    self._cancel_pending(speak_cancelled=True)
                    return False
                ui_state("SPEAKING")
                self.voice.play_or_tts("need_timer_duration", "Say time like 5 minutes.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            self.follow_up.clear_pending()
            self._cmd_log(f"timer duration parsed={seconds}s text='{text}'")
            return self._start_timer(seconds, source_text=text)

        numeric = parse_numeric_input(text)
        if numeric is None:
            # ASR often prepends wake residue ("hey ten"); extract the number token robustly.
            numeric = extract_steps_value(text)
            if numeric is not None:
                self._cmd_log(f"pending numeric fallback '{text}' -> {numeric}")
        if numeric is None:
            norm_text = self._normalize_wake_text(text)
            if is_wake_phrase(norm_text) or self._is_greeting_only(norm_text):
                self._cmd_log(f"pending numeric ignored wake-like='{norm_text}'")
                return False
            attempts = self.follow_up.register_invalid_attempt()
            if attempts >= 2:
                self._cancel_pending(speak_cancelled=True)
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts("need_number", "Say a number from one to ten.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False
        numeric = max(1, min(10, int(numeric)))
        self._cmd_log(f"pending numeric resolved={numeric} text='{text}'")

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

        study_intent = self._study_intent_from_text(t0)
        if study_intent:
            return study_intent, {}

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

        if t0.startswith(("open ", "open up ", "launch ", "start ", "run ", "go to ", "go ", "visit ", "show ")):
            app_name = extract_app_name(t0)
            app = find_app(self.apps, app_name)
            if app:
                return "open app", {"app": app["id"]}
            return None, {}

        if t0 in ("volume up", "voice up", "sound up", "increase volume", "increase voice", "louder", "make it louder"):
            return "volume up", {"steps": 6}
        if t0 in ("volume down", "voice down", "sound down", "decrease volume", "decrease voice", "quieter", "make it quieter"):
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

    def _is_timer_start_request(self, normalized_text: str) -> bool:
        t = " ".join((normalized_text or "").strip().lower().split())
        if not t:
            return False
        if t in TIMER_START_PHRASES:
            return True
        for p in TIMER_START_PHRASES:
            if t.startswith(p + " "):
                return True
        # Fallback for rough STT variants ("timer/taymer/tamer/time").
        tokens = t.split()
        if any(tok in {"timer", "taymer", "taimer", "tamer", "tymer", "timerr", "timmer", "таймер"} for tok in tokens):
            return True
        if t.startswith(("set time", "start time", "put time")):
            return True
        return False

    def _normalize_spoken_command(self, text: str) -> str:
        t = " ".join((text or "").strip().lower().split())
        if not t:
            return t
        if t in {"volume up", "volume down", "brightness up", "brightness down"}:
            return t

        normalize_map = {
            "open louder": "volume up",
            "open louder up": "volume up",
            "open lower": "volume down",
            "open lower down": "volume down",
            "open whats app": "open whatsapp",
            "open whatsup": "open whatsapp",
            "open whats up": "open whatsapp",
            "open what s app": "open whatsapp",
            "open watsap": "open whatsapp",
            "open watsapp": "open whatsapp",
            "open whatsapp web": "open whatsapp",
            "whatsapp web": "whatsapp",
            "whats app web": "whatsapp",
            "the music": "music",
            "my music": "music",
            "musics": "music",
            "new sick": "music",
            "new six": "music",
            "muse ic": "music",
            "timer please": "timer",
            "set timer": "timer",
            "set a timer": "timer",
            "start timer": "timer",
            "start a timer": "timer",
            "tamer": "timer",
            "taymer": "timer",
            "taimer": "timer",
            "tymer": "timer",
            "timerr": "timer",
            "timmer": "timer",
            "open dva up": "open whatsapp",
            "open lock up": "open whatsapp",
            "open the lock stop": "open whatsapp",
            "open won up": "open whatsapp",
            "open notes up": "open whatsapp",
            "num up": "volume up",
            "numb up": "volume up",
            "nom up": "volume up",
            "name up": "volume up",
            "num down": "volume down",
            "numb down": "volume down",
            "nom down": "volume down",
            "name down": "volume down",
            "well him up": "volume up",
            "well im up": "volume up",
            "while im up": "volume up",
            "will im up": "volume up",
            "volume app": "volume up",
            "volum up": "volume up",
            "well you me up": "volume up",
            "for them up": "volume up",
            "for them app": "volume up",
            "four them up": "volume up",
            "well him down": "volume down",
            "well im down": "volume down",
            "while im down": "volume down",
            "will im down": "volume down",
            "volume dawn": "volume down",
            "volum down": "volume down",
            "well you me down": "volume down",
            "for them down": "volume down",
            "four them down": "volume down",
            "them up": "volume up",
            "then up": "volume up",
            "them down": "volume down",
            "then down": "volume down",
            # Common STT variants for "shutdown".
            "shat laun": "shutdown",
            "shut laun": "shutdown",
            "shot laun": "shutdown",
            "shat lawn": "shutdown",
            "shut lawn": "shutdown",
            "shot lawn": "shutdown",
            "shut don": "shutdown",
            "shut dawn": "shutdown",
            "shot down": "shutdown",
            "shat down": "shutdown",
            "shut done": "shutdown",
            "shut down": "shutdown",
            "shut-down": "shutdown",
            "mas up": "mouse up",
            "mouse app": "mouse up",
            "most up": "mouse up",
            "mas down": "mouse down",
            "most down": "mouse down",
            "mas left": "mouse left",
            "most left": "mouse left",
            "mas right": "mouse right",
            "most right": "mouse right",
            "school up": "scroll up",
            "scrawl up": "scroll up",
            "school down": "scroll down",
            "scrawl down": "scroll down",
            "lick": "click",
            "quick": "click",
            "clic": "click",
            "double clic": "double click",
            "double quick": "double click",
            "write click": "right click",
            "right clic": "right click",
            # Close everything command variants
            "close everything": "close everything",
            "close all": "close everything",
            "close it all": "close everything",
            "close the lot": "close everything",
            "shut down everything": "close everything",
            "close and exit": "close everything",
            "exit aidy": "close everything",
            "exit everything": "close everything",
            "quit aidy": "close everything",
            "quit everything": "close everything",
        }

        direct = normalize_map.get(t)
        if direct:
            self._cmd_log(f"normalize '{t}' -> '{direct}'")
            return direct

        if t.endswith(" him up") or (t.endswith("ume up") and "volume up" not in t):
            self._cmd_log(f"normalize heuristic '{t}' -> 'volume up'")
            return "volume up"
        if t.endswith(" im up") and "volume up" not in t:
            self._cmd_log(f"normalize heuristic '{t}' -> 'volume up'")
            return "volume up"
        if len(t.split()) == 2 and t.endswith(" up"):
            p0 = t.split()[0]
            if p0 in {"num", "numb", "nom", "name", "them", "then"}:
                self._cmd_log(f"normalize heuristic '{t}' -> 'volume up'")
                return "volume up"
        if t.endswith(" him down") or (t.endswith("ume down") and "volume down" not in t):
            self._cmd_log(f"normalize heuristic '{t}' -> 'volume down'")
            return "volume down"
        if t.endswith(" im down") and "volume down" not in t:
            self._cmd_log(f"normalize heuristic '{t}' -> 'volume down'")
            return "volume down"
        if len(t.split()) == 2 and t.endswith(" down"):
            p0 = t.split()[0]
            if p0 in {"num", "numb", "nom", "name", "them", "then"}:
                self._cmd_log(f"normalize heuristic '{t}' -> 'volume down'")
                return "volume down"

        shutdown_verb = {
            "shut",
            "shutd",
            "shuted",
            "shutdown",
            "shot",
            "shat",
            "chat",
            "chut",
            "shutit",
        }
        shutdown_tail = {
            "down",
            "dawn",
            "don",
            "done",
            "daown",
            "doun",
            "douwn",
            "dwon",
            "town",
            "laun",
            "lawn",
            "laon",
            "lawn",
        }
        shutdown_fillers = {
            "please",
            "pls",
            "plz",
            "pc",
            "computer",
            "system",
            "now",
            "it",
            "the",
            "my",
        }
        parts = t.split()
        if len(parts) == 2 and parts[0] in shutdown_verb and parts[1] in shutdown_tail:
            self._cmd_log(f"normalize heuristic '{t}' -> 'shutdown'")
            return "shutdown"
        if len(parts) == 3 and parts[0] in shutdown_verb and parts[1] in {"it", "the"} and parts[2] in shutdown_tail:
            self._cmd_log(f"normalize heuristic '{t}' -> 'shutdown'")
            return "shutdown"
        filtered = [p for p in parts if p not in shutdown_fillers]
        if "shutdown" in filtered:
            self._cmd_log(f"normalize heuristic '{t}' -> 'shutdown'")
            return "shutdown"
        if len(filtered) >= 2:
            for i, p in enumerate(filtered):
                if p in shutdown_verb or p.startswith(("shut", "shot", "shat", "chat", "chut")):
                    lookahead = filtered[i + 1 : i + 3]
                    if any(tok in shutdown_tail or tok.startswith(("dow", "daw", "don", "laun", "law")) for tok in lookahead):
                        self._cmd_log(f"normalize heuristic '{t}' -> 'shutdown'")
                        return "shutdown"

        return t

    def _is_command_like_text(self, text: str) -> bool:
        t = " ".join((text or "").strip().lower().split())
        if not t:
            return False
        if len(t) < 3:
            return False
        if t in {"a", "i", "the", "hey", "ok", "okay", "hello", "hi", "they", "them"}:
            return False

        if t in COMMANDS:
            return True
        if t in MUTE_PHRASES or t in UNMUTE_PHRASES:
            return True
        if t in UNDO_LAST_PHRASES or t in UNDO_ALL_PHRASES:
            return True
        if t in REPEAT_PHRASES:
            return True
        if t in CLOSE_ACTIVE_PHRASES:
            return True
        if t in TIMER_START_PHRASES or t in TIMER_CANCEL_PHRASES:
            return True
        if t in CUSTOM_MODE_ON_PHRASES or t in CUSTOM_MODE_OFF_PHRASES:
            return True
        if t in STUDY_MODE_START_PHRASES or t in STUDY_MODE_START_ALIASES or t in STUDY_MODE_STOP_PHRASES or t in STUDY_MODE_STATUS_PHRASES:
            return True
        if self._study_intent_from_text(t) == INTENT_STUDY_MODE_START:
            return True

        if detect_step_intent_from_text(t):
            return True
        if self._is_timer_start_request(t):
            return True
        for app in self.apps:
            if t == (app.get("id") or "").strip().lower():
                return True
            for alias in (app.get("aliases") or []):
                if t == str(alias).strip().lower():
                    return True

        if t.startswith(("open ", "open up ", "launch ", "start ", "run ", "go to ", "go ", "visit ", "show ")):
            return True
        if t.startswith(("close ", "quit ", "exit ", "kill ", "stop ")):
            return True
        if t == "switch" or t.startswith("switch ") or t in ("switch app", "switch window"):
            return True

        return False

    def _is_non_action_utterance(self, text: str) -> bool:
        t = self._normalize_wake_text(text)
        if not t:
            return True
        if self._is_greeting_only(t) or is_wake_phrase(t):
            return True
        if t in {"im not certain", "i m not certain"}:
            return True
        return t in {"a", "i", "the", "they", "them", "uh", "um", "hmm", "mm", "mhm"}

    def _is_ambiguous_mixed_command(self, normalized_text: str) -> bool:
        t = " ".join((normalized_text or "").strip().lower().split())
        if not t:
            return False
        if self._is_command_like_text(t):
            return False

        tokens = set(t.split())
        has_volume = bool(tokens & {"volume", "sound", "voice", "mute", "unmute", "louder", "quieter"})
        has_brightness = bool(tokens & {"brightness", "bright", "brighter", "dim", "dimmer", "screen"})
        has_timer = bool(tokens & {"timer", "minute", "minutes", "second", "seconds"})
        has_power = bool(tokens & {"shutdown", "restart", "lock", "close", "exit", "quit"})
        has_switch = bool(tokens & {"switch", "window", "desktop"})

        domains = sum(
            1 for hit in (has_volume, has_brightness, has_timer, has_power, has_switch) if hit
        )
        return domains >= 2

    def _execute_intent(self, intent: str, entities: dict) -> bool:
        if intent == INTENT_STUDY_MODE_START:
            return self._start_study_mode()

        if intent == INTENT_STUDY_MODE_STOP:
            return self._stop_study_mode(announce=True, prompt_restore_now=True)

        if intent == INTENT_STUDY_MODE_STATUS:
            return self._speak_study_mode_status()

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
            if not self._apply_volume_steps(up=(direction == "UP"), steps=steps):
                ui_state("ERROR")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
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
                self._sleep_error()
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
            if not ok and not self._apply_volume_steps(up=True, steps=1):
                ui_state("ERROR")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
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
            if not self._apply_volume_steps(up=True, steps=steps):
                ui_state("ERROR")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
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
            if not self._apply_volume_steps(up=False, steps=steps):
                ui_state("ERROR")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
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
            self.voice.play_or_tts_async("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = launch_app(app)
            if ok:
                ui_state("SUCCESS")
                time.sleep(0.15)
                ui_state("IDLE")
                return True
            return False

        if intent == "close app":
            app_id = (entities.get("app") or entities.get("id") or "").strip().lower()
            app = find_app(self.apps, app_id)
            if not app:
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts_async("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = close_app(app)
            if ok:
                ui_state("SUCCESS")
                time.sleep(0.15)
                ui_state("IDLE")
                return True
            return False

        if intent == "close active":
            proc = (entities.get("process") or "").strip()
            if not proc:
                return False
            proc_name = proc if proc.lower().endswith(".exe") else (proc + ".exe")
            if self._is_protected_process_for_close(proc_name):
                ui_state("WARNING")
                self.voice.play_or_tts("not_now", "I won't close this app.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            ui_state("SPEAKING")
            self.voice.play_or_tts_async("close_active", "Closing current app")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = close_app_by_process(proc_name, force=False)
            time.sleep(0.1)
            ok2 = close_app_by_process(proc_name, force=True)
            if ok or ok2:
                ui_state("SUCCESS")
                time.sleep(0.15)
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
            self.voice.play_or_tts_async(intent.replace(" ", "_"), response)
            self._deafen_after_speak()
            ui_state("EXECUTING")
            COMMANDS[intent]()
            ui_state("SUCCESS")
            time.sleep(0.15)
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

    def _speak_confirm_prompt(self):
        # Play prompt asynchronously so the mic is hot immediately —
        # the user can say "yes"/"no" while Aidy is still speaking.
        ui_state("CONFIRM")
        spoken = self.voice.play_key_async("are_you_sure_yes_no") or self.voice.play_key_async("are_you_sure")
        if not spoken:
            self.voice.tts_fire_and_forget("Are you sure?")
            self._cmd_log("confirm prompt source=tts text='Are you sure?'")
        else:
            self._cmd_log("confirm prompt source=clip (async)")
        # No deafen — mic stays hot to catch immediate replies
        info("Confirm: awaiting answer")
        self._cmd_log("confirm expected")

    def _speak_confirm_retry(self):
        ui_state("CONFIRM")
        self.voice.play_or_tts_async("confirm_retry", "Yes or no?")
        # No deafen — mic stays hot
        info("Confirm: awaiting answer")
        self._cmd_log("confirm retry expected")

    def _listen_confirm_reply(self, extra_grammar_phrases: set[str] | None = None) -> str | None:
        grammar = set(CONFIRM_GRAMMAR_PHRASES)
        # Add common Vosk mishearings / short-word variants for yes/no
        grammar.update({
            "yes yes", "no no",
            "yes please", "no thanks", "no thank you",
            "yea", "ya", "da", "net",
        })
        for phrase in (extra_grammar_phrases or set()):
            p = " ".join(str(phrase or "").strip().lower().split())
            if p:
                grammar.add(p)
        reply = self.listen_command_vosk(
            max_seconds=4,
            min_listen_ms=300,
            ui_state_label="CONFIRM",
            use_grammar=True,
            speak_on_empty=False,
            grammar_phrases=sorted(grammar),
            stop_audio_on_voice=True,
        )
        if reply:
            return reply
        self._cmd_log("confirm grammar empty -> retry free-form")
        return self.listen_command_vosk(
            max_seconds=3,
            min_listen_ms=300,
            ui_state_label="CONFIRM",
            use_grammar=False,
            speak_on_empty=False,
            stop_audio_on_voice=True,
        )

    def _classify_confirm_reply(self, reply: str, extra_yes_phrases: set[str] | None = None) -> str | None:
        t = " ".join((reply or "").strip().lower().split())
        if not t:
            return None

        if t in CONFIRM_YES:
            return "yes"
        if t in CONFIRM_NO:
            return "no"

        extra_yes = {self._normalize_wake_text(v) for v in (extra_yes_phrases or set()) if v}
        nt = self._normalize_wake_text(t)
        if nt in extra_yes:
            return "yes"

        tokens = nt.split()
        no_tokens = {"no", "nope", "nah", "cancel", "stop", "dont", "not", "never", "know", "net"}
        yes_tokens = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "proceed", "jes", "yas", "yess", "ye", "yse", "yea", "ya", "da"}
        # Safety-first: "no" wins if mixed tokens are detected.
        if any(tok in no_tokens for tok in tokens):
            return "no"
        if extra_yes and "close" in tokens:
            return "yes"
        if any(tok in yes_tokens for tok in tokens):
            return "yes"
        return None

    def _confirm_and_schedule(self, intent: str, entities: dict, delay_seconds: int) -> bool:
        self._speak_confirm_prompt()

        for attempt in range(2):
            reply = self._listen_confirm_reply()
            decision = self._classify_confirm_reply(reply or "")
            if decision == "yes":
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

            if decision == "no":
                self.context_mgr.clear_context()
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False

            if attempt == 0:
                self._speak_confirm_retry()

        self.context_mgr.clear_context()
        ui_state("WARNING")
        self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
        self._deafen_after_speak()
        ui_state("IDLE")
        return False

    _handle_due_tasks_last = 0.0

    def _handle_due_tasks(self, force: bool = False):
        now = time.time()
        if not force and (now - self._handle_due_tasks_last) < 0.5:
            return
        self._handle_due_tasks_last = now
        self._update_timer()
        self._update_study_timer_watchdog()
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
        self._speak_confirm_prompt()

        for attempt in range(2):
            reply = self._listen_confirm_reply()
            decision = self._classify_confirm_reply(reply or "")
            if decision == "yes":
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
                    self._sleep_error()
                    ui_state("IDLE")
                    self.history.break_chain()
                    return False

            if decision == "no":
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            if attempt == 0:
                self._speak_confirm_retry()

        ui_state("WARNING")
        self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
        self._deafen_after_speak()
        ui_state("IDLE")
        self.history.break_chain()
        return False

    def _confirm_close_request(self) -> bool:
        self._speak_confirm_prompt()
        extra_yes = {"close", "close it", "close app", "do it", "yes close"}

        for attempt in range(2):
            reply = self._listen_confirm_reply(extra_grammar_phrases=extra_yes)
            decision = self._classify_confirm_reply(reply or "", extra_yes_phrases=extra_yes)
            if decision == "yes":
                return True

            if decision == "no":
                ui_state("WARNING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            if attempt == 0:
                self._speak_confirm_retry()

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
                if not ok and not self._apply_volume_steps(up=True, steps=1):
                    ui_state("ERROR")
                    self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                    self._deafen_after_speak()
                    ui_state("IDLE")
                    return False
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
                if not self._apply_volume_steps(up=True, steps=steps):
                    ui_state("ERROR")
                    self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                    self._deafen_after_speak()
                    ui_state("IDLE")
                    return False
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
                if not self._apply_volume_steps(up=False, steps=steps):
                    ui_state("ERROR")
                    self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                    self._deafen_after_speak()
                    ui_state("IDLE")
                    return False
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
                if (app.get("type") or "").strip().lower() != "url" and not app.get("safe_to_close"):
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
                if self._is_protected_process_for_close(proc_name):
                    ui_state("WARNING")
                    self.voice.play_or_tts("not_now", "I won't close this app.")
                    self._deafen_after_speak()
                    ui_state("IDLE")
                    return False
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
        self._debug_file_log(f"process_command ENTERED with text='{text}' intent_map_has_music={('music' in self._text_to_intent_map)} intent_map_music={self._text_to_intent_map.get('music', 'NOT_FOUND')} map_size={len(self._text_to_intent_map)}")
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

        # ── Fast-path: custom mode on/off — skip all other processing ────
        t0_quick = " ".join((text or "").strip().lower().split())
        if t0_quick in CUSTOM_MODE_ON_PHRASES:
            info(f"[CustomMode] Voice command matched ON phrase: '{t0_quick}'")
            ui_custom_mode(True)
            ui_state("EXECUTING")
            self._execute_all_custom_mode_slots()
            ui_state("SUCCESS")
            time.sleep(0.3)
            ui_state("IDLE")
            return True

        if t0_quick in CUSTOM_MODE_OFF_PHRASES:
            info(f"[CustomMode] Voice command matched OFF phrase: '{t0_quick}'")
            ui_custom_mode(False)
            ui_state("SPEAKING")
            self.voice.play_or_tts("custom_mode_off", "Custom mode disabled.")
            self._deafen_after_speak()
            ui_state("SUCCESS")
            time.sleep(0.3)
            ui_state("IDLE")
            return True

        normalized_text = self._normalize_spoken_command(text)
        if normalized_text and normalized_text != t0_quick:
            self._cmd_log(f"process remap -> '{normalized_text}'")
            text = normalized_text

        t0 = " ".join((text or "").strip().lower().split())
        self._debug_file_log(f"process_command t0='{t0}' follow_active={self.follow_mode.is_active()}")
        pending = self.follow_up.get_pending()
        pending_active = pending is not None
        pending_type = pending.pending_type if pending else None
        self._cmd_log(
            f"route text='{t0}' pending={'on' if pending_active else 'off'} "
            f"follow={'on' if self.follow_mode.is_active() else 'off'}"
        )

        # ── PENDING_NEED_SONG: user is responding with a song name ────
        # Must run before ANY other routing so the song name isn't
        # misinterpreted as a study-mode trigger, app name, etc.
        if pending_type == PENDING_NEED_SONG:
            if t0 in ("cancel", "stop", "nevermind", "never mind"):
                self.follow_up.clear_pending()
                ui_state("SPEAKING")
                self.voice.play_or_tts("cancelled", "Cancelled.")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False
            self.follow_up.clear_pending()
            song_query = t0.strip()
            if not song_query:
                ui_state("IDLE")
                return False
            from urllib.parse import quote_plus
            music_id = self._preferred_music_app or "yandex_music"
            if music_id == "spotify":
                search_url = f"https://open.spotify.com/search/{quote_plus(song_query)}"
            else:
                search_url = f"https://music.yandex.ru/search?text={quote_plus(song_query)}"
            ui_state("SPEAKING")
            self.voice.play_or_tts_async("open_app", f"Playing {song_query}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            try:
                os.startfile(search_url)
                self._set_context("play_music", {"song": song_query})
                self._set_memory("play_music", {"song": song_query})
                self._record_action("play_music", {"song": song_query})
                ui_state("SUCCESS")
                time.sleep(0.15)
                ui_state("IDLE")
                return True
            except Exception as e:
                error(f"Failed to open music search: {e}")
                ui_state("ERROR")
                self.voice.play_or_tts("open_app_fail", "Sorry, I couldn't open it")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

        study_intent = self._study_intent_from_text(t0)
        self._debug_file_log(f"process_command study_intent={study_intent}")
        if study_intent == INTENT_STUDY_MODE_START:
            if self._study_start_requires_confirmation(t0):
                if not self._confirm_study_mode_request(t0):
                    return False
            if pending_active:
                self.follow_up.clear_pending()
            self.follow_mode.clear()
            self.last_step_actions.clear()
            ok = self._start_study_mode()
            if ok:
                self._set_last_command(text)
                self._set_memory(INTENT_STUDY_MODE_START, {"seconds": STUDY_SESSION_SECONDS})
                self._set_context(INTENT_STUDY_MODE_START, {"seconds": STUDY_SESSION_SECONDS})
            return ok

        if study_intent == INTENT_STUDY_MODE_STOP and self.study_mode_active:
            if not self._confirm_study_stop_request(t0):
                return False
            if pending_active:
                self.follow_up.clear_pending()
            self.follow_mode.clear()
            self.last_step_actions.clear()
            ok = self._stop_study_mode(announce=True, prompt_restore_now=True)
            if ok:
                self._set_last_command(text)
                self._set_memory(INTENT_STUDY_MODE_STOP, {})
                self._set_context(INTENT_STUDY_MODE_STOP, {})
            return ok

        if study_intent == INTENT_STUDY_MODE_STATUS:
            return self._speak_study_mode_status()

        if t0 in ("cancel", "stop") and pending_active:
            self._cancel_pending(speak_cancelled=True)
            self.history.break_chain()
            return False

        if pending_type == PENDING_NEED_TARGET:
            # Rephrase slot should be consumed by this utterance, then normal routing applies.
            self.follow_up.clear_pending()
            pending_active = False
            pending_type = None

        # Timer commands should work even if some previous numeric follow-up is pending.
        if t0 in TIMER_CANCEL_PHRASES:
            if pending_active:
                self.follow_up.clear_pending()
            self.follow_mode.clear()
            self.last_step_actions.clear()
            self.history.break_chain()
            return self._cancel_timer(announce=True)

        if self._is_timer_start_request(t0):
            if pending_active:
                self.follow_up.clear_pending()
            seconds = parse_timer_duration_seconds(text, default_unit="minutes", max_seconds=TIMER_MAX_SECONDS)
            if seconds is None:
                self.follow_up.set_pending(
                    PendingAction(
                        pending_type=PENDING_NEED_TIMER_DURATION,
                        base_intent="timer",
                        entities={},
                    )
                )
                self._cmd_log("timer pending created; waiting duration")
                ui_state("SPEAKING")
                self.voice.play_or_tts("timer_how_long", "For how long?")
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            self._cmd_log(f"timer start parsed={seconds}s text='{text}'")
            return self._start_timer(seconds, source_text=text)

        if pending_active and (t0 in MORE_ACTION_PHRASES or t0 in LESS_ACTION_PHRASES):
            ui_state("SPEAKING")
            self.voice.play_or_tts("need_number", "Say a number.")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        pending_result = self._handle_pending_numeric_flow(text) if pending_active else None
        if pending_result is not None:
            self._debug_file_log(f"EXIT pending_numeric_flow: text='{text}' result={pending_result}")
            return pending_result

        if self.follow_mode.is_active():
            self._debug_file_log(f"process_command: FOLLOW MODE ACTIVE, routing through follow handler")
            routed = classify_follow_input(
                text=text,
                wake_keywords=WAKE_KEYWORDS,
                more_phrases=MORE_ACTION_PHRASES,
                less_phrases=LESS_ACTION_PHRASES,
                pending_active=False,
            )
            kind = routed.get("kind")
            self._debug_file_log(f"process_command: follow routed kind='{kind}'")
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

        if self._is_non_action_utterance(t0):
            self._debug_file_log(f"EXIT non_action_utterance: t0='{t0}'")
            self._cmd_log(f"ignore non-action command='{t0}'")
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

            needs_confirm = intent in DANGEROUS_INTENTS or intent == "close active"
            if intent == "close app":
                _app = find_app(self.apps, (entities.get("app") or entities.get("id") or "").strip().lower())
                if _app and not _app.get("safe_to_close") and ((_app.get("type") or "").strip().lower() != "url"):
                    needs_confirm = True
            if needs_confirm:
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
            win_info = get_active_window_info()
            if not win_info:
                ui_state("IDLE")
                return False

            proc = (win_info.get("process") or "").strip()
            if not proc:
                ui_state("IDLE")
                return False

            if not self._confirm_close_request():
                return False

            proc_name = proc if proc.lower().endswith(".exe") else (proc + ".exe")
            if self._is_protected_process_for_close(proc_name):
                ui_state("WARNING")
                self.voice.play_or_tts("not_now", "I won't close this app.")
                self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            ui_state("SPEAKING")
            self.voice.play_or_tts_async("close_active", "Closing current app")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            self._set_last_command(text)
            ok = close_app_by_process(proc_name, force=False)
            time.sleep(0.1)
            ok2 = close_app_by_process(proc_name, force=True)
            if ok or ok2:
                self._set_memory("close active", {"process": proc})
                ui_state("SUCCESS")
                time.sleep(0.15)
                ui_state("IDLE")
                return True
            ui_state("ERROR")
            self.voice.play_or_tts("close_app_fail", "Sorry, I couldn't close it")
            self._deafen_after_speak()
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

            if (app.get("type") or "").strip().lower() != "url" and not app.get("safe_to_close"):
                if not self._confirm_close_request():
                    return False

            ui_state("SPEAKING")
            self.voice.play_or_tts_async("close_app", f"Closing {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            self._set_last_command(text)
            ok = close_app(app)
            if ok:
                self._set_context("close app", {"app": app["id"]})
                self._set_memory("close app", {"id": app["id"]})
                self._record_action("close app", {"app": app["id"]})
                ui_state("SUCCESS")
                time.sleep(0.15)
                ui_state("IDLE")
                return True
            ui_state("ERROR")
            self.voice.play_or_tts("close_app_fail", "Sorry, I couldn't close it")
            self._deafen_after_speak()
            ui_state("IDLE")
            self.history.break_chain()
            return False

        if t0 in MOUSE_COMMAND_PHRASES:
            ui_state("EXECUTING")
            try:
                mouse_result = execute_mouse_command(t0)
                if mouse_result:
                    info(f"Mouse: {mouse_result}")
                    self._set_last_command(text)
                    self._record_action(mouse_result, {})
                    ui_state("IDLE")
                    return True
            except Exception as e:
                error(f"Mouse command failed: {e}")
            ui_state("IDLE")
            self.history.break_chain()
            return False

        # Offline direct commands without Intent API
        if t0 in COMMANDS:
            if t0 in DANGEROUS_INTENTS:
                return self._confirm_and_execute(t0, COMMANDS[t0], original_text=text)

            response = VOICE_RESPONSES.get(t0, f"Executing {t0}")
            ui_state("SPEAKING")
            self.voice.play_or_tts_async(t0.replace(" ", "_"), response)
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
                time.sleep(0.15)
                ui_state("IDLE")
                return True
            except Exception as e:
                ui_state("ERROR")
                error(f"Exec failed: {e}")
                self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                self._deafen_after_speak()
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
                self._speak_steps_prompt(cfg["base"])
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            return self._execute_step_intent(step_intent, steps, text)

        # ── Play Music: ask what song to search ───────────────
        # Must run BEFORE the generic app-open block because find_app()
        # would match "yandex music" aliases and open the app directly,
        # skipping the "What do you want to listen to?" prompt.
        _local_intent = self._text_to_intent_map.get(t0)
        # Fallback: if exact CSV match missed, check if the spoken text
        # contains a music keyword — catches ASR variants the CSV doesn't list.
        if _local_intent != "play_music" and any(kw in t0 for kw in ("music", "музык", "музыка", "песн")):
            _local_intent = "play_music"
        self._debug_file_log(f"CHECKPOINT play_music: t0='{t0}' _local_intent='{_local_intent}'")
        if _local_intent == "play_music":
            self._debug_file_log(f"play_music matched: t0='{t0}'")
            self.follow_up.set_pending(
                PendingAction(
                    pending_type=PENDING_NEED_SONG,
                    base_intent="play_music",
                    entities={},
                )
            )
            ui_state("SPEAKING")
            self.voice.play_or_tts("ask_song", "What do you want to listen to?")
            self._deafen_after_speak()
            ui_state("IDLE")
            return False

        # Direct app name without "open"/"launch" (e.g., "steam", "chrome")
        app = find_app(self.apps, t0)
        if not app and t0.startswith(("open ", "open up ", "launch ", "start ", "run ", "go to ", "go ", "visit ", "show ")):
            app = find_app(self.apps, extract_app_name(t0))
        if app:
            ui_state("SPEAKING")
            self.voice.play_or_tts_async("open_app", f"Opening {app['id']}")
            self._deafen_after_speak()
            ui_state("EXECUTING")
            ok = launch_app(app)
            if not ok:
                # Fallback: open default browser if "browser" requested
                if "browser" in app.get("aliases", []) or app.get("id") in ("chrome", "browser"):
                    ok = self._open_default_browser()
            if ok:
                self._set_last_command(text)
                self._set_context("open app", {"app": app["id"]})
                self._set_memory("open app", {"id": app["id"]})
                self._record_action("open app", {"app": app["id"]})
                ui_state("SUCCESS")
                time.sleep(0.15)
                ui_state("IDLE")
                return True
            ui_state("ERROR")
            self.voice.play_or_tts("open_app_fail", "Sorry, I couldn't open it")
            self._deafen_after_speak()
            ui_state("IDLE")
            self.history.break_chain()
            return False

        if t0.startswith(("open ", "open up ", "launch ", "start ", "run ", "go to ", "go ", "visit ", "show ")):
            ui_state("WARNING")
            self.voice.play_or_tts("app_not_found", "I couldn't find that app")
            self._deafen_after_speak()
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

        if not self._is_command_like_text(t0):
            self._debug_file_log(f"EXIT not_command_like: t0='{t0}'")
            self._cmd_log(f"reject non-command='{t0}'")
            ui_state("WARNING")
            if not self.voice.muted:
                self.voice.play_or_tts("not_sure", "I'm not sure what you mean")
                self._deafen_after_speak()
            ui_state("IDLE")
            self.history.break_chain()
            return False

        # ── Fast-path: resolve intent locally (O(1) CSV/COMMANDS lookup) ──
        # Skips the slow HTTP intent-API call when the spoken text matches
        # a known command phrase exactly.  Saves 2-5 seconds per command.
        _local_intent = self._resolve_intent_locally(t0)
        if _local_intent:
            intent = _local_intent.strip().lower()
            confidence = 1.0
            info(f"Intent (local): {intent}  conf={confidence:.2f}")
            self._cmd_log(f"local_intent='{intent}' skipped API")
        else:
            if not self.intent_api_ready:
                self.intent_api_ready = start_local_intent_api(self.base_dir)

            if not self.intent_api_ready:
                ui_state("WARNING")
                if not self.voice.muted:
                    self.voice.play_or_tts("not_sure", "I'm not sure what you mean")
                    self._deafen_after_speak()
                ui_state("IDLE")
                self.history.break_chain()
                return False

            ui_state("PROCESSING")
            info("Intent: sending to API...")

            _intent_t0 = time.time()
            result = self.api.get_intent(text)
            _intent_ms = (time.time() - _intent_t0) * 1000
            if _intent_ms > 500:
                get_debug_logger().log_command(
                    text, _intent_ms, result is not None,
                    {"phase": "intent_api_call", "intent_api_ms": round(_intent_ms, 2)},
                )
            if not result:
                self.intent_api_ready = False
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

        if intent in {INTENT_STUDY_MODE_START, "study mode", "start study", "start study mode"}:
            normalized_text = " ".join((text or "").strip().lower().split())
            if self._study_start_requires_confirmation(normalized_text):
                if not self._confirm_study_mode_request(normalized_text):
                    return False
            ok = self._start_study_mode()
            if ok:
                self._set_last_command(text)
                self._set_memory(INTENT_STUDY_MODE_START, {"seconds": STUDY_SESSION_SECONDS})
                self._set_context(INTENT_STUDY_MODE_START, {"seconds": STUDY_SESSION_SECONDS})
            return ok
        if intent in {INTENT_STUDY_MODE_STOP, "stop study mode", "finish study mode", "end study mode"}:
            if self.study_mode_active:
                normalized_text = " ".join((text or "").strip().lower().split())
                if not self._confirm_study_stop_request(normalized_text):
                    return False
            return self._stop_study_mode(announce=True, prompt_restore_now=True)
        if intent in {INTENT_STUDY_MODE_STATUS, "study mode status"}:
            return self._speak_study_mode_status()

        intent_key = (intent or "").replace(" ", "_")

        if intent_key in ("play_music", "play_yandex_music", "music", "open_music"):
            self.follow_up.set_pending(
                PendingAction(
                    pending_type=PENDING_NEED_SONG,
                    base_intent="play_music",
                    entities={},
                )
            )
            ui_state("SPEAKING")
            self.voice.play_or_tts("ask_song", "What do you want to listen to?")
            self._deafen_after_speak()
            ui_state("IDLE")
            return True

        # ── Safety net: if the spoken text clearly mentions music,
        # never fall through to the step-intent (volume/brightness) flow,
        # even if the API misclassified the intent.
        _music_keywords = {"music", "музык", "музыка", "музыку", "песн", "song"}
        if any(kw in t0 for kw in _music_keywords):
            self._debug_file_log(f"music safety-net caught t0='{t0}' (API intent='{intent}')")
            self.follow_up.set_pending(
                PendingAction(
                    pending_type=PENDING_NEED_SONG,
                    base_intent="play_music",
                    entities={},
                )
            )
            ui_state("SPEAKING")
            self.voice.play_or_tts("ask_song", "What do you want to listen to?")
            self._deafen_after_speak()
            ui_state("IDLE")
            return True

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
                self._speak_steps_prompt(cfg["base"])
                self._deafen_after_speak()
                ui_state("IDLE")
                return False
            return self._execute_step_intent(step_intent, steps, text)

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
                self._sleep_error()
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

            if (app.get("type") or "").strip().lower() != "url" and not app.get("safe_to_close"):
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
                self._sleep_error()
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
                self._sleep_error()
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

    # ── Voice-auth helpers ─────────────────────────────────────────────────

    def _flush_stream_buffer(self):
        """Drain all pending data from the mic stream to discard stale noise."""
        if self.stream is None:
            return
        try:
            avail = self.stream.get_read_available()
            chunks_to_read = min(50, max(0, avail // CHUNK_SAMPLES))
            for _ in range(chunks_to_read):
                self.stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
        except Exception:
            pass

    # Known Vosk hallucinations when the user says "aidy" (OOV word).
    _AIDY_VOSK_ALIASES = {
        "aidy", "eighty", "eddie", "eddy", "a d", "lady", "hey d",
        "a b", "id", "maybe", "edie", "eadie", "eady",
        "eighty's", "eighties", "eightie", "edie's",
        "eighty one", "eighty two",
        "hey dee", "hey de", "a de", "a dee",
        "eighty eighty",
        "a t", "eiti", "aide", "eidi", "heady",
        "adi", "ady", "aidi", "eidy", "edi",
    }

    def _enrollment_phrase_match(self, phrase: str, result: str) -> bool:
        p = phrase.lower().strip()
        r = result.lower().strip()
        if not r:
            return False

        # For "aidy": accept any known Vosk alias/hallucination.
        if p == "aidy":
            r_tokens = set(r.split())
            for alias in self._AIDY_VOSK_ALIASES:
                if alias in r or alias in r_tokens:
                    return True
            return False

        # Standard substring match for all other phrases.
        return p in r or r in p

    def _run_admin_enrollment(self) -> None:
        """Interactive Admin voice enrollment flow triggered by the UI 'Enroll Voice' button."""
        try:
            self._drain_control_commands()
            self._enrollment_cancel_requested = False
            self._enrollment_in_progress = True

            print("EVENT:ENROLL_STARTED", flush=True)

            # --- Clean up existing voice data before fresh enrollment ---
            # Clear all rows from the DB (keeps the connection and encoder alive)
            try:
                conn = self.voice_auth._get_conn()
                conn.execute("DELETE FROM voice_embeddings")
                conn.execute("DELETE FROM voice_users")
                conn.commit()
                self.voice_auth._invalidate_cache()
                info("[ENROLL] Cleared voice_profiles DB tables")
            except Exception as e:
                info(f"[ENROLL] Failed to clear DB tables: {e}")

            # Clear VoiceProfiles folder (WAV backups)
            enroll_dir = os.path.join(self.base_dir, "VoiceProfiles")
            if os.path.isdir(enroll_dir):
                for f in os.listdir(enroll_dir):
                    fpath = os.path.join(enroll_dir, f)
                    try:
                        if os.path.isfile(fpath):
                            os.remove(fpath)
                    except Exception as e:
                        info(f"[ENROLL] Failed to delete {fpath}: {e}")
                info("[ENROLL] Cleared VoiceProfiles folder")

            os.makedirs(enroll_dir, exist_ok=True)

            if self.stream is not None:
                self.stop_stream()
            self.stream = self.audio.open(
                format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
                input=True, frames_per_buffer=CHUNK_SAMPLES,
            )
            self.stream.start_stream()

            from .voice_auth import _estimate_snr_db, MIN_ENROLLMENT_SNR_DB, MIN_ENROLLMENT_SNR_DB_DEGRADED

            # 5 phrases for a richer biometric embedding (one embedding per phrase).
            # These prompts elicit natural speech; we use FREE-FORM recognition
            # (no grammar) so Vosk can detect ANY speech — we only need audio, not
            # an exact transcript match.
            phrases_to_say = [
                "my name is admin",
                "hey aidy open the browser please",
                "set a timer for five minutes",
                "she had your dark suit in greasy wash water all year",
            ]
            total = len(phrases_to_say)
            audio_segments: list[bytes] = []

            print("CONTROL:enroll_text:Preparing...|", flush=True)
            self.voice.tts_fire_and_forget("Voice ID setup. I will ask you to say four phrases.")
            time.sleep(1.8)

            for i, phrase in enumerate(phrases_to_say):
                progress = f"Phrase {i + 1} of {total}"
                captured = False
                max_attempts = 4

                for attempt in range(1, max_attempts + 1):
                    print(f"CONTROL:enroll_text:Say: '{phrase}'|{progress}", flush=True)
                    self.voice.tts_fire_and_forget(f"Please say: {phrase}")
                    time.sleep(1.2)

                    self._flush_stream_buffer()
                    self._deafen_until = 0.0
                    self._last_audio_buffer = b""

                    # Free-form recognition — accept ANY speech.
                    # We care about capturing voice audio, not exact words.
                    result = self.listen_command_vosk(
                        use_grammar=False,
                        max_seconds=8,
                        min_listen_ms=800,
                        speak_on_empty=False,
                        ui_state_label="LISTENING",
                        vad_threshold=40,
                        silence_stop_override_ms=2000,
                        force_listen=True,
                    )

                    # Check for cancellation via control command
                    if getattr(self, "_enrollment_cancel_requested", False):
                        print("CONTROL:enroll_text:Cancelled.|", flush=True)
                        self.voice.tts_fire_and_forget("Cancelled.")
                        print("EVENT:ENROLLMENT_FINISHED", flush=True)
                        return

                    # Accept any non-empty Vosk result as valid speech
                    if result and result.strip():
                        audio_data = self._last_audio_buffer
                        if audio_data and len(audio_data) >= 6400:  # at least 0.2s
                            snr = _estimate_snr_db(audio_data)
                            # Two-tier SNR: ideal ≥12 dB, degraded ≥8 dB on 2nd+ attempt
                            if snr < MIN_ENROLLMENT_SNR_DB:
                                if snr >= MIN_ENROLLMENT_SNR_DB_DEGRADED and attempt >= 2:
                                    # Accept degraded quality after retry
                                    info(f"[ENROLL] Phrase {i+1}: accepted at degraded SNR ({snr:.0f}dB)")
                                elif max_attempts - attempt > 0:
                                    print(f"CONTROL:enroll_text:Noisy (SNR {snr:.0f}dB), try again|{progress}", flush=True)
                                    self.voice.tts_fire_and_forget("A bit noisy, please try again.")
                                    time.sleep(1.0)
                                    continue
                            audio_segments.append(audio_data)
                            print(f"CONTROL:enroll_text:✓ Accepted!|{progress}", flush=True)
                            try:
                                self.voice.play_key("success")
                            except Exception:
                                pass
                            time.sleep(0.6)
                            captured = True
                            break

                    if max_attempts - attempt > 0:
                        print(f"CONTROL:enroll_text:Try again, speak clearly|{progress}", flush=True)
                        self.voice.tts_fire_and_forget("I didn't hear you. Please speak clearly.")
                        time.sleep(1.0)

                if not captured:
                    print("CONTROL:enroll_text:Failed.|", flush=True)
                    self.voice.tts_fire_and_forget("Failed. Try again later.")
                    print("EVENT:ENROLLMENT_FINISHED", flush=True)
                    return

            print("CONTROL:enroll_text:Saving voice...|Processing", flush=True)
            timestamp = int(time.time())
            filepath = os.path.join(enroll_dir, f"Admin_Voice_{timestamp}.wav")
            # Save concatenated audio as backup WAV
            combined_audio = b"".join(audio_segments)
            try:
                with wave.open(filepath, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(combined_audio)
            except Exception as e:
                info(f"[ENROLL] WAV save error: {e}")

            num = self.voice_auth._next_user_number()
            label = f"user_{num}(admin)"
            print("CONTROL:enroll_text:Analyzing voice...|Processing", flush=True)
            try:
                ok, msg = self.voice_auth.enroll(audio_segments, role="Admin", label=label)
                info(f"[ENROLL] result: ok={ok}, msg={msg}")
            except Exception as e:
                info(f"[ENROLL] enroll error: {e}")
                ok = False

            if ok:
                print("CONTROL:enroll_text:✓ Voice recorded!|Done", flush=True)
                self.voice.tts_fire_and_forget("Voice registered successfully.")
                self._emit_voice_users_list()
            else:
                print("CONTROL:enroll_text:Profile error.|", flush=True)
                self.voice.tts_fire_and_forget("Profile creation failed.")

            time.sleep(2.0)
            print("EVENT:ENROLLMENT_FINISHED", flush=True)

        except Exception as e:
            info(f"[ENROLL] critical error: {e}")
            print("CONTROL:enroll_text:Error occurred.|", flush=True)
            print("EVENT:ENROLLMENT_FINISHED", flush=True)
        finally:
            self._enrollment_in_progress = False

    def _emit_voice_users_list(self):
        """Send the enrolled users list to the WPF UI via CONTROL protocol.

        Format: CONTROL:voice_users:label|role|expires;label|role|expires;...
        """
        try:
            users = self.voice_auth.list_users()
            if not users:
                print("CONTROL:voice_users:", flush=True)
                return
            parts = []
            for u in users:
                parts.append(f"{u['label']}|{u['role']}|{u['expires']}")
            print(f"CONTROL:voice_users:{';'.join(parts)}", flush=True)
        except Exception as e:
            info(f"[VoiceUsers] emit failed: {e}")
            print("CONTROL:voice_users:", flush=True)

    def _resolve_intent_locally(self, text: str) -> str | None:
        """Fast O(1) lookup: raw spoken text → classified intent key.

        Uses the text_to_intent map built from commands.csv at startup.
        Returns None if no match (caller should fall through).
        """
        t = " ".join((text or "").strip().lower().split())
        intent = self._text_to_intent_map.get(t)
        if intent:
            return intent
        # COMMANDS dict keys are intent keys themselves
        if t in COMMANDS:
            return t
        return None

    # ── Async embedding extraction ──────────────────────────────────────
    # Start extraction right after wake detection so it runs in parallel
    # with command listening, saving 100-300 ms on every command.
    _pending_emb_thread: threading.Thread | None = None
    _pending_emb_result: list = []  # [embedding] when done

    def _start_async_embedding(self):
        """Kick off embedding extraction in a background thread.

        Uses ``_last_audio_buffer`` captured during wake detection.
        Call ``_collect_async_embedding()`` later to get the result.
        """
        if not self.voice_id_enabled or not hasattr(self, "voice_auth"):
            return
        buf = getattr(self, "_last_audio_buffer", b"")
        if not buf:
            return
        self._pending_emb_result = []

        def _extract():
            try:
                emb = self.voice_auth.extract_embedding(buf)
                self._pending_emb_result.append(emb)
            except Exception:
                pass

        self._pending_emb_thread = threading.Thread(target=_extract, daemon=True)
        self._pending_emb_thread.start()

    def _collect_async_embedding(self, timeout: float = 1.0):
        """Wait for the background embedding extraction to finish.

        Returns the embedding if available, else None (caller falls back
        to synchronous extraction).
        """
        t = self._pending_emb_thread
        if t is None:
            return None
        t.join(timeout=timeout)
        self._pending_emb_thread = None
        if self._pending_emb_result:
            return self._pending_emb_result[0]
        return None

    def _voice_auth_check(self, text: str, intent: str | None = None,
                          precomputed_emb=None) -> bool:
        """
        Verify that the current speaker is authorised to run the command
        described by *text*.  Plays an "Access denied" response and returns
        False when blocked; returns True when allowed.

        Cold-start guard: if the resemblyzer encoder hasn't finished loading
        yet, skip RBAC and allow the command (the spectral fallback would
        produce a dimension mismatch against stored resemblyzer embeddings,
        causing a guaranteed false rejection).
        """
        # ── Cold-start bypass ────────────────────────────────────────────
        if not self.voice_auth.is_encoder_ready():
            info("[RBAC] encoder still warming up — bypassing voice check (cold start)")
            return True

        # Use classified intent if provided; fall back to local resolution;
        # last resort: raw text (unknown commands will fail at execution anyway).
        intent_key = intent or self._resolve_intent_locally(text) or text
        intent_key = intent_key.strip().lower().replace(" ", "_")

        allowed, role_label, reason = self.voice_auth.authorize(
            self._last_audio_buffer, intent=intent_key,
            precomputed_emb=precomputed_emb,
        )
        if not allowed:
            info(f"[RBAC] blocked role='{role_label}' intent='{intent_key}'")
            ui_state("ACCESS_DENIED")
            try:
                self.voice.play_access_denied_beep()
                # Short timeout so a cold/hung TTS engine can't freeze us
                self.voice.tts_blocking(reason or "Access denied.", timeout_sec=5.0)
                self._deafen_after_speak()
            except Exception as e:
                warn(f"[RBAC] TTS error during access-denied: {e}")
            # Brief pause so the denied cross is visible
            time.sleep(0.5)
            ui_state("IDLE")
            return False

        info(f"[RBAC] allowed role='{role_label}' sim OK intent='{intent_key}'")
        return True

    # ------------------------------------------------------------------
    # Reusable voice-identity gate for sensitive commands
    # ------------------------------------------------------------------

    def _require_admin_voice(
        self,
        precomputed_emb=None,
        deny_message: str = "Access denied. Only an admin can do that.",
    ) -> bool:
        """Verify that the current speaker is an enrolled Admin.

        Call this at the top of any sensitive command handler.  It handles
        three scenarios transparently:

        1. **voice_id disabled** → returns True (bypass).
        2. **voice_id enabled, speaker is Admin** → returns True.
        3. **voice_id enabled, speaker is NOT Admin** → plays the
           access-denied beep + *deny_message*, then returns False.

        The caller only needs to check the boolean return value:

            if not self._require_admin_voice(precomputed_emb=emb):
                return False

        The method preserves ``last_unknown_embedding`` so that a
        subsequent ``grant_access`` call can still register the
        unknown speaker.
        """
        # Bypass when voice-id is turned off in settings
        if not self.voice_id_enabled:
            return True

        # Preserve the pending unknown embedding across identify()
        saved_emb = self.voice_auth.last_unknown_embedding
        saved_ts = self.voice_auth.last_unknown_ts

        role, sim, _, confidence = self.voice_auth.identify(
            self._last_audio_buffer, precomputed_emb=precomputed_emb,
        )

        # Restore saved state
        self.voice_auth.last_unknown_embedding = saved_emb
        self.voice_auth.last_unknown_ts = saved_ts

        info(f"[ADMIN_GATE] role={role}, sim={sim:.3f}, confidence={confidence}")

        if role == "Admin":
            return True

        # Not an admin — deny
        ui_state("ACCESS_DENIED")
        try:
            self.voice.play_access_denied_beep()
            self.voice.tts_blocking(deny_message, timeout_sec=5.0)
            self._deafen_after_speak()
        except Exception as e:
            warn(f"[ADMIN_GATE] TTS error: {e}")
        time.sleep(0.5)
        ui_state("IDLE")
        return False

    def _voice_auth_grant(self, text: str, precomputed_emb=None):
        """Detect and handle grant-access / revoke / list-users voice commands.

        Returns:
            True  – grant command processed successfully
            False – grant command recognised but failed
            None  – text was NOT a grant command (caller should proceed normally)
        """
        t = " ".join((text or "").strip().lower().split())
        info(f"[GRANT_HANDLER] received text='{t}'")

        # Reuse the embedding extracted once in _safe_process_command
        _grant_emb = precomputed_emb

        # ── "list users" ──────────────────────────────────────────────
        if t in ("list users", "list user"):
            users = self.voice_auth.list_users()
            if not users:
                ui_state("SPEAKING")
                self.voice.tts_blocking("No one is registered yet.")
                ui_state("IDLE")
                self._deafen_after_speak()
                return True
            parts = []
            for u in users:
                parts.append(f"{u['label']}, {u['role']}")
            ui_state("SPEAKING")
            self.voice.tts_blocking(f"Registered users: {'; '.join(parts)}.")
            ui_state("IDLE")
            self._deafen_after_speak()
            return True

        # ── "revoke access" / "revoke" ────────────────────────────────
        if t in ("revoke access", "revoke"):
            info(f"[REVOKE] audio_buffer len={len(self._last_audio_buffer)}")
            if not self._require_admin_voice(
                precomputed_emb=_grant_emb,
                deny_message="Access denied. Only an admin can revoke access.",
            ):
                return False
            success, detail = self.voice_auth.revoke_last()
            info(f"[REVOKE] revoke_last: success={success}, detail={detail}")
            ui_state("SPEAKING")
            if success:
                self.voice.tts_blocking(f"Done. Revoked access for {detail}.")
                self._emit_voice_users_list()
            else:
                self.voice.tts_blocking("There's no one to revoke right now.")
            ui_state("IDLE")
            self._deafen_after_speak()
            return True

        # ── "delete user <label>" / "remove user <label>" ────────────
        del_label = _parse_delete_user_command(text)
        if del_label is not None:
            if not self._require_admin_voice(
                precomputed_emb=_grant_emb,
                deny_message="Access denied. Only an admin can delete users.",
            ):
                return False
            success, msg = self.voice_auth.delete_user(del_label)
            if success:
                self._emit_voice_users_list()
            ui_state("SPEAKING")
            if success:
                self.voice.tts_blocking(f"Done. {msg}")
            else:
                self.voice.tts_blocking(msg)
            ui_state("IDLE")
            self._deafen_after_speak()
            return success

        # ── "grant access" / "grant <role> access" ──────────────────────
        cmd = _parse_grant_command(text)
        # Also accept bare "grant access" (no role parsed)
        if cmd is None:
            bare = " ".join((text or "").strip().lower().split())
            if bare not in ("grant access",):
                return None  # Not a grant command

        # ── Admin check ──────────────────────────────────────────────
        if not self._require_admin_voice(
            precomputed_emb=_grant_emb,
            deny_message="Access denied. Only an admin can grant access.",
        ):
            return False

        # ── Step 1: Ask role ─────────────────────────────────────────
        grant_role = cmd["role"] if cmd else None
        if grant_role is None:
            ui_state("SPEAKING")
            self.voice.play_or_tts("user_or_admin", "User or admin?")
            ui_state("GRANT_ROLE")
            self._deafen_after_speak()
            for _attempt in range(3):
                role_reply = self.listen_command_vosk(
                    max_seconds=8,
                    min_listen_ms=2000,
                    ui_state_label="GRANT_ROLE",
                    use_grammar=True,
                    speak_on_empty=False,
                    grammar_phrases=GRANT_ROLE_GRAMMAR,
                )
                if role_reply:
                    rt = " ".join(role_reply.strip().lower().split())
                    if rt in ("cancel", "stop", "never mind"):
                        ui_state("SPEAKING")
                        self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                        ui_state("IDLE")
                        self._deafen_after_speak()
                        return False
                    for candidate in ("admin", "user", "guest"):
                        if candidate in rt:
                            grant_role = candidate.capitalize()
                            break
                    if grant_role:
                        break
                if _attempt < 2:
                    ui_state("SPEAKING")
                    self.voice.play_or_tts("user_or_admin", "Say user or admin.")
                    ui_state("GRANT_ROLE")
                    self._deafen_after_speak()
            if grant_role is None:
                ui_state("SPEAKING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                ui_state("IDLE")
                self._deafen_after_speak()
                return False

        # ── Step 2: Ask duration ─────────────────────────────────────
        ui_state("SPEAKING")
        self.voice.play_or_tts("by_how_much", "How long? Say minutes, hours, or permanent.")
        ui_state("GRANT_DURATION")
        self._deafen_after_speak()
        duration_minutes = None
        for _attempt in range(3):
            dur_reply = self.listen_command_vosk(
                max_seconds=8,
                min_listen_ms=2000,
                ui_state_label="GRANT_DURATION",
                use_grammar=True,
                speak_on_empty=False,
                grammar_phrases=GRANT_DURATION_GRAMMAR,
            )
            if dur_reply:
                dt = " ".join(dur_reply.strip().lower().split())
                if dt in ("cancel", "stop", "never mind"):
                    ui_state("SPEAKING")
                    self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                    ui_state("IDLE")
                    self._deafen_after_speak()
                    return False
                duration_minutes = parse_grant_duration_minutes(dt)
                if duration_minutes is not None:
                    break
            if _attempt < 2:
                ui_state("SPEAKING")
                self.voice.play_or_tts(
                    "by_how_much",
                    "Say a duration. For example, thirty minutes, two hours, or permanent.",
                )
                ui_state("GRANT_DURATION")
                self._deafen_after_speak()
        if duration_minutes is None:
            ui_state("SPEAKING")
            self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
            ui_state("IDLE")
            self._deafen_after_speak()
            return False

        # ── Step 3: Confirm ──────────────────────────────────────────
        is_permanent = (duration_minutes == -1)
        if is_permanent:
            summary = f"Grant {grant_role} access permanently?"
        elif duration_minutes >= 1440 and duration_minutes % 1440 == 0:
            days = duration_minutes // 1440
            summary = f"Grant {grant_role} access for {days} {'day' if days == 1 else 'days'}?"
        elif duration_minutes >= 60 and duration_minutes % 60 == 0:
            hours = duration_minutes // 60
            summary = f"Grant {grant_role} access for {hours} {'hour' if hours == 1 else 'hours'}?"
        elif duration_minutes > 60:
            hours = duration_minutes // 60
            mins = duration_minutes % 60
            summary = f"Grant {grant_role} access for {hours} {'hour' if hours == 1 else 'hours'} and {mins} minutes?"
        else:
            summary = f"Grant {grant_role} access for {duration_minutes} minutes?"
        ui_state("SPEAKING")
        self.voice.tts_blocking(summary)
        # Use the same robot "Are you sure?" clip as other confirmations
        self._speak_confirm_prompt()
        for _attempt in range(3):
            reply = self._listen_confirm_reply()
            decision = self._classify_confirm_reply(reply or "")
            if decision == "yes":
                break
            if decision == "no":
                ui_state("SPEAKING")
                self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
                ui_state("IDLE")
                self._deafen_after_speak()
                return False
            if _attempt < 2:
                self._speak_confirm_retry()
        else:
            # Three attempts with no clear answer
            ui_state("SPEAKING")
            self.voice.play_or_tts("confirm_cancelled", "Cancelled.")
            ui_state("IDLE")
            self._deafen_after_speak()
            return False

        # ── Execute grant ────────────────────────────────────────────
        ui_state("EXECUTING")
        expires_at = None if is_permanent else time.time() + (duration_minutes * 60)
        success = self.voice_auth.grant_access(
            role=grant_role,
            expires_at=expires_at,
        )
        if success:
            self._emit_voice_users_list()
        ui_state("SPEAKING")
        if success:
            self.voice.play_or_tts("grant_done", f"Done. {grant_role} access granted.")
        else:
            self.voice.tts_blocking("There's no unknown speaker to grant access to. They need to speak first.")
        ui_state("IDLE")
        self._deafen_after_speak()
        return success

    # ── Original safe-process (now with RBAC gate) ─────────────────────────

    def _safe_process_command(self, text: str) -> bool:
        cmd_start = time.time()
        phases: dict[str, float] = {}
        success = False
        dbg = get_debug_logger()

        # ── Custom mode: bypass RBAC entirely ────────────────────────────
        t_check = " ".join((text or "").strip().lower().split())
        self._debug_file_log(f"_safe_process_command received: '{t_check}'")
        if t_check in CUSTOM_MODE_ON_PHRASES or t_check in CUSTOM_MODE_OFF_PHRASES:
            info(f"[CustomMode] _safe_process_command: matched '{t_check}', bypassing RBAC")
            self._debug_file_log(f"CUSTOM_MODE matched! Calling process_command...")
            try:
                success = self.process_command(text)
                self._debug_file_log(f"process_command returned: {success}")
                return success
            except Exception as e:
                import traceback
                self._debug_file_log(f"process_command EXCEPTION: {e}")
                self._debug_file_log(f"process_command traceback: {traceback.format_exc()}")
                error(f"[CustomMode] process_command failed: {e}")
                ui_state("IDLE")
                return False
            finally:
                self._reset_recognition_after_command()
                total_ms = (time.time() - cmd_start) * 1000
                dbg.log_command(text, total_ms, success, phases)

        # ── Shared RBAC + grant/revoke/execute path ────────────────────
        # Works for both voice_id enabled AND disabled.
        # _require_admin_voice() transparently bypasses the voice check
        # when voice_id is off, so grant/revoke commands still work.
        try:
            _shared_emb = None

            # When voice_id is ON, enforce admin enrollment and extract
            # the speaker embedding once for the entire RBAC flow.
            if self.voice_id_enabled:
                if not self.voice_auth.has_admin():
                    ui_state("SPEAKING")
                    try:
                        self.voice.tts_blocking(
                            "Voice ID is enabled but no administrator is enrolled. "
                            "Please use the Enroll Voice button in settings first."
                        )
                        self._deafen_after_speak()
                    except Exception:
                        pass
                    ui_state("IDLE")
                    total_ms = (time.time() - cmd_start) * 1000
                    dbg.log_command(text, total_ms, False, {"reason": "no_admin_enrolled"})
                    return False

                # Extract embedding once — reused by grant, RBAC, etc.
                t0 = time.time()
                _shared_emb = self._collect_async_embedding(timeout=1.0)
                if _shared_emb is None:
                    _shared_emb = self.voice_auth.extract_embedding(self._last_audio_buffer)
                phases["embedding_extract"] = (time.time() - t0) * 1000

            # ── 1. Grant-command detection (before general auth) ─────────
            # Runs regardless of voice_id state — _require_admin_voice
            # inside _voice_auth_grant handles the bypass when disabled.
            t0 = time.time()
            grant_result = self._voice_auth_grant(text, precomputed_emb=_shared_emb)
            phases["voice_auth_grant"] = (time.time() - t0) * 1000
            if grant_result is not None:
                success = bool(grant_result)
                total_ms = (time.time() - cmd_start) * 1000
                dbg.log_command(text, total_ms, success, phases)
                return success

            # ── 2. RBAC check (only when voice_id is on) ─────────────────
            if self.voice_id_enabled:
                local_intent = self._resolve_intent_locally(text)
                t0 = time.time()
                if not self._voice_auth_check(text, intent=local_intent, precomputed_emb=_shared_emb):
                    phases["voice_auth_check"] = (time.time() - t0) * 1000
                    total_ms = (time.time() - cmd_start) * 1000
                    dbg.log_command(text, total_ms, False, phases)
                    return False
                phases["voice_auth_check"] = (time.time() - t0) * 1000

            # ── 3. Execute the command ───────────────────────────────────
            t0 = time.time()
            success = self.process_command(text)
            phases["execute"] = (time.time() - t0) * 1000
            return success
        except Exception as e:
            ui_state("ERROR")
            error(f"Command failed: {e}")
            try:
                if not self.voice.muted:
                    self.voice.play_or_tts("exec_error", "Sorry, something went wrong")
                    self._deafen_after_speak()
            except Exception:
                pass
            ui_state("IDLE")
            self.history.break_chain()
            return False
        finally:
            self._reset_recognition_after_command()
            total_ms = (time.time() - cmd_start) * 1000
            dbg.log_command(text, total_ms, success, phases)

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
        # Use fire-and-forget to avoid blocking startup if pyttsx3
        # COM/SAPI engine hangs on runAndWait() (common on some Windows
        # configs).
        self.voice.tts_fire_and_forget("Aidy is ready")

        try:
            self.start_stream()

            # ── Startup self-trigger guard ──────────────────────────────
            # The TTS "Aidy is ready" plays through speakers and leaks
            # back into the microphone.  Vosk recognises "aidy" from this
            # echo and immediately fires a false wake trigger.
            #
            # Fix: sleep long enough for TTS playback + echo to finish,
            # then flush any audio frames the mic accumulated during that
            # window and create a *fresh* wake recognizer with no history.
            # This guarantees the first recognition cycle starts from
            # clean silence, not from stale TTS echo data.
            time.sleep(1.2)
            self._flush_audio(200)
            self.wake_recognizer = self._new_wake_recognizer()
            # Short additional deafen to cover any late reverb / tail echo
            self._deafen_until = time.time() + 0.15

            if self.push_to_talk_enabled and not self._ptt_listening_flag:
                self._pause_audio_capture()
                ui_state("IDLE")
            else:
                ui_state("LISTENING")

            while True:
                self._refresh_runtime_config_if_needed()
                self._drain_control_commands()
                if self._pending_admin_enrollment:
                    self._pending_admin_enrollment = False
                    self._run_admin_enrollment()
                    continue
                if not self._is_listening_allowed():
                    if self.window_switch_active:
                        self.end_window_switch(cancel=True)
                    self._handle_due_tasks()
                    ui_state("IDLE")
                    # Block on control queue instead of busy-polling.
                    # 0.5s is responsive enough — PTT key press triggers
                    # start_listening which resumes audio immediately.
                    try:
                        cmd = self._control_queue.get(timeout=0.5)
                        try:
                            self._handle_control_command(cmd)
                        except Exception as e:
                            warn(f"Control command failed '{cmd}': {e}")
                    except queue.Empty:
                        pass
                    continue

                with self._study_lock:
                    restore_pending = bool(self.study_restore_prompt_pending)
                if restore_pending:
                    self._maybe_offer_restore_workspace()
                    continue

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

                pending = self.follow_up.get_pending()
                if pending:
                    self._handle_due_tasks()
                    is_rephrase = pending.pending_type == PENDING_NEED_TARGET
                    is_timer_pending = pending.pending_type == PENDING_NEED_TIMER_DURATION
                    is_song_pending = pending.pending_type == PENDING_NEED_SONG
                    is_numeric_pending = pending.pending_type in (PENDING_NEED_STEPS, PENDING_NEED_CHOICE)
                    # Use focused numeric grammar for step/choice followups
                    # to improve Vosk accuracy for short words like "one", "two".
                    followup_grammar = None
                    if is_numeric_pending:
                        followup_grammar = NUMERIC_FOCUSED_GRAMMAR
                    cmd_text = self.listen_command_vosk(
                        max_seconds=(10 if is_timer_pending else (12 if is_rephrase else (10 if is_song_pending else 8))),
                        min_listen_ms=(600 if is_timer_pending else (400 if is_numeric_pending else 800)),
                        ui_state_label=("LISTENING" if is_rephrase else "FOLLOWUP"),
                        use_grammar=(pending.pending_type not in (PENDING_NEED_TIMER_DURATION, PENDING_NEED_TARGET, PENDING_NEED_SONG)),
                        grammar_phrases=followup_grammar,
                    )
                    if cmd_text:
                        self._safe_process_command(cmd_text)
                    else:
                        if is_timer_pending:
                            attempts = self.follow_up.register_invalid_attempt()
                            if attempts < 3:
                                self._cmd_log(
                                    f"timer pending empty attempt={attempts}; reprompt duration"
                                )
                                ui_state("SPEAKING")
                                self.voice.play_or_tts("timer_how_long", "For how long?")
                                self._deafen_after_speak()
                                ui_state("IDLE")
                                continue
                        self.follow_up.clear_pending()
                        ui_state("IDLE")
                    continue

                if self.follow_mode.is_active():
                    self._handle_due_tasks()
                    cmd_text = self.listen_command_smart(
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
                            self._play_wake_ack()
                            if tail:
                                self._safe_process_command(tail)
                            else:
                                cmd2 = self.listen_command_smart(max_seconds=20)
                                if cmd2:
                                    self._safe_process_command(cmd2)
                                else:
                                    ui_state("IDLE")
                            continue
                        self._safe_process_command(cmd_text)
                    else:
                        ui_state("IDLE")
                    continue

                wake_tail = self.wait_for_wake()
                self._handle_due_tasks()
                if wake_tail == "__CONTROL_BREAK__":
                    # Re-enter top of loop where _pending_admin_enrollment is checked.
                    continue
                if wake_tail == "__STUDY_RESTORE_PROMPT__":
                    self._maybe_offer_restore_workspace()
                    continue
                if wake_tail == self.PTT_PAUSED_TOKEN:
                    ui_state("IDLE")
                    continue
                if wake_tail:
                    if self._is_non_action_utterance(wake_tail):
                        self._cmd_log(f"wake tail ignored non-action='{wake_tail}'")
                        self._debug_file_log(f"WAKE_TAIL non-action dropped: '{wake_tail}'")
                        ui_state("IDLE")
                        continue
                    self._cmd_log(f"wake tail='{wake_tail}'")
                    self._debug_file_log(f"WAKE_TAIL processing: '{wake_tail}'")
                    self._safe_process_command(wake_tail)
                    ui_state("IDLE")
                    continue

                if not self._is_listening_allowed():
                    self._debug_file_log("LISTENING not allowed, going IDLE")
                    ui_state("IDLE")
                    continue

                # Start embedding extraction in parallel with command listening
                # so RBAC check doesn't add 100-300ms after the command is heard.
                self._start_async_embedding()

                cmd_text = self._listen_post_wake_command(max_attempts=2)
                self._debug_file_log(f"POST_WAKE result: '{cmd_text}'")
                if cmd_text == "__SKIP_WAKE_COMMAND__":
                    self._cmd_log("post-wake aborted; back to wake mode")
                    self._speak_not_heard()
                    ui_state("IDLE")
                    continue
                if cmd_text:
                    self._debug_file_log(f"EXECUTING command: '{cmd_text}'")
                    self._safe_process_command(cmd_text)
                    # Safety: always return to IDLE after command execution
                    ui_state("IDLE")
                else:
                    self._cmd_log("post-wake empty; back to wake mode")
                    self._speak_not_heard()
                    ui_state("IDLE")
                continue

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
            if self.audio is not None:
                self.audio.terminate()
            info("AIDY stopped")









