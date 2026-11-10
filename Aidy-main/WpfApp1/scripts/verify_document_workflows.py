"""Real Google -> real Notepad/Word workflows, using only fresh test documents.

Text input, simulated yes for one test-owned close confirmation. No microphone
claim and no sending/publishing. Existing user documents are never closed.
"""
import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'PythonCore'))


def enumerate_windows(allowed_pids=None):
    import win32gui
    from aidy.ai_windows import owner, process_name
    rows = []
    def collect(hwnd, _):
        pid = owner(hwnd)
        if not pid or not win32gui.IsWindowVisible(hwnd):
            return
        process = process_name(pid)
        if (allowed_pids and pid in allowed_pids) or (not allowed_pids and process in {'notepad.exe', 'winword.exe'}):
            rows.append(dict(hwnd=hwnd, pid=pid, title=win32gui.GetWindowText(hwnd)))
    win32gui.EnumWindows(collect, None)
    return rows


def make_runtime(allowed_pids=None):
    import win32gui
    from aidy.ai_runtime import AIRuntime
    from aidy.ai_engine import AIEngine
    from aidy.ai_provider import AIConfig, GeminiProvider
    from aidy.ai_tools import Registry
    from aidy.apps import load_apps_config
    host = SimpleNamespace(base_dir=str(ROOT), apps=load_apps_config(str(ROOT)), voice_id_enabled=False,
        _stop_event=threading.Event(), stop_stream=lambda: None, voice=SimpleNamespace(muted=True),
        _enum_visible_real_windows=lambda: enumerate_windows(allowed_pids),
        _is_protected_process_for_close=lambda process: False,
        _listen_confirm_reply=lambda: 'yes', _classify_confirm_reply=lambda reply: reply,
        _deafen_after_speak=lambda: None,
        _send_wm_close=lambda hwnd: (win32gui.PostMessage(hwnd, 0x10, 0, 0) is None))
    runtime = AIRuntime(host)
    runtime.config = AIConfig(mode='api')
    runtime.progress = lambda: None
    runtime.poll_cancel_audio = lambda: None
    runtime.catalog.refresh(timeout=4)
    discovered = runtime.catalog.relevant('Notepad Word Excel')
    host.apps.extend(a for a in discovered if a['id'] not in {v['id'] for v in host.apps})
    registry = Registry([a['id'] for a in host.apps])
    registry.installed_ids = {a['id'] for a in discovered}
    registry.installed_apps = [dict(id=a['id'],name=a['display_name'],source=a['type']) for a in discovered]
    engine = AIEngine(GeminiProvider(runtime.config), registry,
                      runtime.authorize, runtime.confirm, runtime.execute)
    runtime.engine = engine
    engine.start_agent = runtime.start_agent
    engine.refresh_agent = lambda ref: runtime.desktop_task.observe(ref)
    engine.prepare_agent_step = lambda step: runtime.desktop_task.prepare(step)
    engine.action_guard = runtime.action_guard
    engine.registry.windows = runtime.windows.snapshot()
    engine.registry.active_window = runtime.windows.active_ref
    return runtime


