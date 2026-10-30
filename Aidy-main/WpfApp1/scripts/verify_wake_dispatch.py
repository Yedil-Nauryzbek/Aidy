"""Measure accepted wake -> playback API, excluding ASR and output-driver latency.

Use the real wake orchestration, queue suspension, Voice and preloaded WAV path.
Mock only recognition, devices and the final PlaySound call; no mic or speakers.
"""
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import Mock, patch

import numpy as np
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
sys.path.insert(0, str(ROOT / 'PythonCore/aidy/tests'))
from test_audio_pipeline import bare_aidy
from aidy import assistant as core, voice
from aidy.audio_capture import BufferedInput, FRAME_BYTES


def main():
    command = decode_audio(str(ROOT / '.validation/synthesized-4.wav'), sampling_rate=16000)
    cases = {
        'empty': np.zeros(0, np.float32),
        'continuous_noise': np.random.default_rng(7).normal(0, .03, 80000).astype(np.float32),
        'complete_command': np.concatenate([command, np.zeros(8000, np.float32)]),
        'unfinished_speech': np.tile(command[:6400], 12),
    }
    rows = []
    for case, samples in cases.items():
        for repeat in range(3):
            aidy = bare_aidy()
            stream = BufferedInput(Mock())
            aidy.stream = stream
            timings, states = {}, []
            engine = Mock()
            engine.getProperty.return_value = []
            with patch.object(voice.pyttsx3, 'init', return_value=engine):
                aidy.voice = voice.Voice(str(ROOT), aidy._before_playback, aidy._after_playback)
            aidy._play_wake_ack = lambda: core.Aidy._play_wake_ack(aidy)
            pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
            for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                stream._callback(pcm[offset:offset + FRAME_BYTES], 320, {}, 0)
            def recognized(*args, **kwargs):
                timings['accepted'] = time.perf_counter()
                return 'hey'
            def playback(data, flags):
                timings['output'] = time.perf_counter()
                assert stream._suspended
                assert states[-1] == 'SPEAKING'
            aidy.speech.transcribe.side_effect = recognized
            aidy._capture_utterance = Mock(return_value=np.ones(8000, np.float32))
            stream.read = Mock(side_effect=AssertionError('Blocked on new microphone input'))
            try:
                with patch.object(core, 'ui_state', side_effect=states.append), \
                     patch.object(voice.winsound, 'PlaySound', side_effect=playback):
                    assert aidy.wait_for_wake() is None
                aidy._capture_utterance.assert_called_once()
                assert states == ['LISTENING', 'SPEAKING', 'COMMAND_LISTENING'], states
                if case == 'complete_command':
                    assert aidy._post_wake_capture is not None, 'Lost a completed queued command'
                elapsed = (timings['output'] - timings['accepted']) * 1000
                rows.append(dict(case=case, repeat=repeat, dispatch_ms=round(elapsed, 3)))
            finally:
                stream.close()
    result = dict(median_ms=round(statistics.median(r['dispatch_ms'] for r in rows), 3),
                  max_ms=max(r['dispatch_ms'] for r in rows), rows=rows,
                  scope='Recognized wake to PlaySound entry; recognition and physical output latency excluded.')
    (ROOT / '.validation/wake-dispatch.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
