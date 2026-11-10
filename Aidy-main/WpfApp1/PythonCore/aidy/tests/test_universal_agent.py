"""Task boundaries, observed planning and executor policy. No user UI effects."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aidy.ai_engine import AIEngine, Result
from aidy.ai_tools import AIError, Registry
from aidy.ai_provider import AIConfig, Credentials, GeminiProvider, decode_arguments
from aidy.ai_files import TaskFiles
from aidy.ai_automation import DesktopTask
from aidy.ai_catalog import AppCatalog
from aidy.ai_windows import Windows, owned_by
from test_ai_layer import FakeProvider, plan, step, HTTPResponse, response


class LoopTests(unittest.TestCase):
    def make(self, *plans, **limits):
        self.provider = FakeProvider(*plans)
        self.provider.config = replace(AIConfig(mode='api'), **limits)
        self.execute = Mock(return_value=Result('verified', 'Saved.'))
        engine = AIEngine(self.provider, Registry(['notepad']), lambda *a: True,
                          Mock(return_value=True), self.execute)
        engine.start_agent = Mock()
        def refresh(ref):
            engine.registry.desktop_state = {'revision': len(self.execute.call_args_list)}
        engine.refresh_agent = Mock(side_effect=refresh)
        engine.action_guard = Mock(return_value=False)
        self.engine = engine
        return engine

    def observe(self):
        return step('observe_desktop', {'window': ''})

    def test_replans_after_action_and_discards_stale_plan_tail(self):
        engine = self.make(plan(self.observe(), step('open_app', {'app': 'notepad'})),
                           plan(step('task_complete', {})))
        result = engine.run('Inspect desktop', ('local', 'Admin'))
        self.assertTrue(result.ok)
        self.assertEqual(self.execute.call_count, 1)
        self.assertEqual(len(self.provider.seen), 2)
        engine.refresh_agent.assert_called_once()

    def test_intermediate_failure_stops_without_replanning_or_retry(self):
        engine = self.make(plan(self.observe()), plan(step('task_complete', {})))
        self.execute.return_value = Result('failed', 'The foreground window changed.')
        self.assertFalse(engine.run('Inspect desktop', ('local', 'Admin')).ok)
        self.assertEqual(len(self.provider.seen), 1)
        self.assertEqual(self.execute.call_count, 1)

    def test_action_limit_prevents_remaining_actions(self):
        engine = self.make(plan(self.observe()), plan(self.observe()), max_actions=1)
        self.assertIn('action limit', engine.run('Inspect desktop', ('local', 'Admin')).message)
        self.assertEqual(self.execute.call_count, 1)

    def test_model_call_limit_stops_without_extra_request(self):
        engine = self.make(plan(self.observe()), max_model_calls=1)
        self.assertIn('model-call limit', engine.run('Inspect desktop', ('local', 'Admin')).message)
        self.assertEqual(len(self.provider.seen), 1)

    def test_observation_dialog_stops_instead_of_using_old_tail(self):
        engine = self.make(plan(self.observe()))
        engine.refresh_agent.side_effect = AIError('An unexpected dialog is open.')
        self.assertIn('dialog', engine.run('Inspect desktop', ('local', 'Admin')).message)
        self.assertEqual(self.execute.call_count, 1)

    def test_cancellation_stops_next_action(self):
        engine = self.make(plan(self.observe()), plan(self.observe()))
        def execute(s):
            engine.cancel()
            return Result('verified', 'Done.')
        self.execute.side_effect = execute
        self.assertEqual(engine.run('Inspect desktop', ('local', 'Admin')).status, 'cancelled')
        self.assertEqual(self.execute.call_count, 1)

    def test_dispatched_does_not_become_claimed_success(self):
        engine = self.make(plan(step('open_url', {'url': 'https://example.com'})), plan(step('task_complete', {})))
        self.execute.return_value = Result('dispatched', 'Request sent.')
        result = engine.run('Open example.com', ('local', 'Admin'))
        self.assertEqual(result.status, 'dispatched')
        self.assertIn('unverified', result.message)

    def test_unknown_outcome_cannot_be_followed_by_another_mutation(self):
        action = step('open_url', {'url': 'https://example.com'})
        engine = self.make(plan(action), plan(action))
        self.execute.return_value = Result('dispatched', 'No observed outcome.')
        self.assertIn('unverified outcome', engine.run('Open example.com', ('local', 'Admin')).message)
        self.assertEqual(self.execute.call_count, 1)

    def test_already_attempted_mutation_cannot_repeat_in_same_task(self):
        action = step('open_app', {'app': 'notepad'})
        engine = self.make(plan(self.observe()), plan(action), plan(action))
        result = engine.run('Inspect and open Notepad', ('local', 'Admin'))
        self.assertIn('already attempted', result.message)
        self.assertEqual(self.execute.call_count, 2)

    def test_late_continuation_model_answer_cannot_start_remaining_actions(self):
        engine = self.make(plan(self.observe()))
        engine.provider.config = replace(engine.provider.config, timeout_seconds=.04)
        release = threading.Event()
        original = engine.provider.interpret
        calls = []
        def interpret(*args):
            calls.append(1)
            if len(calls) == 1:
                return original(*args)
            release.wait(1)
            return plan(step('open_app', {'app': 'notepad'}))
        engine.provider.interpret = interpret
        result = engine.run('Inspect then open Notepad', ('local', 'Admin'))
        release.set()
        self.assertIn('timed out', result.message)
        self.assertEqual(self.execute.call_count, 1)

    def test_clarification_reply_retains_only_same_identity_task_scope(self):
        engine = self.make(plan(question='Which folder?'), plan(self.observe()), plan(step('task_complete', {})))
        engine.run('Inspect a folder', ('local', 'Admin'))
        self.assertTrue(engine.run('C:\\test', ('local', 'Admin')).ok)
        self.assertIn('Inspect a folder', engine.start_agent.call_args.args[0])
        self.assertIn('C:\\test', engine.start_agent.call_args.args[0])

    def test_new_command_replaces_pending_scope(self):
        engine = self.make(plan(question='Which folder?'), plan(self.observe()), plan(step('task_complete', {})))
        engine.run('Create a draft', ('local', 'Admin'))
        engine.run('Open another editor', ('local', 'Admin'))
        self.assertEqual(engine.start_agent.call_args.args[0], 'Open another editor')

    def test_repeated_request_id_cannot_execute_again(self):
        engine = self.make(plan(self.observe()), plan(step('task_complete', {})))
        engine.run('Inspect desktop', ('local', 'Admin'), request_id='fixed')
        self.assertFalse(engine.run('Inspect desktop', ('local', 'Admin'), request_id='fixed').ok)
        self.assertEqual(self.execute.call_count, 1)

    def test_extra_confirmation_rechecks_permissions_and_guard(self):
        engine = self.make(plan(self.observe()), plan(step('task_complete', {})))
        engine.action_guard.return_value = True
        engine.confirm.return_value = False
        self.assertFalse(engine.run('Inspect desktop', ('local', 'Admin')).ok)
        self.execute.assert_not_called()

    def test_legacy_unbound_input_cannot_enter_agent_loop(self):
        engine = self.make(plan(self.observe()), plan(step('mouse_action', {'action': 'click'})))
        self.assertIn('cannot be mixed', engine.run('Inspect desktop', ('local', 'Admin')).message)
        self.assertEqual(self.execute.call_count, 1)

    def test_disabled_agent_has_no_side_effects(self):
        engine = self.make(plan(self.observe()), agent_enabled=False)
        self.assertFalse(engine.run('Inspect desktop', ('local', 'Admin')).ok)
        self.execute.assert_not_called()

    def test_scheduling_generic_actions_is_rejected(self):
        registry = Registry([])
        with self.assertRaises(AIError):
            registry.plan(plan(step('file_action', dict(operation='write', path='C:\\out.txt', destination='', text='draft'), 5)))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='aidy-scope-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.files = TaskFiles(f'Create a text document in "{self.root}"')

    def args(self, operation, path, text='', destination=''):
        return dict(operation=operation, path=str(path), destination=str(destination), text=text)

    def test_create_read_move_and_no_overwrite(self):
        first, second = self.root / 'first.txt', self.root / 'second.txt'
        self.assertTrue(self.files.execute(self.args('write', first, 'hello\nworld')).ok)
        self.assertEqual(self.files.execute(self.args('read', first)).message, 'hello\nworld')
        with self.assertRaises(AIError):
            self.files.execute(self.args('write', first, 'changed'))
        self.assertEqual(first.read_text(), 'hello\nworld')
        self.assertTrue(self.files.execute(self.args('move', first, destination=second)).ok)
        self.assertFalse(first.exists())
        self.assertEqual(second.read_text(), 'hello\nworld')

    def test_escape_and_sibling_prefix_rejected(self):
        for path in [self.root / '..' / 'outside.txt', Path(str(self.root) + '-other') / 'out.txt']:
            with self.subTest(path=path), self.assertRaises(AIError):
                self.files.path(str(path), write=True)

    def test_no_implicit_grant_from_model_paths(self):
        with self.assertRaises(AIError):
            TaskFiles('Create a draft').path(str(self.root / 'draft.txt'), write=True)

    def test_executable_alternate_stream_device_and_network_paths_rejected(self):
        for path in [str(self.root / 'run.ps1'), str(self.root / 'a.txt') + ':stream', '\\\\server\\share\\a.txt', '\\\\?\\C:\\a.txt']:
            with self.subTest(path=path), self.assertRaises(AIError):
                self.files.path(path, write=True)

    def test_destination_must_also_be_granted(self):
        first = self.root / 'first.txt'
        first.write_text('draft')
        with self.assertRaises(AIError):
            self.files.execute(self.args('move', first, destination=self.root.parent / 'other.txt'))
        self.assertTrue(first.exists())

    def test_new_folder_grant_allows_requested_descendants(self):
        folder = self.root / 'new'
        self.assertTrue(self.files.execute(self.args('mkdir', folder)).ok)
        self.assertTrue(self.files.execute(self.args('write', folder / 'out.txt', 'draft')).ok)

    def test_text_file_verification_preserves_explicit_crlf_bytes(self):
        path = self.root / 'windows.txt'
        self.assertEqual(self.files.execute(self.args('write', path, 'first\r\nsecond')).status, 'verified')
        self.assertEqual(path.read_bytes(), b'first\r\nsecond')


class DesktopPolicyTests(unittest.TestCase):
    def make(self, request='Type a draft'):
        runtime = SimpleNamespace(engine=Mock(), windows=Mock())
        task = DesktopTask(runtime, request)
        task.views['w1'] = dict(view='v1', elements=[dict(id='e1', name='Send', automation_id='submit')])
        return task

    def test_draft_never_authorizes_send_button(self):
        with self.assertRaises(AIError):
            self.make().guard(step('ui_action', dict(window='w1', element='e1', operation='invoke', text='')))

    def test_explicit_send_still_requires_confirmation(self):
        self.assertTrue(self.make('Send the message').guard(step('ui_action', dict(window='w1', element='e1', operation='invoke', text=''))))

    def test_negative_send_instruction_cannot_authorize_sending(self):
        for request in ["Write a draft, don't send it", 'Do not send this', 'Draft without sending']:
            with self.subTest(request=request), self.assertRaises(AIError):
                self.make(request).guard(step('ui_action', dict(window='w1', element='e1', operation='invoke', text='')))

    def test_new_draft_cannot_replace_an_existing_document(self):
        task = self.make('Open Notepad and type a draft')
        task.views['w1']['elements'] = [dict(id='e1', name='Text editor', automation_id='', control=50030, value='Existing unsaved work')]
        with self.assertRaises(AIError):
            task.guard(step('ui_action', dict(window='w1', element='e1', operation='set_text', text='new draft')))

    def test_unknown_element_cannot_be_invented(self):
        with self.assertRaises(AIError):
            self.make().guard(step('ui_action', dict(window='w1', element='guessed', operation='invoke', text='')))

    def test_focus_change_stops_even_attempt_to_reclaim_focus(self):
        task = self.make()
        task.input_window = dict(hwnd=123)
        with patch('aidy.ai_windows._user.GetForegroundWindow', return_value=456), self.assertRaises(AIError):
            task.guard(step('window_action', dict(window='w1', operation='focus')))

    def test_file_read_cannot_become_an_instruction(self):
        registry = Registry([])
        with self.assertRaises(AIError):
            registry.plan(plan(step('powershell', {'code': 'untrusted window text'})))

    def test_dialog_ownership_does_not_follow_same_process_alone(self):
        owners = {200: 100, 300: 0}
        with patch('aidy.ai_windows._user.GetWindow', side_effect=lambda hwnd, flag: owners.get(hwnd, 0)):
            self.assertTrue(owned_by(200, 100))
            self.assertFalse(owned_by(300, 100))

    def test_save_as_never_inspects_other_same_process_documents(self):
        with tempfile.TemporaryDirectory(prefix='aidy-save-') as folder:
            path = Path(folder) / 'out.txt'
            task = self.make(f'Save the document to "{path}"')
            window = dict(hwnd=100, pid=10, process='notepad.exe', title='Test')
            task.runtime.windows.targets = dict(root=window,
                unrelated=dict(window, hwnd=300, title='Other unsaved user document'),
                dialog=dict(window, hwnd=200, title='Save as'))
            task.engine.task_deadline = time.monotonic() + 10
            task.engine.cancel_event = threading.Event()
            task.typed['w1'] = 'draft'
            def call(target, operation, **kwargs):
                if operation == 'observe':
                    self.assertEqual(target['hwnd'], 100)
                    return dict(elements=[dict(control=50030, id='editor')], view='view')
                if operation == 'save_as':
                    self.assertEqual(target['hwnd'], 200)
                    path.write_text('draft', encoding='utf-8')
                return dict(status='dispatched', message='Sent')
            task.call = Mock(side_effect=call)
            with patch('aidy.ai_windows.owned_by', side_effect=lambda hwnd, parent: hwnd == 200 and parent == 100):
                self.assertEqual(task.save(window, 'w1', str(path)).status, 'verified')
            self.assertFalse(any(c.args[0]['hwnd'] == 300 for c in task.call.call_args_list))


class ProviderContextTests(unittest.TestCase):
    def test_encoded_arguments_are_json_and_still_pass_typed_local_validation(self):
        payload = plan(step('ui_action', dict(window='w1', element='e1', operation='set_text', text='draft')))
        payload['steps'][0]['arguments'] = json.dumps(payload['steps'][0]['arguments'])
        self.assertTrue(Registry([]).plan(decode_arguments(payload)))
        for arguments in ['{"app":"one","app":"two"}', 'eval("x")', '{"value":NaN}']:
            with self.subTest(arguments=arguments), self.assertRaises(AIError):
                decode_arguments(dict(steps=[dict(arguments=arguments)]))

    def test_agent_off_exposes_only_existing_tools(self):
        session = Mock()
        session.request.return_value = HTTPResponse(payload=response(plan(question='What should I do?')))
        provider = GeminiProvider(AIConfig(mode='api', agent_enabled=False), Credentials(['placeholder']), session)
        self.addCleanup(provider.close)
        provider.interpret('Create a new document', [], Registry([]), threading.Event())
        tools = [item['name'] for item in session.request.call_args.kwargs['json']['tools'][0]['functionDeclarations']]
        self.assertNotIn('ui_action', tools)
        self.assertIn('plan_task', tools)
        self.assertIn('open_app', provider.available_tools(Registry([])))

    def test_configurable_limits_reject_wrong_types_and_unbounded_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ai.local.json'
            for name, value in [('max_actions', True), ('max_actions', 1000000), ('max_model_calls', 0), ('agent_enabled', 'yes'), ('task_timeout_seconds', 301)]:
                path.write_text(json.dumps({name: value}))
                with self.subTest(name=name, value=value), self.assertRaises(AIError):
                    AIConfig.load(folder)

    def test_context_cap_preserves_task_and_does_not_mutate_live_observation(self):
        registry = Registry(['browser'])
        registry.agent_mode = True
        registry.desktop_state = {'interface': {'elements': [dict(id=str(i), name='x' * 1000) for i in range(100)]}}
        original = copy.deepcopy(registry.desktop_state)
        session = Mock()
        session.request.return_value = HTTPResponse(payload=response(plan(step('task_complete', {}))))
        provider = GeminiProvider(AIConfig(mode='api', max_context_chars=12000), Credentials(['placeholder']), session)
        self.addCleanup(provider.close)
        provider.interpret('Inspect the requested page', [], registry, threading.Event())
        context = session.request.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertLessEqual(len(context), 12000)
        self.assertIn('Inspect the requested page', context)
        self.assertEqual(registry.desktop_state, original)

    def test_multiline_document_text_accepted_but_control_keys_rejected(self):
        registry = Registry([])
        item = step('ui_action', dict(window='w1', element='e1', operation='set_text', text='hello\nworld'))
        self.assertTrue(registry.plan(plan(item)))
        item['arguments']['text'] = 'bad\x00text'
        with self.assertRaises(AIError):
            registry.plan(plan(item))

    def test_catalog_cache_and_duplicate_ids_use_real_targets(self):
        catalog = AppCatalog()
        with patch.object(catalog, '_read', return_value=[dict(id='one', display_name='Test App', process='test.exe', target='C:\\test.exe')]) as read:
            self.assertEqual(len(catalog.refresh(timeout=1)), 1)
            catalog.refresh()
            read.assert_called_once()
        self.assertEqual(catalog.relevant('Open Test App')[0]['id'], 'one')


if __name__ == '__main__':
    unittest.main()
