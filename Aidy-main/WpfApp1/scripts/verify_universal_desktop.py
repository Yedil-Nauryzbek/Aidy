"""Real Windows integration tests; creates and touches only its own UI/files.

--api additionally runs real Google planning against the isolated test window.
No microphone/speech/Voice ID claims are made by this text-driven harness.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))


def fixture():
    import win32con
    import win32gui
    instance = win32gui.GetModuleHandle(None)
    controls = {}
    def message(hwnd, msg, wp, lp):
        if msg == win32con.WM_APP + 1:
            dialog = win32gui.CreateWindow('AidyAgentValidation', 'Aidy Unexpected Dialog', win32con.WS_OVERLAPPEDWINDOW,
                                           150, 150, 300, 150, hwnd, 0, instance, None)
            controls['dialog'] = dialog
            win32gui.EnableWindow(hwnd, False)
            win32gui.ShowWindow(dialog, win32con.SW_SHOW)
            return 0
        if msg == win32con.WM_APP + 2:
            win32gui.EnableWindow(hwnd, True)
            if win32gui.IsWindow(controls.get('dialog', 0)):
                win32gui.DestroyWindow(controls['dialog'])
            return 0
        if msg == win32con.WM_COMMAND and wp & 0xffff == 102:
            text = win32gui.GetWindowText(controls['edit'])
            win32gui.SetWindowText(controls['status'], 'Applied: ' + text)
            return 0
        if msg == win32con.WM_DESTROY and hwnd == controls.get('main'):
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wp, lp)
    cls = win32gui.WNDCLASS()
    cls.hInstance = instance
    cls.lpszClassName = 'AidyAgentValidation'
    cls.lpfnWndProc = message
    cls.hbrBackground = win32con.COLOR_WINDOW + 1
    win32gui.RegisterClass(cls)
    hwnd = win32gui.CreateWindow(cls.lpszClassName, 'Aidy Agent Test', win32con.WS_OVERLAPPEDWINDOW,
                                 80, 80, 580, 300, 0, 0, instance, None)
    controls['main'] = hwnd
    controls['edit'] = win32gui.CreateWindow('EDIT', '', win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER | win32con.ES_AUTOHSCROLL,
                                            20, 30, 410, 28, hwnd, 101, instance, None)
    controls['button'] = win32gui.CreateWindow('BUTTON', 'Apply', win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.BS_PUSHBUTTON,
                                              20, 80, 100, 30, hwnd, 102, instance, None)
    controls['status'] = win32gui.CreateWindow('STATIC', 'Ready', win32con.WS_CHILD | win32con.WS_VISIBLE,
                                              20, 135, 500, 50, hwnd, 103, instance, None)
    other = win32gui.CreateWindow(cls.lpszClassName, 'Aidy Focus Test', win32con.WS_OVERLAPPEDWINDOW,
                                  700, 80, 300, 180, 0, 0, instance, None)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    win32gui.ShowWindow(other, win32con.SW_SHOW)
    print(json.dumps(dict(hwnd=hwnd, other=other, pid=os.getpid())), flush=True)
    win32gui.PumpMessages()


def own_windows(pids=None, handles=None):
    import win32gui
    import win32process
    rows = []
    def callback(hwnd, _):
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if win32gui.IsWindowVisible(hwnd) and ((pids and pid in pids) or (handles and hwnd in handles)):
            rows.append(dict(hwnd=hwnd, pid=pid, title=win32gui.GetWindowText(hwnd)))
    win32gui.EnumWindows(callback, None)
    return rows


def run(api=False, notepad=False, installed=False, browser=False):
    import win32con
    import win32gui
    from aidy.ai_automation import UIClient, DesktopTask
    from aidy.ai_catalog import AppCatalog
    from aidy.ai_engine import AIEngine, Result
    from aidy.ai_provider import AIConfig, GeminiProvider
    from aidy.ai_tools import AIError, Registry
    from aidy.ai_windows import Windows, process_name
    fixture_process = subprocess.Popen([sys.executable, '-X', 'utf8', __file__, '--fixture'], stdout=subprocess.PIPE,
                                        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    details = json.loads(fixture_process.stdout.readline())
    window = dict(hwnd=details['hwnd'], pid=details['pid'], process=process_name(details['pid']), title='Aidy Agent Test')
    other = dict(hwnd=details['other'], pid=details['pid'], process=process_name(details['pid']), title='Aidy Focus Test')
    client, cancel = UIClient(), threading.Event()
    rows = []
    def check(name, fn):
        start = time.perf_counter()
        try:
            detail = fn()
            rows.append(dict(test=name, passed=True, execution_ms=round((time.perf_counter()-start)*1000, 2), detail=detail))
        except Exception as exc:
            rows.append(dict(test=name, passed=False, execution_ms=round((time.perf_counter()-start)*1000, 2), error=str(exc)))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    def call(w, op, **kwargs):
        return client.call(w, op, cancel, lambda: None, **kwargs)
    try:
        state = call(window, 'observe')
        (ROOT / '.validation/universal-fixture-view.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
        edit = next(e for e in state['elements'] if e['control'] == 50004)
        button = next(e for e in state['elements'] if e['name'] == 'Apply')
        def input_and_invoke():
            call(window, 'focus')
            state = call(window, 'observe')
            call(window, 'set_text', element=edit['id'], view=state['view'], text='Hello Aidy')
            state = call(window, 'observe')
            assert any(e['value'] == 'Hello Aidy' for e in state['elements'])
            result = call(window, 'invoke', element=button['id'], view=state['view'])
            assert result['status'] == 'verified', result
            state = call(window, 'observe')
            assert any('Applied: Hello Aidy' in e['name'] for e in state['elements']), state
            return 'Real ValuePattern, InvokePattern and UI state readback'
        check('real_uia_text_button_chain', input_and_invoke)
        def stale():
            state = call(window, 'observe')
            call(window, 'set_text', element=edit['id'], view=state['view'], text='Changed')
            try:
                call(window, 'invoke', element=button['id'], view=state['view'])
            except AIError as exc:
                assert 'changed' in str(exc)
                return str(exc)
            raise AssertionError('Stale view was accepted')
        check('real_stale_element_view_rejected', stale)
        def focus():
            state = call(window, 'observe')
            call(other, 'focus')
            try:
                call(window, 'set_text', element=edit['id'], view=state['view'], text='MUST NOT TYPE')
            except AIError as exc:
                assert 'foreground' in str(exc)
                return str(exc)
            raise AssertionError('Input reached a window without focus')
        check('real_focus_change_stops_input', focus)
        def cancelled():
            cancel.set()
            try:
                call(window, 'focus')
            except AIError:
                return 'Cancelled before mutation'
            finally:
                cancel.clear()
            raise AssertionError('Cancelled action executed')
        check('real_cancel_stops_action', cancelled)
        def unexpected_dialog():
            call(window, 'focus')
            win32gui.SendMessage(window['hwnd'], win32con.WM_APP + 1, 0, 0)
            try:
                call(window, 'observe')
            except AIError as exc:
                assert 'dialog' in str(exc)
                return 'A real disabled owner/modal transition stopped the task'
            finally:
                win32gui.SendMessage(window['hwnd'], win32con.WM_APP + 2, 0, 0)
            raise AssertionError('Unexpected dialog was ignored')
        check('real_unexpected_dialog_stops_action', unexpected_dialog)
        catalog = AppCatalog()
        catalog.refresh(timeout=5)
        configured = {app['id'] for app in json.loads((ROOT / 'apps.json').read_text())['apps']}
        check('real_catalog', lambda: dict(total=len(catalog.entries), sources=sorted({app['type'] for app in catalog.entries}),
                                          extra_applications=sum(app['id'] not in configured for app in catalog.entries)))
        if installed:
            def open_installed():
                from aidy.apps import launch_app
                from aidy.ai_windows import owner
                app = next(a for a in catalog.entries if a['display_name'] == 'Paint' and a['type'] == 'shell')
                assert app['id'] not in configured
                previous = []
                win32gui.EnumWindows(lambda h, _: previous.append(h), None)
                assert launch_app(app)
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    candidates = []
                    win32gui.EnumWindows(lambda h, _: candidates.append(h) if h not in previous and win32gui.IsWindowVisible(h) and 'paint' in win32gui.GetWindowText(h).lower() else None, None)
                    if candidates:
                        hwnd = candidates[0]
                        pid = owner(hwnd)
                        w = dict(hwnd=hwnd, pid=pid, process=process_name(pid), title=win32gui.GetWindowText(hwnd))
                        state = call(w, 'observe')
                        assert state['elements']
                        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                        return dict(application='Paint', registration='AppsFolder', outside_apps_json=True, observed_process=w['process'])
                    time.sleep(.03)
                raise AssertionError('A new Paint window did not appear')
            check('real_installed_application_not_in_apps_json', open_installed)
        if browser:
            def browser_controls():
                from aidy.apps import default_browser_app, launch_app
                from aidy.ai_windows import owner
                from urllib.parse import urlencode
                app = default_browser_app()
                assert app and app['process'] in {'msedge.exe', 'chrome.exe', 'brave.exe'}, 'This live harness needs a Chromium browser'
                previous = []
                win32gui.EnumWindows(lambda h, _: previous.append(h), None)
                query = 'Aidy UI Automation validation'
                url = 'https://www.google.com/search?' + urlencode({'q': query})
                assert launch_app(dict(app, args=['--new-window', url]))
                deadline = time.monotonic() + 8
                target = None
                while time.monotonic() < deadline:
                    candidates = []
                    win32gui.EnumWindows(lambda h, _: candidates.append(h) if h not in previous and win32gui.IsWindowVisible(h) else None, None)
                    for hwnd in candidates:
                        pid = owner(hwnd)
                        if pid and process_name(pid) == app['process']:
                            target = dict(hwnd=hwnd, pid=pid, process=app['process'], title=win32gui.GetWindowText(hwnd))
                            break
                    if target:
                        break
                    time.sleep(.03)
                assert target, 'New test browser window unavailable'
                stage = 'focus'
                try:
                    call(target, 'focus')
                    stage = 'search URL observation'
                    deadline = time.monotonic() + 6
                    found = False
                    while time.monotonic() < deadline:
                        state = call(target, 'observe')
                        if any('google.com/search' in e['value'] and 'Aidy' in e['value'] for e in state['elements'] if e['control'] == 50004):
                            found = True
                            break
                        time.sleep(.05)
                    assert found, 'Search URL was not exposed by the browser accessibility tree'
                    stage = 'find field'
                    result = call(target, 'browser', action='find', text='Aidy')
                    assert result['status'] == 'verified', result
                    stage = 'tab observation'
                    tabs = [e for e in call(target, 'observe')['elements'] if e['control'] == 50019]
                    assert tabs, 'No accessible browser tabs'
                    return dict(browser=app['process'], search_url_verified=True, find_field_verified=True, accessible_tabs=len(tabs))
                except Exception as exc:
                    raise AssertionError(stage + ': ' + str(exc)) from None
                finally:
                    # Only the newly created test window, no user browser window.
                    if win32gui.IsWindow(target['hwnd']):
                        win32gui.PostMessage(target['hwnd'], win32con.WM_CLOSE, 0, 0)
            check('real_saved_browser_search_find_tabs', browser_controls)
        if api:
            def cloud():
                host = SimpleNamespace(base_dir=str(ROOT), apps=[], voice_id_enabled=False,
                    _stop_event=threading.Event(), stop_stream=lambda: None,
                    _enum_visible_real_windows=lambda: own_windows(pids={details['pid']}),
                    _is_protected_process_for_close=lambda p: False)
                from aidy.ai_runtime import AIRuntime
                runtime = AIRuntime(host)
                runtime.progress = lambda: None
                runtime.poll_cancel_audio = lambda: None
                runtime.config = AIConfig(mode='api', max_model_calls=12)
                provider = GeminiProvider(runtime.config)
                engine = AIEngine(provider, Registry([]), runtime.authorize, lambda *a: False, runtime.execute)
                runtime.engine = engine
                engine.start_agent = runtime.start_agent
                engine.refresh_agent = lambda ref: runtime.desktop_task.observe(ref)
                engine.action_guard = runtime.action_guard
                engine.registry.windows = runtime.windows.snapshot()
                engine.registry.active_window = runtime.windows.active_ref
                try:
                    result = engine.run('In Aidy Agent Test, enter Cloud checked into the text field, click Apply, and verify the Applied status.', ('local', 'Admin'))
                    print(json.dumps(dict(cloud_status=result.status, message=result.message, timings=engine.timings, history=list(engine.history)), ensure_ascii=False), flush=True)
                    view = runtime.uia.call(window, 'observe', threading.Event(), lambda: None)
                    assert result.status == 'verified', result
                    assert any('Applied: Cloud checked' in e['name'] for e in view['elements']), view
                    return dict(status=result.status, timings=engine.timings, real_api=True, real_windows=True,
                                speech_recognition_ms=None, feedback_audio_ms=None)
                finally:
                    runtime.close()
            check('real_google_observe_act_verify_loop', cloud)
        if notepad:
            def save_notepad():
                from aidy.ai_runtime import AIRuntime
                from aidy.apps import launch_app
                app = next(a for a in catalog.entries if a['display_name'].lower() == 'notepad' and a['type'] == 'exe')
                folder = ROOT / '.validation/universal-files'
                folder.mkdir(exist_ok=True)
                source = folder / ('input-' + str(time.time_ns()) + '.txt')
                source.write_text('', encoding='utf-8')
                all_before = []
                win32gui.EnumWindows(lambda h, _: all_before.append(h), None)
                # Packaged Notepad restores old tabs and can ignore direct MSIX
                # executable file arguments. Use its real New window menu command
                # before touching any editor, so restored user tabs stay untouched.
                existing = []
                from aidy.ai_windows import owner
                win32gui.EnumWindows(lambda h, _: existing.append(dict(hwnd=h, pid=owner(h), process='notepad.exe', title=win32gui.GetWindowText(h)))
                    if win32gui.IsWindowVisible(h) and owner(h) and process_name(owner(h)) == 'notepad.exe' and win32gui.GetWindowText(h).endswith('- Notepad') else None, None)
                if not existing:
                    assert launch_app(app)
                    deadline = time.monotonic() + 4
                    while not existing and time.monotonic() < deadline:
                        win32gui.EnumWindows(lambda h, _: existing.append(dict(hwnd=h, pid=owner(h), process='notepad.exe', title=win32gui.GetWindowText(h)))
                            if win32gui.IsWindowVisible(h) and owner(h) and process_name(owner(h)) == 'notepad.exe' and win32gui.GetWindowText(h).endswith('- Notepad') else None, None)
                        time.sleep(.03)
                current = existing[0]
                state = call(current, 'observe')
                menu = next(e for e in state['elements'] if e['automation_id'] == 'File' and e['control'] == 50011)
                call(current, 'focus')
                state = call(current, 'observe')
                call(current, 'invoke', element=menu['id'], view=state['view'])
                state = call(current, 'observe')
                new_window = next(e for e in state['elements'] if e['name'] == 'New window' and e['control'] == 50011)
                call(current, 'invoke', element=new_window['id'], view=state['view'])
                deadline = time.monotonic() + 5
                target = None
                while time.monotonic() < deadline:
                    candidates = []
                    win32gui.EnumWindows(lambda h, _: candidates.append(h) if win32gui.IsWindowVisible(h) and h not in all_before else None, None)
                    for hwnd in candidates:
                        from aidy.ai_windows import owner
                        pid = owner(hwnd)
                        if process_name(pid) == 'notepad.exe' and win32gui.GetWindowText(hwnd).endswith('- Notepad'):
                            target = dict(hwnd=hwnd, pid=pid, process='notepad.exe', title=win32gui.GetWindowText(hwnd))
                            break
                    if target:
                        break
                    time.sleep(.03)
                assert target, 'A new isolated Notepad window did not appear'
                output = folder / ('notepad-' + str(time.time_ns()) + '.txt')
                host = SimpleNamespace(base_dir=str(ROOT), apps=[app], voice_id_enabled=False,
                    _stop_event=threading.Event(), stop_stream=lambda: None,
                    _enum_visible_real_windows=lambda: own_windows(pids={target['pid']}),
                    _is_protected_process_for_close=lambda p: False)
                runtime = AIRuntime(host)
                runtime.progress = lambda: None
                runtime.poll_cancel_audio = lambda: None
                engine = SimpleNamespace(cancel_event=threading.Event(), task_deadline=time.monotonic()+30,
                                         registry=Registry([app['id']]), _check=lambda d: None, identity=('local','Admin'))
                runtime.engine = engine
                runtime.desktop_task = task = DesktopTask(runtime, f'Type Hello from Aidy and save to "{output}"')
                try:
                    task.observe('')
                    ref = next(ref for ref, w in runtime.windows.targets.items() if w['hwnd'] == target['hwnd'])
                    task.call(target, 'focus')
                    task.observe(ref)
                    doc = next(e for e in task.views[ref]['elements'] if e['control'] in {50004, 50030})
                    text = 'Hello from Aidy.\nThis is a disposable test paragraph with multiple lines.\nThe saved file must match this text exactly.'
                    typed = task.execute(dict(tool='ui_action', arguments=dict(window=ref, element=doc['id'], operation='set_text', text=text)))
                    assert typed.status == 'verified', typed
                    saved = task.execute(dict(tool='save_document', arguments=dict(window=ref, path=str(output))))
                    assert saved.status == 'verified', saved
                    assert output.read_text(encoding='utf-8-sig') == text
                    return dict(path=str(output), status=saved.status, real_uia=True)
                finally:
                    runtime.uia.close()
                    # Only our new window, only after file readback succeeded.
                    if output.exists() and win32gui.IsWindow(target['hwnd']):
                        win32gui.PostMessage(target['hwnd'], win32con.WM_CLOSE, 0, 0)
            check('real_notepad_type_save_readback', save_notepad)
        report = dict(scope='Real isolated Win32 UI and files; Google optional; no microphone or audio output.', results=rows)
        (ROOT / '.validation/universal-desktop.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        assert all(row['passed'] for row in rows), 'One or more Windows checks failed'
    finally:
        client.close()
        for hwnd in [details['hwnd'], details['other']]:
            if win32gui.IsWindow(hwnd):
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try:
            fixture_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            fixture_process.terminate()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--api', action='store_true')
    parser.add_argument('--notepad', action='store_true')
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    fixture() if args.fixture else run(api=args.api, notepad=args.notepad, installed=args.installed, browser=args.browser)
