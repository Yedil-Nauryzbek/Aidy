"""Repeatable, harmless replay: real Whisper, original router with mocked OS/voice.

Run --label before/after. API timings come from test_ai_layer or --smoke separately;
this does not represent live microphone or desktop performance.
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
# Optional starting snapshot makes the before run independently reproducible.
SOURCE = Path(os.environ.get('AIDY_BENCHMARK_SOURCE', str(ROOT)))
sys.path[:0] = [str(SOURCE / 'PythonCore'), str(SOURCE / 'PythonCore/aidy/tests')]
from aidy.speech import SpeechRecognizer
from aidy.config import VAD_SILENCE_MS
from aidy.audio_capture import UtteranceBuffer, FRAME_BYTES
from aidy import assistant as core
from test_app_routing import AppRoutingTests


def summary(values):
    ordered = sorted(values)
    return {'n': len(values), 'median_ms': round(statistics.median(values), 2),
            'p95_ms': round(ordered[max(0, int(len(values) * .95 + .999) - 1)], 2)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    recognizer = SpeechRecognizer()
    recordings = []
    for index, phrase in [(2, 'open browser'), (3, 'close steam')]:
        samples = decode_audio(str(ROOT / f'.validation/latency-{index}.wav'), sampling_rate=16000)
        pcm = (np.concatenate([samples, np.zeros(16000)]) * 32767).astype(np.int16).tobytes()
        collector = UtteranceBuffer(silence_ms=VAD_SILENCE_MS)
        for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
            if collector.feed(pcm[offset:offset + FRAME_BYTES]):
                break
        recordings.append((phrase, collector.finish()))
    rows = []
    fixture = AppRoutingTests()
    aidy = fixture.make_assistant()
    try:
        recognizer.transcribe(recordings[0][1], purpose='command')
        for _ in range(10):
            for phrase, audio in recordings:
                start = time.perf_counter()
                transcript = recognizer.transcribe(audio, purpose='command')
                decoded = time.perf_counter()
                marks = {}
                find_app = core.find_app
                def resolve(*args, **kwargs):
                    value = find_app(*args, **kwargs)
                    if value:
                        marks['interpreted'] = time.perf_counter()
                    return value
                def action(*args, **kwargs):
                    marks['dispatch_ms'] = (time.perf_counter() - decoded) * 1000
                    marks['action_completion_ms'] = 0.0  # immediate mocked executor
                    return True
                with patch.object(core, 'launch_app', side_effect=action), \
                     patch.object(core, 'close_app', side_effect=action), \
                     patch.object(core, 'find_app', side_effect=resolve):
                    ok = aidy.process_command(phrase)
                interpretation_ms = (marks.get('interpreted', decoded) - decoded) * 1000
                rows.append({'phrase': phrase, 'transcript': transcript, 'ok': bool(ok),
                             'decode_ms': (decoded - start) * 1000,
                             'interpretation_ms': interpretation_ms,
                             'interpretation_to_dispatch_ms': marks.get('dispatch_ms', 0) - interpretation_ms,
                             'action_completion_ms': marks.get('action_completion_ms'),
                             'feedback_start_ms': None,
                             'transcript_to_mock_dispatch_ms': marks.get('dispatch_ms'),
                             'route_total_ms': (time.perf_counter() - decoded) * 1000})
    finally:
        recognizer.close()
        fixture.doCleanups()
    result = {'scope': 'Synthesized audio replay; real warm Whisper; original interpretation; '
                       'mocked confirmation, OS actions and voice. Decode excludes VAD silence. '
                       'No live microphone. No API calls. p95 is descriptive for only 20 samples.',
              'endpoint_silence_ms': VAD_SILENCE_MS,
              'summary': {key: summary([r[key] for r in rows if r[key] is not None])
                          for key in ['decode_ms', 'interpretation_ms', 'interpretation_to_dispatch_ms',
                                      'action_completion_ms', 'transcript_to_mock_dispatch_ms', 'route_total_ms']},
              'rows': rows}
    output = ROOT / f'.validation/api-{args.label}-benchmark.json'
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
