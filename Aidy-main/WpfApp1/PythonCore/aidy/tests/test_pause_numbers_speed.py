"""Pause tolerance, context-only number recognition and compact AI contracts."""
import json
import unittest
from unittest.mock import Mock, patch
import numpy as np

from aidy.ai_provider import GeminiProvider, AIConfig, Credentials
from aidy.ai_tools import Registry
from aidy.config import VAD_SILENCE_MS
from aidy.speech import normalize_number_reply
import test_speech as speech_tests
import test_ai_layer as ai_tests
from test_audio_pipeline import bare_aidy, speech_collector, VOICE_FRAME, SILENT_FRAME, FakeStream
from test_speech_activity import completed
from aidy import assistant as core


class PauseTests(unittest.TestCase):
    def test_short_stutters_and_pauses_are_one_utterance(self):
        for pause_ms in [100, 240, 400, 500]:
            collector = speech_collector(silence_ms=VAD_SILENCE_MS)
            for frame in [VOICE_FRAME] * 15 + [SILENT_FRAME] * (pause_ms // 20) + [VOICE_FRAME] * 15:
                self.assertFalse(collector.feed(frame), pause_ms)
            for _ in range((VAD_SILENCE_MS // 20) - 1):
                self.assertFalse(collector.feed(SILENT_FRAME))
            self.assertTrue(collector.feed(SILENT_FRAME))
            self.assertEqual(collector.voiced_frames, 30)

    def test_pause_inside_combined_wake_and_command_does_not_execute_a_fragment(self):
        host = bare_aidy()
        host.stream = FakeStream([VOICE_FRAME] * 15 + [SILENT_FRAME] * 20 +
                                 [VOICE_FRAME] * 15 + [SILENT_FRAME] * 35)
        stream = host.stream
        host.start_stream = Mock()
        host.speech.supports_previews = True
        host.speech.start_preview.side_effect = [completed('aidy open'), completed('aidy open the browser')]
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertEqual(host.wait_for_wake(), 'open the browser')
        self.assertEqual(stream.reads, 85)
        host.process_command.assert_not_called()
        host._play_wake_ack.assert_called_once()

    def test_number_mode_is_selected_only_for_pending_amount(self):
        host = bare_aidy()
        host._ai = Mock(awaiting_number=True)
        host._sync_state_before_listen = Mock()
        host._capture_utterance = Mock(return_value=np.zeros(16000, np.float32))
        host._transcribe_capture = Mock(return_value='10')
        self.assertEqual(host.listen_command(), '10')
        self.assertEqual(host._capture_utterance.call_args.kwargs['purpose'], 'number')
        self.assertEqual(host._capture_utterance.call_args.kwargs['silence_ms'], 400)
        host._ai.awaiting_number = False
        host.listen_command()
        self.assertEqual(host._capture_utterance.call_args.kwargs['purpose'], 'command')
        self.assertEqual(host._capture_utterance.call_args.kwargs['silence_ms'], 700)


class NumberTests(unittest.TestCase):
    setUp = speech_tests.TestSpeechRecognizer.setUp

    def make(self):
        return speech_tests.SpeechRecognizer(cache_dir=self.temp.name, model_factory=self.factory, runtime=self.runtime)

    def test_numeric_context_has_numbers_instead_of_app_hotwords(self):
        self.model.supports_short_windows = True
        self.model.transcribe.side_effect = lambda *a, **kw: speech_tests.recognized(speech_tests.segment('Ten.'))
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio, purpose='number'), '10')
        self.assertEqual(self.model.encoder_frames, 1500)
        self.assertIsNone(self.model.transcribe.call_args.kwargs['hotwords'])
        self.assertIn('how many', self.model.transcribe.call_args.kwargs['initial_prompt'])
        self.assertNotIn('Discord', self.model.transcribe.call_args.kwargs['initial_prompt'])

    def test_reported_phonetic_errors_are_normalized_only_in_number_context(self):
        recognizer = self.make()
        for transcript, expected in [('Zen.', '10'), ('Buff.', '5'), ('Baf.', '5'), ('Five.', '5')]:
            self.model.transcribe.side_effect = lambda *a, text=transcript, **kw: speech_tests.recognized(speech_tests.segment(text))
            self.assertEqual(recognizer.transcribe(self.audio, purpose='number'), expected)
            self.assertEqual(recognizer.transcribe(self.audio, purpose='command'), transcript.lower().rstrip('.'))

    def test_number_normalization_preserves_negation_cancellation_and_new_commands(self):
        for text in ['cancel', 'stop', 'not ten', "don't", 'open browser', 'zen music', 'five five']:
            self.assertEqual(normalize_number_reply(text), text)

    def test_numeric_prompt_does_not_rescue_unconfident_or_silent_audio(self):
        self.model.transcribe.side_effect = lambda *a, **kw: speech_tests.recognized(speech_tests.segment('Ten.', avg_logprob=-1.5))
        recognizer = self.make()
        self.assertEqual(recognizer.transcribe(self.audio, purpose='number'), '')
        self.model.transcribe.reset_mock()
        self.assertEqual(recognizer.transcribe(np.zeros(16000, np.float32), purpose='number'), '')
        self.model.transcribe.assert_not_called()


class CompactProviderTests(unittest.TestCase):
    def make(self, payload):
        session = Mock()
        session.request.return_value = ai_tests.HTTPResponse(payload=ai_tests.response(payload))
        self.session = session
        self.provider = GeminiProvider(AIConfig(mode='api'), Credentials(['test-placeholder']), session)
        self.addCleanup(self.provider.close)
        self.registry = Registry(['browser', 'discord', 'steam', 'gpt', 'chatgpt_app'])
        self.registry.windows = [{'id': 'w1', 'title': 'Do not send unnecessary window context', 'process': 'editor.exe'}]

    def test_bare_browser_gets_open_close_contract_and_still_calls_google(self):
        self.make(ai_tests.plan(ai_tests.step('open_app', {'app': 'browser'})))
        import threading
        result = self.provider.interpret('open the browser', [], self.registry, threading.Event())
        self.assertEqual(result['steps'][0]['arguments']['app'], 'browser')
        self.session.request.assert_called_once()
        body = self.session.request.call_args.kwargs['json']
        tools = body['generationConfig']['responseJsonSchema']['properties']['steps']['items']['properties']['tool']['enum']
        self.assertEqual(set(tools), {'open_app', 'close_app'})
        self.assertNotIn('web_search', json.dumps(body))
        self.assertNotIn('unnecessary window context', json.dumps(body))

    def test_contextual_ambiguous_compound_and_search_requests_keep_full_contract(self):
        self.make(ai_tests.plan(ai_tests.step()))
        for request in ['open it', 'close everything', 'close yourself', "don't open steam", 'open browser and search cats',
                        'open browser in ten seconds', 'actually open Steam instead', 'open ChatGPT', 'open chatgpt app']:
            self.assertFalse(self.provider._explicit_app_request(request, self.registry), request)

    def test_unexpected_search_response_for_bare_browser_cannot_execute(self):
        self.make(ai_tests.plan(ai_tests.step('web_search', {'query': 'browser'})))
        import threading
        with self.assertRaises(ai_tests.AIError):
            self.provider.interpret('open the browser', [], self.registry, threading.Event())

    def test_search_with_actual_query_retains_search_tool(self):
        self.make(ai_tests.plan(ai_tests.step('web_search', {'query': 'cats'})))
        import threading
        self.provider.interpret('open browser and search cats', [], self.registry, threading.Event())
        body = self.session.request.call_args.kwargs['json']
        self.assertIn('web_search', json.dumps(body))
        self.assertIn('unnecessary window context', json.dumps(body))

    def test_compact_schema_cannot_expand_to_multiple_or_scheduled_actions(self):
        import threading
        for payload in [ai_tests.plan(ai_tests.step(), ai_tests.step()), ai_tests.plan(ai_tests.step(delay=5))]:
            self.make(payload)
            with self.assertRaises(ai_tests.AIError):
                self.provider.interpret('open Discord', [], self.registry, threading.Event())


if __name__ == '__main__':
    unittest.main()
