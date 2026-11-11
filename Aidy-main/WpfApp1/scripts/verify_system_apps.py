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
    parser.add_argument('--only',choices=['bin_open','notepad_edit_save','notepad_unicode','notepad_find_read','calculator'])
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
    execute=engine.execute
    def traced_execute(step):
        try:
            result=execute(step)
            print('Action '+step['tool']+': '+result.status+' '+result.message[:200],flush=True)
            return result
        except Exception as exc:
            print('Action error '+step['tool']+': '+type(exc).__name__+': '+str(exc),flush=True)
            raise
    engine.execute=traced_execute
    prepare=engine.prepare_agent_step
    def traced_prepare(step):
        try:return prepare(step)
        except Exception as exc:
            print('Preparation error '+step['tool']+': '+type(exc).__name__+': '+str(exc)+
                  ' ref='+repr(step['arguments'].get('window'))+' last='+repr(engine.last_window),flush=True)
            raise
    engine.prepare_agent_step=traced_prepare
    output=ROOT/'.validation'/('system-app-document-'+str(time.time_ns())+'.txt')
    unicode_output=ROOT/'.validation'/('system-app-Юникод-'+str(time.time_ns())+'.txt')
    find_output=ROOT/'.validation'/('system-app-find-'+str(time.time_ns())+'.txt')
    report=[]
    owned_documents=[]
    expected_texts={'notepad_edit_save':'Hi Aidy test','notepad_unicode':'Hi "Aidy"\nLine two\nExtra 🚀',
                    'notepad_find_read':'Alpha beta gamma'}
    output_paths={'notepad_edit_save':output,'notepad_unicode':unicode_output,'notepad_find_read':find_output}
    requests=[
        ('bin_open','Open the Recycle Bin.'),
        ('notepad_edit_save', 'Create a new Notepad document containing exactly "Hello Aidy". '
         'Append " test" to it. Replace "Hello" with "Hi" in that document. '
         'Save this same document as "'+str(output)+'".'),
        ('notepad_unicode', 'Create a new Notepad document containing this exact JSON-quoted text '
         '(decode newline escapes): '+json.dumps('Hello "Aidy"\nLine two',ensure_ascii=False)+'. '
         'Append a newline followed by "Extra 🚀". Replace "Hello" with "Hi". '
         'Save this same document as "'+str(unicode_output)+'".'),
        ('notepad_find_read', 'Create a new Notepad document containing exactly "Alpha beta gamma". '
         'Find and select "beta" in it, read its text, and save this same document as "'+str(find_output)+'".'),
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
            if label in expected_texts:
                target=output_paths[label]
                expected=expected_texts[label]
                entry['saved_exact_text']=target.exists() and target.read_text(encoding='utf-8-sig')==expected
                entry['path']=str(target)
                ref=engine.last_window
                if entry['saved_exact_text'] and ref in runtime.windows.targets:
                    owned_documents.append((dict(runtime.windows.targets[ref]),expected))
            report.append(entry)
            print(json.dumps(entry,ensure_ascii=True),flush=True)
        path=ROOT/'.validation'/'system-apps-desktop.json'
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        return 0 if all(row['status']=='verified' and row.get('saved_exact_text',True) for row in report) else 1
    finally:
        for owned,expected in owned_documents:
            close_owned_notepad(owned,expected)
        runtime.close()


if __name__=='__main__':
    raise SystemExit(main())
