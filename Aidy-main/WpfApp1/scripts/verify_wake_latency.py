"""Compare streamed wake-to-reply latency; real Whisper/VAD, no mic or OS actions.

Uses the synthesized recordings produced by verify_speech.py. Reply playback is
mocked, so measurements end at the playback hook, before output-device latency.
"""
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from unittest.mock import Mock, patch

import numpy as np
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
sys.path.insert(0, str(ROOT / 'PythonCore/aidy/tests'))
from test_audio_pipeline import bare_aidy
from aidy import assistant as core, speech
from aidy.audio_capture import BufferedInput, UtteranceBuffer, FRAME_BYTES


def replay(recognizer, path, mode, settings=None):
    samples = decode_audio(str(path), sampling_rate=16000)
    samples = np.concatenate([samples, np.zeros(32000, np.float32)])
    pcm = (samples * 32767).astype(np.int16).tobytes()
    frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)]
    detector = UtteranceBuffer(silence_ms=240)
    last_voice = 0
    for index, frame in enumerate(frames):
        ended = detector.feed(frame)
        if detector.started and detector.quiet_frames == 0:
            last_voice = index * .02
        if ended:
            break
    assert detector.finish() is not None, path.name

    aidy = bare_aidy()
    aidy.speech = recognizer
    aidy.voice.muted = False
    aidy._play_wake_ack = lambda: core.Aidy._play_wake_ack(aidy)
    stream = BufferedInput(Mock())
    aidy.stream = stream
    aidy.start_stream = Mock()
    timings = {}
    states = []
    def reply(key):
        timings['reply'] = time.perf_counter()
        aidy._before_playback()
        aidy._after_playback()
        return True
    aidy.voice.play_key.side_effect = reply
    def feed():
        timings['start'] = time.perf_counter()
        for index, frame in enumerate(frames):
            remaining = timings['start'] + index * .02 - time.perf_counter()
            if aidy._stop_event.wait(max(0, remaining)):
                return
            stream._callback(frame, 320, {}, 0)
    producer = threading.Thread(target=feed, daemon=True)
    watchdog = threading.Timer(10, aidy._stop_event.set)
    try:
        endpoint, preview, window = settings or ((240, 120, 15) if mode == 'before' else
            (core.WAKE_SILENCE_MS, core.WAKE_PREVIEW_SILENCE_MS, speech.WAKE_PROBE_WINDOW_SECONDS))
        with patch.object(core, 'WAKE_SILENCE_MS', endpoint), \
             patch.object(core, 'WAKE_PREVIEW_SILENCE_MS', preview), \
             patch.object(speech, 'WAKE_PROBE_WINDOW_SECONDS', window), \
             patch.object(core, 'ui_state', side_effect=states.append):
            watchdog.start()
            producer.start()
            tail = aidy.wait_for_wake()
            assert tail is None and 'reply' in timings, (mode, path.name, tail, states)
            assert states == ['LISTENING', 'SPEAKING', 'COMMAND_LISTENING'], states
        return dict(mode=mode, file=path.name,
                    reply_after_vad_speech_end_ms=round((timings['reply'] - timings['start'] - last_voice) * 1000, 1))
    finally:
        aidy._stop_event.set()
        watchdog.cancel()
        producer.join(timeout=2)
        stream.close()


def main():
    recognizer = speech.SpeechRecognizer()
    rows = []
    try:
        for repeat in range(3):
            for index in (0, 14):  # Hey / Aidy, identical PCM in both modes.
                for mode in (('before', 'after') if repeat % 2 == 0 else ('after', 'before')):
                    row = replay(recognizer, ROOT / f'.validation/synthesized-{index}.wav', mode)
                    rows.append(dict(repeat=repeat, **row))
                    print(json.dumps(rows[-1]), flush=True)
    finally:
        recognizer.close()
    summary = {mode: round(statistics.median(row['reply_after_vad_speech_end_ms']
                        for row in rows if row['mode'] == mode), 1) for mode in ('before', 'after')}
    result = dict(median_ms=summary, rows=rows,
                  scope='Synthesized Hey/Aidy, real-time input and real VAD/Whisper; '
                        'speech end is the last VAD-positive frame; excludes output-device latency.')
    (ROOT / '.validation/wake-latency.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
