import sys
from pathlib import Path
import unittest
import threading
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.audio_capture import AudioLevelMeter, BufferedInput


def pcm(amplitude):
    return (amplitude * np.sin(np.arange(320) * .25)).astype(np.int16).tobytes()


class AudioLevelTests(unittest.TestCase):
    def test_silence_noise_dc_and_loud_input_are_bounded(self):
        emitted = []
        meter = AudioLevelMeter(emitted.append, clock=Mock(side_effect=range(6)))
        for frame in (bytes(640), pcm(60), np.full(320, 3000, np.int16).tobytes(),
                      pcm(1000), pcm(5000), pcm(30000)):
            meter.feed(frame)
        self.assertEqual(emitted[:3], [0, 0, 0])
        self.assertGreater(emitted[3], 0)
        self.assertGreater(emitted[4], emitted[3])
        self.assertEqual(emitted[5], 1)

    def test_telemetry_is_throttled_and_close_resets_it(self):
        emit = Mock()
        meter = AudioLevelMeter(emit, clock=Mock(side_effect=[0, .02, .04, .06]))
        for _ in range(4):
            meter.feed(pcm(2000))
        self.assertEqual(emit.call_count, 2)
        meter.reset()
        emit.assert_called_with(0)

    def test_meter_preserves_audio_and_input_callback_closes_cleanly(self):
        driver = Mock()
        stream = BufferedInput(driver)
        self.addCleanup(stream.close)
        frame = pcm(2000)
        stream._callback(frame, 320, {}, 0)
        self.assertEqual(stream.read(320), frame)
        stream.close()
        self.assertFalse(stream._level_meter._worker.is_alive())

    def test_blocked_animation_output_never_blocks_microphone_callback(self):
        entered = threading.Event()
        release = threading.Event()
        meter = Mock()
        meter.feed.side_effect = lambda data: (entered.set(), release.wait(2))
        with patch('aidy.audio_capture.AudioLevelMeter', return_value=meter):
            stream = BufferedInput(Mock())
        frame = pcm(2000)
        try:
            stream._callback(frame, 320, {}, 0)
            self.assertTrue(entered.wait(1))
            done = threading.Event()
            def capture():
                for _ in range(20):
                    stream._callback(frame, 320, {}, 0)
                done.set()
            callback = threading.Thread(target=capture, daemon=True)
            callback.start()
            self.assertTrue(done.wait(1), 'UI output blocked audio input')
            for _ in range(21):
                self.assertEqual(stream.read(320), frame)
            self.assertLessEqual(len(stream._level_meter._latest), 1)
            stream.close()
        finally:
            release.set()
            stream.close()
            stream._level_meter._worker.join(timeout=1)
        self.assertFalse(stream._level_meter._worker.is_alive())

    def test_failed_audio_open_closes_animation_worker(self):
        driver = Mock()
        driver.open.side_effect = OSError('device unavailable')
        relay = Mock()
        with patch('aidy.audio_capture.AudioLevelRelay', return_value=relay):
            with self.assertRaises(OSError):
                BufferedInput(driver)
        relay.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
