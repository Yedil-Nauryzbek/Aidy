"""Discover local registrations only. Launch targets never come from the model."""
import hashlib
import os
from pathlib import Path
import queue
import re
import threading
import time
import winreg


class AppCatalog:
    def __init__(self):
        self.entries = []
        self.expires = 0
        self._pending = None
        self._version = None

    @staticmethod
    def _signature():
        marks = []
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for name in (r'Software\Microsoft\Windows\CurrentVersion\App Paths',
                         r'Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages'):
                try:
                    with winreg.OpenKey(root, name) as key:
                        marks.append(winreg.QueryInfoKey(key))
                except OSError:
                    pass
        for base in (os.environ.get('APPDATA', ''), os.environ.get('PROGRAMDATA', '')):
            folder = Path(base) / 'Microsoft/Windows/Start Menu/Programs'
            try:
                marks.extend((str(path), path.stat().st_mtime_ns) for path in [folder, *list(folder.rglob('*'))[:800]] if path.is_dir())
            except OSError:
                pass
        return tuple(marks)

    @staticmethod
    def _read():
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        rows = []
        seen = set()
        def add(label, kind, target, process=''):
            if not label or target.casefold() in seen:
                return
            if re.search(r'uninstall|installer|setup|update|удалить|установка', label, re.I):
                return  # Installation/removal is not an ordinary app launch.
            if kind == 'lnk' and process in {'cmd.exe', 'powershell.exe', 'pwsh.exe', 'msiexec.exe', 'wscript.exe', 'cscript.exe'}:
                return
            seen.add(target.casefold())
            rows.append(dict(id='installed_' + hashlib.sha256(target.casefold().encode()).hexdigest()[:16],
                             display_name=label[:150], type=kind, target=target,
                             process=process, args=[], aliases=[label.casefold()], browser_processes=[]))
        try:
            shell = win32com.client.Dispatch('Shell.Application')
            try:
                for item in shell.NameSpace('shell:AppsFolder').Items():
                    appid = item.ExtendedProperty('System.AppUserModel.ID')
                    if isinstance(appid, str) and re.fullmatch(r'[\w.\-]+![\w.\-]+', appid):
                        add(item.Name, 'shell', 'shell:AppsFolder\\' + appid)
            except Exception:
                pass
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(root, r'Software\Microsoft\Windows\CurrentVersion\App Paths', 0, winreg.KEY_READ | view) as key:
                            for i in range(winreg.QueryInfoKey(key)[0]):
                                name = winreg.EnumKey(key, i)
                                try:
                                    with winreg.OpenKey(key, name) as child:
                                        target = os.path.expandvars(winreg.QueryValueEx(child, '')[0]).strip('"')
                                    if Path(target).is_absolute() and Path(target).is_file() and target.lower().endswith('.exe'):
                                        add(Path(name).stem, 'exe', target, Path(target).name.lower())
                                except OSError:
                                    pass
                    except OSError:
                        pass
            for base in (os.environ.get('APPDATA', ''), os.environ.get('PROGRAMDATA', '')):
                folder = Path(base) / 'Microsoft/Windows/Start Menu/Programs'
                for link in list(folder.rglob('*.lnk'))[:800]:
                    try:
                        shortcut = shell.NameSpace(str(link.parent)).ParseName(link.name).GetLink
                        target = shortcut.Path
                        # Only application shortcuts; exclude URL, document and script links.
                        if target.lower().endswith('.exe') and Path(target).is_file():
                            add(link.stem, 'lnk', str(link), Path(target).name.lower())
                    except Exception:
                        pass
            return rows
        finally:
            pythoncom.CoUninitialize()

    def refresh(self, force=False, timeout=1.5):
        version = self._signature()
        if not force and version == self._version and time.monotonic() < self.expires:
            return self.entries
        if self._pending is None:
            self._pending = queue.Queue(1)
            result = self._pending
            def worker():
                try:
                    result.put(self._read())
                except Exception:
                    result.put([])
            threading.Thread(target=worker, name='aidy-installed-apps', daemon=True).start()
        try:
            self.entries = self._pending.get(timeout=timeout)
            self._pending = None
            self.expires = time.monotonic() + 60
            self._version = version
        except queue.Empty:
            pass
        return self.entries

    def relevant(self, request, limit=16):
        words = set(re.findall(r'\w+', request.casefold())) - {'open', 'the', 'app', 'application', 'please', 'and', 'in'}
        ranked = sorted(self.entries, key=lambda a: -sum(len(w) for w in words if w in a['display_name'].casefold()))
        matches = [a for a in ranked if any(w in a['display_name'].casefold() for w in words)]
        # Prefer registered executables over duplicate Start Menu links.
        unique = {}
        for app in matches:
            key = (app['display_name'].casefold(), app['process'] or app['target'])
            unique.setdefault(key, app)
        return list(unique.values())[:limit]
