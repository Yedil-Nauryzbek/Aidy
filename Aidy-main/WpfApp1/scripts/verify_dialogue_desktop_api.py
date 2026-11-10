"""Real Google interpretation; mocked confirmations/execution, no desktop actions."""
import json
from pathlib import Path
import sys
import time
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.ai_desktop import discover_chatgpt
from aidy.ai_engine import AIEngine, Result
from aidy.ai_provider import AIConfig, GeminiProvider
from aidy.ai_tools import Registry


def main():
    desktop = discover_chatgpt()
    print(json.dumps({'installed_chatgpt_found': bool(desktop)}), flush=True)
    apps = [a['id'] for a in json.loads((ROOT / 'apps.json').read_text())['apps']] + ['chatgpt_app']
    provider = GeminiProvider(AIConfig(mode='api'))
    execute = Mock(return_value=Result('dispatched', 'Request sent.'))
    confirm = Mock(return_value=True)
    engine = AIEngine(provider, Registry(apps), lambda *a: True, confirm, execute)
    engine.confirm_batch = Mock(return_value=True)
    checks = []

    def check(text, identity, expected_tools, status=None, expected_app=None):
        execute.reset_mock()
        confirm.reset_mock()
        engine.confirm_batch.reset_mock()
        start = time.perf_counter()
        result = engine.run(text, (identity, 'Admin'))
        steps = [call.args[0] for call in execute.call_args_list]
        passed = [s['tool'] for s in steps] == expected_tools
        if status:
            passed &= result.status == status
        else:
            passed &= result.ok
        if expected_app:
            passed &= bool(steps) and steps[0]['arguments'].get('app') == expected_app
        if len(expected_tools) == 2 and all(t == 'close_app' for t in expected_tools):
            passed &= engine.confirm_batch.call_count == 1 and confirm.call_count == 0
        row = dict(request=text, passed=bool(passed), status=result.status,
                   tools=[s['tool'] for s in steps], elapsed_ms=round((time.perf_counter() - start) * 1000, 2))
        checks.append(row)
        print(json.dumps(row), flush=True)
        if not passed:
            print('Safe outcome: ' + result.message, flush=True)
        return steps

    try:
        check('close Discord and Steam', 'closing', ['close_app', 'close_app'])
        check('volume up and brightness up', 'levels', [], 'clarification')
        levels = check('three', 'levels', ['volume_change', 'brightness_change'])
        if levels:
            checks[-1]['passed'] &= all(s['arguments']['steps'] == 3 for s in levels)
        check('open the ChatGPT app, not the website', 'desktop', ['open_app'], expected_app='chatgpt_app')
        check('open browser and write number 3 in the search box', 'search', ['web_search'])
        check('open the ChatGPT website', 'website', ['open_app'], expected_app='gpt')
        check('flibberty wobble', 'unclear', [], 'clarification')
    finally:
        provider.close()
        (ROOT / '.validation/dialogue-desktop-api.json').write_text(json.dumps(checks, indent=2))
    assert len(checks) == 7 and all(c['passed'] for c in checks), 'API dialogue regression failed'


if __name__ == '__main__':
    main()
