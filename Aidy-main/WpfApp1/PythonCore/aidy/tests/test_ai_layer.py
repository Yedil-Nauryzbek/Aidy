"""Deterministic API, dialogue and real Aidy boundary tests. No desktop changes."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.ai_tools import AIError, Registry, TOOLS, permission, needs_confirmation
from aidy.ai_provider import AIConfig, Credentials, GeminiProvider, parse_response, retry_after
from aidy.ai_engine import AIEngine, Result
from aidy.ai_runtime import AIRuntime
from aidy.voice_auth import VoiceAuthManager, GUEST_ALLOWED_INTENTS, ADMIN_ONLY_INTENTS


def step(tool='open_app', arguments=None, delay=0):
    return {'tool': tool, 'arguments': {'app': 'discord'} if arguments is None else arguments, 'delay_seconds': delay}


def plan(*steps, question=''):
    return {'clarification': question, 'steps': list(steps)}


def response(payload):
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(payload)}]}}]}


class FakeProvider:
    config = AIConfig(mode='api', timeout_seconds=3)
    credentials = Credentials(['test-placeholder'])

    def __init__(self, *plans):
        self.plans = list(plans)
        self.seen = []
        self.closed = False

    def interpret(self, text, history, registry, cancel):
        self.seen.append((text, copy.deepcopy(history)))
        value = self.plans.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry(['discord', 'steam'])

    def test_each_tool_has_valid_typed_schema_and_permission(self):
        for name, tool in TOOLS.items():
            args = {}
            for key, schema in tool.parameters['properties'].items():
                args[key] = (schema['enum'][0] if 'enum' in schema else
                             schema['minimum'] if schema['type'] == 'integer' else
                             [] if schema['type'] == 'array' else
                             False if schema['type'] == 'boolean' else 'discord')
            if name == 'voice_access':
                args['role'] = 'Guest'
            if name == 'close_window':
                args['window'] = '$active'
            candidate = step(name, args)
            with self.subTest(tool=name):
                self.assertEqual(self.registry.plan(plan(candidate))['steps'][0], candidate)
                self.assertNotEqual(permission(candidate), 'dynamic')
        self.assertEqual(set(self.registry.schema()['properties']['steps']['items']['properties']['tool']['enum']), set(TOOLS))

    def test_baseline_command_families_are_exposed(self):
        from aidy import assistant as core
        self.assertEqual(set(core.COMMANDS), {x.replace('_', ' ') for x in TOOLS['system_action'].parameters['properties']['action']['enum']})
        self.assertTrue({'mouse_action', 'study_mode', 'custom_mode', 'voice_access', 'undo', 'repeat',
                         'window_switch', 'timer_start', 'timer_cancel', 'assistant_audio'} <= set(TOOLS))

    def test_unknown_extra_and_wrong_types_rejected(self):
        bad = [step('powershell', {}), step('open_app', {'app': 'cmd /c calc'}),
               step('open_app', {'app': 'steam', 'shell': True}),
               step('volume_change', {'direction': 'up', 'steps': True}),
               step('volume_change', {'direction': 'up', 'steps': 11}),
               step('set_volume', {'value': -1}), step('timer_start', {'seconds': 43201}),
               step('assistant_audio', {'muted': 1}), step('system_action', {'action': 'eval'}),
               step('close_app', {'app': '$unknown'})]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(AIError):
                self.registry.plan(plan(item))

    def test_plan_limits_and_ambiguity(self):
        for candidate in [plan(), plan(step(), question='Which app?'), plan(*[step()] * 7),
                          plan(step('mouse_action', {'action': 'click'}, 5)), plan(step(delay=5), step())]:
            with self.assertRaises(AIError):
                self.registry.plan(candidate)
        self.assertEqual(self.registry.plan(plan(question='Which app?'))['clarification'], 'Which app?')

    def test_entire_plan_validated_before_execution(self):
        provider = FakeProvider(plan(step(), step('not_a_tool', {})))
        execute = Mock()
        engine = AIEngine(provider, self.registry, Mock(return_value=True), Mock(return_value=True), execute)
        self.assertFalse(engine.run('two actions', ('local', 'Admin')).ok)
        execute.assert_not_called()

    def test_confirmations_are_local_rules(self):
        for item in [step('close_app'), step('system_action', {'action': 'shutdown'}),
                     step('system_action', {'action': 'restart'}), step('custom_mode', {'action': 'start'}),
                     step('close_everything', {}), step('study_mode', {'action': 'stop'})]:
            self.assertTrue(needs_confirmation(item))

    def test_every_csv_intent_has_an_api_family(self):
        import csv
        raw = (Path(__file__).resolve().parents[3] / 'commands.csv').read_text(encoding='utf-8-sig').lstrip()
        intents = {row['intent'] for row in csv.DictReader(raw.splitlines())}
        covered = {'open app', 'close app', 'custom_mode', 'play_music', 'unmute', 'undo_action', 'repeat',
                   'volume up', 'mute', 'close active', 'volume down', 'brightness up', 'brightness down',
                   'shutdown', 'open cmd', 'restart', 'lock', 'show desktop', 'screenshot', 'task manager',
                   'switch window', 'close everything', 'mouse_up', 'mouse_down', 'mouse_left', 'mouse_right',
                   'click', 'scroll_up', 'scroll_down', 'double_click', 'right_click'}
        self.assertEqual(intents, covered)
        self.assertIn('music', self.registry.app_ids)


class ParsingTests(unittest.TestCase):
    def test_complete_response(self):
        self.assertEqual(parse_response(response(plan(step()))), plan(step()))

    def test_incomplete_blocked_multiple_missing_and_duplicate_json(self):
        candidates = [{}, {'candidates': []}, {'candidates': [{ 'finishReason': 'MAX_TOKENS'}]},
                      {'candidates': [response(plan(step()))['candidates'][0]] * 2}]
        for text in ['{"steps":[],"steps":[]}', 'NaN', '{', '```json\n{}\n```']:
            item = response({})
            item['candidates'][0]['content']['parts'][0]['text'] = text
            candidates.append(item)
        for item in candidates:
            with self.assertRaises(AIError):
                parse_response(item)


class EngineTests(unittest.TestCase):
    def make(self, *plans):
        self.provider = FakeProvider(*plans)
        self.execute = Mock(return_value=Result('dispatched', 'Dispatched; final state unverified.'))
        self.authorize = Mock(return_value=True)
        self.confirm = Mock(return_value=True)
        self.engine = AIEngine(self.provider, Registry(['discord', 'steam']), self.authorize, self.confirm, self.execute)
        return self.engine

    def run_plan(self, text='request', identity=('local', 'Admin'), request_id=None):
        return self.engine.run(text, identity, request_id)

    def test_multi_step_order_and_contextual_app(self):
        self.make(plan(step(), step(arguments={'app': 'steam'})), plan(step('close_app', {'app': '$last'})))
        self.assertTrue(self.run_plan().ok)
        self.assertTrue(self.run_plan('now close it').ok)
        self.assertEqual([call.args[0]['arguments']['app'] for call in self.execute.call_args_list], ['discord', 'steam', 'steam'])
        self.assertEqual(self.confirm.call_count, 1)
        self.assertTrue(self.provider.seen[1][1])

    def test_clarification_then_followup_keeps_context(self):
        self.make(plan(question='Which application?'), plan(step()))
        self.assertEqual(self.run_plan('open it').status, 'clarification')
        self.assertTrue(self.engine.pending)
        self.assertTrue(self.run_plan('Discord').ok)
        self.assertTrue(self.engine.pending)  # bounded conversational follow-up window
        self.assertEqual(self.provider.seen[1][1][0]['clarification'], 'Which application?')

    def test_unknown_last_target_never_guessed(self):
        self.make(plan(step('close_app', {'app': '$last'})))
        self.assertIn('Which application', self.run_plan().message)
        self.execute.assert_not_called()

    def test_correction_uses_only_new_plan(self):
        self.make(plan(step()), plan(step(arguments={'app': 'steam'})))
        self.run_plan('Open Discord')
        self.run_plan('Actually open Steam instead')
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual(self.execute.call_args.args[0]['arguments']['app'], 'steam')

    def test_partial_failure_stops_dependent_steps(self):
        self.make(plan(step(), step(arguments={'app': 'steam'}), step('timer_start', {'seconds': 30})))
        self.execute.side_effect = [Result('verified', 'Discord running.'), Result('failed', 'Steam unavailable.')]
        result = self.run_plan()
        self.assertFalse(result.ok)
        self.assertIn('1 of 3', result.message)
        self.assertEqual(self.execute.call_count, 2)

    def test_authorization_is_after_model_and_per_step(self):
        self.make(plan(step(), step('system_action', {'action': 'shutdown'})))
        self.authorize.side_effect = lambda identity, action: action != 'shutdown'
        result = self.run_plan(identity=(42, 'Guest'))
        self.assertFalse(result.ok)
        self.assertEqual(self.execute.call_count, 1)
        self.assertIn('Access denied', result.message)

    def test_auth_rechecked_after_confirmation(self):
        self.make(plan(step('close_app')))
        self.authorize.side_effect = [True, False]
        self.assertFalse(self.run_plan().ok)
        self.confirm.assert_called_once()
        self.execute.assert_not_called()

    def test_confirmation_rejection(self):
        self.make(plan(step('close_app'), step()))
        self.confirm.return_value = False
        self.assertFalse(self.run_plan().ok)
        self.execute.assert_not_called()

    def test_cancellation_during_confirmation(self):
        self.make(plan(step('close_app')))
        self.confirm.side_effect = lambda *args: self.engine.cancel() or True
        self.assertEqual(self.run_plan().status, 'cancelled')
        self.execute.assert_not_called()

    def test_request_and_step_deduplication(self):
        self.make(plan(step()))
        self.assertTrue(self.run_plan(request_id='same-request').ok)
        self.assertFalse(self.run_plan(request_id='same-request').ok)
        self.assertEqual(len(self.provider.seen), 1)
        self.execute.assert_called_once()

    def test_failed_executor_is_not_retried(self):
        self.make(plan(step(), step()))
        self.execute.side_effect = RuntimeError('private error detail')
        result = self.run_plan(request_id='one')
        self.assertNotIn('private error', result.message)
        self.assertFalse(self.run_plan(request_id='one').ok)
        self.execute.assert_called_once()

    def test_identity_role_switch_clears_context(self):
        self.make(plan(step()), plan(step()), plan(step()))
        for identity in [(1, 'Admin'), (2, 'Admin'), (2, 'Guest')]:
            self.run_plan(identity=identity)
        self.assertTrue(all(not row[1] for row in self.provider.seen))

    def test_repeat_requires_fresh_confirmation(self):
        self.make(plan(step('close_app')), plan(step('repeat', {})))
        self.run_plan()
        self.run_plan('repeat')
        self.assertEqual(self.confirm.call_count, 2)

    def test_undo_chain_uses_checked_inverse(self):
        self.make(plan(step(), step(arguments={'app': 'steam'})), plan(step('undo', {'scope': 'chain'})))
        self.run_plan()
        self.assertTrue(self.run_plan('undo all').ok)
        inverses = [call.args[0] for call in self.execute.call_args_list[2:]]
        self.assertEqual([(s['tool'], s['arguments']['app']) for s in inverses], [('close_app', 'steam'), ('close_app', 'discord')])
        self.assertEqual(self.confirm.call_count, 2)
        self.assertEqual(self.engine.undo_chain, [])

    def test_schedule_checks_again_and_never_executes_twice(self):
        self.make(plan(step(delay=1)))
        self.assertEqual(self.run_plan().status, 'scheduled')
        self.execute.assert_not_called()
        due, action, actor, action_id = self.engine.scheduled[1]
        self.engine.scheduled[1] = (0, action, actor, action_id)
        self.assertTrue(self.engine.tick()[0].ok)
        self.assertEqual(self.engine.tick(), [])
        self.execute.assert_called_once()

    def test_schedule_revocation_and_cancel(self):
        self.make(plan(step(delay=1)))
        self.run_plan()
        row = self.engine.scheduled[1]
        self.engine.scheduled[1] = (0, *row[1:])
        self.authorize.return_value = False
        self.assertFalse(self.engine.tick()[0].ok)
        self.execute.assert_not_called()

    def test_close_and_late_provider_cannot_execute(self):
        entered, released = threading.Event(), threading.Event()
        self.make()
        def interpret(*args):
            entered.set()
            released.wait(1)
            return plan(step())
        self.provider.interpret = interpret
        result = []
        thread = threading.Thread(target=lambda: result.append(self.run_plan()))
        thread.start()
        self.assertTrue(entered.wait(1))
        self.engine.close()
        thread.join(1)
        released.set()
        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0].ok)
        self.execute.assert_not_called()
        self.assertTrue(self.provider.closed)

    def test_busy_request_is_rejected_without_second_provider_call(self):
        self.make(plan(step()))
        self.engine._lock.acquire()
        try:
            self.assertFalse(self.run_plan().ok)
        finally:
            self.engine._lock.release()
        self.assertFalse(self.provider.seen)

    def test_timeout_failure_never_uses_original_router(self):
        self.make(AIError('Google request timed out.'))
        self.assertFalse(self.run_plan().ok)
        self.execute.assert_not_called()

    def test_saved_preferences_are_resolved_before_context_and_confirmation(self):
        self.make(plan(step(arguments={'app': 'music'})), plan(step('close_app', {'app': '$last'})))
        self.engine.registry.aliases['music'] = 'steam'
        self.run_plan('play music')
        self.run_plan('close it')
        self.assertEqual(self.execute.call_args.args[0]['arguments']['app'], 'steam')
        self.assertEqual(self.confirm.call_args.args[0]['arguments']['app'], 'steam')

    def test_revoked_second_step_preserves_first_for_undo(self):
        self.make(plan(step(), step('system_action', {'action': 'shutdown'})))
        self.authorize.side_effect = lambda who, what: what != 'shutdown'
        self.run_plan()
        self.assertEqual([s['tool'] for s in self.engine.undo_chain], ['open_app'])

    def test_context_expiration_does_not_drop_authorized_schedule(self):
        self.make(plan(step(delay=3600)), plan(step('current_time', {})))
        self.run_plan()
        self.engine.context_until = 0
        self.run_plan()
        self.assertEqual(len(self.engine.scheduled), 1)

    def test_cancel_scheduled_is_explicit(self):
        self.make(plan(step(delay=3600)), plan(step('cancel_scheduled', {'id': 0})))
        self.run_plan()
        self.assertTrue(self.run_plan().ok)
        self.assertFalse(self.engine.scheduled)


class HTTPResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code, self.payload, self.headers = status, payload if payload is not None else response(plan(step())), headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield json.dumps(self.payload).encode()


class ProviderTests(unittest.TestCase):
    def make(self, *responses):
        self.session = Mock()
        self.session.request.side_effect = responses
        self.credentials = Credentials(['placeholder-one', 'placeholder-two', 'placeholder-three'])
        self.provider = GeminiProvider(AIConfig(mode='api', timeout_seconds=3), self.credentials, self.session)
        self.addCleanup(self.provider.close)
        return self.provider

    def interpret(self):
        return self.provider.interpret('Open Discord.', [], Registry(['discord']), threading.Event())

    def test_supported_endpoint_and_secret_only_header(self):
        self.make(HTTPResponse())
        self.interpret()
        call = self.session.request.call_args
        self.assertEqual(call.args[1], 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent')
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertNotIn('placeholder-one', json.dumps(call.kwargs['json']))
        self.assertIn('responseJsonSchema', call.kwargs['json']['generationConfig'])

    def test_invalid_key_excluded_then_bounded_fallback(self):
        self.make(HTTPResponse(400, {'error': {'details': [{'reason': 'API_KEY_INVALID'}]}}), HTTPResponse())
        self.assertTrue(self.interpret()['steps'])
        self.assertEqual(self.credentials.invalid, {0})
        self.assertEqual(self.session.request.call_count, 2)

    def test_permission_denial_does_not_rotate(self):
        self.make(HTTPResponse(403, {'error': {}}))
        with self.assertRaises(AIError):
            self.interpret()
        self.assertEqual(self.credentials.active, 0)
        self.assertEqual(self.session.request.call_count, 1)

    def test_shared_rate_limit_honors_retry_after_without_rotation(self):
        self.make(HTTPResponse(429, {'error': {}}, {'Retry-After': '60'}))
        with self.assertRaises(AIError):
            self.interpret()
        with self.assertRaises(AIError):
            self.interpret()
        self.assertGreater(self.credentials.cooldown_until, time.monotonic() + 58)
        self.assertEqual(self.session.request.call_count, 1)
        self.assertEqual(self.credentials.active, 0)

    def test_daily_quota_is_terminal_for_all_keys(self):
        self.make(HTTPResponse(429, {'error': {'details': [{'@type': 'google.rpc.QuotaFailure', 'quotaId': 'RequestsPerDay'}]}}))
        with self.assertRaises(AIError):
            self.interpret()
        self.assertTrue(self.credentials.quota_exhausted)
        with self.assertRaises(AIError):
            self.credentials.key()

    def test_network_errors_retry_bounded_and_redacted(self):
        import requests
        self.make(requests.ConnectionError('placeholder-one'), requests.Timeout('placeholder-two'))
        with self.assertRaises(AIError) as raised:
            self.interpret()
        self.assertEqual(self.session.request.call_count, 2)
        self.assertNotIn('placeholder', str(raised.exception))

    def test_server_error_retry_after_too_long_does_not_wait(self):
        self.make(HTTPResponse(503, {}, {'Retry-After': '120'}))
        with self.assertRaises(AIError):
            self.interpret()
        self.assertEqual(self.session.request.call_count, 1)
        self.assertGreater(self.credentials.cooldown_until, time.monotonic() + 119)

    def test_cancellation_prevents_network(self):
        self.make(HTTPResponse())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(AIError):
            self.provider.interpret('hello', [], Registry([]), cancel)
        self.session.request.assert_not_called()

    def test_close_releases_session_and_rejects_requests(self):
        self.make(HTTPResponse())
        self.provider.close()
        self.session.close.assert_called_once()
        with self.assertRaises(AIError):
            self.interpret()

    def test_retry_after_google_details_and_http_date(self):
        self.assertEqual(retry_after({}, {'error': {'details': [{'@type': 'google.rpc.RetryInfo', 'retryDelay': '2.5s'}]}}), 2.5)
        from email.utils import formatdate
        self.assertGreater(retry_after({'Retry-After': formatdate(time.time() + 60, usegmt=True)}, {}), 58)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.execute('CREATE TABLE voice_users (id INTEGER, role TEXT, expires_at REAL)')
        self.connection.executemany('INSERT INTO voice_users VALUES (?,?,?)', [(1, 'Admin', None), (2, 'User', None), (3, 'Guest', None)])
        self.auth = VoiceAuthManager.__new__(VoiceAuthManager)
        self.auth._get_conn = lambda: self.connection
        self.addCleanup(self.connection.close)

    def test_role_parity_at_execution_boundary(self):
        for intent in GUEST_ALLOWED_INTENTS | ADMIN_ONLY_INTENTS | {'open_cmd', 'task_manager', 'unknown'}:
            self.assertTrue(self.auth.authorize_identity((1, 'Admin'), intent))
            self.assertEqual(self.auth.authorize_identity((2, 'User'), intent), intent not in ADMIN_ONLY_INTENTS)
            self.assertEqual(self.auth.authorize_identity((3, 'Guest'), intent), intent in GUEST_ALLOWED_INTENTS)

    def test_missing_expired_revoked_and_changed_role_fail_closed(self):
        self.assertFalse(self.auth.authorize_identity((999, 'Admin'), 'open_app'))
        self.assertFalse(self.auth.authorize_identity((3, 'Admin'), 'open_app'))
        self.connection.execute('UPDATE voice_users SET expires_at=? WHERE id=1', (time.time() - 1,))
        self.assertFalse(self.auth.authorize_identity((1, 'Admin'), 'open_app'))
        self.connection.execute('DELETE FROM voice_users WHERE id=2')
        self.assertFalse(self.auth.authorize_identity((2, 'User'), 'open_app'))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        import test_app_routing
        self.env = patch.dict(os.environ, {'AIDY_INTERPRETATION_MODE': 'original'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.fixture = test_app_routing.AppRoutingTests()
        self.host = self.fixture.make_assistant()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.host._ai
        self.runtime.config = AIConfig(mode='api')
        self.runtime.reload = Mock()
        self.runtime.poll_cancel_audio = Mock(side_effect=self.runtime.progress)
        self.runtime.report = Mock()
        self.runtime.windows.snapshot = Mock(return_value=[])
        self.host.voice_id_enabled = False
        self.host.voice = Mock(muted=True)

    def attach(self, *plans):
        self.provider = FakeProvider(*plans)
        self.runtime.engine = AIEngine(self.provider, Registry([a['id'] for a in self.host.apps]),
                                       self.runtime.authorize, self.runtime.confirm, self.runtime.execute)

    def test_accepted_task_bypasses_all_local_interpreters_in_api_mode(self):
        from aidy import assistant as core
        self.attach(plan(step()))
        self.host.api.get_intent = Mock(side_effect=AssertionError('local model called'))
        self.host.process_command = Mock(side_effect=AssertionError('local router called'))
        self.host._tasklist_process_names = Mock(return_value={'discord.exe'})
        with patch.object(core, 'launch_app', return_value=True) as launch:
            self.assertTrue(self.host._safe_process_command('Would you bring up Discord for me?'))
        launch.assert_called_once()
        self.assertEqual(len(self.provider.seen), 1)

    def test_api_failure_has_no_local_fallback(self):
        self.attach(AIError('offline'))
        self.host.process_command = Mock()
        self.assertFalse(self.host._safe_process_command('open Discord'))
        self.host.process_command.assert_not_called()

    def test_cancellation_needs_no_network_or_identity(self):
        self.attach()
        self.runtime.identity = Mock(side_effect=AssertionError('identity required'))
        self.assertFalse(self.host._safe_process_command('cancel'))
        self.assertFalse(self.provider.seen)

    def test_same_speaker_confirmation_required(self):
        self.attach()
        self.host._listen_confirm_reply = Mock(return_value='yes')
        self.runtime.identity = Mock(return_value=(2, 'Admin'))
        self.assertFalse(self.runtime.confirm(step('close_app'), (1, 'Admin')))

    def test_absolute_volume_does_not_fall_back_to_relative_change(self):
        from aidy import assistant as core
        with patch.object(core, 'set_volume_percent', return_value=False), patch.object(self.host, '_apply_volume_steps') as relative:
            self.assertFalse(self.runtime.execute(step('set_volume', {'value': 30})).ok)
        relative.assert_not_called()

    def test_windows_failure_not_reported_as_success(self):
        from aidy import assistant as core
        with patch.dict(core.COMMANDS, {'shutdown': lambda: 1, 'show desktop': lambda: False}):
            for action in ['shutdown', 'show_desktop']:
                self.assertFalse(self.runtime.execute(step('system_action', {'action': action})).ok)

    def test_known_executor_without_observable_state_says_dispatched(self):
        from aidy import assistant as core
        self.host._tasklist_process_names = Mock(return_value=set())
        with patch.object(core, 'launch_app', return_value=True):
            result = self.runtime.execute(step())
        self.assertEqual(result.status, 'dispatched')
        self.assertEqual(result.message, 'Opening discord.')

    def test_non_action_filter_accepts_natural_phrasing_only_in_api_mode(self):
        self.assertFalse(self.host._is_non_action_utterance('Could you make things a little quieter?'))

    def test_mode_reload_closes_provider_and_preserves_original_router(self):
        self.attach(plan(step()))
        self.runtime.reload = AIRuntime.reload.__get__(self.runtime)
        with patch('aidy.ai_runtime.AIConfig.load', return_value=AIConfig(mode='original')):
            self.runtime.reload()
        self.assertFalse(self.runtime.enabled)
        self.assertTrue(self.provider.closed)
        self.host.process_command = Mock(return_value=True)
        with patch('aidy.ai_runtime.AIConfig.load', return_value=AIConfig(mode='original')):
            self.assertTrue(self.host._safe_process_command('open Discord'))
        self.host.process_command.assert_called_once()

    def test_malformed_config_never_silently_falls_back(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'AIDY_AI_CONFIG': str(Path(folder) / 'ai.local.json')}):
            Path(folder, 'ai.local.json').write_text('{broken')
            with self.assertRaises(AIError):
                AIConfig.load(folder)

    def test_workspace_restoration_rechecks_owner_and_permission(self):
        self.runtime._study_identity = (1, 'Admin')
        self.runtime.identity = Mock(return_value=(2, 'Admin'))
        self.assertFalse(self.runtime.confirm_workspace_identity())
        self.runtime.identity.return_value = (1, 'Admin')
        self.runtime.authorize = Mock(return_value=False)
        self.assertFalse(self.runtime.confirm_workspace_identity())

    def test_custom_slots_are_frozen_at_confirmation(self):
        self.attach()
        self.runtime.engine.identity = ('local', 'Admin')
        self.host._listen_confirm_reply = Mock(return_value='yes')
        with tempfile.TemporaryDirectory() as folder:
            self.host.config_path = str(Path(folder) / 'config.json')
            saved = {'custom_mode': {'slots': [{'action_type': 'open_app', 'target': 'discord'}]}}
            Path(self.host.config_path).write_text(json.dumps(saved))
            self.assertTrue(self.runtime.confirm(step('custom_mode', {'action': 'start'}), ('local', 'Admin')))
            saved['custom_mode']['slots'][0] = {'action_type': 'function', 'target': 'shutdown_computer'}
            Path(self.host.config_path).write_text(json.dumps(saved))
            with patch.object(self.runtime, 'execute', return_value=Result('dispatched', 'Sent')) as execute:
                self.assertTrue(self.runtime._custom_mode().ok)
                self.assertEqual(execute.call_args.args[0]['tool'], 'open_app')
                self.assertEqual(execute.call_args.args[0]['arguments'], {'app': 'discord'})

    def test_custom_mode_stops_on_failed_slot(self):
        self.attach()
        self.runtime.engine.identity = ('local', 'Admin')
        self.runtime._confirmed_slots = [{'action_type': 'open_app', 'target': 'discord'}, {'action_type': 'open_app', 'target': 'steam'}]
        with patch.object(self.runtime, 'execute', return_value=Result('failed', 'Missing app')) as execute:
            self.assertFalse(self.runtime._custom_mode().ok)
            execute.assert_called_once()


if __name__ == '__main__':
    unittest.main()
