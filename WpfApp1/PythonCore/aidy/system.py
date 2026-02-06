import ctypes
import subprocess
import re

import pyautogui

try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    PYCaw_OK = True
except Exception:
    PYCaw_OK = False


def parse_first_int(text: str) -> int | None:
    m = re.search(r"\b(\d{1,3})\b", text or "")
    if not m:
        return None
    v = int(m.group(1))
    return max(0, min(100, v))


def set_volume_percent(p: int) -> bool:
    if not PYCaw_OK:
        return False
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(p / 100.0, None)
        return True
    except Exception:
        return False


def volume_steps(up: bool, steps: int):
    key = "volumeup" if up else "volumedown"
    for _ in range(max(1, steps)):
        pyautogui.press(key)


def show_desktop():
    try:
        VK_LWIN = 0x5B
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(VK_LWIN, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x44, 0, KEYEVENTF_KEYUP, 0)
        ctypes.windll.user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        pass

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(New-Object -ComObject Shell.Application).MinimizeAll()"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
        return True
    except Exception:
        pass

    try:
        pyautogui.hotkey("win", "d")
        return True
    except Exception:
        return False


def open_cmd_new_console(keep_open: bool = True, cmdline: str | None = None):
    args = ["cmd.exe", "/K" if keep_open else "/C"]
    if cmdline:
        args.append(cmdline)
    subprocess.Popen(
        args,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


def run_powershell_hidden(ps_command: str):
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False
    )


def take_screenshot():
    try:
        VK_SNAPSHOT = 0x2C
        VK_LWIN = 0x5B
        KEYEVENTF_KEYUP = 0x0002

        ctypes.windll.user32.keybd_event(VK_LWIN, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_SNAPSHOT, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_SNAPSHOT, 0, KEYEVENTF_KEYUP, 0)
        ctypes.windll.user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        pass

    try:
        pyautogui.hotkey("win", "prtsc")
        return True
    except Exception:
        return False


def open_task_manager():
    try:
        subprocess.Popen(
            ["taskmgr.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        return True
    except Exception:
        pass

    try:
        pyautogui.hotkey("ctrl", "shift", "esc")
        return True
    except Exception:
        return False


def get_active_window_info() -> dict | None:
    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        title = title_buf.value or ""

        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = int(pid.value)
        if pid_val <= 0:
            return None

        ps_cmd = f"(Get-Process -Id {pid_val}).ProcessName"
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            stderr=subprocess.DEVNULL,
            text=True
        )
        proc = (out or "").strip()
        if not proc:
            return None

        return {"pid": pid_val, "process": proc, "title": title}
    except Exception:
        return None
