import os
import sys
from datetime import datetime

UI_MODE = "--ui" in sys.argv

def ui_state(name: str):
    if UI_MODE:
        print(f"STATE:{name}", flush=True)

def ui_command(text: str):
    if UI_MODE:
        print(f"COMMAND:{text}", flush=True)


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
