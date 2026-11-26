"""Replay short navigation replies with real VAD/Whisper; no devices or actions.

Compare the previous app-name context/15-second window with the navigation
context/8-second window. Also verify the clean bundled confirmation recording.
"""
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from unittest.mock import Mock

import numpy as np
import win32com.client
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
sys.path.insert(0, str(ROOT / 'PythonCore/aidy/tests'))
from test_audio_pipeline import bare_aidy
from aidy.audio_capture import BufferedInput, FRAME_BYTES
from aidy.speech import SpeechRecognizer


def replay(recognizer, samples, word, mode, noise):
    active = np.flatnonzero(np.abs(samples) > max(.002, np.abs(samples).max() * .006))
    speech_end = .4 + (int(active[-1]) + 1) / 16000
    samples = np.concatenate([np.zeros(6400, np.float32), samples, np.zeros(16000, np.float32)])
    t = np.arange(samples.size) / 16000
    rng = np.random.default_rng(36)
    fan = np.sin(2 * np.pi * 120 * t) + .4 * np.sin(2 * np.pi * 240 * t) + rng.normal(0, .7, t.size)
    pcm = (np.clip(samples + noise * fan, -1, 1) * 32767).astype(np.int16).tobytes()
    aidy = bare_aidy()
    aidy.speech = recognizer
    aidy.window_switch_active = mode == 'after'
    aidy.start_stream = Mock()
    stream = BufferedInput(Mock())
    aidy.stream = stream
    started = time.perf_counter()
    def feed():
        for i, offset in enumerate(range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)):
            if aidy._stop_event.wait(max(0, started + i * .02 - time.perf_counter())):
                return
            stream._callback(pcm[offset:offset + FRAME_BYTES], 320, {}, 0)
    producer = threading.Thread(target=feed, daemon=True)
    watchdog = threading.Timer(10, aidy._stop_event.set)
    try:
        watchdog.start()
        producer.start()
        text = aidy.listen_command(max_seconds=3, speak_on_empty=False)
        assert text == word, (word, text, mode, noise)
        return dict(word=word, mode=mode, noise=noise,
                    after_speech_ms=round((time.perf_counter() - started - speech_end) * 1000))
    finally:
        aidy._stop_event.set()
        watchdog.cancel()
        producer.join(timeout=2)
        stream.close()


def main():
    output = ROOT / '.validation'
    output.mkdir(exist_ok=True)
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    voice.Voice = next(v for v in voice.GetVoices() if 'zira' in v.GetDescription().lower())
    recordings = {}
    for word in ('left', 'right', 'done'):
        path = output / f'control-{word}.wav'
        stream = win32com.client.Dispatch('SAPI.SpFileStream')
        stream.Format.Type = 18
        stream.Open(str(path), 3)
        voice.AudioOutputStream = stream
        voice.Speak(word + '.')
        stream.Close()
        recordings[word] = decode_audio(str(path), sampling_rate=16000)
    recognizer = SpeechRecognizer()
    rows = []
    try:
        for noise in (0.0, .03):
            for word, samples in recordings.items():
                for mode in ('before', 'after'):
                    row = replay(recognizer, samples, word, mode, noise)
                    rows.append(row)
                    print(json.dumps(row), flush=True)
        clean = decode_audio(str(ROOT / 'Assets/voice/are_you_sure_clean.wav'), sampling_rate=16000)
        assert recognizer.transcribe(clean, purpose='confirmation') == 'are you sure'
        assert np.max(np.abs(clean)) < .99, 'Clipped confirmation recording'
    finally:
        recognizer.close()
    summary = {mode: round(statistics.median(row['after_speech_ms'] for row in rows if row['mode'] == mode))
               for mode in ('before', 'after')}
    result = dict(median_ms=summary, rows=rows, confirmation_seconds=round(clean.size / 16000, 3),
                  scope='Synthesized short navigation replies in real time, including fan noise; '
                        'no actual system actions or output-device latency.')
    (output / 'response-timing.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
