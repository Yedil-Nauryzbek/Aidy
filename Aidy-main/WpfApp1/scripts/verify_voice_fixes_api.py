"""Real Gemini, synthetic desktop catalog, mocked execution/confirmation only."""
import importlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.ai_provider import AIConfig, GeminiProvider
from aidy.ai_tools import Registry
from aidy.ai_engine import AIEngine, Result


def main():
    # Preserve the actual previous provider/registry as the comparison baseline.
    saved = ROOT / '.validation/before-voice-fixes/provider_source'
    package = saved / 'aidy_before'
    package.mkdir(parents=True, exist_ok=True)
    (package / '__init__.py').write_text('')
    for name in ['ai_provider.py', 'ai_tools.py']:
        data = subprocess.check_output(['git', 'show', '6fe2636:PythonCore/aidy/' + name], cwd=ROOT)
        (package / name).write_bytes(data)
    sys.path.insert(0, str(saved))
    old_provider = importlib.import_module('aidy_before.ai_provider')
    old_tools = importlib.import_module('aidy_before.ai_tools')
    app_ids = [app['id'] for app in json.loads((ROOT / 'apps.json').read_text())['apps']]
    before = old_provider.GeminiProvider(old_provider.AIConfig(mode='api'))
    after = GeminiProvider(AIConfig(mode='api'))
    old_registry, registry = old_tools.Registry(app_ids), Registry(app_ids)
    comparison = []
    try:
        for index, phrase in enumerate(['open browser', 'close steam']):
            variants = [('before', before, old_registry), ('after', after, registry)]
            if index:
                variants.reverse()
            for label, provider, catalog in variants:
                started = time.perf_counter()
                result = provider.interpret(phrase, [], catalog, threading.Event())
                elapsed = (time.perf_counter() - started) * 1000
                assert len(result['steps']) == 1 and not result['clarification']
                expected = 'open_app' if phrase.startswith('open') else 'close_app'
                assert result['steps'][0]['tool'] == expected
                comparison.append({'version': label, 'request': phrase, 'elapsed_ms': round(elapsed, 2)})
        registry.windows = [{'id': 'w1', 'process': 'notepad.exe', 'title': 'Notes - Notepad'},
                            {'id': 'w2', 'process': 'msedge.exe', 'title': 'ChatGPT - Edge'}]
        registry.active_window = 'w1'
        executed = []
        def execute(action):
            executed.append(action)
            return Result('verified', 'Mock executor completed.')
        engine = AIEngine(after, registry, lambda *args: True, lambda *args: True, execute)
        cases = [
            ('close ad', 'exit_assistant'),
            ('close yourself', 'exit_assistant'),
            ('close everything except ChatGPT', 'close_everything'),
            ('close it', 'close_window'),
            ('Open Discord', 'open_app'),
            ('Now close it', 'close_app'),
        ]
        checks = []
        for index, (phrase, expected) in enumerate(cases):
            executed.clear()
            started = time.perf_counter()
            identity = ('dialogue' if index >= 4 else str(index), 'Admin')
            result = engine.run(phrase, identity)
            passed = result.ok and len(executed) == 1 and executed[0]['tool'] == expected
            if passed and expected == 'close_everything':
                passed = bool(set(executed[0]['arguments']['except_apps']) & {'gpt', 'w2'})
            if passed and phrase == 'close it':
                passed = executed[0]['arguments']['window'] == 'w1'
            if passed and phrase == 'Now close it':
                passed = executed[0]['arguments']['app'] == 'discord'
            row = {'request': phrase, 'passed': passed, 'status': result.status,
                   'tools': [s['tool'] for s in executed], 'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}
            checks.append(row)
            print(json.dumps(row), flush=True)
            if not result.ok:
                break
        summaries = {label: {'n': 2, 'median_ms': round(statistics.median(r['elapsed_ms'] for r in comparison if r['version'] == label), 2)}
                     for label in ['before', 'after']}
        data = {'scope': 'Real API, synthetic text/windows, mocked desktop and confirmations. Two latency samples per version; no tail estimate.',
                'comparison': comparison, 'summary': summaries, 'checks': checks}
        (ROOT / '.validation/voice-fixes-api.json').write_text(json.dumps(data, indent=2))
        print(json.dumps(summaries))
        assert len(checks) == len(cases) and all(r['passed'] for r in checks), 'Reported command regression failed'
    finally:
        before.close()
        after.close()


if __name__ == '__main__':
    main()
