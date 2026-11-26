"""Replay sample loss at real capture boundaries; never use physical devices."""
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pyaudio

from test_audio_pipeline import bare_aidy, speech_collector, VOICE_FRAME, SILENT_FRAME
from aidy import assistant as core
from aidy.audio_capture import AudioDiscontinuityError, BufferedInput


class AudioRecoveryTests(unittest.TestCase):
    def stream(self):
        driver = Mock()
        driver.open.return_value.is_active.return_value = True
        stream = BufferedInput(driver)
        self.addCleanup(stream.close)
        return stream

    def phrase(self, stream):
        for frame in [VOICE_FRAME] * 15 + [SILENT_FRAME] * 35:
            stream._callback(frame, 320, {}, 0)

    def test_overflow_and_stale_audio_have_distinct_recoverable_error(self):
        for kind in ('driver', 'queue', 'stale'):
            with self.subTest(kind=kind):
                stream = self.stream()
                if kind == 'driver':
                    stream._callback(VOICE_FRAME, 320, {}, pyaudio.paInputOverflow)
                elif kind == 'queue':
                    for _ in range(501):
                        stream._callback(VOICE_FRAME, 320, {}, 0)
                else:
                    stream._frames.put((0, VOICE_FRAME))
                with self.assertRaises(AudioDiscontinuityError):
                    stream.read(320)

    def test_affected_command_is_discarded_and_next_capture_works(self):
        aidy = bare_aidy()
        broken, fresh = self.stream(), self.stream()
        aidy.stream = broken
        broken._callback(VOICE_FRAME, 320, {}, pyaudio.paInputOverflow)
        self.phrase(fresh)
        def reopen():
            if aidy.stream is None:
                aidy.stream = fresh
                aidy._audio_generation += 1
        aidy.start_stream = Mock(side_effect=reopen)
        aidy.speech.transcribe.return_value = 'open browser'
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertIsNone(aidy.listen_command(speak_on_empty=False))
            aidy.speech.transcribe.assert_not_called()
            self.assertTrue(broken._closed)
            self.assertFalse(aidy._stop_event.is_set())
            self.assertEqual(aidy.listen_command(speak_on_empty=False), 'open browser')
        aidy.speech.transcribe.assert_called_once()
        aidy.process_command.assert_not_called()

    def test_sample_loss_during_whisper_discards_wake_and_inline_command(self):
        aidy = bare_aidy()
        aidy.stream = self.stream()
        broken = aidy.stream
        def decode(*args, **kwargs):
            broken._callback(VOICE_FRAME, 320, {}, pyaudio.paInputOverflow)
            return 'hey open browser'
        aidy.speech.transcribe.side_effect = decode
        self.assertIsNone(aidy._transcribe_capture(np.ones(8000, np.float32), purpose='wake'))
        self.assertTrue(broken._closed)
        self.assertIsNone(aidy.stream)
        aidy.process_command.assert_not_called()
        self.assertFalse(aidy._stop_event.is_set())

    def test_pending_check_race_restarts_wake_without_action(self):
        aidy = bare_aidy()
        aidy.stream = self.stream()
        aidy.speech.transcribe.return_value = 'hey open browser'
        # Loss immediately after the post-inference health check, even when
        # the transcript already contains a command and needs no queue scan.
        aidy.stream.check_health = Mock(side_effect=[None, None, AudioDiscontinuityError('overflow')])
        def capture(**kwargs):
            if aidy.stream is None:
                aidy._stop_event.set()
                return None
            return np.ones(8000, np.float32)
        aidy._capture_utterance = Mock(side_effect=capture)
        self.assertEqual(aidy.wait_for_wake(), aidy.PTT_PAUSED_TOKEN)
        aidy.process_command.assert_not_called()
        aidy._play_wake_ack.assert_not_called()

    def test_wake_loop_recovers_before_accepting_a_fresh_utterance(self):
        aidy = bare_aidy()
        broken, fresh = self.stream(), self.stream()
        aidy.stream = broken
        broken._callback(VOICE_FRAME, 320, {}, pyaudio.paInputOverflow)
        self.phrase(fresh)
        def reopen():
            if aidy.stream is None:
                aidy.stream = fresh
                aidy._audio_generation += 1
        aidy.start_stream = Mock(side_effect=reopen)
        aidy.speech.transcribe.return_value = 'hey open browser'
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertEqual(aidy.wait_for_wake(), 'open browser')
        aidy.speech.transcribe.assert_called_once()
        self.assertFalse(aidy._stop_event.is_set())


if __name__ == '__main__':
    unittest.main()
