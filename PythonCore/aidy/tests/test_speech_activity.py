"""Noise, streaming VAD and speculative decode must never authorize fragments."""
from concurrent.futures import Future
import unittest
from unittest.mock import Mock, patch

import numpy as np
from faster_whisper.vad import get_vad_model

from test_audio_pipeline import bare_aidy, speech_collector, FakeStream, VOICE_FRAME, SILENT_FRAME
from aidy import assistant as core
from aidy.audio_capture import BufferedInput, UtteranceBuffer
from aidy.speech_activity import SpeechActivity


def fan_audio(seconds=1.6):
    t = np.arange(int(16000 * seconds)) / 16000
    rng = np.random.default_rng(36)
    return (.03 * (np.sin(2 * np.pi * 120 * t) + .4 * np.sin(2 * np.pi * 240 * t)
                   + rng.normal(0, .7, t.size))).astype(np.float32)


def completed(text):
    result = Future()
    result.set_result(text)
    return result


class SpeechActivityTests(unittest.TestCase):
    def test_streaming_state_matches_bundled_batch_model(self):
        pcm = (fan_audio()[:512 * 40] * 32767).astype(np.int16)
        # The bundled batch wrapper zeroes its last 64 samples in place while
        # constructing contexts. Use the same final samples for this comparison.
        pcm[-64:] = 0
        expected = get_vad_model()(pcm.astype(np.float32) / 32768).reshape(-1)
        detector = SpeechActivity()
        observed = []
        for offset in range(0, len(pcm), 512):
            detector.is_speech(pcm[offset:offset + 512].tobytes(), 16000)
            observed.append(detector.probability)
        np.testing.assert_allclose(observed, expected, atol=1e-5)
        other = SpeechActivity()
        self.assertFalse(np.shares_memory(detector.h, other.h))
        self.assertFalse(np.shares_memory(detector.context, other.context))

    def test_hysteresis_keeps_soft_endings_and_feeds_silence(self):
        session = Mock()
        state = np.zeros((1, 1, 128), np.float32)
        session.run.side_effect = [(np.array([p]), state, state) for p in (.9, .35, .2)]
        detector = SpeechActivity(session=session)
        self.assertTrue(detector.is_speech(bytes(1024), 16000))
        self.assertTrue(detector.is_speech(bytes(1024), 16000))
        self.assertFalse(detector.is_speech(bytes(1024), 16000))

    def test_fan_neither_holds_capture_open_nor_suppresses_wake_reply(self):
        pcm = (fan_audio() * 32767).astype(np.int16).tobytes()
        stream = BufferedInput(Mock())
        self.addCleanup(stream.close)
        collector = UtteranceBuffer(silence_ms=240)
        for offset in range(0, len(pcm) - 639, 640):
            frame = pcm[offset:offset + 640]
            collector.feed(frame)
            stream._callback(frame, 320, {}, 0)
        self.assertFalse(collector.started)
        self.assertFalse(stream.has_pending_speech())
        aidy = bare_aidy()
        aidy.voice.muted = False
        aidy.stream = stream
        aidy._capture_utterance = Mock(return_value=np.ones(8000, np.float32))
        aidy.speech.transcribe.return_value = 'hey'
        self.assertIsNone(aidy.wait_for_wake())
        aidy._play_wake_ack.assert_called_once()

    def test_preview_is_reused_only_after_endpoint(self):
        aidy = bare_aidy()
        stream = FakeStream([VOICE_FRAME] * 15 + [SILENT_FRAME] * 35)
        aidy.stream = stream
        aidy.start_stream = Mock()
        aidy.speech.supports_previews = True
        def preview(audio, purpose):
            self.assertEqual(stream.reads, 29)  # 280 ms pause, before endpoint.
            aidy.process_command.assert_not_called()
            return completed('open steam')
        aidy.speech.start_preview.side_effect = preview
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertEqual(aidy.listen_command(speak_on_empty=False), 'open steam')
        self.assertEqual(stream.reads, 50)  # The full 700 ms pause was observed.
        aidy.speech.transcribe.assert_not_called()
        aidy.process_command.assert_not_called()

    def test_continuation_invalidates_preview_without_losing_syllables(self):
        aidy = bare_aidy()
        stream = FakeStream([VOICE_FRAME] * 15 + [SILENT_FRAME] * 20
                            + [VOICE_FRAME] * 15 + [SILENT_FRAME] * 35)
        aidy.stream = stream
        aidy.start_stream = Mock()
        aidy.speech.supports_previews = True
        snapshots = []
        def preview(audio, purpose):
            snapshots.append(audio)
            return completed('open' if len(snapshots) == 1 else 'open steam')
        aidy.speech.start_preview.side_effect = preview
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertEqual(aidy.listen_command(speak_on_empty=False), 'open steam')
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(np.count_nonzero(snapshots[-1]), 30 * 320)
        self.assertEqual(stream.reads, 85)
        aidy.speech.transcribe.assert_not_called()
        aidy.process_command.assert_not_called()

    def test_wake_preview_starts_early_but_ack_waits_for_complete_endpoint(self):
        aidy = bare_aidy()
        stream = FakeStream([VOICE_FRAME] * 15 + [SILENT_FRAME] * 8)
        aidy.stream = stream
        aidy.start_stream = Mock()
        aidy.speech.supports_previews = True
        def preview(audio, purpose):
            self.assertEqual(stream.reads, 17)  # 40 ms quiet.
            aidy._play_wake_ack.assert_not_called()
            return completed('hey')
        aidy.speech.start_preview.side_effect = preview
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertIsNone(aidy.wait_for_wake())
        self.assertEqual(stream.reads, 21)  # 120 ms endpoint.
        aidy.speech.transcribe.assert_not_called()
        aidy._play_wake_ack.assert_called_once()

    def test_uncertain_wake_preview_checks_complete_audio_with_normal_window(self):
        aidy = bare_aidy()
        aidy.stream = FakeStream([VOICE_FRAME] * 15 + [SILENT_FRAME] * 35)
        aidy.start_stream = Mock()
        aidy.speech.supports_previews = True
        aidy.speech.start_preview.return_value = completed(None)
        aidy.speech.transcribe.return_value = 'hey aidy'
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertIsNone(aidy.wait_for_wake())
        aidy.speech.transcribe.assert_called_once()
        self.assertEqual(aidy.speech.transcribe.call_args.kwargs,
                         {'purpose': 'wake', 'skip_wake_probe': True})
        self.assertEqual(aidy.speech.transcribe.call_args.args[0].size, 50 * 320)

    def test_resumed_wake_speech_invalidates_preview_and_preserves_negation(self):
        aidy = bare_aidy()
        stream = FakeStream([VOICE_FRAME] * 15 + [SILENT_FRAME] * 4 +
                            [VOICE_FRAME] * 15 + [SILENT_FRAME] * 35)
        aidy.stream = stream
        aidy.start_stream = Mock()
        aidy.speech.supports_previews = True
        aidy.speech.start_preview.side_effect = [completed('hey'), completed(None)]
        aidy.speech.transcribe.return_value = "hey don't open steam"
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertEqual(aidy.wait_for_wake(), "don't open steam")
        self.assertEqual(stream.reads, 69)
        aidy.speech.transcribe.assert_called_once()
        self.assertEqual(aidy.speech.transcribe.call_args.args[0].size, 69 * 320)
        aidy._play_wake_ack.assert_called_once()

    def test_uncertain_preview_verifies_complete_audio_once(self):
        aidy = bare_aidy()
        aidy.stream = FakeStream([VOICE_FRAME] * 15 + [SILENT_FRAME] * 35)
        aidy.start_stream = Mock()
        aidy.speech.supports_previews = True
        aidy.speech.start_preview.return_value = completed(None)
        aidy.speech.transcribe.return_value = "don't open steam"
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertEqual(aidy.listen_command(speak_on_empty=False), "don't open steam")
        aidy.speech.transcribe.assert_called_once()
        self.assertTrue(aidy.speech.transcribe.call_args.kwargs['full_context'])
        self.assertEqual(aidy.speech.transcribe.call_args.args[0].size, 50 * 320)

    def test_ptt_never_previews_before_release(self):
        aidy = bare_aidy()
        aidy.push_to_talk_enabled = True
        aidy.speech.supports_previews = True
        stream = FakeStream([VOICE_FRAME] * 15 + [SILENT_FRAME] * 14)
        aidy.stream = stream
        aidy.start_stream = Mock()
        def release():
            if stream.reads == 29:
                aidy.stop_listening()
        aidy._drain_control_commands.side_effect = release
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertIsNotNone(aidy._capture_utterance(finalize_on_release=True))
        aidy.speech.start_preview.assert_not_called()


if __name__ == '__main__':
    unittest.main()
