import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aidy.speech import (
    MODEL_ID, SpeechRecognizer, SpeechRecognitionError,
    default_model_cache, normalize_transcript,
)


def segment(text="Open browser.", **overrides):
    values = dict(text=text, avg_logprob=-0.2, no_speech_prob=0.01, compression_ratio=1.0)
    values.update(overrides)
    return SimpleNamespace(**values)


def recognized(*segments, speech_seconds=0.8):
    return iter(segments), SimpleNamespace(duration_after_vad=speech_seconds)


class TestSpeechRecognizer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache_patch = patch("aidy.speech._cache_is_complete", return_value=True)
        self.cached = self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        env_patch = patch.dict(os.environ, {
            "AIDY_WHISPER_DEVICE": "auto", "AIDY_WHISPER_COMPUTE_TYPE": "auto",
            "AIDY_WHISPER_CPU_THREADS": "2", "AIDY_WHISPER_BEAM_SIZE": "3",
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.runtime = Mock()
        self.runtime.get_cuda_device_count.return_value = 0
        self.runtime.get_supported_compute_types.side_effect = lambda device: (
            {"float16", "float32"} if device == "cuda" else {"int8", "float32"}
        )
        self.model = Mock()
        self.model.transcribe.side_effect = lambda *args, **kwargs: recognized(segment())
        self.factory = Mock(return_value=self.model)
        self.audio = (0.05 * np.sin(np.arange(16000, dtype=np.float32) * 0.12)).astype(np.float32)

    def make(self, **kwargs):
        return SpeechRecognizer(cache_dir=self.temp.name, model_factory=self.factory, runtime=self.runtime, **kwargs)

    def test_loads_once_reuses_cache_and_allows_repeated_command(self):
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio), "open browser")
        self.assertEqual(recognizer.transcribe(self.audio), "open browser")
        self.factory.assert_called_once()
        self.assertEqual(self.factory.call_args.args, (MODEL_ID,))
        self.assertTrue(self.factory.call_args.kwargs["local_files_only"])
        self.assertEqual(recognizer.device, "cpu")
        self.assertEqual(recognizer.compute_type, "int8")
        self.assertEqual(self.model.transcribe.call_args.kwargs["language"], "en")
        self.assertEqual(self.model.transcribe.call_args.kwargs["task"], "transcribe")
        self.assertTrue(self.model.transcribe.call_args.kwargs["vad_filter"])
        self.assertFalse(self.model.transcribe.call_args.kwargs["condition_on_previous_text"])
        self.assertIsNone(self.model.transcribe.call_args.kwargs["initial_prompt"])

    def test_missing_cache_enables_constructor_download(self):
        self.cached.return_value = False
        self.make()
        self.assertFalse(self.factory.call_args.kwargs["local_files_only"])
        self.assertEqual(self.factory.call_args.kwargs["download_root"], self.temp.name)

    def test_short_window_retries_uncertainty_with_same_whisper_model(self):
        self.model.supports_short_windows = True
        windows = []
        def decode(*args, **kwargs):
            windows.append(self.model.encoder_frames)
            if self.model.encoder_frames < 3000:
                return recognized(segment('Open steam', avg_logprob=-1.3))
            return recognized(segment("Don't open Steam."))
        self.model.transcribe.side_effect = decode
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio), "don't open steam")
        self.assertEqual(windows, [1500, 3000])
        self.factory.assert_called_once()

    def test_long_input_is_never_cut_to_short_window(self):
        self.model.supports_short_windows = True
        recognizer = self.make()
        recognizer.transcribe(np.tile(self.audio, 25))
        self.assertEqual(self.model.encoder_frames, 2600)
        recognizer.transcribe(np.tile(self.audio, 30))
        self.assertEqual(self.model.encoder_frames, 3000)
        self.assertIsNone(self.model.transcribe.call_args.kwargs['max_new_tokens'])

    def test_standalone_wake_uses_one_short_probe(self):
        self.model.supports_short_windows = True
        self.model.transcribe.side_effect = lambda *a, **kw: recognized(segment('Aidy'))
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio, purpose='wake'), 'aidy')
        self.assertEqual(self.model.encoder_frames, 1000)
        self.model.transcribe.assert_called_once()
        decoded_audio = self.model.transcribe.call_args.args[0]
        np.testing.assert_array_equal(decoded_audio[:3200], np.zeros(3200, np.float32))
        np.testing.assert_array_equal(decoded_audio[3200:3200 + self.audio.size], self.audio)
        np.testing.assert_array_equal(decoded_audio[3200 + self.audio.size:], np.zeros(4800, np.float32))
        self.assertEqual(self.model.transcribe.call_args.kwargs['vad_parameters']['speech_pad_ms'], 300)

    def test_explicit_full_window_disables_wake_probe(self):
        self.model.supports_short_windows = True
        recognizer = self.make()
        recognizer.window_seconds = 30
        recognizer.transcribe(self.audio, purpose='wake')
        self.assertEqual(self.model.encoder_frames, 3000)
        self.assertEqual(self.model.transcribe.call_args.args[0].size, self.audio.size)

    def test_wake_probe_preserves_samples_and_verification_keeps_confidence_checks(self):
        self.model.supports_short_windows = True
        windows = []
        def decode(audio, **kwargs):
            windows.append(self.model.encoder_frames)
            if self.model.encoder_frames == 1000:
                np.testing.assert_array_equal(audio[3200:3200 + self.audio.size], self.audio)
                self.assertEqual(audio.size, self.audio.size + 8000)
            else:
                np.testing.assert_array_equal(audio, self.audio)
            # Uncertainty must still be verified, and rejected if it persists.
            return recognized(segment('Hey', avg_logprob=-1.2))
        self.model.transcribe.side_effect = decode
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio, purpose='wake'), '')
        self.assertEqual(windows, [1000, 1500, 3000])

    def test_wake_probe_never_accepts_uncertainty_repetition_or_a_command_tail(self):
        self.model.supports_short_windows = True
        recognizer = self.make()
        for first in (segment('Aidy', avg_logprob=-1.2), segment('Hey open Steam'),
                      segment('Aidy a d a'), segment('Hi'),
                      segment('Aidy', tokens=list(range(16)))):
            windows = []
            def decode(*a, **kw):
                windows.append(self.model.encoder_frames)
                return recognized(first if self.model.encoder_frames == 1000 else
                                  segment("Hey Aidy don't open Steam"))
            self.model.transcribe.side_effect = decode
            with self.subTest(text=first.text):
                self.assertEqual(recognizer.transcribe(self.audio, purpose='wake'),
                                 "hey aidy don't open steam")
                self.assertEqual(windows, [1000, 1500])

    def test_confident_exact_wake_echo_needs_no_second_decode(self):
        self.model.supports_short_windows = True
        self.model.transcribe.side_effect = lambda *a, **kw: recognized(segment('Hey. Hey.'))
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio, purpose='wake', preview=True), 'hey')
        self.model.transcribe.assert_called_once()
        self.assertEqual(self.model.encoder_frames, 1000)

    def test_navigation_omits_app_names_and_uses_short_window(self):
        self.model.supports_short_windows = True
        self.model.transcribe.side_effect = lambda *a, **kw: recognized(segment('Done. Done.'))
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio, purpose='navigation'), 'done')
        self.assertIsNone(self.model.transcribe.call_args.kwargs['hotwords'])
        self.assertEqual(self.model.encoder_frames, 800)
        self.model.transcribe.assert_called_once()

    def test_uncertain_wake_preview_resumes_with_normal_context_without_repeating_probe(self):
        self.model.supports_short_windows = True
        self.model.transcribe.side_effect = lambda *a, **kw: recognized(segment('Hey open Steam'))
        recognizer = self.make()
        self.assertIsNone(recognizer.transcribe(self.audio, purpose='wake', preview=True))
        self.assertEqual(self.model.encoder_frames, 1000)
        self.assertEqual(recognizer.transcribe(self.audio, purpose='wake', skip_wake_probe=True),
                         'hey open steam')
        self.assertEqual(self.model.encoder_frames, 1500)

    def test_uncertain_preview_never_retries_an_unfinished_phrase(self):
        self.model.supports_short_windows = True
        self.model.transcribe.side_effect = lambda *args, **kwargs: recognized(
            segment('Open Steam', avg_logprob=-1.3))
        recognizer = self.make()
        self.assertIsNone(recognizer.transcribe(self.audio, preview=True))
        self.model.transcribe.assert_called_once()
        self.assertEqual(self.model.encoder_frames, 1500)
        self.model.transcribe.side_effect = lambda *args, **kwargs: recognized(segment("Don't open Steam"))
        self.assertEqual(recognizer.transcribe(self.audio, full_context=True), "don't open steam")
        self.assertEqual(self.model.encoder_frames, 3000)
        self.model.warm_up.assert_called_once_with(1500)

    def test_preview_does_not_queue_obsolete_fragments_and_worker_closes(self):
        recognizer = self.make()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def decode(*args, **kwargs):
            started.set()
            if not release.wait(timeout=2):
                raise RuntimeError('test worker was not released')
            return 'open steam'
        with patch.object(recognizer, 'transcribe', side_effect=decode):
            future = recognizer.start_preview(self.audio)
            self.assertTrue(started.wait(timeout=1))
            self.assertIsNone(recognizer.start_preview(self.audio))
            release.set()
            self.assertEqual(future.result(timeout=1), 'open steam')
        recognizer.close()
        self.assertIsNone(recognizer.start_preview(self.audio))

    def test_short_token_budget_does_not_accept_a_truncated_command(self):
        self.model.supports_short_windows = True
        self.model.transcribe.side_effect = lambda *args, **kwargs: recognized(
            segment('open steam', tokens=list(range(16))))
        recognizer = self.make()
        self.assertIsNone(recognizer.transcribe(self.audio, preview=True))
        self.assertEqual(self.model.transcribe.call_args.kwargs['max_new_tokens'], 16)

    def test_wake_context_is_separate_from_commands_and_never_forces_a_result(self):
        recognizer = self.make()
        recognizer.transcribe(self.audio, purpose="wake")
        self.assertEqual(self.model.transcribe.call_args.kwargs["hotwords"], "Aidy.")
        recognizer.transcribe(self.audio)
        self.assertIn("Steam", self.model.transcribe.call_args.kwargs["hotwords"])
        self.model.transcribe.side_effect = lambda *args, **kw: recognized(
            segment("Aidy", avg_logprob=-1.2))
        self.assertEqual(recognizer.transcribe(self.audio, purpose="wake"), "")

    def test_silence_dc_and_short_audio_never_reach_model(self):
        recognizer = self.make()
        for audio in (np.zeros(16000, np.float32), np.ones(16000, np.float32) * 0.2, self.audio[:1600]):
            self.assertEqual(recognizer.transcribe(audio), "")
        self.model.transcribe.assert_not_called()

    def test_invalid_audio_fails_instead_of_being_misinterpreted(self):
        recognizer = self.make()
        invalid = (self.audio.astype(np.int16), self.audio.reshape(2, -1),
                   np.array([float("nan")] * 16000, np.float32), self.audio * 100)
        for audio in invalid:
            with self.subTest(dtype=audio.dtype, shape=audio.shape):
                with self.assertRaises(ValueError):
                    recognizer.transcribe(audio)
        self.model.transcribe.assert_not_called()

    def test_vad_rejection_blocks_plausible_hallucinated_command(self):
        self.model.transcribe.side_effect = lambda *args, **kwargs: recognized(segment(), speech_seconds=0.0)
        self.assertEqual(self.make().transcribe(self.audio), "")

    def test_unreliable_segment_rejects_complete_utterance(self):
        recognizer = self.make()
        for metrics in ({"avg_logprob": -1.2}, {"no_speech_prob": 0.9},
                        {"compression_ratio": 3.0}, {"avg_logprob": float("nan")}):
            with self.subTest(metrics=metrics):
                self.model.transcribe.side_effect = lambda *args, **kwargs: recognized(
                    segment("Don't", **metrics), segment("open browser")
                )
                self.assertEqual(recognizer.transcribe(self.audio), "")

    def test_followup_number_and_negation_are_preserved(self):
        self.model.transcribe.side_effect = lambda *args, **kwargs: recognized(segment("Don't open browser."))
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio), "don't open browser")
        self.model.transcribe.side_effect = lambda *args, **kwargs: recognized(segment("2.5"))
        self.assertEqual(recognizer.transcribe(self.audio), "2.5")

    def test_confident_live_yes_scores_do_not_trigger_retry_or_rejection(self):
        self.model.supports_short_windows = True
        recognizer = self.make()
        for score, no_speech in ((-.138, .548), (-.182, .593), (-.161, .686)):
            self.model.transcribe.reset_mock()
            self.model.transcribe.side_effect = lambda *a, **kw: recognized(
                segment('Yes.', avg_logprob=score, no_speech_prob=no_speech))
            self.assertEqual(recognizer.transcribe(self.audio, purpose='confirmation'), 'yes')
            self.model.transcribe.assert_called_once()
            self.assertIsNone(self.model.transcribe.call_args.kwargs['hotwords'])
        self.model.transcribe.side_effect = lambda *a, **kw: recognized(
            segment('yes', avg_logprob=-.603, no_speech_prob=.755))
        self.assertEqual(recognizer.transcribe(self.audio, purpose='confirmation'), '')

    def test_short_english_utterance_scores_are_accepted(self):
        recognizer = self.make()
        for text, score, purpose in (("Hey.", -0.779, "wake"),
                                      ("Open browser.", -0.834, "command"),
                                      ("Five.", -0.988, "command")):
            self.model.transcribe.side_effect = lambda *args, t=text, p=score, **kwargs: recognized(segment(t, avg_logprob=p))
            self.assertTrue(recognizer.transcribe(self.audio, purpose=purpose))

    def test_model_load_failure_is_explicit_without_simulated_result(self):
        self.factory.side_effect = RuntimeError("model unavailable")
        with self.assertRaisesRegex(SpeechRecognitionError, "could not load"):
            self.make()
        self.factory.assert_called_once()

    def test_cuda_initialization_failure_falls_back_once(self):
        self.runtime.get_cuda_device_count.return_value = 1
        self.factory.side_effect = [RuntimeError("missing CUDA runtime"), self.model]
        recognizer = self.make()
        self.assertEqual(recognizer.device, "cpu")
        self.assertEqual(recognizer.transcribe(self.audio), "open browser")
        self.assertEqual(self.factory.call_count, 2)

    def test_requested_cuda_without_gpu_uses_supported_cpu_precision(self):
        recognizer = self.make(device="cuda", compute_type="float16")
        self.assertEqual((recognizer.device, recognizer.compute_type), ("cpu", "int8"))

    def test_cuda_lazy_iteration_failure_discards_partial_result_and_retries_cpu(self):
        self.runtime.get_cuda_device_count.return_value = 1
        gpu_model = Mock()

        def broken_segments():
            yield segment("open browser")
            raise RuntimeError("CUDA allocation failure")

        gpu_model.transcribe.return_value = (broken_segments(), SimpleNamespace(duration_after_vad=0.8))
        self.factory.side_effect = [gpu_model, self.model]
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio), "open browser")
        self.assertEqual(recognizer.device, "cpu")
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(recognizer.transcribe(self.audio), "open browser")
        self.assertEqual(self.factory.call_count, 2)

    def test_cpu_failure_after_gpu_fallback_does_not_retry_indefinitely(self):
        self.runtime.get_cuda_device_count.return_value = 1
        gpu_model = Mock()
        gpu_model.transcribe.side_effect = RuntimeError("CUDA error")
        self.model.transcribe.side_effect = RuntimeError("CPU error")
        self.factory.side_effect = [gpu_model, self.model]
        recognizer = self.make()
        with self.assertRaisesRegex(SpeechRecognitionError, "CPU transcription failed"):
            recognizer.transcribe(self.audio)
        self.assertEqual(self.factory.call_count, 2)

    def test_cpu_lazy_error_is_not_returned_as_partial_command(self):
        def broken_segments():
            yield segment()
            raise RuntimeError("decode failed")

        self.model.transcribe.return_value = (broken_segments(), SimpleNamespace(duration_after_vad=0.8))
        self.model.transcribe.side_effect = None
        with self.assertRaisesRegex(SpeechRecognitionError, "decode failed"):
            self.make().transcribe(self.audio)
        self.factory.assert_called_once()

    def test_close_prevents_future_inference(self):
        recognizer = self.make()
        recognizer.close()
        with self.assertRaisesRegex(SpeechRecognitionError, "closed"):
            recognizer.transcribe(self.audio)
        self.model.transcribe.assert_not_called()

    def test_cache_location_does_not_depend_on_working_directory(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name, "AIDY_MODEL_CACHE": ""}):
            self.assertEqual(default_model_cache(), Path(self.temp.name) / "Aidy" / "models")
        with patch.dict(os.environ, {"AIDY_MODEL_CACHE": "relative/cache"}):
            with self.assertRaisesRegex(ValueError, "absolute"):
                default_model_cache()

    def test_normalization_preserves_apostrophes_and_decimal_numbers(self):
        self.assertEqual(normalize_transcript("Don't set 2.5 minutes!"), "don't set 2.5 minutes")
        self.assertEqual(normalize_transcript("Open example.com."), "open example.com")


if __name__ == "__main__":
    unittest.main()
