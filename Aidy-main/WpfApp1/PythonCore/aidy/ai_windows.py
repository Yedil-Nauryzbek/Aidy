"""Read desktop windows and close explicitly selected, frozen HWND/PID targets.

No process-wide killing: a browser hosting a preserved site keeps all its tabs.
Window titles are untrusted labels, never commands or permission evidence.
"""
import ctypes
from ctypes import wintypes
import os
import re
import time
from urllib.parse import urlparse

from .ai_tools import AIError
from .ai_engine import Result


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_user = ctypes.WinDLL('user32', use_last_error=True)
_kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel.CloseHandle.restype = wintypes.BOOL
_user.GetForegroundWindow.restype = wintypes.HWND
_user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user.IsWindow.argtypes = [wintypes.HWND]
_user.IsWindow.restype = wintypes.BOOL
_user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user.GetWindow.restype = wintypes.HWND


def owned_by(hwnd, target):
    """Only an actual native owner chain can authorize a task's expected dialog."""
    for _ in range(8):
        hwnd = int(_user.GetWindow(hwnd, 4) or 0)  # GW_OWNER
        if hwnd == target:
            return True
        if not hwnd:
            break
    return False


def process_name(pid):
    handle = _kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return ''
    try:
        size = wintypes.DWORD(32768)
        value = ctypes.create_unicode_buffer(size.value)
        if _kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size)):
            return os.path.basename(value.value).lower()
        return ''
    finally:
        _kernel.CloseHandle(handle)


def owner(hwnd):
    if not _user.IsWindow(hwnd):
        return None
    pid = wintypes.DWORD()
    _user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


