"""Counterbalanced before/after real Google interpretation, no desktop effects."""
import importlib
import json
from pathlib import Path
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.ai_provider import AIConfig, GeminiProvider
from aidy.ai_tools import Registry
from aidy.ai_catalog import AppCatalog


def main():
    backup = Path.home() / 'AppData/Local/Aidy/backups/universal-agent-20261108-083305/PythonCore/aidy'
    package = ROOT / '.validation/universal-before/aidy_before'
    package.mkdir(parents=True, exist_ok=True)
    (package / '__init__.py').write_text('')
    for name in ['ai_tools.py', 'ai_provider.py']:
        (package / name).write_bytes((backup / name).read_bytes())
    sys.path.insert(0, str(package.parent))
    old = importlib.import_module('aidy_before.ai_provider')
    old_tools = importlib.import_module('aidy_before.ai_tools')
    ids = [app['id'] for app in json.loads((ROOT / 'apps.json').read_text())['apps']] + ['browser', 'chatgpt_app']
    providers = dict(before=old.GeminiProvider(old.AIConfig(mode='api')), after=GeminiProvider(AIConfig(mode='api')))
    registries = dict(before=old_tools.Registry(ids), after=Registry(ids))
    catalog = AppCatalog()
    catalog.refresh(timeout=5)
    rows = []
    cases = ['Open the browser', 'Open the browser and search for Windows UI Automation',
             'Open Paint', 'Inspect Aidy Agent Test and enter Hello in its text field']
    try:
        for index, task in enumerate(cases):
            installed = catalog.relevant(task)
            registries['after'].installed_apps = [dict(id=a['id'], name=a['display_name'], source=a['type']) for a in installed]
            registries['after'].app_ids = frozenset(ids + [a['id'] for a in installed])
            for label in (['before', 'after'] if index % 2 == 0 else ['after', 'before']):
                start = time.perf_counter()
                try:
                    result = providers[label].interpret(task, [], registries[label], threading.Event())
                    row = dict(version=label, task=task, api_ms=round((time.perf_counter()-start)*1000, 2),
                               plan=result, recognition_ms=None, execution_ms=None, feedback_ms=None)
                except Exception as exc:
                    row = dict(version=label, task=task, api_ms=round((time.perf_counter()-start)*1000, 2), error=str(exc))
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        for provider in providers.values():
            provider.close()
    summary = {label: dict(n=sum(r['version']==label for r in rows),
                          successful_calls=sum(r['version']==label and 'plan' in r for r in rows),
                          median_api_ms=round(statistics.median(r['api_ms'] for r in rows if r['version']==label), 2)) for label in providers}
    report = dict(scope='Same four text requests, counterbalanced real Google calls. No ASR, audio feedback or desktop actions. Different capability catalogs reflect actual implementations. This is not an end-to-end latency or speedup claim.', summary=summary, rows=rows)
    (ROOT / '.validation/universal-api-before-after.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