def close_owned_notepad(window, text):
    import win32gui
    import comtypes.client
    from aidy.ai_windows import owner, owned_by
    if owner(window['hwnd']) != window['pid']:
        return
    module = comtypes.client.GetModule('UIAutomationCore.dll')
    client = comtypes.client.CreateObject(module.CUIAutomation, interface=module.IUIAutomation)
    root = client.ElementFromHandle(window['hwnd'])
    tabs = root.FindAll(4, client.CreatePropertyCondition(30003, 50019))
    editors = root.FindAll(4, client.CreatePropertyCondition(30003, 50030))
    if tabs.Length != 1 or editors.Length != 1:
        return
    actual = editors.GetElement(0).GetCurrentPattern(10014).QueryInterface(module.IUIAutomationTextPattern).DocumentRange.GetText(-1)
    if actual.replace('\r','\n').rstrip('\n') != text.rstrip('\n'):
        return
    win32gui.PostMessage(window['hwnd'], 0x10, 0, 0)
    deadline = time.monotonic()+2
    while win32gui.IsWindow(window['hwnd']) and time.monotonic()<deadline:
        candidates = [window['hwnd']]
        win32gui.EnumWindows(lambda h,_: candidates.append(h) if owner(h)==window['pid'] and owned_by(h,window['hwnd']) and win32gui.IsWindowVisible(h) else None, None)
        for hwnd in candidates:
            try:
                buttons = client.ElementFromHandle(hwnd).FindAll(4, client.CreatePropertyCondition(30003,50000))
                discard = [buttons.GetElement(i) for i in range(buttons.Length) if buttons.GetElement(i).CurrentName.casefold().replace('&','') in {"don't save",'don’t save'}]
                if len(discard)==1:
                    discard[0].GetCurrentPattern(10000).QueryInterface(module.IUIAutomationInvokePattern).Invoke()
                    break
            except Exception:
                pass
        time.sleep(.02)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',action='append',default=[])
    options=parser.parse_args()
    import win32gui
    from aidy.ai_windows import owner, process_name
    rows = []
    # Close a real isolated fixture after a context reference; never a user app.
    process = subprocess.Popen([sys.executable,'-X','utf8',str(ROOT/'scripts/verify_universal_desktop.py'),'--fixture'],
        stdout=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    fixture = json.loads(process.stdout.readline())
    runtime = make_runtime({fixture['pid']})
    try:
        target = next(ref for ref,w in runtime.windows.targets.items() if w['hwnd']==fixture['hwnd'])
        runtime.engine.identity = ('local','Admin')
        runtime.engine.context_until = time.monotonic()+300
        runtime.engine.last_app = 'notepad'
        runtime.engine.last_window = target
        result = runtime.engine.run('Close it', ('local','Admin'))
        row = dict(test='real_close_it_context', passed=result.status=='verified' and owner(fixture['hwnd']) is None,
                   status=result.status,message=result.message,timings=runtime.engine.timings)
        rows.append(row);print(json.dumps(row),flush=True)
    finally:
        runtime.close()
        if process.poll() is None:
            for key in ('hwnd','other'):
                if win32gui.IsWindow(fixture[key]):win32gui.PostMessage(fixture[key],0x10,0,0)
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.terminate()
    output = ROOT / '.validation/document-notepad-saved-essay.txt'
    if output.exists():
        output = output.with_name('document-notepad-saved-' + str(time.time_ns()) + '.txt')
    cases = [('Notepad',f'Open Notepad, create a new document, write an essay about friendship in exactly 250 words, and save it to "{output}".', 'notepad_250_word_essay_saved'),
             ('Word','Open Word, create a new document, and write an essay about reading in exactly 250 words.','word_250_word_essay'),
             ('Notepad','open a notepad and then write a text for 250 words','reported_notepad_without_topic')]
    for application, task, label in cases:
        if options.case and label not in options.case:
            continue
        runtime = make_runtime()
        try:
            started=time.perf_counter()
            result=runtime.engine.run(task,('local','Admin'))
            history=list(runtime.engine.history)
            written=[r for turn in history for r in turn.get('results',[]) if r['step']['tool']=='document_write' and r['status']=='verified']
            draft=written[-1]['step']['arguments']['text'] if written else ''
            state=written[-1].get('verified_state',{}) if written else {}
            saved_ok = output.exists() and output.read_text(encoding='utf-8-sig').replace('\r\n','\n') == draft.replace('\r\n','\n') if label=='notepad_250_word_essay_saved' else True
            from aidy.ai_writing import writing_hints, length_tolerance
            tolerance=0 if writing_hints(task).exact else length_tolerance(250)
            row=dict(test=f'real_{label}',passed=result.status=='verified' and abs(len(draft.split())-250)<=tolerance and saved_ok,
                     status=result.status,message=result.message,word_count=len(draft.split()),
                     total_ms=round((time.perf_counter()-started)*1000,2),timings=runtime.engine.timings,
                     tools=[r['step']['tool'] for turn in history for r in turn.get('results',[])],
                     verified_state=state, saved_file_verified=saved_ok if label=='notepad_250_word_essay_saved' else None)
            rows.append(row);print(json.dumps(row),flush=True)
            (ROOT/f'.validation/document-{label}-history.json').write_text(json.dumps(history,indent=2),encoding='utf-8')
            if draft:
                (ROOT/f'.validation/document-{application.lower()}-essay.txt').write_text(draft,encoding='utf-8')
            if state and application=='Notepad':
                close_owned_notepad(dict(hwnd=state['hwnd'],pid=state['pid']),draft)
        finally:
            runtime.close()
    report=dict(scope='Real Google and real Windows app actions, text driven; simulated yes only for the fixture close. No live speech test.',results=rows)
    (ROOT/'.validation/document-workflows.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if not all(row['passed'] for row in rows):raise SystemExit(1)


if __name__=='__main__':main()
