"""Real Google semantic plans; optional actual app/document execution."""
import argparse
import json
from pathlib import Path
import sys
import time
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'PythonCore'))
from aidy.ai_engine import AIEngine,Result
from aidy.ai_tools import Registry
from aidy.ai_provider import GeminiProvider,AIConfig


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--desktop',action='store_true')
    parser.add_argument('--case',action='append',default=[])
    options=parser.parse_args()
    rows=[]
    if not options.desktop:
        # The model is real. OS calls and confirmations are mocked for these
        # interpretation/boundary cases; no app is opened or closed here.
        provider=GeminiProvider(AIConfig(mode='api'))
        registry=Registry(['chatgpt_app','whatsapp_web','notepad','discord','steam','gpt'])
        execute=Mock(return_value=Result('verified','Verified test effect.'))
        engine=AIEngine(provider,registry,lambda *a:True,lambda *a:True,execute)
        engine.start_agent=lambda text:None;engine.refresh_agent=lambda ref:None
        engine.confirm_batch=Mock(return_value=True)
        cases=[
            ('reported_two_apps','open the chatgpt up and then open the whatsapp',['open_app','open_app'],['chatgpt_app','whatsapp_web']),
            ('natural_two_apps','I need WhatsApp ready for a conversation, and also bring up the ChatGPT desktop app.',['open_app','open_app'],['whatsapp_web','chatgpt_app']),
            ('three_apps_exception',"First start Notepad, afterwards bring up ChatGPT and WhatsApp. Leave Steam alone.",['open_app']*3,['notepad','chatgpt_app','whatsapp_web']),
            ('negation',"Open ChatGPT and WhatsApp, but don't close anything or send any messages.",['open_app']*2,['chatgpt_app','whatsapp_web']),
            ('confirmed_close','Close Discord and then Steam.',['close_app','close_app'],['discord','steam']),
            ('amount_and_timer','Set the volume to 25 percent and then start a ten minute timer.',['set_volume','timer_start'],[]),
            ('context_open','Open ChatGPT and then WhatsApp.',['open_app','open_app'],['chatgpt_app','whatsapp_web']),
            ('context_first','Now close the first application that you opened.',['close_app'],['chatgpt_app']),
        ]
        if options.case:cases=[c for c in cases if c[0] in options.case]
        if not cases:parser.error('No matching test cases.')
        try:
            for label,request,expected,apps in cases:
                execute.reset_mock();engine.confirm_batch.reset_mock()
                started=time.perf_counter();result=engine.run(request,('context' if label.startswith('context_') else label,'Admin'))
                steps=[call.args[0] for call in execute.call_args_list]
                actual_apps=[s['arguments']['app'] for s in steps if 'app' in s['arguments']]
                passed=result.ok and [s['tool'] for s in steps]==expected and (not apps or actual_apps==apps)
                if label=='confirmed_close':passed=passed and engine.confirm_batch.call_count==1
                if label=='amount_and_timer':passed=passed and steps[0]['arguments']['value']==25 and steps[1]['arguments']['seconds']==600
                row=dict(test=label,request=request,passed=passed,status=result.status,message=result.message,
                         steps=steps,timings=dict(engine.timings),elapsed_ms=round((time.perf_counter()-started)*1000,2))
                rows.append(row);print(json.dumps(row),flush=True)
        finally:engine.close()
        suffix='semantic-api'
    else:
        import win32gui
        from aidy.ai_windows import owner
        from aidy.ai_desktop import discover_chatgpt
        from verify_document_workflows import make_runtime,close_owned_notepad
        runtime=make_runtime()
        def visible():
            values=[]
            def collect(hwnd,_):
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) and owner(hwnd):
                    values.append(dict(hwnd=hwnd,pid=owner(hwnd),title=win32gui.GetWindowText(hwnd)))
            win32gui.EnumWindows(collect,None)
            return values
        runtime.host._enum_visible_real_windows=visible
        desktop=discover_chatgpt()
        if desktop and not any(a['id']=='chatgpt_app' for a in runtime.host.apps):runtime.host.apps.append(desktop)
        runtime.engine.registry.app_ids=frozenset(a['id'] for a in runtime.host.apps)|{'browser','music','aidy'}
        runtime.engine.registry.windows=runtime.windows.snapshot()
        runtime.engine.registry.active_window=runtime.windows.active_ref
        output=ROOT/f'.validation/task-saved-{time.time_ns()}.txt'
        cases=[('reported_actual_apps','open the chatgpt up and then open the whatsapp'),
               ('draft_save_then_app',f'Open Notepad, create a new document, write a short essay about computers in 100 words, save it to "{output}", and then open the ChatGPT app.')]
        if options.case:cases=[c for c in cases if c[0] in options.case]
        try:
            for label,request in cases:
                recorded=[]
                real_execute=runtime.execute
                def execute(step):
                    result=real_execute(step)
                    recorded.append(dict(step=step,status=result.status,message=result.message,data=result.data))
                    return result
                runtime.engine.execute=execute
                runtime.engine.registry.windows=runtime.windows.snapshot()
                started=time.perf_counter();result=runtime.engine.run(request,('local','Admin'))
                tools=[r['step']['tool'] for r in recorded]
                passed=result.ok
                if label=='reported_actual_apps':
                    apps=[r['step']['arguments'].get('app') for r in recorded if r['step']['tool']=='open_app']
                    passed=passed and apps==['chatgpt_app','whatsapp_web']
                else:
                    drafts=[r for r in recorded if r['step']['tool']=='document_write' and r['status']=='verified']
                    text=drafts[-1]['step']['arguments']['text'] if drafts else ''
                    passed=passed and any((a.get('data') or {}).get('effect')=='document_saved' for a in recorded) and tools[-1]=='open_app' and output.exists() and output.read_text(encoding='utf-8-sig').replace('\r\n','\n')==text.replace('\r\n','\n')
                    if drafts and drafts[-1]['data']:close_owned_notepad(drafts[-1]['data'],text)
                row=dict(test=label,request=request,passed=passed,status=result.status,message=result.message,
                         actions=recorded,timings=dict(runtime.engine.timings),elapsed_ms=round((time.perf_counter()-started)*1000,2),
                         task_progress=runtime.engine.registry.task_progress)
                rows.append(row);print(json.dumps({k:v for k,v in row.items() if k not in {'actions','task_progress'}}),flush=True)
                (ROOT/f'.validation/task-{label}-history.json').write_text(json.dumps(list(runtime.engine.history),indent=2),encoding='utf-8')
                (ROOT/'.validation/task-desktop.json').write_text(json.dumps(dict(scope='Real Google, real Windows apps/editor readback, text input. No microphone test.',results=rows),indent=2),encoding='utf-8')
                if not passed:break
        finally:runtime.close()
        suffix='desktop'
    (ROOT/f'.validation/task-{suffix}.json').write_text(json.dumps(dict(scope='Real Google. Desktop actions are real only with --desktop; other cases mock OS actions.',results=rows),indent=2),encoding='utf-8')
    if not all(row['passed'] for row in rows):raise SystemExit(1)


if __name__=='__main__':main()
