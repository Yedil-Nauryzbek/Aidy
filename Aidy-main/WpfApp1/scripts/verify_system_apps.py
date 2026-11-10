"""Opt-in real API + Windows smoke test; only fresh test documents are edited.

Never empties the user's Recycle Bin or restores/deletes their files.
Run from the project root with .venv/Scripts/python.exe scripts/verify_system_apps.py.
"""
import json
from pathlib import Path
import sys
import time
import argparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'PythonCore'))
from verify_document_workflows import make_runtime, close_owned_notepad


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--only',choices=['bin_open','notepad_edit_save','calculator'])
    selected=parser.parse_args().only
    runtime=make_runtime()
    engine=runtime.engine
    interpret=engine.provider.interpret
    def traced(*args,**kwargs):
        try:return interpret(*args,**kwargs)
        except Exception as exc:
            print('Planning error: '+type(exc).__name__+': '+str(exc),flush=True)
            raise
    engine.provider.interpret=traced
    output=ROOT/'.validation'/('system-app-document-'+str(time.time_ns())+'.txt')
    report=[]
    owned=None
    expected='Hi Aidy test'
    requests=[
        ('bin_open','Open the Recycle Bin.'),
        ('notepad_edit_save', 'Create a new Notepad document containing exactly "Hello Aidy". '
         'Append " test" to it. Replace "Hello" with "Hi" in that document. '
         'Save this same document as "'+str(output)+'".'),
        ('calculator','Calculate 2 plus 3 times 4 in Calculator.'),
    ]
    try:
        for label,text in requests:
            if selected and selected!=label:
                continue
            start=time.monotonic()
            engine.registry.windows=runtime.windows.snapshot()
            engine.registry.active_window=runtime.windows.active_ref
            result=engine.run(text,('local','Admin'))
            entry=dict(test=label,status=result.status,message=result.message,elapsed_ms=round((time.monotonic()-start)*1000),timings=engine.timings)
            if label=='notepad_edit_save':
                entry['saved_exact_text']=output.exists() and output.read_text(encoding='utf-8-sig')==expected
                entry['path']=str(output)
                ref=engine.last_window
                if ref in runtime.windows.targets:
                    owned=dict(runtime.windows.targets[ref])
            report.append(entry)
            print(json.dumps(entry,ensure_ascii=True),flush=True)
        path=ROOT/'.validation'/'system-apps-desktop.json'
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        return 0 if all(row['status']=='verified' and row.get('saved_exact_text',True) for row in report) else 1
    finally:
        if owned and output.exists() and output.read_text(encoding='utf-8-sig')==expected:
            close_owned_notepad(owned,expected)
        runtime.close()


if __name__=='__main__':
    raise SystemExit(main())
