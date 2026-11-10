"""Before/after ASR and feedback generation, without executing any voice task.

Real cached tiny.en and SAPI-to-WAV. Audio playback/microphone are not measured.
API and real desktop execution have their own independent evidence files.
"""
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))


def main():
    import win32com.client
    from faster_whisper.audio import decode_audio
    from aidy import speech, ai_runtime
    backup = Path.home() / 'AppData/Local/Aidy/backups/universal-agent-20261108-083305/PythonCore/aidy'
    spec = importlib.util.spec_from_file_location('aidy_snapshot', backup / '__init__.py', submodule_search_locations=[str(backup)])
    package = importlib.util.module_from_spec(spec)
    sys.modules['aidy_snapshot'] = package
    spec.loader.exec_module(package)
    previous_speech = importlib.import_module('aidy_snapshot.speech')
    previous_runtime = importlib.import_module('aidy_snapshot.ai_runtime')
    recognizers = dict(before=previous_speech.SpeechRecognizer(), after=speech.SpeechRecognizer())
    recordings = [decode_audio(str(ROOT / f'.validation/latency-{i}.wav'), sampling_rate=16000) for i in (2, 3)]
    asr_rows, feedback_rows = [], []
    try:
        for recognizer in recognizers.values():
            recognizer.transcribe(recordings[0], purpose='command')
        for index in range(4):
            for label in (['before', 'after'] if index % 2 == 0 else ['after', 'before']):
                start = time.perf_counter()
                transcript = recognizers[label].transcribe(recordings[index % 2], purpose='command')
                asr_rows.append(dict(version=label, case='open browser' if index % 2 == 0 else 'close steam',
                                     transcript=transcript, recognition_ms=round((time.perf_counter()-start)*1000, 2)))
    finally:
        for recognizer in recognizers.values():
            recognizer.close()
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    for index in range(4):
        for label, module in ([('before', previous_runtime), ('after', ai_runtime)] if index % 2 == 0 else [('after', ai_runtime), ('before', previous_runtime)]):
            path = ROOT / f'.validation/universal-feedback-{label}-{index}.wav'
            stream = win32com.client.Dispatch('SAPI.SpFileStream')
            stream.Format.Type = 18
            stream.Open(str(path), 3)
            voice.AudioOutputStream = stream
            adapter = module.AIRuntime.__new__(module.AIRuntime)
            adapter.engine = None
            adapter.host = SimpleNamespace(voice=SimpleNamespace(muted=False, tts_blocking=voice.Speak),
                                           _stop_event=threading.Event(), _deafen_after_speak=lambda: None)
            start = time.perf_counter()
            adapter.report(module.Result('verified', 'Done.'))
            elapsed = (time.perf_counter()-start)*1000
            stream.Close()
            feedback_rows.append(dict(version=label, feedback_generation_ms=round(elapsed, 2),
                                      audible_playback_ms=None))
    summary = {label: dict(n_recognition=4, n_feedback_generation=4,
                          median_recognition_ms=round(statistics.median(r['recognition_ms'] for r in asr_rows if r['version']==label),2),
                          median_feedback_generation_ms=round(statistics.median(r['feedback_generation_ms'] for r in feedback_rows if r['version']==label),2)) for label in recognizers}
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = dict(scope='Independent stage measurements: same prerecorded synthetic English phrases, warm cached real tiny.en, counterbalanced; real SAPI renders identical Done feedback to WAV. No live microphone, audible playback, API routing or desktop actions in this script.',
                  recognition_source_unchanged=digest(backup/'speech.py')==digest(ROOT/'PythonCore/aidy/speech.py'),
                  endpoint_wait_ms_before=700, endpoint_wait_ms_after=700, summary=summary,
                  recognition=asr_rows, feedback_generation=feedback_rows)
    (ROOT/'.validation/universal-stages-before-after.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
