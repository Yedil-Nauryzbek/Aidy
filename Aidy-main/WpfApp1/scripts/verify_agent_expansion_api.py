"""Real Google regression/planning comparison; all effects are mocked."""
import importlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'PythonCore'))


def main():
    backup=Path((ROOT/'.validation/agent-expansion-backup.txt').read_text(encoding='utf8'))
    extracted=ROOT/'.validation/agent-expansion-before'
    with zipfile.ZipFile(backup/'source.zip') as archive:archive.extractall(extracted)
    package=extracted/'PythonCore/aidy'
    spec=importlib.util.spec_from_file_location('aidy_expansion_before',package/'__init__.py',submodule_search_locations=[str(package)])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    parts={label:{name:importlib.import_module(prefix+'.'+name) for name in ['ai_provider','ai_tools','ai_engine']}
           for label,prefix in [('before','aidy_expansion_before'),('after','aidy')]}
    apps=json.loads((ROOT/'apps.json').read_text(encoding='utf-8-sig'))['apps']
    names={a['id']:a.get('aliases',[]) for a in apps};names['chatgpt_app']=['chatgpt','gpt app','chat with the app']
    providers={label:p['ai_provider'].GeminiProvider(p['ai_provider'].AIConfig.load(ROOT)) for label,p in parts.items()}
    rows=[]
    cases=[
        ('reported_shorthand','Open WhatsApp and then GPT app',['before','after'],['open_app','open_app']),
        ('reported_asr','Open the WhatsApp and then open the chat with the app',['after','before'],['open_app','open_app']),
        ('browser_all','Close all the websites in this browser',['after'],['browser_action']),
        ('browser_except','Close all browser tabs except YouTube',['after'],['browser_action']),
        ('browser_chain','Select the YouTube tab and then close other tabs',['after'],['browser_action','browser_action']),
        ('unlisted_file_task','Find text files in my Downloads folder',['after'],['file_action']),
    ]
    try:
        for case,text,labels,expected in cases:
            for label in labels:
                provider=providers[label];registry=parts[label]['ai_tools'].Registry([a['id'] for a in apps]+['chatgpt_app'])
                registry.app_names=names
                registry.windows=[dict(id='w1',process='msedge.exe',title='Test browser')];registry.active_window='w1'
                effects=[]
                def execute(step):
                    effects.append(step)
                    return parts[label]['ai_engine'].Result('verified','Verified mock effect.')
                engine=parts[label]['ai_engine'].AIEngine(provider,registry,lambda *a:True,lambda *a:True,execute)
                engine.start_agent=lambda text:None;engine.refresh_agent=lambda ref:None
                started=time.perf_counter();outcome=engine.run(text,('test','Admin'))
                tools=[s['tool'] for s in effects]
                passed=outcome.ok and tools==expected
                if case.startswith('reported'):
                    passed=passed and [s['arguments'].get('app') for s in effects]==['whatsapp_web','chatgpt_app']
                if case=='browser_all':passed=passed and effects[0]['arguments']['operation']=='close_all_tabs'
                if case=='browser_except':passed=passed and effects[0]['arguments']['operation']=='close_tabs_except' and 'youtube' in effects[0]['arguments']['text'].casefold()
                if case=='browser_chain':passed=passed and [s['arguments']['operation'] for s in effects]==['select_tab','close_other_tabs'] and engine.timings.get('model_calls')==1
                if case=='unlisted_file_task':passed=passed and effects[0]['arguments']['operation']=='search' and bool(effects[0]['arguments']['text'])
                row=dict(case=case,version=label,passed=passed,status=outcome.status,message=outcome.message,
                         elapsed_ms=round((time.perf_counter()-started)*1000,2),timings=engine.timings,steps=effects)
                rows.append(row);print(json.dumps(row),flush=True)
                (ROOT/'.validation/agent-expansion-api.json').write_text(json.dumps(dict(scope='Synthetic text, real configured Google. Every OS/confirmation effect is mocked. No live speech accuracy or universal speed guarantee.',rows=rows),indent=2),encoding='utf8')
                time.sleep(7*max(1,engine.timings.get('model_calls',1)))
    finally:
        for provider in providers.values():provider.close()
    if not all(r['passed'] for r in rows if r['version']=='after'):raise SystemExit('An updated API scenario failed.')


if __name__=='__main__':main()
