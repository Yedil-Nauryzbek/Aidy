"""Opt-in real Google requests with mocked authorization, confirmations and OS.

Never prints keys, raw HTTP errors, or arbitrary provider text. Results contain
only expected tool IDs and synthetic test inputs. No microphone or voice data.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.ai_provider import AIConfig, GeminiProvider
from aidy.ai_tools import AIError, Registry
from aidy.ai_engine import AIEngine, Result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--validate-keys', action='store_true')
    parser.add_argument('--compare-baseline', action='store_true', help='Use exactly the two original-mode latency phrases.')
    args = parser.parse_args()
    cfg = AIConfig(mode='api')
    provider = GeminiProvider(cfg)
    if args.validate_keys:
        try:
            print(json.dumps(provider.validate_credentials(threading.Event())))
        finally:
            provider.close()
        return
    executed = []
    def execute(step):
        executed.append(step)
        return Result('verified', 'Mock executor completed.')
    registry = Registry([a['id'] for a in json.loads((ROOT / 'apps.json').read_text())['apps']])
    engine = AIEngine(provider, registry, lambda *args: True, lambda *args: True, execute)
    cases = [
        ('Open Discord.', [('open_app', {'app': 'discord'})]),
        ('Now close it.', [('close_app', {'app': 'discord'})]),
        ('Make it a little quieter.', [('volume_change', {'direction': 'down', 'steps': 1})]),
        ('A little more.', [('volume_change', {'direction': 'down', 'steps': 1})]),
        ('Open the browser and then open Discord.', [('open_app', {'app': 'browser'}), ('open_app', {'app': 'discord'})]),
        ('Actually, open Steam instead.', [('open_app', {'app': 'steam'})]),
        ('What time is it?', [('current_time', {})]),
        ('Close it.', []),  # new identity; no last app -> clarification
    ]
    if args.compare_baseline:
        cases = [('open browser', [('open_app', {'app': 'browser'})]),
                 ('close steam', [('close_app', {'app': 'steam'})])]
    rows = []
    try:
        for index, (text, expected) in enumerate(cases):
            executed.clear()
            start = time.perf_counter()
            result = engine.run(text, ('smoke' if index < 7 else 'new-user', 'Admin'))
            actual = [(s['tool'], s['arguments']) for s in executed]
            passed = actual == expected and (result.ok if expected else result.status == 'clarification')
            rows.append({'request': text, 'passed': passed, 'status': result.status,
                         'elapsed_ms': round((time.perf_counter() - start) * 1000, 2),
                         'timings': engine.timings, 'executed_tools': [s['tool'] for s in executed]})
            print(json.dumps({k: v for k, v in rows[-1].items() if k != 'timings'}), flush=True)
            # Do not continue generating on quota/auth/provider failure.
            if result.status in {'failed', 'cancelled'}:
                print(result.message, flush=True)
                break
    finally:
        engine.close()
    times = [r['elapsed_ms'] for r in rows]
    result = {'model': cfg.model, 'scope': 'Real Google API, synthetic text, mocked authorization/confirmation/desktop. No live audio or OS actions.',
              'summary': {'n': len(times), 'median_ms': statistics.median(times), 'min_ms': min(times), 'max_ms': max(times),
                          'tail_note': 'This small sample is insufficient for a reliable tail percentile.'}, 'rows': rows}
    output = 'api-same-requests.json' if args.compare_baseline else 'api-smoke.json'
    (ROOT / '.validation' / output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result['summary']))
    if len(rows) != len(cases) or not all(r['passed'] for r in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
