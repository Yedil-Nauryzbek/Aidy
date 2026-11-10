"""Regression tests for activation, capture, and orchestration; no real devices/actions."""
import os
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aidy import assistant as assistant_module
from aidy.assistant import Aidy
from aidy.activation import match_wake
from aidy.audio_capture import BufferedInput, FRAME_BYTES, UtteranceBuffer
from aidy.config import WAKE_KEYWORDS


VOICE_FRAME = np.full(320, 1200, dtype=np.int16).tobytes()
SILENT_FRAME = bytes(FRAME_BYTES)


class SpeechVAD:
    def is_speech(self, frame, rate):
        return any(frame)


class FakeStream:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.closed = False
        self.active = True
        self.reads = 0

    def read(self, samples, exception_on_overflow):
        self.reads += 1
        return next(self.frames, b"")

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        self.closed = True


def bare_aidy():
    """Skip device/model/TTS construction while retaining actual Aidy methods."""
    aidy = Aidy.__new__(Aidy)
    aidy._stop_event = threading.Event()
    aidy._audio_lock = threading.RLock()
    aidy._run_lock = threading.Lock()
    aidy._study_lock = threading.RLock()
    aidy._playback_active = threading.Event()
    aidy._audio_generation = 0
    aidy._capture_rate = 16000
    aidy._resample_state = None
    aidy._pcm_pending = b""
    aidy._deafen_until = 0
    aidy._closed = False
    aidy.stream = None
    aidy.audio = Mock()
    aidy.config_path = os.path.join(ROOT, "__nonexistent_test_config__.json")
    aidy.voice = Mock(muted=True)
    aidy.speech = Mock()
    aidy.apps = []
    aidy.push_to_talk_enabled = False
    aidy._ptt_listening_flag = True
    aidy._ptt_consumed = False
    aidy.window_switch_active = False
    aidy.study_restore_prompt_pending = False
    aidy.command_phrases = []
    aidy.follow_up = Mock()
    aidy.follow_up.get_pending.return_value = None
    aidy.follow_mode = Mock()
    aidy.follow_mode.is_active.return_value = False
    aidy.history = Mock()
    aidy._refresh_runtime_config_if_needed = Mock()
    aidy._drain_control_commands = Mock()
    aidy._handle_due_tasks = Mock()
    aidy._play_wake_ack = Mock()
    aidy.process_command = Mock(return_value=True)
    # run() cleanup is separately exercised elsewhere; never touch real services.
    aidy.close = Mock(side_effect=aidy._stop_event.set)
    return aidy


def speech_collector(**kwargs):
    return UtteranceBuffer(vad=SpeechVAD(), **kwargs)


class ActivationTests(unittest.TestCase):
    def test_every_configured_wake_preserves_the_command_and_negation(self):
        for phrase in WAKE_KEYWORDS:
            with self.subTest(phrase=phrase):
                self.assertEqual(match_wake(phrase, WAKE_KEYWORDS), (True, ""))
                self.assertEqual(match_wake(phrase + ", don't close Steam", WAKE_KEYWORDS),
                                 (True, "don't close steam"))

    def test_complete_english_wake_extracts_command(self):
        self.assertEqual(match_wake("Hey Aidy, open browser!", WAKE_KEYWORDS),
                         (True, "open browser"))
        self.assertEqual(match_wake("hey 80 open browser", WAKE_KEYWORDS),
                         (True, "open browser"))
        self.assertEqual(match_wake("Hey Aidy, don't open browser!", WAKE_KEYWORDS),
                         (True, "don't open browser"))
        self.assertEqual(match_wake("Hey Aidy, open example.com", WAKE_KEYWORDS),
                         (True, "open example.com"))

    def test_bare_greetings_and_ordinary_speech_do_not_activate(self):
        for text in ("hi", "okay", "open browser", "william", "well im up",
                     "eighty", "80", "please tell aidy to open browser", "aidyman"):
            with self.subTest(text=text):
                self.assertEqual(match_wake(text, WAKE_KEYWORDS), (False, ""))


