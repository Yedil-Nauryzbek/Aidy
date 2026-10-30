import os
import json
import time
import subprocess
import re
import winreg
from urllib.parse import urlparse

from .logui import info, warn
from .system import get_active_window_info, close_active_tab, switch_to_previous_window


DEFAULT_BROWSER_PROCESSES = (
    "opera.exe",
    "msedge.exe",
    "chrome.exe",
    "firefox.exe",
    "brave.exe",
    "browser.exe",
)

GENERIC_BROWSER_ALIASES = {'browser', 'the browser', 'my browser', 'web browser', 'default browser'}


def default_browser_app():
    """Resolve the user's HTTPS association, not a hard-coded Chrome alias."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice') as key:
            progid = winreg.QueryValueEx(key, 'ProgId')[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\shell\open\command') as key:
            command = os.path.expandvars(winreg.QueryValueEx(key, '')[0])
        match = re.match(r'^\s*"([^"]+\.exe)"|^\s*([^\s]+\.exe)', command, re.I)
        if not match:
            return None
        executable = match.group(1) or match.group(2)
        process = os.path.basename(executable).lower()
        if process not in DEFAULT_BROWSER_PROCESSES or not os.path.isfile(executable):
            return None
        return dict(id='browser', type='exe', aliases=sorted(GENERIC_BROWSER_ALIASES),
                    target=executable, process=process, args=[], browser_processes=[])
    except OSError:
        return None


def extract_app_name(text: str) -> str:
    t = (text or "").strip().lower()
    t = " ".join(t.split())
    prefixes = (
        "open up ",
        "open ",
        "launch ",
        "start ",
        "run ",
        "go to ",
        "go ",
        "visit ",
        "show ",
    )
    for p in prefixes:
        if t.startswith(p):
            return t[len(p):].strip()
    return t



def _normalize_app_text(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""
    t = re.sub(r"[^a-z0-9а-яё ]+", " ", t)
    return " ".join(t.split())


def load_apps_config(base_dir: str):
    path = os.path.join(base_dir, "apps.json")
    if not os.path.exists(path):
        warn("apps.json not found рядом с Aidy.py. App launching disabled.")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        apps = cfg.get("apps", []) or []
        out = []
        for a in apps:
            app_id = str(a.get("id") or "").strip().lower()
            a_type = str(a.get("type") or "").strip().lower()

            aliases = a.get("aliases") or []
            aliases = [str(x).strip().lower() for x in aliases if str(x).strip()]

            target = str(a.get("target") or "").strip()
            target = os.path.expandvars(target)

            args = a.get("args") or []
            args = [os.path.expandvars(str(x)) for x in args]

            proc = str(a.get("process") or "").strip()
            proc = os.path.expandvars(proc)

            browser_processes = a.get("browser_processes") or []
            browser_processes = [str(x).strip().lower() for x in browser_processes if str(x).strip()]
            browser_processes = [p if p.endswith(".exe") else (p + ".exe") for p in browser_processes]

            if not app_id or not a_type or not aliases or not target:
                continue

            out.append({
                "id": app_id,
                "type": a_type,
                "aliases": aliases,
                "target": target,
                "args": args,
                "process": proc,
                "browser_processes": browser_processes,
            })

        browser = default_browser_app()
        if browser is not None:
            for app in out:
                app['aliases'] = [alias for alias in app['aliases'] if alias not in GENERIC_BROWSER_ALIASES]
            # An explicit generic entry is updated to the same default used for
            # opening URLs; named Chrome/Edge entries retain their own targets.
            out = [app for app in out if app['id'] != 'browser']
            out.append(browser)
            info(f"Default browser target: {browser['process']}")
        info(f"Apps loaded: {len(out)} (apps.json)")
        return out

    except Exception as e:
        warn(f"apps.json read failed: {e}")
        return []


def extract_close_app_name(text: str) -> str:
    t = (text or "").strip().lower()
    t = " ".join(t.split())
    prefixes = ("close ", "quit ", "exit ", "kill ", "stop ")
    for p in prefixes:
        if t.startswith(p):
            return t[len(p):].strip()
    return t


def find_app(apps: list, name: str):
    q = _normalize_app_text(name)
    if not q:
        return None

    q_compact = q.replace(" ", "")

    # Match the complete target. Substring matching made Discord's alias "ds"
    # match "closedsteam" and opened Discord for "closed steam".
    matches = []
    for app in apps:
        aliases = [_normalize_app_text(x) for x in
                   [app.get("id") or "", *(app.get("aliases") or [])]]
        if any(alias and (q == alias or q_compact == alias.replace(" ", ""))
               for alias in aliases):
            matches.append(app)
    return matches[0] if len(matches) == 1 else None


def normalize_app_command(text: str, apps: list) -> str:
    """Correct a command verb only when its complete app target is known."""
    text = " ".join((text or "").lower().split()).strip(".!?, ")
    candidate = re.sub(r"^(?:(?:can|could|would) you |please )+", "", text)
    candidate = re.sub(r"(?: please| for me| now)+$", "", candidate)
    match = re.fullmatch(r"(open up|open|launch|start|run|go to|visit|show|close|closed|clothes|cloze|quit|exit|kill|stop) (.+)", candidate)
    if not match:
        return text
    verb, target = match.groups()
    app = find_app(apps, target) or find_app(apps, re.sub(r"^(?:the|my) ", "", target))
    if app is None:
        return text
    action = "close" if verb in {"close", "closed", "clothes", "cloze", "quit", "exit", "kill", "stop"} else "open"
    return f"{action} {app['id']}"


def launch_app(app: dict) -> bool:
    a_type = (app.get("type") or "").lower()
    target = (app.get("target") or "").strip()
    args = app.get("args") or []

    if not a_type or not target:
        return False

    try:
        if a_type == "exe":
            if (":\\" in target or target.startswith("\\\\")) and not os.path.exists(target):
                return False
            subprocess.Popen([target, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True

        if a_type in ("lnk", "shell", "url", "folder"):
            os.startfile(target)
            return True

        return False
    except Exception:
        return False


def close_app_by_process(proc_name: str, force: bool = False) -> bool:
    proc_name = (proc_name or "").strip().strip('"')
    if not proc_name:
        return False
    try:
        args = ["taskkill", "/IM", proc_name]
        if force:
            args.append("/F")
        r = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return r.returncode == 0
    except Exception:
        return False


def _url_target_matches_title(app: dict, title: str) -> bool:
    title = (title or "").strip().lower()
    if not title:
        return False

    app_id = (app.get("id") or "").strip().lower()
    keywords = []

    if app_id == "youtube":
        keywords.extend(["youtube", "you tube", "yt"])
    elif app_id == "gpt":
        keywords.extend(["chatgpt", "chat gpt", "gpt"])

    target = (app.get("target") or "").strip().lower()
    if target:
        host = (urlparse(target).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        for part in host.split("."):
            part = part.strip()
            if part and part not in ("com", "org", "net", "www"):
                keywords.append(part)

    for alias in (app.get("aliases") or []):
        a = str(alias).strip().lower()
        if len(a) >= 3:
            keywords.append(a)

    for k in keywords:
        if k and k in title:
            return True
    return False


def _collect_url_keywords(app: dict) -> list[str]:
    keywords = []

    app_id = (app.get("id") or "").strip().lower()
    if app_id == "youtube":
        keywords.extend(["youtube", "you tube", "yt"])
    elif app_id == "gpt":
        keywords.extend(["chatgpt", "chat gpt", "gpt"])

    target = (app.get("target") or "").strip().lower()
    if target:
        host = (urlparse(target).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        for part in host.split("."):
            part = part.strip()
            if part and part not in ("com", "org", "net", "www"):
                keywords.append(part)

    for alias in (app.get("aliases") or []):
        a = str(alias).strip().lower()
        if len(a) >= 3:
            keywords.append(a)

    out = []
    for k in keywords:
        if k and k not in out:
            out.append(k)
    return out


def _is_browser_process(name: str, processes: list[str]) -> bool:
    p = (name or "").strip().lower()
    if p and not p.endswith(".exe"):
        p += ".exe"
    return p in processes


def _taskkill_by_title(process: str, keyword: str, force: bool = False) -> bool:
    p = (process or "").strip()
    k = (keyword or "").strip()
    if not p or not k:
        return False
    try:
        cmd = [
            "taskkill",
            "/FI", f"IMAGENAME eq {p}",
            "/FI", f"WINDOWTITLE eq *{k}*",
        ]
        if force:
            cmd.append("/F")
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return r.returncode == 0
    except Exception:
        return False


def close_app(app: dict) -> bool:
    a_type = (app.get("type") or "").strip().lower()
    if a_type == "url":
        extra = [x for x in (app.get("browser_processes") or []) if x]

        processes = []
        for p in [*extra, *DEFAULT_BROWSER_PROCESSES]:
            q = str(p).strip().lower()
            if not q:
                continue
            if not q.endswith(".exe"):
                q += ".exe"
            if q not in processes:
                processes.append(q)

        keywords = _collect_url_keywords(app)
        active = get_active_window_info() or {}
        active_proc = (active.get("process") or "").strip().lower()
        active_title = (active.get("title") or "").strip().lower()

        if _is_browser_process(active_proc, processes) and _url_target_matches_title(app, active_title):
            return close_active_tab()

        if switch_to_previous_window():
            time.sleep(0.18)
            active2 = get_active_window_info() or {}
            active2_proc = (active2.get("process") or "").strip().lower()
            active2_title = (active2.get("title") or "").strip().lower()
            if _is_browser_process(active2_proc, processes) and _url_target_matches_title(app, active2_title):
                return close_active_tab()

        for p in processes:
            for k in keywords:
                if _taskkill_by_title(p, k, force=False):
                    time.sleep(0.12)
                    _taskkill_by_title(p, k, force=True)
                    return True

        return False

    proc = (app.get("process") or "").strip()
    if proc:
        ok = close_app_by_process(proc, force=False)
        time.sleep(0.15)
        ok2 = close_app_by_process(proc, force=True)
        return ok or ok2

    fallback = (app.get("id") or "").strip()
    if fallback:
        ok = close_app_by_process(fallback + ".exe", force=False)
        time.sleep(0.15)
        ok2 = close_app_by_process(fallback + ".exe", force=True)
        return ok or ok2

    return False

