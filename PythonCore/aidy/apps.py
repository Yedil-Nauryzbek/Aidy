import os
import json
import time
import subprocess

from .logui import info, warn


def extract_app_name(text: str) -> str:
    t = (text or "").strip().lower()
    t = " ".join(t.split())
    prefixes = ("open ", "launch ", "start ", "run ")
    for p in prefixes:
        if t.startswith(p):
            return t[len(p):].strip()
    return t


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

            if not app_id or not a_type or not aliases or not target:
                continue

            out.append({
                "id": app_id,
                "type": a_type,
                "aliases": aliases,
                "target": target,
                "args": args,
                "process": proc,
            })

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
    q = (name or "").strip().lower()
    q = " ".join(q.split())
    if not q:
        return None

    for a in apps:
        if q in a["aliases"]:
            return a
    for a in apps:
        if q == a["id"]:
            return a
    for a in apps:
        for al in a["aliases"]:
            if al and (al in q or q in al):
                return a
    return None


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
            cmd = f'start "" "{target}"'
            subprocess.Popen(["cmd.exe", "/C", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except Exception:
        return False


def close_app(app: dict) -> bool:
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