class Windows:
    def __init__(self, host):
        self.host = host
        self.targets = {}
        self.active_ref = None
        self.active_app = None
        self._frozen = None
        self._reference_ids = {}
        self._next_reference = 1

    def protected(self, window):
        name = window['process']
        return (not name or window['pid'] in {os.getpid(), os.getppid()} or
                name in getattr(self.host, '_CLOSE_EVERYTHING_PROTECTED', set()) or
                name in {'explorer.exe', 'shellexperiencehost.exe', 'startmenuexperiencehost.exe',
                         'applicationframehost.exe', 'searchhost.exe'} or
                self.host._is_protected_process_for_close(name))

    def snapshot(self):
        foreground = int(_user.GetForegroundWindow() or 0)
        cache = {}
        self.targets = {}
        self.active_ref = None
        self.active_app = None
        live_keys = set()
        for item in self.host._enum_visible_real_windows()[:64]:
            pid = int(item.get('pid') or 0)
            hwnd = int(item.get('hwnd') or 0)
            if not pid or not hwnd:
                continue
            if pid not in cache:
                cache[pid] = process_name(pid)
            window = dict(item, process=cache[pid])
            if hwnd == foreground and (pid == os.getppid() or window['process'] == 'wpfapp1.exe'):
                self.active_app = 'aidy'
            # Close protection must not hide an ordinary editor from reading,
            # selection and text tasks. The close path still rechecks protection.
            if self.protected(window) and window['process'] not in {'code.exe','code - insiders.exe','systemsettings.exe'}:
                # Reading/navigating an Explorer folder must not grant authority
                # to close Explorer's desktop/taskbar process.
                import win32gui
                if window['process'] != 'explorer.exe' or win32gui.GetClassName(hwnd) not in {'CabinetWClass','ExploreWClass'}:
                    continue
            # Stable across observations; a disappearing window must not retarget
            # an old w1 reference to an unrelated new foreground application.
            key = (hwnd, pid)
            live_keys.add(key)
            if key not in self._reference_ids:
                self._reference_ids[key] = 'w' + str(self._next_reference)
                self._next_reference += 1
            ref = self._reference_ids[key]
            self.targets[ref] = window
            if hwnd == foreground:
                self.active_ref = ref
        self._reference_ids = {key: ref for key, ref in self._reference_ids.items() if key in live_keys}
        return [{'id': ref, 'process': w['process'], 'title': ' '.join(w['title'].split())[:100]}
                for ref, w in self.targets.items()][:24]

    def matches(self, window, app_id):
        app = next((a for a in self.host.apps if a['id'] == app_id), None)
        if app is None:
            return False
        process = str(app.get('process') or '').lower()
        if process and not process.endswith('.exe'):
            process += '.exe'
        # A URL's browser process is not the identity of that website.
        if app.get('type') != 'url' and process and window['process'] == process:
            return True
        names = [app_id] + list(app.get('aliases') or [])
        if app_id == 'chatgpt_app':
            from .apps import DEFAULT_BROWSER_PROCESSES
            if window['process'] in DEFAULT_BROWSER_PROCESSES:
                return False
            names += [app.get('display_name', 'ChatGPT'), 'chat gpt']
        if app.get('type') == 'url':
            domain = urlparse(app.get('target') or '').hostname or ''
            if domain:
                names.append(domain.removeprefix('www.').split('.')[0])
        if app_id in {'gpt', 'chatgpt'}:
            names += ['chatgpt', 'chat gpt']
            if window['process'] == 'chatgpt.exe':
                return True
        title = window['title'].casefold()
        return any(len(n) >= 3 and re.search(r'(?<!\w)' + re.escape(n.casefold()) + r'(?!\w)', title)
                   for n in names if isinstance(n, str))

    def bind_one(self, ref):
        if ref == '$active':
            ref = self.active_ref
        target = self.targets.get(ref)
        if not target:
            raise AIError('Which window should I close? Make it active or name the application.')
        if self.protected(target):
            raise AIError('This window is protected from closing.')
        self._frozen = [dict(target)]
        return 'close ' + ' '.join(target['title'].split())[:100]

    def bind_app(self, app_id):
        matches = [dict(w) for w in self.targets.values() if self.matches(w, app_id)]
        if not matches:
            return False
        self._frozen = matches
        return True

    def bind_all(self, exceptions, aliases):
        kept = set()
        labels = []
        for value in exceptions:
            value = aliases.get(value, value)
            if value == 'aidy':
                labels.append('Aidy')
                continue  # It is always protected from a bulk close.
            if value in self.targets:
                matches = [value]
            else:
                matches = [ref for ref, w in self.targets.items() if self.matches(w, value)]
            if not matches:
                # Never silently drop an exception and close the user's app.
                raise AIError('I could not locate the application to keep open. Make its window visible and try again.')
            kept.update(matches)
            labels.append(value)
        self._frozen = [dict(w) for ref, w in self.targets.items() if ref not in kept and not self.protected(w)]
        summary = f'close {len(self._frozen)} windows'
        if labels:
            summary += ', keeping ' + ', '.join(labels)
        return summary

    def close_frozen(self, cancel, authorized):
        windows, self._frozen = self._frozen, None
        if windows is None:
            return Result('failed', 'No confirmed window selection is available.')
        if not windows:
            return Result('verified', 'No other eligible windows are open.')
        sent, failed, already_closed = [], 0, 0
        for window in windows:
            if cancel.is_set() or not authorized():
                break
            hwnd, pid = window['hwnd'], window['pid']
            if owner(hwnd) != pid:
                if owner(hwnd) is None:
                    already_closed += 1
                    continue  # Already closed, not a reused handle.
                failed += 1
                continue
            if self.protected(window) or process_name(pid) != window['process']:
                failed += 1
                continue
            if self.host._send_wm_close(hwnd):
                sent.append(window)
            else:
                failed += 1
        deadline = time.monotonic() + .4
        remaining = sent
        while remaining and time.monotonic() < deadline and not cancel.is_set():
            remaining = [w for w in remaining if owner(w['hwnd']) == w['pid']]
            if remaining:
                time.sleep(.02)
        closed = already_closed + len(sent) - len(remaining)
        if cancel.is_set():
            return Result('cancelled', f'Cancelled after {closed} windows closed and {len(remaining)} close requests sent.')
        if failed or len(sent) + already_closed < len(windows):
            return Result('failed', f'{closed} windows closed; {len(remaining)} await the application; other windows could not be closed.')
        if remaining:
            return Result('dispatched', f'{closed} windows closed; {len(remaining)} received a close request. Check for unsaved-work prompts.')
        return Result('verified', f'{closed} windows closed.')
