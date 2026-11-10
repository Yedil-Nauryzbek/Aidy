"""Dialogue regressions: no real windows, browser, sound or hardware changes."""
import copy
import threading
import time
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from aidy.ai_engine import AIEngine, Result
from aidy.ai_provider import compact_history
from aidy.ai_runtime import AIRuntime
from aidy.ai_tools import AIError, Registry
from aidy.ai_desktop import discover_chatgpt
from aidy.ai_windows import Windows
import test_ai_layer as layer
from test_ai_layer import FakeProvider, plan, step


def adjustments():
    return [step('volume_change', {'direction': 'up', 'steps': 1}),
            step('brightness_change', {'direction': 'up', 'steps': 1})]


class DialogueTests(unittest.TestCase):
    def make(self, *plans):
        self.execute = Mock(return_value=Result('dispatched', 'Adjustment sent.'))
        self.provider = FakeProvider(*plans)
        self.engine = AIEngine(self.provider, Registry(['discord', 'steam']),
                               lambda *a: True, Mock(return_value=True), self.execute)
        return self.engine

    def test_missing_amount_never_changes_either_control(self):
        engine = self.make(plan(*adjustments()))
        result = engine.run('volume up and brightness up', ('local', 'Admin'))
        self.assertEqual(result.status, 'clarification')
        self.assertEqual(result.voice_key, 'by_how_much')
        self.execute.assert_not_called()
        self.assertEqual(len(compact_history(list(engine.history))[0]['pending_steps']), 2)

    def test_numeric_reply_keeps_both_deferred_controls_even_if_model_drops_one(self):
        engine = self.make(plan(*adjustments()), plan(adjustments()[0]))
        engine.run('volume up and brightness up', ('local', 'Admin'))
        self.assertTrue(engine.run('three', ('local', 'Admin')).ok)
        calls = [c.args[0] for c in self.execute.call_args_list]
        self.assertEqual([s['tool'] for s in calls], ['volume_change', 'brightness_change'])
        self.assertEqual([s['arguments']['steps'] for s in calls], [3, 3])
        self.assertEqual(len(self.provider.seen), 2)

    def test_unrelated_query_number_is_not_a_volume_amount(self):
        engine = self.make(plan(step('web_search', {'query': 'number 3'}), adjustments()[0]))
        self.assertEqual(engine.run('search number 3 and volume up', ('local', 'Admin')).status, 'clarification')
        self.execute.assert_not_called()

    def test_partial_amount_still_asks_before_any_change(self):
        engine = self.make(plan(*adjustments()))
        self.assertEqual(engine.run('volume up two steps and brightness up', ('local', 'Admin')).status, 'clarification')
        self.execute.assert_not_called()

    def test_followup_preserves_quantity_that_was_already_specified(self):
        levels = adjustments()
        levels[0]['arguments']['steps'] = 2
        engine = self.make(plan(*levels), plan(adjustments()[1]))
        engine.run('volume up two steps and brightness up', ('local', 'Admin'))
        self.assertTrue(engine.run('three', ('local', 'Admin')).ok)
        self.assertEqual([c.args[0]['arguments']['steps'] for c in self.execute.call_args_list], [2, 3])

    def test_explicit_amounts_do_not_require_another_question(self):
        engine = self.make(plan(*adjustments()))
        self.assertTrue(engine.run('volume up one step and brightness up one step', ('local', 'Admin')).ok)
        self.assertEqual(self.execute.call_count, 2)

    def test_new_speaker_cannot_complete_old_pending_adjustments(self):
        engine = self.make(plan(*adjustments()), plan(question='What should I do?'))
        engine.run('volume up and brightness up', ('a', 'Admin'))
        self.assertFalse(engine.run('three', ('b', 'Admin')).ok)
        self.execute.assert_not_called()

    def test_expired_pending_plan_does_not_execute_on_number(self):
        engine = self.make(plan(*adjustments()), plan(question='What should I do?'))
        engine.run('volume up and brightness up', ('a', 'Admin'))
        engine.pending_until = 0
        self.assertEqual(engine.run('three', ('a', 'Admin')).status, 'clarification')
        self.execute.assert_not_called()

    def test_cancel_discards_pending_adjustments(self):
        engine = self.make(plan(*adjustments()))
        engine.run('volume up and brightness up', ('a', 'Admin'))
        engine.cancel()
        self.assertIsNone(engine.pending_adjustments)

    def test_one_batch_confirmation_and_permission_recheck_before_each_close(self):
        engine = self.make(plan(step('close_app'), step('close_app', {'app': 'steam'})))
        engine.confirm_batch = Mock(return_value=True)
        engine.authorize = lambda *a: self.execute.call_count == 0
        result = engine.run('close discord and steam', ('local', 'Admin'))
        self.assertFalse(result.ok)
        engine.confirm_batch.assert_called_once()
        engine.confirm.assert_not_called()
        self.execute.assert_called_once()

    def test_rejected_batch_has_no_partial_closes(self):
        engine = self.make(plan(step('close_app'), step('close_app', {'app': 'steam'})))
        engine.confirm_batch = Mock(return_value=False)
        self.assertFalse(engine.run('close discord and steam', ('local', 'Admin')).ok)
        self.execute.assert_not_called()

    def test_successful_chain_has_one_short_acknowledgement(self):
        engine = self.make(plan(step(), step('open_app', {'app': 'steam'})))
        self.execute.return_value = Result('dispatched', 'Detailed executor explanation. ' * 20)
        self.assertEqual(engine.run('open Discord and Steam', ('local', 'Admin')).message, '2 action requests sent.')

    def test_late_provider_cannot_execute_after_deadline(self):
        engine = self.make()
        release = threading.Event()
        engine.provider.config = SimpleNamespace(timeout_seconds=.05)
        engine.provider.interpret = lambda *a: release.wait(1) and plan(step())
        started = time.monotonic()
        result = engine.run('strange words', ('local', 'Admin'))
        self.assertLess(time.monotonic() - started, .5)
        self.assertIn('timed out', result.message)
        release.set()
        self.execute.assert_not_called()
        self.assertFalse(engine.busy)

    def test_empty_and_control_character_searches_are_rejected(self):
        for query in ['', '  ', 'hello\nworld']:
            with self.subTest(query=query), self.assertRaises(AIError):
                Registry([]).plan(plan(step('web_search', {'query': query})))

    def test_closing_last_reference_keeps_sequential_resolution(self):
        engine = self.make(plan(step('close_app'), step('close_app', {'app': '$last'})))
        engine.confirm_batch = Mock(return_value=True)
        engine.last_app = 'steam'
        engine.identity = ('local', 'Admin')
        engine.context_until = time.monotonic() + 60
        self.assertTrue(engine.run('close discord, then close it', ('local', 'Admin')).ok)
        self.assertEqual([c.args[0]['arguments']['app'] for c in self.execute.call_args_list], ['discord', 'discord'])
        engine.confirm_batch.assert_not_called()


