import os
import sys
from datetime import datetime

UI_MODE = "--ui" in sys.argv

_last_ui_state: str = ""

def ui_state(name: str):
    global _last_ui_state
    if UI_MODE:
        if name == _last_ui_state:
            return
        _last_ui_state = name
        print(f"STATE:{name}", flush=True)

def ui_command(text: str):
    if UI_MODE:
        print(f"COMMAND:{text}", flush=True)

def ui_timer(event: str, remaining_seconds: int, total_seconds: int):
    if UI_MODE:
        print(
            f"TIMER:{event}:{int(max(0, remaining_seconds))}:{int(max(0, total_seconds))}",
            flush=True,
        )

def ui_study_mode(active: bool):
    if UI_MODE:
        print(f"STUDYMODE:{'on' if active else 'off'}", flush=True)

def ui_local_mode(active: bool):
    if UI_MODE:
        print(f"LOCALMODE:{'on' if active else 'off'}", flush=True)

def ui_custom_mode(active: bool):
    if UI_MODE:
        print(f"CUSTOMMODE:{'on' if active else 'off'}", flush=True)


LOG_LEVEL = os.environ.get("AIDY_LOG", "INFO").upper()
LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}

def _ts():
    return datetime.now().strftime("%H:%M:%S")

def log(level: str, msg: str):
    if LEVELS.get(level, 20) >= LEVELS.get(LOG_LEVEL, 20):
        print(f"{_ts()} [{level:<5}] {msg}", flush=True)

def debug(msg):
    log("DEBUG", msg)

def info(msg):
    log("INFO", msg)

def warn(msg):
    log("WARN", msg)

def error(msg):
    log("ERROR", msg)
