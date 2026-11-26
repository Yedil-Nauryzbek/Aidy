"""Fast app interpretation cannot omit actions or weaken the general planner."""
import copy
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aidy.ai_app_profile import app_chain, validate_chain
from aidy.ai_engine import AIEngine, Result
from aidy.ai_provider import AIConfig, Credentials, GeminiProvider, function_plan
from aidy.ai_tasks import task_schema
from aidy.ai_runtime import AIRuntime
from aidy.ai_tools import Registry, AIPlanError
from test_ai_layer import HTTPResponse, response, plan, step
from test_audio_pipeline import bare_aidy


class ProcessingProfileTests(unittest.TestCase):
    def test_api_handoff_reuses_capture_and_executor_still_closes_it(self):
        aidy = bare_aidy()
        aidy._ai = SimpleNamespace(enabled=True, awaiting_number=False, reload=Mock(), run=Mock(return_value=True))
        aidy._capture_utterance = Mock(return_value=object())
        aidy._transcribe_capture = Mock(return_value='open discord')
        aidy.stop_stream = Mock()
        self.assertEqual(aidy.listen_command(speak_on_empty=False), 'open discord')
        self.assertTrue(aidy._safe_process_command('open discord'))
        aidy.stop_stream.assert_not_called()
        runtime = AIRuntime.__new__(AIRuntime)
        runtime.host = aidy
        runtime._confirmed_actions = {}
        runtime.execute(step('current_time', {}))
        aidy.stop_stream.assert_called_once()

    def test_empty_error_original_and_ptt_capture_are_not_kept_for_api(self):
        for enabled, ptt, transcript in [(False,False,'open discord'),(True,True,'open discord'),(True,False,None)]:
            aidy = bare_aidy()
            aidy._ai = SimpleNamespace(enabled=enabled, awaiting_number=False)
            aidy.push_to_talk_enabled = ptt
            aidy._capture_utterance = Mock(return_value=object())
            aidy._transcribe_capture = Mock(return_value=transcript)
            aidy.stop_stream = Mock()
            aidy.listen_command(speak_on_empty=False)
            aidy.stop_stream.assert_called_once()
        aidy._transcribe_capture.side_effect = OSError('device lost')
        aidy.stop_stream.reset_mock()
        with self.assertRaises(OSError):aidy.listen_command(speak_on_empty=False)
        aidy.stop_stream.assert_called_once()

    def test_general_contract_limits_model_tool_choices_and_reports_specific_structural_errors(self):
        schema = task_schema(Registry([]), ['open_app', 'close_app'])
        goal = schema['properties']['goals']['items']['properties']
        self.assertEqual(goal['steps']['items']['properties']['tool']['enum'], ['close_app', 'open_app'])
        self.assertEqual(schema['properties']['continuation']['enum'], ['new_task', 'continue_task'])
        bad = dict(continuation='new_task', clarification='', goals=[dict(id='g1')])
        data = {'candidates': [dict(finishReason='STOP', content=dict(parts=[dict(functionCall=dict(name='plan_task', args=bad))]))]}
        with self.assertRaisesRegex(AIPlanError, 'Each goal needs'):
            function_plan(data, {'plan_task', 'open_app', 'close_app'})

    def registry(self):
        registry = Registry(['discord', 'steam', 'whatsapp_web', 'chrome'], {'browser': 'chrome'})
        registry.app_names = {'whatsapp_web': ['whatsapp', 'whats app']}
        return registry

    def test_complete_explicit_chains_qualify_but_contextual_tasks_do_not(self):
        registry = self.registry()
        self.assertEqual(app_chain('Open WhatsApp and then open the Discord', registry),
                         [('open_app', 'whatsapp_web'), ('open_app', 'discord')])
        for text in ["Don't open Steam and open Discord", 'Open Steam and write Hello',
                     'Open Discord and close it', 'Open Steam after five seconds and open Discord',
                     'Open WhatsApp and search for Discord',
                     'Open Unknown and open Steam', 'Open Steam and open Steam but not Discord',
                     'Open ChatGPT and then open Discord']:
            with self.subTest(text=text):
                self.assertIsNone(app_chain(text, registry))
        self.assertEqual(app_chain('Open WhatsApp and Discord',registry),
                         [('open_app','whatsapp_web'),('open_app','discord')])

    def test_pending_task_retry_and_agent_observation_keep_the_general_contract(self):
        for field, value in [('pending_task', {'goals': []}), ('plan_feedback', 'Fix the plan'), ('agent_mode', True)]:
            registry = self.registry()
            setattr(registry, field, value)
            self.assertIsNone(app_chain('Open Steam and open Discord', registry))

    def test_alias_ambiguity_cannot_select_a_target(self):
        registry = self.registry()
        registry.app_names['steam'] = ['whatsapp']
        self.assertIsNone(app_chain('Open WhatsApp and open Discord', registry))

    def test_omitted_extra_reversed_or_changed_actions_are_rejected_before_dispatch(self):
        registry = self.registry()
        expected = app_chain('Open Steam and close Discord', registry)
        good = [step('open_app', {'app': 'steam'}), step('close_app', {'app': 'discord'})]
        bad = [good[:1], good + good[:1], good[::-1], [good[0], step('open_app', {'app': 'discord'})]]
        for actions in bad:
            with self.subTest(actions=actions), self.assertRaises(AIPlanError):
                validate_chain(registry.plan(plan(*copy.deepcopy(actions))), expected, registry)

    def test_repeated_actions_remain_distinct_model_selected_goals(self):
        registry = self.registry()
        expected = app_chain('Open Steam and open Steam', registry)
        result = validate_chain(registry.plan(plan(step('open_app', {'app': 'steam'}),
                                                 step('open_app', {'app': 'steam'}))), expected, registry)
        self.assertEqual([g['id'] for g in result['goals']], ['g1', 'g2'])
        self.assertEqual([g['step_start'] for g in result['goals']], [0, 1])

    def test_generic_browser_follows_saved_preference(self):
        registry = self.registry()
        expected = app_chain('Open browser and open Steam', registry)
        self.assertEqual(expected[0], ('open_app', 'chrome'))
        validate_chain(registry.plan(plan(step('open_app', {'app': 'browser'}),
                                         step('open_app', {'app': 'steam'}))), expected, registry)

    def test_incomplete_model_answer_replans_with_full_contract_without_partial_execution(self):
        session = Mock()
        session.request.side_effect = [HTTPResponse(payload=response(dict(clarification='',steps=[dict(tool='open_app',app='steam')]))),
            HTTPResponse(payload=response(plan(step('open_app', {'app': 'steam'}), step('open_app', {'app': 'discord'}))))]
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), session)
        self.addCleanup(provider.close)
        executed = Mock(return_value=Result('verified', 'Done.'))
        engine = AIEngine(provider, self.registry(), lambda *a: True, lambda *a: True, executed)
        engine.local_commands = False  # exercises the Google replan path
        result = engine.run('Open Steam and open Discord', ('local', 'Admin'))
        self.assertTrue(result.ok)
        self.assertEqual([c.args[0]['arguments']['app'] for c in executed.call_args_list], ['steam', 'discord'])
        bodies = [call.kwargs['json'] for call in session.request.call_args_list]
        self.assertIn('responseJsonSchema', bodies[0]['generationConfig'])
        self.assertIn('tools', bodies[1])
        self.assertEqual(engine.timings['model_calls'], 2)

    def test_successful_chain_keeps_details_in_ui_and_uses_short_honest_voice_reply(self):
        runtime = AIRuntime.__new__(AIRuntime)
        runtime.engine = None
        runtime.host = SimpleNamespace(voice=Mock(muted=False), _stop_event=threading.Event(), _deafen_after_speak=Mock())
        result = Result('dispatched', '2 of 2 goals requested; some final states are unverified.', spoken_message='Requests sent.')
        with patch('aidy.ai_runtime.UI_MODE', True), patch('builtins.print') as output:
            runtime.report(result)
        runtime.host.voice.tts_blocking.assert_called_once_with('Requests sent.')
        output.assert_any_call('AI_RESPONSE:' + result.message, flush=True)

    def test_confirmation_prompt_speaks_its_short_question_and_shows_the_full_one(self):
        runtime = AIRuntime.__new__(AIRuntime)
        runtime.engine = None
        runtime.host = SimpleNamespace(voice=Mock(muted=False), _stop_event=threading.Event(), _deafen_after_speak=Mock())
        result = Result('clarification', 'invoke Send in WhatsApp - Edge. Are you sure?', 'are_you_sure_clean',
                        spoken_message='Press Send. Are you sure?')
        with patch('aidy.ai_runtime.UI_MODE', True), patch('builtins.print') as output:
            runtime.report(result)
        runtime.host.voice.play_or_tts.assert_called_once_with('are_you_sure_clean', 'Press Send. Are you sure?')
        output.assert_any_call('AI_RESPONSE:' + result.message, flush=True)

    def ui_confirmation_runtime(self):
        runtime = AIRuntime.__new__(AIRuntime)
        runtime.host = SimpleNamespace()
        runtime.windows = SimpleNamespace(_frozen=None)
        runtime._confirmed_slots = None
        runtime._confirmed_actions = {}
        view = dict(elements=[dict(id='e1', name='Send', automation_id=''), dict(id='e2', name='Отправить', automation_id='')])
        runtime.desktop_task = SimpleNamespace(views={'w1': view}, target=lambda ref: dict(title='WhatsApp and 11 more pages - Personal - Microsoft Edge'))
        return runtime

    def test_ui_confirmation_keeps_the_window_on_screen_but_not_in_speech(self):
        runtime = self.ui_confirmation_runtime()
        for element, spoken in [('e1', 'Press Send'), ('e2', 'Press this control')]:
            with self.subTest(element=element):
                label, said, _ = runtime._prepare_confirmation(step('ui_action', dict(window='w1', element=element, operation='invoke', text='')))
                self.assertIn('WhatsApp and 11 more pages', label)
                self.assertEqual(said, spoken)

    def test_send_enter_point_click_and_long_names_get_clear_short_questions(self):
        runtime = self.ui_confirmation_runtime()
        runtime.desktop_task.views['w1']['elements'].append(
            dict(id='e3', name='Remove this person from the group and notify everyone', automation_id=''))
        cases = [(step('key_input', dict(window='w1', element='', chord='enter')), 'Send the message'),
                 (step('click_point', dict(window='w1', x=10, y=10, gesture='click', target='block button')), 'Click block button'),
                 (step('ui_action', dict(window='w1', element='e3', operation='invoke', text='')), 'Press Remove this person from the')]
        for action, spoken in cases:
            with self.subTest(tool=action['tool']):
                self.assertEqual(runtime._prepare_confirmation(action)[1], spoken)

    def test_confirm_batch_reports_the_short_spoken_question(self):
        runtime = self.ui_confirmation_runtime()
        runtime.report = Mock()
        runtime.engine = SimpleNamespace(cancel_event=threading.Event())
        runtime.host = SimpleNamespace(_listen_confirm_reply=Mock(return_value='no'), _classify_confirm_reply=Mock(return_value='no'),
                                       _stop_event=threading.Event())
        with patch('aidy.ai_runtime.ui_state'):
            self.assertFalse(runtime.confirm_batch([step('ui_action', dict(window='w1', element='e1', operation='invoke', text=''))], ('local', 'Admin')))
        prompt = runtime.report.call_args.args[0]
        self.assertEqual(prompt.spoken_message, 'Press Send. Are you sure?')
        self.assertIn('Microsoft Edge', prompt.message)

    def test_failures_and_answers_are_not_replaced_with_success_acknowledgements(self):
        runtime = AIRuntime.__new__(AIRuntime)
        runtime.engine = None
        runtime.host = SimpleNamespace(voice=Mock(muted=False), _stop_event=threading.Event(), _deafen_after_speak=Mock())
        for result in [Result('failed', 'The app could not be opened.', spoken_message='Done.'),
                       Result('verified', 'The time is 17:30.'), Result('clarification', 'Which application?')]:
            runtime.host.voice.reset_mock()
            runtime.report(result)
            runtime.host.voice.tts_blocking.assert_called_once_with(result.message)


if __name__ == '__main__':
    unittest.main()