class CatalogTests(unittest.TestCase):
    def catalog(self, app_ids):
        shell = Mock()
        shell.NameSpace.return_value.Items.return_value = [
            SimpleNamespace(Name='ChatGPT', ExtendedProperty=lambda key, value=value: value)
            for value in app_ids]
        return patch('win32com.client.Dispatch', return_value=shell)

    def test_desktop_uses_actual_registered_id(self):
        with self.catalog(['Actual.Package_123!Main']):
            app = discover_chatgpt()
        self.assertEqual(app['target'], 'shell:AppsFolder\\Actual.Package_123!Main')

    def test_missing_ambiguous_or_invalid_desktop_never_substitutes_website(self):
        for entries in [[], ['Package.A!App', 'Package.B!App'], ['cmd /c calc']]:
            with self.subTest(entries=entries), self.catalog(entries):
                self.assertIsNone(discover_chatgpt())

    def test_catalog_read_has_a_deadline_and_late_result_has_no_actions(self):
        release = threading.Event()
        with patch('win32com.client.Dispatch', side_effect=lambda *a: release.wait(1)):
            start = time.monotonic()
            self.assertIsNone(discover_chatgpt(timeout=.02))
            self.assertLess(time.monotonic() - start, .5)
            release.set()

    def test_desktop_window_target_does_not_match_a_browser_site(self):
        host = Mock(apps=[dict(id='chatgpt_app', type='shell', display_name='ChatGPT', aliases=[])])
        windows = Windows(host)
        self.assertTrue(windows.matches(dict(process='chatgpt.exe', title='ChatGPT'), 'chatgpt_app'))
        self.assertFalse(windows.matches(dict(process='msedge.exe', title='ChatGPT - Edge'), 'chatgpt_app'))


