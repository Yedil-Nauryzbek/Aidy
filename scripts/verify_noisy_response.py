"""Real-time fan/hum replay through capture + Whisper; actions and replies mocked.

Run verify_speech.py first to create the synthesized files. Never uses a microphone.
"""
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock, patch

import numpy as np
from faster_whisper.audio import decode_audio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy import assistant as core
from aidy.audio_capture import BufferedInput, FRAME_BYTES
from aidy.speech import SpeechRecognizer


def main():
    rows = []
    with patch.object(core.pyaudio, 'PyAudio'), \
         patch.object(core, 'Voice', return_value=Mock(muted=False)), \
         patch.object(core, 'start_local_intent_api', return_value=True), \
         patch.object(core, 'stop_local_intent_api'):
        aidy = core.Aidy(str(ROOT))
        aidy.speech = SpeechRecognizer()
        aidy._play_wake_ack = Mock()
        aidy._detect_wake_with_tail = Mock(wraps=aidy._detect_wake_with_tail)
        aidy.start_stream = Mock()
        try:
            for level in (0.0, .03):
                for index, purpose, expected in [(0, 'wake', 'hey'), (14, 'wake', 'aidy'),
                                                  (4, 'command', 'open browser'),
                                                  (8, 'command', 'close steam'),
                                                  (30, 'command', 'please do not close discord')]:
                    source = decode_audio(str(ROOT / f'.validation/synthesized-{index}.wav'), sampling_rate=16000)
                    active = np.flatnonzero(np.abs(source) > max(.002, float(np.abs(source).max()) * .006))
                    speech_end = 1 + (int(active[-1]) + 1) / 16000
                    samples = np.concatenate([np.zeros(16000, np.float32), source, np.zeros(32000, np.float32)])
                    t = np.arange(samples.size) / 16000
                    rng = np.random.default_rng(36)
                    fan = np.sin(2 * np.pi * 120 * t) + .4 * np.sin(2 * np.pi * 240 * t) + rng.normal(0, .7, t.size)
                    pcm = (np.clip(samples + fan * level, -1, 1) * 32767).astype(np.int16).tobytes()
                    driver = Mock()
                    driver.open.return_value.is_active.return_value = True
                    stream = BufferedInput(driver)
                    aidy.stream = stream
                    aidy._audio_generation += 1
                    aidy._play_wake_ack.reset_mock()
                    aidy._detect_wake_with_tail.reset_mock()
                    feeding = threading.Event()
                    feeding.set()
                    started = time.perf_counter()
                    def feed():
                        for frame_index, offset in enumerate(range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)):
                            if not feeding.is_set():
                                return
                            delay = started + frame_index * .02 - time.perf_counter()
                            if delay > 0:
                                time.sleep(delay)
                            stream._callback(pcm[offset:offset + FRAME_BYTES], 320, {}, 0)
                    producer = threading.Thread(target=feed, daemon=True)
                    producer.start()
                    try:
                        if purpose == 'wake':
                            assert aidy.wait_for_wake() is None
                            aidy._play_wake_ack.assert_called_once()
                            text = aidy._detect_wake_with_tail.call_args.args[0]
                        else:
                            text = aidy.listen_command(speak_on_empty=False)
                            assert text == expected, (expected, text)
                        response_ms = round((time.perf_counter() - started - speech_end) * 1000)
                        row = dict(phrase=expected, fan_level=level, text=text,
                                   after_speech_ms=response_ms, wake_reply_requested=aidy._play_wake_ack.called)
                        rows.append(row)
                        print(json.dumps(row), flush=True)
                    finally:
                        feeding.clear()
                        producer.join(timeout=1)
                        aidy.stop_stream()
        finally:
            aidy.close()
    (ROOT / '.validation/noisy-response.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
