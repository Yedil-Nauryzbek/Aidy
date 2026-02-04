import os
import sys
import socket
import time
import subprocess

import requests

from .logui import warn, error


def is_port_open(host: str, port: int, timeout=0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def start_local_intent_api(base_dir: str):
    host, port = "127.0.0.1", 8008
    if is_port_open(host, port):
        return True

    api_dir = os.path.join(base_dir, "Api")
    app_py = os.path.join(api_dir, "app.py")
    if not os.path.exists(app_py):
        warn(f"Local API not found: {app_py}")
        return False

    py = sys.executable

    try:
        subprocess.Popen(
            [py, "-m", "uvicorn", "app:app", "--host", host, "--port", str(port)],
            cwd=api_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception as e:
        warn(f"Failed to start local API: {e}")
        return False

    for _ in range(30):
        if is_port_open(host, port):
            return True
        time.sleep(0.1)

    warn("Local API did not open port 8008")
    return False


class IntentAPI:
    def __init__(self, url: str):
        self.url = url

    def get_intent(self, text: str):
        try:
            r = requests.post(self.url, json={"text": text}, timeout=5)
            if r.status_code == 200:
                return r.json()
            error(f"API error: HTTP {r.status_code}")
            return None
        except requests.exceptions.RequestException as e:
            error(f"API connection error: {e}")
            return None