class AdapterTests(unittest.TestCase):
    setUp = layer.RuntimeTests.setUp
    attach = layer.RuntimeTests.attach

    def test_batch_preserves_distinct_frozen_targets_and_asks_only_once(self):
        closes = [step('close_app'), step('close_app', {'app': 'steam'})]
        self.attach(plan(*closes))
        self.host._listen_confirm_reply = Mock(return_value='yes')
        self.host._classify_confirm_reply = Mock(return_value='yes')
        def bind(app):
            self.runtime.windows._frozen = [{'app': app}]
            return True
        self.runtime.windows.bind_app = Mock(side_effect=bind)
        selected = []
        def close(*a):
            selected.extend(copy.deepcopy(self.runtime.windows._frozen))
            return Result('verified', 'Closed.')
        self.runtime.windows.close_frozen = Mock(side_effect=close)
        self.assertTrue(self.runtime.run('close discord and steam'))
        self.host._listen_confirm_reply.assert_called_once()
        self.assertEqual(selected, [{'app': 'discord'}, {'app': 'steam'}])
        confirmation = self.runtime.report.call_args_list[0].args[0]
        self.assertEqual(confirmation.voice_key, 'are_you_sure_clean')

    def test_confirmation_and_amount_use_existing_clips(self):
        self.host.voice.muted = False
        for key in ['are_you_sure_clean', 'by_how_much']:
            AIRuntime.report(self.runtime, Result('clarification', 'Short prompt?', key))
            self.host.voice.play_or_tts.assert_called_with(key, 'Short prompt?')
        self.host.voice.tts_blocking.assert_not_called()

    def test_browser_search_encodes_query_without_shell_or_typing(self):
        query = 'cats & dogs; $(calc) / ? # Привет'
        with patch('aidy.assistant.launch_app', return_value=True) as launch, patch('aidy.ai_runtime.os.startfile') as start:
            self.assertTrue(self.runtime.execute(step('web_search', {'query': query})).ok)
        start.assert_not_called()
        launched = launch.call_args.args[0]
        url = urlparse(launched['args'][-1])
        self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'www.google.com', '/search'))
        self.assertEqual(parse_qs(url.query)['q'], [query])

    def test_opening_app_does_not_wait_for_tasklist(self):
        self.host._tasklist_process_names = Mock(side_effect=AssertionError('slow tasklist'))
        with patch('aidy.assistant.launch_app', return_value=True):
            self.assertTrue(self.runtime.execute(step()).ok)
        self.host._tasklist_process_names.assert_not_called()

    def test_brightness_failure_is_short_and_not_reported_as_done(self):
        from aidy.system import BrightnessResult
        with patch('aidy.system.adjust_brightness', return_value=BrightnessResult(False,'Windows brightness control is unavailable.',True)):
            result = self.runtime.execute(adjustments()[1])
        self.assertFalse(result.ok)
        self.assertEqual(result.message, 'Windows brightness control is unavailable.')
        self.assertTrue(result.data['independent_control_unavailable'])

    def test_desktop_target_goes_to_registered_app_not_a_website(self):
        self.host.apps.append(dict(id='chatgpt_app', type='shell', target='shell:AppsFolder\\Test.App!App', aliases=[]))
        with patch('aidy.assistant.launch_app', return_value=True) as launch:
            self.assertTrue(self.runtime.execute(step('open_app', {'app': 'chatgpt_app'})).ok)
        self.assertEqual(launch.call_args.args[0]['type'], 'shell')
        self.assertTrue(launch.call_args.args[0]['target'].startswith('shell:AppsFolder\\'))

    def test_late_cancellation_transcript_is_not_applied_to_next_turn(self):
        future = Future()
        self.runtime._cancel_future = future
        self.attach(plan(step()))
        with patch('aidy.assistant.launch_app', return_value=True):
            self.assertTrue(self.runtime.run('open discord'))
        self.assertIsNone(self.runtime._cancel_future)
        future.set_result('cancel')
        self.assertTrue(self.runtime.engine.pending)

    def test_cancellation_decode_does_not_block_processing_deadline(self):
        self.attach()
        future = Future()
        self.runtime.progress = Mock()
        self.host._is_listening_allowed = Mock(return_value=True)
        self.host._is_deafened = Mock(return_value=False)
        self.host.start_stream = Mock()
        self.host.stream = Mock()
        self.host.stream.read.return_value = bytes(640)
        self.host._capture_rate = 16000
        self.host._pcm_pending = b''
        self.host.speech = Mock()
        self.host.speech.start_preview.return_value = future
        self.host._transcribe_capture = Mock(side_effect=AssertionError('synchronous decode'))
        self.runtime._cancel_collector = Mock(truncated=False)
        self.runtime._cancel_collector.feed.return_value = True
        start = time.monotonic()
        AIRuntime.poll_cancel_audio(self.runtime)
        self.assertLess(time.monotonic() - start, .2)
        self.assertIs(self.runtime._cancel_future, future)
        self.host._transcribe_capture.assert_not_called()


if __name__ == '__main__':
    unittest.main()
