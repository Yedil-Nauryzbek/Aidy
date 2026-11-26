"""Compare the prior revision with pause/number/compact-API fixes. No OS actions."""
import argparse
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
from aidy.speech import SpeechRecognizer
from aidy.ai_provider import AIConfig, GeminiProvider
from aidy.ai_tools import Registry


def previous():
    parent = ROOT / '.validation/pause-before'
    package = parent / 'aidy_previous'
    package.mkdir(parents=True, exist_ok=True)
    (package / '__init__.py').write_text('')
    for name in ['speech', 'whisper_backend', 'activation', 'config', 'logui', 'ai_provider', 'ai_tools']:
        data = subprocess.check_output(['git', 'show', 'f6ab6e0:PythonCore/aidy/' + name + '.py'], cwd=ROOT)
        (package / (name + '.py')).write_bytes(data)
    sys.path.insert(0, str(parent))
    return importlib.import_module('aidy_previous.speech'), importlib.import_module('aidy_previous.ai_provider'), importlib.import_module('aidy_previous.ai_tools')


def replay(before_speech):
    import win32com.client
    from faster_whisper.audio import decode_audio
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    voices = voice.GetVoices()
    for index in range(voices.Count):
        if 'zira' in voices.Item(index).GetDescription().lower():
            voice.Voice = voices.Item(index)
            break
    numbers = 'one two three four five six seven eight nine ten'.split()
    cases = [(word, str(i + 1), 0) for i, word in enumerate(numbers)] + [('five', '5', -1), ('ten', '10', -1)]
    models = {'before': before_speech.SpeechRecognizer(), 'after': SpeechRecognizer()}
    rows = []
    controls = []
    try:
        for index, (word, expected, rate) in enumerate(cases):
            path = ROOT / f'.validation/pause-number-{index}.wav'
            stream = win32com.client.Dispatch('SAPI.SpFileStream')
            stream.Format.Type = 18
            stream.Open(str(path), 3)
            voice.AudioOutputStream = stream
            voice.Rate = rate
            voice.Speak(word + '.')
            stream.Close()
            audio = decode_audio(str(path), sampling_rate=16000)
            for label in (['before', 'after'] if index % 2 == 0 else ['after', 'before']):
                start = time.perf_counter()
                text = models[label].transcribe(audio, purpose='number' if label == 'after' else 'command')
                value = str(numbers.index(text) + 1) if text in numbers else text
                rows.append(dict(version=label, expected=expected, transcript=text, correct=value == expected,
                                 decode_ms=round((time.perf_counter() - start) * 1000, 2)))
        for index, word in enumerate(['Cancel.', 'Stop.', "Don't change it.", 'Open Discord instead.', 'Not ten.']):
            path = ROOT / f'.validation/pause-control-{index}.wav'
            stream = win32com.client.Dispatch('SAPI.SpFileStream')
            stream.Format.Type = 18
            stream.Open(str(path), 3)
            voice.AudioOutputStream = stream
            voice.Rate = 0
            voice.Speak(word)
            stream.Close()
            text = models['after'].transcribe(decode_audio(str(path), sampling_rate=16000), purpose='number')
            safe = text not in numbers + [str(i) for i in range(1, 11)]
            if word in ['Cancel.', 'Stop.']:
                safe &= text == word.lower().rstrip('.')
            controls.append(dict(phrase=word, transcript=text, safe=safe))
    finally:
        for model in models.values():
            model.close()
    summary = {label: dict(n=12, correct=sum(r['correct'] for r in rows if r['version'] == label),
                           median_decode_ms=round(statistics.median(r['decode_ms'] for r in rows if r['version'] == label), 2))
               for label in models}
    print(json.dumps({'synthetic_number_replay': summary}), flush=True)
    return dict(scope='Synthesized Zira speech, two speaking rates, same cached tiny.en model. No live accent/microphone. Decode excludes endpoint waiting.',
                summary=summary, rows=rows, controls=controls)


def api(before_provider, before_tools):
    ids = [a['id'] for a in json.loads((ROOT / 'apps.json').read_text())['apps']] + ['browser', 'chatgpt_app']
    providers = {'before': before_provider.GeminiProvider(before_provider.AIConfig(mode='api', timeout_seconds=8)),
                 'after': GeminiProvider(AIConfig(mode='api'))}
    registries = {'before': before_tools.Registry(ids), 'after': Registry(ids)}
    rows = []
    try:
        for index, (text, tool, target) in enumerate([('open the browser', 'open_app', 'browser'),
                                                    ('close Steam', 'close_app', 'steam'),
                                                    ('open Discord', 'open_app', 'discord')]):
            for label in (['before', 'after'] if index % 2 == 0 else ['after', 'before']):
                start = time.perf_counter()
                try:
                    plan = providers[label].interpret(text, [], registries[label], threading.Event())
                    passed = (len(plan['steps']) == 1 and plan['steps'][0]['tool'] == tool and
                              plan['steps'][0]['arguments'].get('app') == target)
                except Exception:
                    passed = False
                row = dict(version=label, request=text, passed=passed, elapsed_ms=round((time.perf_counter() - start) * 1000, 2))
                rows.append(row)
                print(json.dumps(row), flush=True)
    finally:
        for provider in providers.values():
            provider.close()
    summary = {label: dict(n=3, passed=sum(r['passed'] for r in rows if r['version'] == label),
                           median_ms=round(statistics.median(r['elapsed_ms'] for r in rows if r['version'] == label), 2))
               for label in providers}
    print(json.dumps({'real_api': summary}), flush=True)
    return dict(scope='Six real Google requests, three per revision, counterbalanced. No execution or confirmations. No reliable tail estimate from n=3.',
                summary=summary, rows=rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', action='store_true')
    args = parser.parse_args()
    old_speech, old_provider, old_tools = previous()
    result = {'speech': replay(old_speech)}
    if args.api:
        result['api'] = api(old_provider, old_tools)
        (ROOT / '.validation/pause-api-speed.json').write_text(json.dumps(result['api'], indent=2))
    (ROOT / '.validation/pause-numbers-speed.json').write_text(json.dumps(result, indent=2))
    assert result['speech']['summary']['after']['correct'] == 12, 'Number replay regression'
    assert all(row['safe'] for row in result['speech']['controls']), 'Numeric context lost a cancellation or negation'
    if args.api:
        assert result['api']['summary']['after']['passed'] == 3, 'Compact API regression'


if __name__ == '__main__':
    main()
