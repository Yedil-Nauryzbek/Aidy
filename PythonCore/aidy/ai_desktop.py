"""Read the installed Windows app catalog; never invent executable paths."""
import queue
import re
import threading


def discover_chatgpt(timeout=1.5):
    result = queue.Queue(maxsize=1)

    def read_catalog():
        initialized = False
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            initialized = True
            shell = win32com.client.Dispatch('Shell.Application')
            matches = []
            for item in shell.NameSpace('shell:AppsFolder').Items():
                if item.Name.strip().lower() == 'chatgpt':
                    app_id = item.ExtendedProperty('System.AppUserModel.ID')
                    if isinstance(app_id, str) and re.fullmatch(r'[A-Za-z0-9._-]+![A-Za-z0-9._-]+', app_id):
                        matches.append(app_id)
            result.put(matches[0] if len(set(matches)) == 1 else None)
        except Exception:
            result.put(None)
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    threading.Thread(target=read_catalog, name='aidy-app-catalog', daemon=True).start()
    try:
        app_id = result.get(timeout=timeout)
    except queue.Empty:
        return None
    if not app_id:
        return None
    return dict(id='chatgpt_app', type='shell', target='shell:AppsFolder\\' + app_id,
                aliases=['chatgpt desktop app', 'installed chatgpt'], display_name='ChatGPT',
                process='', args=[], browser_processes=[])
