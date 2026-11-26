"""Opt-in real Google check of conversational Aidy. No desktop effects.

Runs small talk, a live fact, an action and a mixed request through AIEngine
with a mocked executor, authorization and confirmation. Prints the status,
planned tools, the spoken reply, grounding source titles and wall time.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.ai_provider import AIConfig, GeminiProvider
from aidy.ai_tools import AIError, Registry
from aidy.ai_engine import AIEngine, Result

CASES = ('how are you doing today', "what's the dollar rate in tenge today",
         'open notepad', 'open notepad and tell me a joke')


def main():
    provider = GeminiProvider(replace(AIConfig.load(ROOT), mode='api'))
    executed = []
    def execute(step):
        executed.append(step['tool'] + ':' + json.dumps(step['arguments']))
        return Result('verified', 'Mock executor completed.')
    registry = Registry([a['id'] for a in json.loads((ROOT / 'apps.json').read_text(encoding='utf-8'))['apps']])
    engine = AIEngine(provider, registry, lambda *a: True, lambda *a: True, execute)
    engine.announce = lambda: print('   [announce] Let me check.', flush=True)
    try:
        for text in sys.argv[1:] or CASES:
            executed.clear()
            time.sleep(1.5)  # stay clear of the shared cooldown after a transient 5xx
            started = time.perf_counter()
            result = engine.run(text, ('local', 'Admin'))
            reply, sources = (result.message, (result.data or {}).get('sources', [])) if result.status == 'answered' else ('', [])
            if result.ok and engine.turn_conversation:
                try:
                    reply, sources, _ = engine.answer(engine.turn_conversation)
                except AIError as exc:
                    reply = str(exc)
            tone = ((result.data or {}).get('tone') or (engine.turn_conversation or {}).get('tone') or '')
            print(f'> {text}\n   status={result.status} tools={executed} tone={tone} '
                  f'seconds={time.perf_counter() - started:.2f} hedged={getattr(engine.provider, "hedged_requests", 0)} '
                  f'timings={ {k: round(v) for k, v in engine.timings.items()} }', flush=True)
            if result.status != 'answered':
                print('   outcome: ' + result.message[:200])
            if reply:
                print('   reply: ' + reply[:400])
            if sources:
                print('   sources: ' + '; '.join(s['title'] for s in sources))
    finally:
        engine.close()


if __name__ == '__main__':
    main()
