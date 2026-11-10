"""Counterbalanced real Google planning and silent SAPI feedback measurements.

Uses the pre-change archive recorded in .validation/processing-backup.txt.
Only synthetic requests/catalogs go to Google; no desktop actions are executed.
"""
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from types import SimpleNamespace
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'PythonCore'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--interval', type=float, default=7)
    parser.add_argument('--case', action='append')
    parser.add_argument('--output', default='processing-api-before-after')
    options = parser.parse_args()
    backup = Path((ROOT/'.validation/processing-backup.txt').read_text(encoding='utf8'))
    previous = ROOT/'.validation/processing_before'
    with zipfile.ZipFile(backup/'source.zip') as archive:
        archive.extractall(previous)
    package = previous/'PythonCore/aidy'
    spec = importlib.util.spec_from_file_location('aidy_processing_before', package/'__init__.py',
                                                submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    modules = {label: {part: importlib.import_module(prefix+'.'+part) for part in ('ai_provider','ai_tools','ai_engine')}
               for label,prefix in [('before','aidy_processing_before'),('after','aidy')]}
    apps = json.loads((ROOT/'apps.json').read_text(encoding='utf-8-sig'))['apps']
    ids = [a['id'] for a in apps]+['chatgpt_app']
    names = {a['id']:a.get('aliases',[]) for a in apps}
    providers, calls = {}, []
    for label, parts in modules.items():
        provider = parts['ai_provider'].GeminiProvider(parts['ai_provider'].AIConfig.load(ROOT))
        original = provider._request
        def record(method,suffix,body,cancel,_original=original,_label=label,**kwargs):
            started=time.perf_counter()
            result=_original(method,suffix,body,cancel,**kwargs)
            calls.append(dict(version=_label,request_bytes=len(json.dumps(body).encode()),
                              http_ms=round((time.perf_counter()-started)*1000,2),usage=result.get('usageMetadata',{})))
            return result
        provider._request=record
        providers[label]=provider
    cases = [
        ('two_apps','Open the WhatsApp and then open Discord',[('open_app','whatsapp_web'),('open_app','discord')]),
        ('browser_chain','Open Steam and then open the browser',[('open_app','steam'),('open_app','browser')]),
        ('mixed_chain','Open Discord and close Steam',[('open_app','discord'),('close_app','steam')]),
        ('one_app','Open Discord',[('open_app','discord')]),
        ('negation',"Don't open Steam; open Discord instead",[('open_app','discord')]),
        ('exception','Close everything except ChatGPT',None),
        ('search','Open the browser and search for lunar calendar',[('web_search',None)]),
        ('writing','Open Notepad and write a 100-word essay about education',[('document_write',None)]),
    ]
    if options.case:cases=[case for case in cases if case[0] in options.case]
    rows=[]
    next_allowed=0
    try:
        for round_index in range(options.rounds):
            for index,(case,text,expected) in enumerate(cases):
                order=['before','after'] if (index+round_index)%2==0 else ['after','before']
                for label in order:
                    registry=modules[label]['ai_tools'].Registry(ids)
                    registry.app_names=names
                    time.sleep(max(0,next_allowed-time.monotonic()))
                    start=time.perf_counter();call_start=len(calls)
                    try:
                        executed=[]
                        def execute(step):
                            executed.append(step)
                            return modules[label]['ai_engine'].Result('verified','Verified mock outcome.')
                        engine=modules[label]['ai_engine'].AIEngine(providers[label],registry,lambda *a:True,lambda *a:True,execute)
                        engine.start_agent=lambda text:None
                        engine.refresh_agent=lambda ref:None
                        engine.confirm_batch=lambda *a:True
                        result=engine.run(text,('benchmark','Admin'))
                        selected=[(s['tool'],s['arguments'].get('app')) for s in executed]
                        passed=result.ok and (expected is None or selected==expected)
                        if case=='exception':
                            passed=passed and len(executed)==1 and executed[0]['tool']=='close_everything' and bool(
                                set(executed[0]['arguments']['except_apps']) & {'gpt','chatgpt_app'})
                        if case=='search':
                            passed=result.ok and selected in [[('web_search',None)],[('open_app','browser'),('web_search',None)]] and executed[-1]['arguments']['query'].casefold()=='lunar calendar'
                        if case=='writing':
                            draft=executed[0]['arguments'] if executed else {}
                            passed=passed and draft.get('word_count')==100 and 95<=len(draft.get('text','').split())<=105 and draft.get('new_document')
                        row=dict(version=label,case=case,round=round_index,passed=passed,selected=selected,
                                 status=result.status,model_calls=engine.timings.get('model_calls'),message=result.message,
                                 elapsed_ms=round((time.perf_counter()-start)*1000,2),calls=calls[call_start:])
                    except Exception as error:
                        row=dict(version=label,case=case,round=round_index,passed=False,
                                 elapsed_ms=round((time.perf_counter()-start)*1000,2),error=providers[label].credentials.redact(str(error)))
                    rows.append(row)
                    next_allowed=time.monotonic()+options.interval*max(1,len(calls)-call_start)
                    print(json.dumps({k:v for k,v in row.items() if k!='calls'}),flush=True)
                    (ROOT/'.validation/processing-api-in-progress.json').write_text(json.dumps(rows,indent=2),encoding='utf8')
    finally:
        for provider in providers.values():provider.close()
    summary={label:dict(n=sum(r['version']==label for r in rows),passed=sum(r['version']==label and r['passed'] for r in rows),
                       median_success_ms=round(statistics.median(r['elapsed_ms'] for r in rows if r['version']==label and r['passed']),2),
                       app_chain_success_median_ms=round(statistics.median(r['elapsed_ms'] for r in rows if r['version']==label and r['passed'] and r['case'] in {'two_apps','browser_chain','mixed_chain'}),2)) for label in providers}
    result=dict(scope='Synthetic text tasks, real configured Google model and persistent sessions, existing bounded plan correction, mocked confirmations/desktop effects; counterbalanced ordering with quota pacing excluded from timings. No microphone, real app execution or audible feedback. Small sample, no live-accent accuracy claim.',
                backup=str(backup),model=providers['after'].config.model,summary=summary,rows=rows)
    (ROOT/f'.validation/{options.output}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(summary),flush=True)
    if not all(r['passed'] for r in rows if r['version']=='after'):
        raise SystemExit('At least one updated planning case failed; inspect the report.')


if __name__=='__main__':main()
