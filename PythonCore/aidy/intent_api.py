import os
import sys
import socket
import time
import subprocess
import threading
from pathlib import Path

import requests

from .logui import info, warn, error


_owned_process: subprocess.Popen | None = None
_process_lock = threading.RLock()


def _api_is_healthy(host: str, port: int) -> bool:
    """A listening port alone may belong to an unrelated service."""
    try:
        response = requests.get(f"http://{host}:{port}/", timeout=(0.3, 0.5))
        if response.status_code != 200:
            return False
        payload = response.json()
        return (
            isinstance(payload, dict)
            and payload.get("status") == "ok"
            and {"encoder_loaded", "clf_loaded", "startup_notes"}.issubset(payload)
        )
    except (requests.exceptions.RequestException, ValueError):
        return False


def stop_local_intent_api():
    """Stop only the child started by this Python process."""
    global _owned_process
    with _process_lock:
        process = _owned_process
        _owned_process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        except OSError as exc:
            warn(f"Local Intent API shutdown failed: {exc}")


def is_port_open(host: str, port: int, timeout=0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _resolve_api_dir(base_dir: str) -> str | None:
    base_dir = os.path.abspath(base_dir or ".")
    candidates = [
        os.path.join(base_dir, "Api"),
        os.path.join(base_dir, "PythonCore", "Api"),
        os.path.join(os.path.dirname(base_dir), "Api"),
    ]

    for api_dir in candidates:
        app_py = os.path.join(api_dir, "app.py")
        if os.path.exists(app_py):
            return api_dir
    return None


def start_local_intent_api(base_dir: str):
    with _process_lock:
        return _start_local_intent_api(base_dir)


def _start_local_intent_api(base_dir: str):
    global _owned_process
    host, port = "127.0.0.1", 8008
    if _api_is_healthy(host, port):
        return True

    # Retire a failed child before checking whether another service owns the
    # port. Never infer ownership from a port number or a process name.
    stop_local_intent_api()
    if is_port_open(host, port):
        warn("Port 8008 is occupied by an unavailable or unrelated service; using offline command rules.")
        return False

    api_dir = _resolve_api_dir(base_dir)
    if not api_dir:
        warn(f"Local API not found near base_dir={base_dir}")
        return False

    py = sys.executable

    try:
        env = os.environ.copy()
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        env.setdefault("HF_HUB_OFFLINE", "1")

        log_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share")) / "Aidy" / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        log_path = log_root / "intent-api.log"
        with log_path.open("ab", buffering=0) as log_file:
            log_file.write(f"\n--- Intent API startup {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
            _owned_process = subprocess.Popen(
                [py, "-u", "-m", "uvicorn", "app:app", "--host", host, "--port", str(port)],
                cwd=api_dir,
                # The desktop's stdin pipe belongs exclusively to the assistant's
                # control reader; the HTTP service must never inherit it.
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=env,
            )
        info(f"Local Intent API starting with {py}; log: {log_path}")
    except Exception as e:
        warn(f"Failed to start local API: {e}")
        return False

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if _owned_process.poll() is not None:
            warn(f"Local Intent API exited with code {_owned_process.returncode}; see {log_path}")
            stop_local_intent_api()
            return False
        if _api_is_healthy(host, port):
            info("Local Intent API ready")
            return True
        time.sleep(0.1)

    warn(f"Local Intent API did not become ready; see {log_path}")
    stop_local_intent_api()
    return False


class IntentAPI:
    def __init__(self, url: str):
        self.url = url

    def get_intent(self, text: str):
        if not (text or "").strip():
            return None
        try:
            # Keep API fallback responsive so recognition loop never "hangs" on a bad request.
            r = requests.post(self.url, json={"text": text}, timeout=(0.6, 2.5))
            if r.status_code == 200:
                result = r.json()
                if isinstance(result, dict):
                    return result
                error("API error: invalid response object")
                return None
            error(f"API error: HTTP {r.status_code}")
            return None
        except (requests.exceptions.RequestException, ValueError) as e:
            error(f"API connection error: {e}")
            return None
