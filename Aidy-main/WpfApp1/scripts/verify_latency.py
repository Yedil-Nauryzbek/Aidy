"""Compare warm Whisper decoding on identical synthesized audio; no OS actions."""
import json
from pathlib import Path
import statistics
import sys
import time

import numpy as np
import win32com.client
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.audio_capture import UtteranceBuffer, FRAME_BYTES
from aidy.config import VAD_SILENCE_MS
from aidy.speech import SpeechRecognizer


def main():
    output = ROOT / '.validation'
    output.mkdir(exist_ok=True)
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    voices = voice.GetVoices()
    for index in range(voices.Count):
        if 'zira' in voices.Item(index).GetDescription().lower():
            voice.Voice = voices.Item(index)
            break
    cases = [('hey', 'wake'), ('aidy', 'wake'),
             ('open browser', 'command'), ('close steam', 'command')]
    recordings = []
    for index, (phrase, purpose) in enumerate(cases):
        path = output / f'latency-{index}.wav'
        stream = win32com.client.Dispatch('SAPI.SpFileStream')
        stream.Format.Type = 18
        stream.Open(str(path), 3)
        voice.AudioOutputStream = stream
        voice.Speak(phrase + '.')
        stream.Close()
        samples = decode_audio(str(path), sampling_rate=16000)
        samples = np.concatenate([samples, np.zeros(16000, np.float32)])
        pcm = (samples * 32767).astype(np.int16).tobytes()
        collector = UtteranceBuffer(silence_ms=VAD_SILENCE_MS)
        for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
            if collector.feed(pcm[offset:offset + FRAME_BYTES]):
                break
        audio = collector.finish()
        assert audio is not None, phrase
        recordings.append((phrase, purpose, audio))

    recognizer = SpeechRecognizer()
    rows = []
    try:
        # Warm both encoder shapes, then alternate order to reduce load bias.
        for window in (30, 15):
            recognizer.window_seconds = window
            recognizer.transcribe(recordings[0][2], purpose='wake')
        for repeat in range(3):
            for phrase, purpose, audio in recordings:
                for window in ((30, 15) if repeat % 2 == 0 else (15, 30)):
                    recognizer.window_seconds = window
                    started = time.perf_counter()
                    text = recognizer.transcribe(audio, purpose=purpose)
                    elapsed = time.perf_counter() - started
                    assert text == phrase, (phrase, window, text)
                    rows.append(dict(phrase=phrase, window=window, repeat=repeat,
                                     text=text, seconds=round(elapsed, 4)))
    finally:
        recognizer.close()
    summary = {str(window): {
        'mean_seconds': round(statistics.mean(r['seconds'] for r in rows if r['window'] == window), 4),
        'median_seconds': round(statistics.median(r['seconds'] for r in rows if r['window'] == window), 4),
    } for window in (30, 15)}
    result = dict(model=recognizer.model_id, device=recognizer.device,
                  threads=recognizer.cpu_threads, summary=summary, rows=rows,
                  scope='Warm decoding only; excludes endpoint silence, reply playback and OS actions.')
    (output / 'latency-comparison.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