class CollectorTests(unittest.TestCase):
    def test_silence_and_brief_noise_are_rejected(self):
        for frames in ([SILENT_FRAME] * 50,
                       [VOICE_FRAME] * 4 + [SILENT_FRAME] * 30):
            collector = speech_collector()
            for frame in frames:
                if collector.feed(frame):
                    break
            self.assertIsNone(collector.finish())

    def test_valid_pcm_is_normalized_mono_float32(self):
        collector = speech_collector(silence_ms=40)
        for frame in [VOICE_FRAME] * 12 + [SILENT_FRAME] * 2:
            done = collector.feed(frame)
        self.assertTrue(done)
        audio = collector.finish()
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(audio.ndim, 1)
        self.assertAlmostEqual(float(audio.max()), 1200 / 32768)

    def test_duration_limit_rejects_incomplete_command(self):
        collector = speech_collector(max_seconds=.3)
        for _ in range(20):
            if collector.feed(VOICE_FRAME):
                break
        self.assertTrue(collector.truncated)
        self.assertIsNone(collector.finish())

    def test_bad_frame_size_is_explicit_error(self):
        with self.assertRaises(ValueError):
            speech_collector().feed(b"bad")


class CaptureIntegrationTests(unittest.TestCase):
    def test_standalone_wake_restores_acknowledgement(self):
        aidy = bare_aidy()
        aidy._capture_utterance = Mock(return_value=np.ones(8000, np.float32))
        aidy.speech.transcribe.return_value = "eddie"
        self.assertIsNone(aidy.wait_for_wake())
        aidy._play_wake_ack.assert_called_once()
        aidy.process_command.assert_not_called()

    def test_pending_speech_check_does_not_consume_input_or_accept_silence(self):
        stream = BufferedInput(Mock(), rate=16000)
        for _ in range(20):
            stream._callback(SILENT_FRAME, 320, {}, 0)
        self.assertFalse(stream.has_pending_speech())
        for _ in range(10):
            stream._callback(VOICE_FRAME, 320, {}, 0)
        with patch("aidy.audio_capture.SpeechActivity", return_value=SpeechVAD()):
            self.assertTrue(stream.has_pending_speech())
        self.assertEqual(stream.read(320), SILENT_FRAME)
        self.assertEqual(stream._frames.qsize(), 29)

    def test_wake_uses_short_clip_with_recording_and_tts_fallbacks(self):
        aidy = bare_aidy()
        aidy.voice.muted = False
        aidy.voice.play_key.return_value = True
        Aidy._play_wake_ack(aidy)
        aidy.voice.play_key.assert_called_once_with('wake_fast')
        aidy.voice.play_key.reset_mock()
        aidy.voice.play_key.side_effect = [False, True]
        Aidy._play_wake_ack(aidy)
        self.assertEqual([c.args[0] for c in aidy.voice.play_key.call_args_list], ['wake_fast', 'wake'])
        aidy.voice.play_key.side_effect = None
        aidy.voice.play_key.return_value = False
        Aidy._play_wake_ack(aidy)
        aidy.voice.tts_blocking.assert_called_once_with("I'm listening.")
        aidy.voice.reset_mock()
        aidy.voice.muted = True
        Aidy._play_wake_ack(aidy)
        aidy.voice.play_key.assert_not_called()

    def test_pending_speech_supports_44100_hz_microphones(self):
        stream = BufferedInput(Mock(), rate=44100)
        frame = np.full(882, 1200, np.int16).tobytes()
        for _ in range(10):
            stream._callback(frame, 882, {}, 0)
        vad = Mock()
        vad.is_speech.return_value = True
        with patch("aidy.audio_capture.SpeechActivity", return_value=vad):
            self.assertTrue(stream.has_pending_speech())
        for call in vad.is_speech.call_args_list:
            self.assertEqual(len(call.args[0]), FRAME_BYTES)
            self.assertEqual(call.args[1], 16000)
        self.assertEqual(stream.read(882), frame)

    def test_ptt_does_not_finalize_on_a_pause_before_release(self):
        aidy = bare_aidy()
        aidy.push_to_talk_enabled = True
        stream = FakeStream([VOICE_FRAME] * 12 + [SILENT_FRAME] * 30 + [VOICE_FRAME] * 12)
        aidy.stream = stream
        aidy.start_stream = Mock()
        def release():
            if stream.reads == 54:
                aidy.stop_listening()
        aidy._drain_control_commands.side_effect = release
        with patch.object(assistant_module, "UtteranceBuffer", side_effect=speech_collector):
            audio = aidy._capture_utterance(finalize_on_release=True)
        self.assertEqual(audio.size, 54 * 320)

    def test_inference_keeps_listening_state_stable(self):
        aidy = bare_aidy()
        aidy.speech.transcribe.return_value = 'closed steam'
        with patch.object(assistant_module, 'ui_state') as state:
            self.assertEqual(aidy._transcribe_capture(np.ones(16000, np.float32)), 'closed steam')
        state.assert_not_called()

    def test_command_spoken_during_wake_inference_is_preserved(self):
        aidy = bare_aidy()
        aidy.voice.muted = False
        driver = Mock()
        driver.open.return_value.is_active.return_value = True
        stream = BufferedInput(driver)
        aidy.stream = stream
        aidy.start_stream = Mock()
        self.addCleanup(stream.close)
        aidy._play_wake_ack = lambda: Aidy._play_wake_ack(aidy)
        def reply(key):
            aidy._before_playback()
            # Assistant audio must never be mistaken for the queued command.
            stream._callback(VOICE_FRAME, 320, {}, 0)
            aidy._after_playback()
            return True
        aidy.voice.play_key.side_effect = reply

        def enqueue_phrase():
            for frame in [VOICE_FRAME] * 15 + [SILENT_FRAME] * 35:
                stream._callback(frame, 320, {}, 0)

        enqueue_phrase()

        def transcribe(audio, purpose):
            if purpose == "wake":
                # This is exactly the user's report: the next command arrives
                # before recognition of "Hey" has returned.
                enqueue_phrase()
                return "hey"
            return "open browser"

        aidy.speech.transcribe.side_effect = transcribe
        with patch.object(assistant_module, "UtteranceBuffer", side_effect=speech_collector), \
             patch('aidy.audio_capture.SpeechActivity', return_value=SpeechVAD()), \
             patch.object(assistant_module, 'ui_state') as state:
            self.assertIsNone(aidy.wait_for_wake())
            self.assertFalse(stream._closed)
            aidy.voice.play_key.assert_called_once()
            self.assertEqual([call.args[0] for call in state.call_args_list],
                             ['LISTENING', 'SPEAKING', 'COMMAND_LISTENING'])
            text = aidy.listen_command(speak_on_empty=False)
            self.assertEqual(text, "open browser")
            self.assertIsNone(aidy._post_wake_capture)
            command_audio = aidy.speech.transcribe.call_args.args[0]
            self.assertEqual(np.count_nonzero(command_audio), 15 * 320)
            aidy._safe_process_command(text)
        aidy.process_command.assert_called_once_with("open browser")
        self.assertTrue(stream._closed)

    def test_combined_wake_and_command_also_acknowledges_before_command_state(self):
        aidy = bare_aidy()
        aidy.voice.muted = False
        aidy._capture_utterance = Mock(return_value=np.ones(8000, np.float32))
        aidy.speech.transcribe.return_value = "hey aidy don't open steam"
        aidy._play_wake_ack = lambda: Aidy._play_wake_ack(aidy)
        def reply(key):
            aidy._before_playback()
            aidy._after_playback()
            return True
        aidy.voice.play_key.side_effect = reply
        with patch.object(assistant_module, 'ui_state') as state:
            self.assertEqual(aidy.wait_for_wake(), "don't open steam")
        self.assertEqual([call.args[0] for call in state.call_args_list],
                         ['LISTENING', 'SPEAKING', 'COMMAND_LISTENING'])
        aidy.voice.play_key.assert_called_once()
        aidy.process_command.assert_not_called()

    def test_wake_reply_never_waits_for_or_scans_incoming_speech(self):
        aidy = bare_aidy()
        aidy.voice.muted = False
        stream = BufferedInput(Mock())
        self.addCleanup(stream.close)
        aidy.stream = stream
        # A long, unfinished phrase used to keep the acknowledgement waiting
        # for its endpoint. No fresh microphone read is permitted here.
        for _ in range(100):
            stream._callback(VOICE_FRAME, 320, {}, 0)
        stream.read = Mock(side_effect=AssertionError('Waited for microphone after wake acceptance'))
        stream.has_pending_speech = Mock(side_effect=AssertionError('Scanned speech before reply'))
        aidy._capture_utterance = Mock(return_value=np.ones(8000, np.float32))
        aidy.speech.transcribe.return_value = 'hey'
        aidy._play_wake_ack = lambda: Aidy._play_wake_ack(aidy)
        events = []
        def reply(key):
            events.append('reply')
            aidy._before_playback()
            # Samples arriving during playback cannot complete the old phrase.
            for _ in range(20):
                stream._callback(SILENT_FRAME, 320, {}, 0)
            aidy._after_playback()
            return True
        aidy.voice.play_key.side_effect = reply
        def collector(**kwargs):
            self.assertEqual(events, ['reply'])
            return speech_collector(**kwargs)
        with patch.object(assistant_module, 'UtteranceBuffer', side_effect=collector):
            self.assertIsNone(aidy.wait_for_wake())
        aidy._capture_utterance.assert_called_once()
        stream.read.assert_not_called()
        self.assertIsNone(aidy._post_wake_capture)
        self.assertEqual(stream._frames.qsize(), 0)

    def test_detached_queue_contains_only_audio_before_playback(self):
        stream = BufferedInput(Mock())
        self.addCleanup(stream.close)
        stream._callback(VOICE_FRAME, 320, {}, 0)
        snapshot = stream.suspend(take_pending=True)
        stream._callback(SILENT_FRAME, 320, {}, 0)
        self.assertEqual([data for _, data in snapshot], [VOICE_FRAME])
        self.assertEqual(stream._frames.qsize(), 0)
        stream.resume()
        self.assertEqual(stream.read(320), b'')

    def test_queued_command_resamples_44100_and_rejects_missing_endpoint(self):
        aidy = bare_aidy()
        import time
        voiced = np.full(882, 1200, np.int16).tobytes()
        quiet = bytes(len(voiced))
        frames = [(time.monotonic(), data) for data in [voiced] * 15 + [quiet] * 35]
        with patch.object(assistant_module, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertIsNotNone(aidy._completed_queued_command((frames, b'', 44100, None)))
            self.assertIsNone(aidy._completed_queued_command((frames[:15], b'', 44100, None)))
            self.assertIsNone(aidy._completed_queued_command(([(0, voiced)], b'', 44100, None)))

    def test_interruption_discards_command_saved_before_wake_reply(self):
        aidy = bare_aidy()
        aidy._post_wake_capture = (np.ones(8000, np.float32), None, 0)
        aidy.stop_stream()
        self.assertIsNone(aidy._post_wake_capture)

    def test_buffer_consumes_once_and_close_discards_audio(self):
        stream = BufferedInput(Mock())
        stream._callback(VOICE_FRAME, 320, {}, 0)
        self.assertEqual(stream.read(320), VOICE_FRAME)
        self.assertEqual(stream.read(320), b"")
        stream._callback(VOICE_FRAME, 320, {}, 0)
        stream.close()
        self.assertEqual(stream.read(320), b"")

    def test_buffer_overflow_never_replays_partial_audio(self):
        stream = BufferedInput(Mock())
        for _ in range(501):
            stream._callback(VOICE_FRAME, 320, {}, 0)
        with self.assertRaisesRegex(OSError, "overflow"):
            stream.read(320)

    def test_ptt_confirmation_waits_for_next_press_then_finalizes(self):
        aidy = bare_aidy()
        aidy.push_to_talk_enabled = True
        aidy._ptt_listening_flag = False
        stream = FakeStream([VOICE_FRAME] * 20)
        aidy.stream = stream
        aidy.start_stream = Mock()
        turns = 0

        def controls():
            nonlocal turns
            turns += 1
            if turns == 3:
                aidy.start_listening()
            if stream.reads == 15:
                aidy.stop_listening()

        aidy._drain_control_commands.side_effect = controls
        with patch.object(assistant_module, "UtteranceBuffer", side_effect=speech_collector):
            result = aidy._capture_utterance(wait_seconds=1, finalize_on_release=True)
        self.assertIsNotNone(result)
        self.assertGreater(turns, 3)
        aidy.process_command.assert_not_called()

    def test_microphone_open_failure_has_no_recognition_fallback(self):
        aidy = bare_aidy()
        aidy.audio.get_default_input_device_info.side_effect = OSError("no microphone")
        with self.assertRaises(OSError):
            aidy.start_stream()
        self.assertIsNone(aidy.stream)
        aidy.speech.transcribe.assert_not_called()
        aidy.process_command.assert_not_called()

    def test_microphone_read_error_never_transcribes_or_executes(self):
        aidy = bare_aidy()
        aidy.stream = Mock()
        aidy.stream.read.side_effect = OSError("input overflow")
        with self.assertRaises(OSError):
            aidy.listen_command(speak_on_empty=False)
        aidy.speech.transcribe.assert_not_called()
        aidy.process_command.assert_not_called()

    def test_empty_capture_never_runs_whisper(self):
        aidy = bare_aidy()
        aidy._capture_utterance = Mock(return_value=None)
        self.assertIsNone(aidy.listen_command(speak_on_empty=False))
        aidy.speech.transcribe.assert_not_called()

    def test_ptt_release_finalizes_existing_speech(self):
        aidy = bare_aidy()
        aidy.push_to_talk_enabled = True
        stream = FakeStream([VOICE_FRAME] * 10)
        aidy.stream = stream

        def release_after_speech():
            if stream.reads == 10:
                aidy.stop_listening()

        aidy._drain_control_commands.side_effect = release_after_speech
        with patch.object(assistant_module, "UtteranceBuffer", side_effect=speech_collector):
            audio = aidy._capture_utterance(finalize_on_release=True)
        self.assertIsNotNone(audio)
        self.assertGreaterEqual(audio.size, 3200)
        self.assertTrue(stream.closed)
        self.assertFalse(aidy._ptt_listening_flag)

    def test_playback_closes_capture_and_discards_old_partial_speech(self):
        aidy = bare_aidy()
        old_stream = FakeStream([VOICE_FRAME] * 10)
        fresh_stream = FakeStream([VOICE_FRAME] * 5 + [SILENT_FRAME] * 2)
        aidy.stream = old_stream
        interrupted = False

        def timer_speaks():
            nonlocal interrupted
            if not interrupted and old_stream.reads == 10:
                interrupted = True
                aidy._before_playback()
                aidy._after_playback()
                aidy._deafen_until = 0

        def reopen():
            if aidy.stream is None:
                aidy.stream = fresh_stream
                aidy._audio_generation += 1

        aidy._handle_due_tasks.side_effect = timer_speaks
        aidy.start_stream = Mock(side_effect=reopen)
        with patch.object(assistant_module, "UtteranceBuffer", side_effect=speech_collector):
            audio = aidy._capture_utterance(wait_seconds=.1, silence_ms=40)
        self.assertIsNone(audio)  # Fresh 100 ms is insufficient; old audio was discarded.
        self.assertTrue(old_stream.closed)
        self.assertFalse(aidy._playback_active.is_set())

    def test_playback_during_inference_invalidates_result(self):
        aidy = bare_aidy()

        def interrupted_transcription(*args, **kwargs):
            aidy._before_playback()
            aidy._after_playback()
            return "open browser"

        aidy.speech.transcribe.side_effect = interrupted_transcription
        self.assertIsNone(aidy._transcribe_capture(np.ones(3200, dtype=np.float32)))
        aidy.process_command.assert_not_called()

    def test_shutdown_during_inference_invalidates_result(self):
        aidy = bare_aidy()

        def cancelled_transcription(*args, **kwargs):
            aidy._stop_event.set()
            return "open browser"

        aidy.speech.transcribe.side_effect = cancelled_transcription
        self.assertIsNone(aidy._transcribe_capture(np.ones(3200, dtype=np.float32)))

    def test_empty_and_cancelled_commands_never_execute(self):
        aidy = bare_aidy()
        self.assertFalse(aidy._safe_process_command(" "))
        self.assertFalse(aidy._safe_process_command("don't open browser"))
        self.assertFalse(aidy._safe_process_command("please do not open browser"))
        aidy._stop_event.set()
        self.assertFalse(aidy._safe_process_command("open browser"))
        aidy.process_command.assert_not_called()

    def test_microphone_close_is_idempotent_and_clears_buffers(self):
        aidy = bare_aidy()
        stream = Mock()
        aidy.stream = stream
        aidy._pcm_pending = VOICE_FRAME
        aidy._resample_state = object()
        aidy.stop_stream()
        aidy.stop_stream()
        stream.close.assert_called_once()
        self.assertEqual(aidy._pcm_pending, b"")
        self.assertIsNone(aidy._resample_state)

    def test_wake_playback_reuses_device_but_discards_every_echo_frame(self):
        aidy = bare_aidy()
        driver = Mock()
        stream = BufferedInput(driver)
        self.addCleanup(stream.close)
        aidy.stream = stream
        aidy.voice.muted = False
        stream._callback(VOICE_FRAME, 320, {}, 0)
        generation = aidy._audio_generation
        aidy._before_playback()
        self.assertGreater(aidy._audio_generation, generation)
        self.assertTrue(stream._suspended)
        for _ in range(25):
            stream._callback(VOICE_FRAME, 320, {}, 0)
        self.assertEqual(stream._frames.qsize(), 0)
        aidy._after_playback()
        aidy.start_stream()
        self.assertTrue(stream._suspended)  # Acoustic tail guard still active.
        aidy._deafen_until = 0
        aidy.start_stream()
        self.assertFalse(stream._suspended)
        self.assertIs(aidy.stream, stream)
        driver.open.assert_called_once()
        driver.open.return_value.close.assert_not_called()
        stream._callback(VOICE_FRAME, 320, {}, 0)
        self.assertEqual(stream.read(320), VOICE_FRAME)


class MainLoopTests(unittest.TestCase):
    def test_real_command_router_launches_browser_once_per_request(self):
        base = os.path.abspath(os.path.join(ROOT, ".."))
        with patch.object(assistant_module.pyaudio, "PyAudio"), \
             patch.object(assistant_module, "Voice", return_value=Mock(muted=True)), \
             patch.object(assistant_module, "start_local_intent_api", return_value=True), \
             patch.object(assistant_module, "stop_local_intent_api"), \
             patch.object(assistant_module, "launch_app", return_value=True) as launch:
            aidy = Aidy(base_dir=base)
            aidy._sleep_success = Mock()
            aidy._record_action = Mock()
            try:
                self.assertTrue(aidy._safe_process_command("open browser"))
                self.assertEqual(launch.call_count, 1)
                self.assertTrue(aidy._safe_process_command("open browser"))
                self.assertEqual(launch.call_count, 2)
                self.assertFalse(aidy._safe_process_command("don't open browser"))
                self.assertEqual(launch.call_count, 2)
            finally:
                aidy.close()

    def run_utterances(self, texts):
        aidy = bare_aidy()
        aidy.start_stream = Mock()
        captures = iter(texts)

        def capture(**kwargs):
            try:
                text = next(captures)
            except StopIteration:
                aidy._stop_event.set()
                return None
            if text is None:
                return None
            aidy.speech.transcribe.return_value = text
            return np.ones(3200, dtype=np.float32)

        aidy._capture_utterance = Mock(side_effect=capture)
        aidy.apps = []
        with patch.object(assistant_module, "SpeechRecognizer", return_value=aidy.speech):
            aidy.run()
        return aidy

    def test_each_intentional_activation_executes_once(self):
        aidy = self.run_utterances([
            "hey aidy open browser", "open browser", "hey aidy open browser",
        ])
        self.assertEqual(aidy.process_command.call_count, 2)
        self.assertEqual([call.args[0] for call in aidy.process_command.call_args_list],
                         ["open browser", "open browser"])
        self.assertEqual(aidy._play_wake_ack.call_count, 2)

    def test_speech_without_wake_never_executes(self):
        aidy = self.run_utterances(["open browser", "hello", "eighty", "well im up"])
        aidy.process_command.assert_not_called()
        aidy._play_wake_ack.assert_not_called()

    def test_wake_without_command_times_out_without_action(self):
        aidy = self.run_utterances(["hey aidy", None])
        aidy._play_wake_ack.assert_called_once()
        aidy.process_command.assert_not_called()

    def test_model_load_failure_cleans_up_without_actions(self):
        aidy = bare_aidy()
        aidy.start_stream = Mock()
        with patch.object(assistant_module, "SpeechRecognizer", side_effect=RuntimeError("model unavailable")):
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                aidy.run()
        aidy.close.assert_called_once()
        aidy.start_stream.assert_not_called()
        aidy.process_command.assert_not_called()

    def test_startup_microphone_failure_cleans_up_without_actions(self):
        aidy = bare_aidy()
        aidy.audio.get_default_input_device_info.side_effect = OSError("no input device")
        with patch.object(assistant_module, "SpeechRecognizer", return_value=aidy.speech):
            with self.assertRaisesRegex(OSError, "no input device"):
                aidy.run()
        aidy.close.assert_called_once()
        aidy.process_command.assert_not_called()

    def test_duplicate_run_is_rejected_before_loading_model(self):
        aidy = bare_aidy()
        aidy._run_lock.acquire()
        with patch.object(assistant_module, "SpeechRecognizer") as load:
            with self.assertRaisesRegex(RuntimeError, "already running"):
                aidy.run()
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
