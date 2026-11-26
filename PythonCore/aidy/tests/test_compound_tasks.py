"""Compound requests keep every clause until its observed outcome is reached."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.ai_tasks import task_schema
from aidy.ai_tools import Registry, AIPlanError, UI_EFFECT_TOOLS
from aidy.ai_engine import AIEngine, Result
import test_ai_layer as fixtures
import test_document_workflows as native_fixtures
from test_task_understanding import goal, task, planned


def observe():
    return fixtures.step('observe_desktop', {'window': ''})


class CompoundPlanningTests(unittest.TestCase):
    provider = native_fixtures.NativePlanningTests.provider

    def test_goal_declaring_ui_tools_always_needs_followup(self):
        value = planned(goal('Open the chat with Bacon', observe(), required=['observe_desktop', 'ui_action']))
        self.assertTrue(value['goals'][0]['requires_followup'])

    def test_dependent_goal_link_is_normalized_instead_of_rejecting_the_plan(self):
        payload = task(goal('Open Discord', fixtures.step('open_app', {'app': 'discord'})),
                       goal('Open the chat with Ajile', observe(), id='g2', required=['observe_desktop', 'ui_action']))
        payload['goals'][1]['criteria'] = [dict(kind='tool_verified', target='ui_action', source_goal='g1')]
        provider = self.provider(native_fixtures.native('plan_task', payload))
        plan = provider.interpret('open discord and open the chat with ajile', [], Registry(['discord']), threading.Event())
        self.assertEqual(plan['goals'][1]['criteria'][0]['source_goal'], '')
        self.assertEqual(len(plan['goals']), 2)

    def test_initial_schema_declares_future_ui_tools_without_offering_them_as_steps(self):
        schema = task_schema(Registry([]), ['open_app', 'observe_desktop'], UI_EFFECT_TOOLS)
        goal_schema = schema['properties']['goals']['items']['properties']
        self.assertIn('ui_action', goal_schema['required_tools']['items']['enum'])
        self.assertNotIn('ui_action', goal_schema['steps']['items']['properties']['tool']['enum'])

    def test_legacy_mouse_action_inside_an_app_task_is_sent_back_for_replanning(self):
        payload = task(goal('Open Telegram', fixtures.step('open_app', {'app': 'telegram'})),
                       goal('Write hello to Ajile', fixtures.step('mouse_action', {'action': 'click'}), id='g2'))
        provider = self.provider(native_fixtures.native('plan_task', payload))
        with self.assertRaises(AIPlanError):
            provider.interpret('open telegram and write hello to ajile', [], Registry(['telegram']), threading.Event())

    def test_launch_only_plan_that_drops_a_clause_gets_one_correction(self):
        launch_only = task(goal('Open WhatsApp Web', fixtures.step('open_app', {'app': 'whatsapp_web'})))
        provider = self.provider(native_fixtures.native('plan_task', launch_only))
        registry = Registry(['whatsapp_web'])
        with self.assertRaises(AIPlanError):
            provider.interpret('open whatsapp and then find the chat with bacon', [], registry, threading.Event())
        # The retry carries feedback; the guard never turns into a hard failure.
        registry.plan_feedback = 'Every clause of the request is a goal.'
        provider = self.provider(native_fixtures.native('plan_task', launch_only))
        self.assertEqual(len(provider.interpret('open whatsapp and then find the chat with bacon', [], registry, threading.Event())['goals']), 1)

    def test_two_app_launches_are_not_treated_as_a_dropped_clause(self):
        both = task(goal('Open Discord', fixtures.step('open_app', {'app': 'discord'})),
                    goal('Open Steam', fixtures.step('open_app', {'app': 'steam'}), id='g2'))
        provider = self.provider(native_fixtures.native('plan_task', both))
        self.assertEqual(len(provider.interpret('open discord and steam', [], Registry(['discord', 'steam']), threading.Event())['goals']), 2)

    def test_standalone_spoken_click_keeps_the_legacy_mouse_command(self):
        payload = task(goal('Click', fixtures.step('mouse_action', {'action': 'click'})))
        provider = self.provider(native_fixtures.native('plan_task', payload))
        plan = provider.interpret('click', [], Registry([]), threading.Event())
        self.assertEqual(plan['steps'][0]['tool'], 'mouse_action')


class CompoundExecutionTests(unittest.TestCase):
    def engine(self, *plans):
        self.provider = fixtures.FakeProvider(*plans)
        self.execute = Mock(return_value=Result('verified', 'Done.'))
        engine = AIEngine(self.provider, Registry(['discord', 'whatsapp_web']), lambda *a: True, Mock(return_value=True), self.execute)
        engine.start_agent = Mock()
        engine.refresh_agent = Mock()
        return engine

    def test_open_app_then_chat_continues_after_the_launch(self):
        initial = planned(goal('Open Discord', fixtures.step('open_app', {'app': 'discord'})),
                          goal('Open the chat with Ajile', observe(), id='g2', required=['observe_desktop', 'ui_action']))
        click = fixtures.plan(fixtures.step('ui_action', dict(window='w1', element='e1', operation='invoke', text='')))
        engine = self.engine(initial, click, fixtures.plan(fixtures.step('task_complete', {})))
        result = engine.run('open discord and open the chat with ajile', ('local', 'Admin'))
        self.assertEqual(result.status, 'verified')
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list], ['open_app', 'observe_desktop', 'ui_action'])
        self.assertEqual(len(self.provider.seen), 3)

    def chat_task(self, *later):
        initial = planned(goal('Open WhatsApp Web', fixtures.step('open_app', {'app': 'whatsapp_web'})),
                          goal('Open the chat with Bacon', observe(), id='g2', required=['observe_desktop', 'ui_action']))
        return self.engine(initial, *later)

    def test_failure_that_sent_no_input_is_replanned_once_with_feedback(self):
        navigate = fixtures.plan(fixtures.step('browser_action', dict(window='$browser', operation='navigate', text='https://web.whatsapp.com')))
        click = fixtures.plan(fixtures.step('ui_action', dict(window='w1', element='e1', operation='invoke', text='')))
        engine = self.chat_task(navigate, click, fixtures.plan(fixtures.step('task_complete', {})))
        outcomes = iter([Result('verified', 'Opened.'), Result('verified', 'Desktop inspected.'),
                         Result('failed', 'The browser address field did not receive focus. No address was entered.'),
                         Result('verified', 'Interface changed.')])
        self.execute.side_effect = lambda step: next(outcomes)
        result = engine.run('open whatsapp and open the chat with bacon', ('local', 'Admin'))
        self.assertEqual(result.status, 'verified')
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],
                         ['open_app', 'observe_desktop', 'browser_action', 'ui_action'])

    def test_failure_with_unknown_input_state_stops_without_another_model_call(self):
        click = fixtures.plan(fixtures.step('ui_action', dict(window='w1', element='e1', operation='invoke', text='')))
        engine = self.chat_task(click)
        outcomes = iter([Result('verified', 'Opened.'), Result('verified', 'Desktop inspected.'),
                         Result('failed', 'UI Automation timed out. The action may have been sent and will not be retried.')])
        self.execute.side_effect = lambda step: next(outcomes)
        result = engine.run('open whatsapp and open the chat with bacon', ('local', 'Admin'))
        self.assertEqual(result.status, 'failed')
        self.assertEqual(len(self.provider.seen), 2)

    def test_clarification_answer_is_interpreted_with_the_original_request(self):
        question = dict(clarification='WhatsApp app is not installed. Open WhatsApp Web?', steps=[])
        engine = self.engine(question, fixtures.plan(fixtures.step('open_app', {'app': 'whatsapp_web'})))
        original = 'open the whatsapp app and then open the chat with bacon'
        self.assertEqual(engine.run(original, ('local', 'Admin')).status, 'clarification')
        engine.run('yes', ('local', 'Admin'))
        self.assertEqual(self.provider.seen[1][0], original + '\nUser clarification: yes')


class KeyboardToolTests(unittest.TestCase):
    def registry_step(self, tool, arguments):
        return Registry([]).plan(dict(clarification='', steps=[fixtures.step(tool, arguments)]))['steps'][0]

    def test_navigation_chords_are_allowed_and_dangerous_ones_are_not(self):
        for chord in ('enter', 'ctrl+k', 'ctrl+f', 'ctrl+l', 'alt+left', 'f5'):
            self.registry_step('key_input', dict(window='w1', element='', chord=chord))
        for chord in ('alt+f4', 'ctrl+enter', 'delete', 'win+r', 'ctrl+shift+delete'):
            with self.subTest(chord=chord), self.assertRaises(AIPlanError):
                self.registry_step('key_input', dict(window='w1', element='', chord=chord))

    def test_type_text_is_one_bounded_line(self):
        self.registry_step('type_text', dict(window='w1', element='', text='Bacon'))
        for text in ('hello\nsend', 'x' * 501):
            with self.subTest(text=text[:10]), self.assertRaises(AIPlanError):
                self.registry_step('type_text', dict(window='w1', element='', text=text))

    def test_worker_refuses_chords_outside_the_allowlist_before_any_input(self):
        from aidy.ai_uia_worker import Automation
        from aidy.ai_tools import AIError
        with self.assertRaises(AIError):
            Automation.__new__(Automation).keys(dict(hwnd=1, pid=1, process='x.exe'), 'alt+f4')

    def task(self, request, focus_name, composed=False):
        from types import SimpleNamespace
        from aidy.ai_automation import DesktopTask
        from aidy.ai_runtime import AIRuntime
        window = dict(hwnd=7, pid=3, process='chrome.exe', title='WhatsApp')
        runtime = SimpleNamespace(engine=Mock(), windows=SimpleNamespace(targets={'w1': window}, active_ref='w1'),
                                  _confirmation_key=AIRuntime._confirmation_key)
        task = DesktopTask(runtime, request)
        task.input_window = dict(window)
        task.call = Mock(return_value=dict(focus=dict(id='f1', name=focus_name, automation_id='', control=50004)))
        if composed:
            task.composed['w1'] = focus_name
        return task

    def enter(self):
        return fixtures.step('key_input', dict(window='w1', element='', chord='enter'))

    def test_enter_in_a_search_box_needs_no_confirmation(self):
        self.assertFalse(self.task('open the chat with bacon', 'Search or start a new chat', composed=True).keyboard_guard(self.enter()))

    def test_enter_in_a_message_box_is_refused_without_a_send_request(self):
        from aidy.ai_tools import AIError
        for request in ('write hello to ajile but do not send it', 'write hello to ajile'):
            with self.subTest(request=request), self.assertRaises(AIError):
                self.task(request, 'Type a message').keyboard_guard(self.enter())

    def test_enter_after_typing_into_an_unnamed_field_is_confirmed_when_sending_was_asked(self):
        task = self.task('send hello to bacon', '', composed=True)
        self.assertTrue(task.keyboard_guard(self.enter()))
        self.assertEqual(list(task.focus_bindings.values()), ['f1'])

    def test_element_named_by_unique_partial_text_is_resolved(self):
        task = self.task('open the chat with bacon', 'Search')
        task.views['w1'] = dict(view='v1', elements=[
            dict(id='e1', name='Bacon 🥓 yesterday: see you', automation_id='', control=50007),
            dict(id='e2', name='Ajile', automation_id='', control=50007),
            dict(id='e3', name='bacon recipes', automation_id='', control=50020)])
        step = task.prepare(fixtures.step('ui_action', dict(window='w1', element='bacon', operation='select', text='')))
        self.assertEqual(step['arguments']['element'], 'e1')
        task.views['w1']['elements'].append(dict(id='e4', name='Bacon fan club', automation_id='', control=50007))
        with self.assertRaises(AIPlanError):
            task.prepare(fixtures.step('ui_action', dict(window='w1', element='bacon', operation='select', text='')))

    def test_typing_never_needs_confirmation_and_binds_the_checked_focus(self):
        task = self.task('write hello to ajile but do not send it', 'Type a message')
        self.assertFalse(task.keyboard_guard(fixtures.step('type_text', dict(window='w1', element='', text='hello'))))
        self.assertEqual(list(task.focus_bindings.values()), ['f1'])


class MessengerHintTests(unittest.TestCase):
    def test_contact_named_by_an_emoji_is_searched_by_the_emoji_itself(self):
        from aidy.ai_recipes import app_hints
        for app in ('whatsapp', 'telegram'):
            with self.subTest(app=app):
                hint = app_hints(f'open {app} and open the contact with the bacon emoji', Registry([]))[app]
                self.assertIn('emoji character', hint)


class SpokenReplyTests(unittest.TestCase):
    """Aidy answers a finished task with one short prerecorded word; answers stay spoken."""
    def engine(self, payload, *results):
        from aidy.ai_tools import Registry as Reg
        engine = AIEngine(fixtures.FakeProvider(payload), Reg([]), lambda *a: True, lambda *a: True, Mock(side_effect=list(results)))
        engine.start_agent = lambda text: None
        return engine

    def url(self, address):
        return fixtures.step('open_url', {'url': address})

    def test_two_web_goals_end_with_a_single_done(self):
        payload = planned(goal('Search Oscar Hartman on YouTube', self.url('https://www.youtube.com/results?search_query=oscar')),
                          goal('Search skills on GitHub', self.url('https://github.com/search?q=skills'), id='g2'))
        sent = Result('dispatched', 'Website request sent.', spoken_message='Done.')
        result = self.engine(payload, sent, sent).run('search oscar on youtube and then skills on github', ('local', 'Admin'))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.spoken_message, 'Done.')

    def test_single_goal_keeps_the_executor_spoken_reply(self):
        payload = planned(goal('Search Oscar Hartman on YouTube', self.url('https://www.youtube.com/results?search_query=oscar')))
        result = self.engine(payload, Result('verified', 'Website opened.', spoken_message='Done.')).run(
            'search oscar hartman on youtube', ('local', 'Admin'))
        self.assertEqual((result.status, result.spoken_message), ('verified', 'Done.'))

    def test_plain_action_chain_says_done(self):
        steps = fixtures.plan(fixtures.step('volume_change', {'direction': 'up', 'steps': 2}),
                              fixtures.step('brightness_change', {'direction': 'up', 'steps': 2}))
        sent = Result('dispatched', 'Adjustment sent.')
        result = self.engine(steps, sent, sent).run('volume up by two and brightness up by two', ('local', 'Admin'))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.spoken_message, 'Done.')

    def test_answers_to_questions_in_a_chain_are_kept(self):
        payload = planned(goal('Tell the current time', fixtures.step('current_time', {})),
                          goal('Open YouTube', self.url('https://www.youtube.com/'), id='g2'))
        result = self.engine(payload, Result('verified', 'It is 10:15.'),
                             Result('verified', 'Website opened.', spoken_message='Done.')).run(
            'what time is it and open youtube', ('local', 'Admin'))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.spoken_message, '')
        self.assertIn('It is 10:15.', result.message)

    def test_read_answers_are_not_replaced_by_done(self):
        read = fixtures.step('file_action', {'operation': 'read', 'path': 'C:\\notes.txt', 'destination': '', 'text': ''})
        payload = planned(goal('Search GitHub', self.url('https://github.com/search?q=skills')),
                          goal('Read notes', read, id='g2'))
        result = self.engine(payload, Result('verified', 'Website opened.', spoken_message='Done.'),
                             Result('verified', 'Buy milk')).run('search github and read my notes', ('local', 'Admin'))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.spoken_message, '')
        self.assertIn('Buy milk', result.message)


class ActivationPolicyTests(unittest.TestCase):
    """Only activations with outward or irreversible effects need a spoken confirmation."""
    def task(self, request, name, control):
        from types import SimpleNamespace
        from aidy.ai_automation import DesktopTask
        runtime = SimpleNamespace(engine=Mock(), windows=Mock())
        task = DesktopTask(runtime, request)
        task.views['w1'] = dict(view='v1', elements=[dict(id='e1', name=name, automation_id='', control=control)])
        return task

    def invoke(self):
        return fixtures.step('ui_action', dict(window='w1', element='e1', operation='invoke', text=''))

    def test_search_boxes_chat_rows_links_and_plain_buttons_run_without_confirmation(self):
        for name, control in [('Поиск или новый чат', 50004), ('Search or start new chat', 50004),
                              ('Bacon 🥓 yesterday: send me the photo and delete the old one', 50007),
                              ('How to delete a Facebook account - wikiHow', 50005),
                              ('Mystery', 50000), ('New chat', 50000), ('WhatsApp', 50019)]:
            with self.subTest(name=name):
                self.assertFalse(self.task('open the chat with bacon', name, control).guard(self.invoke()))

    def test_outward_or_irreversible_commands_are_confirmed(self):
        for name, control in [('Block', 50000), ('Voice call', 50000), ('Выйти из группы', 50011),
                              ('Report spam', 50007), ('Sign out', 50005), ('Clear chat', 50011)]:
            with self.subTest(name=name):
                self.assertTrue(self.task('open the chat with bacon', name, control).guard(self.invoke()))

    def test_permission_download_run_and_data_loss_buttons_are_confirmed(self):
        for name in ['Allow', 'Download', 'Run anyway', 'Keep anyway', "Don't save", 'Replace the file in the destination',
                     'Confirm', 'Move to Recycle Bin', 'Cancel subscription', 'Аудиозвонок', 'Разрешить', 'Скачать',
                     'Не сохранять']:
            with self.subTest(name=name):
                self.assertTrue(self.task('open the chat with bacon', name, 50000).guard(self.invoke()))

    def test_nameless_command_and_program_rows_are_confirmed(self):
        self.assertTrue(self.task('open the chat with bacon', '', 50000).guard(self.invoke()))
        self.assertTrue(self.task('open my downloads', 'setup.exe', 50007).guard(self.invoke()))
        self.assertFalse(self.task('open my downloads', 'notes.txt', 50007).guard(self.invoke()))

    def test_short_send_row_still_follows_the_send_rules(self):
        from aidy.ai_tools import AIError
        with self.assertRaises(AIError):
            self.task('open the chat with bacon', 'Send', 50007).guard(self.invoke())
        self.assertTrue(self.task('send hello to bacon', 'Send', 50000).guard(self.invoke()))


class VisionFallbackTests(unittest.TestCase):
    def task(self, request='open the chat with bacon', process='telegram.exe'):
        from types import SimpleNamespace
        from aidy.ai_automation import DesktopTask
        from aidy.ai_runtime import AIRuntime
        window = dict(hwnd=7, pid=3, process=process, title='App')
        runtime = SimpleNamespace(engine=Mock(), windows=SimpleNamespace(targets={'w1': window}, active_ref='w1'),
                                  _confirmation_key=AIRuntime._confirmation_key)
        task = DesktopTask(runtime, request)
        task.input_window = dict(window)
        task.screens['w1'] = [0, 0, 800, 600]
        return task

    def view(self, count, control=50000):
        return dict(view='v', elements=[dict(id='e%d' % i, name='Item %d' % i, automation_id='', control=control) for i in range(count)])

    def test_screenshot_only_when_accessibility_exposes_too_little(self):
        self.assertTrue(self.task().needs_vision('w1', self.view(3)))
        self.assertFalse(self.task().needs_vision('w1', self.view(12)))
        browser = self.task(process='msedge.exe')
        self.assertTrue(browser.needs_vision('w1', self.view(20)))
        self.assertFalse(browser.needs_vision('w1', self.view(80)))

    def click(self, x=500, y=500, target='chat with Bacon'):
        return fixtures.step('click_point', dict(window='w1', x=x, y=y, gesture='click', target=target))

    def hit(self, name, control=50007, label=None):
        return dict(hit=dict(id='h1', name=name, label=label or name, control=control, password=False, x=1, y=1))

    def test_point_click_on_a_chat_row_needs_no_confirmation_and_binds_the_hit(self):
        task = self.task()
        task.call = Mock(return_value=self.hit('Bacon'))
        self.assertFalse(task.point_guard(self.click()))
        self.assertEqual(list(task.focus_bindings.values()), ['h1'])
        self.assertEqual(task.call.call_args.kwargs['rect'], [0, 0, 800, 600])

    def test_point_click_on_send_follows_the_send_rules(self):
        from aidy.ai_tools import AIError
        task = self.task('write hello to bacon but do not send it')
        task.call = Mock(return_value=self.hit('', control=50000, label=' Send '))
        with self.assertRaises(AIError):
            task.point_guard(self.click(target='message field'))
        task = self.task('send hello to bacon')
        task.call = Mock(return_value=self.hit('', control=50000, label=' Send '))
        self.assertTrue(task.point_guard(self.click(target='send button')))

    def test_only_risky_point_clicks_are_confirmed_and_missing_screenshot_is_replanned(self):
        task = self.task()
        task.call = Mock(return_value=self.hit('Mystery', control=50000))
        self.assertFalse(task.point_guard(self.click(target='Mystery button')))
        task.call = Mock(return_value=self.hit('Block', control=50000))
        self.assertTrue(task.point_guard(self.click(target='block button')))
        task.call = Mock(return_value=self.hit('', control=50000, label=''))
        self.assertTrue(task.point_guard(self.click(target='voice call button')))
        task.screens.clear()
        with self.assertRaises(AIPlanError):
            task.point_guard(self.click())

    def test_click_point_coordinates_are_bounded(self):
        with self.assertRaises(AIPlanError):
            Registry([]).plan(dict(clarification='', steps=[self.click(x=1200)]))

    def test_screenshot_is_attached_and_click_point_offered_only_in_vision_mode(self):
        provider = native_fixtures.NativePlanningTests.provider(self, native_fixtures.native('task_complete', {}),
                                                                native_fixtures.native('task_complete', {}))
        registry = Registry(['discord'])
        registry.agent_mode = True
        registry.desktop_state = dict(interface=dict(elements=[], view='v'))
        provider.interpret('open the chat with ajile', [], registry, threading.Event())
        body = self.session.request.call_args.kwargs['json']
        self.assertEqual(len(body['contents'][0]['parts']), 1)
        self.assertNotIn('click_point', [d['name'] for d in body['tools'][0]['functionDeclarations']])
        registry.screen_image = 'aGVsbG8='
        provider.interpret('open the chat with ajile', [], registry, threading.Event())
        body = self.session.request.call_args.kwargs['json']
        self.assertEqual(body['contents'][0]['parts'][1]['inlineData'], {'mimeType': 'image/jpeg', 'data': 'aGVsbG8='})
        self.assertIn('click_point', [d['name'] for d in body['tools'][0]['functionDeclarations']])


class RecipeTests(unittest.TestCase):
    def test_only_the_relevant_app_hint_is_added(self):
        from aidy.ai_recipes import app_hints
        registry = Registry([])
        self.assertEqual(set(app_hints('open discord and open the chat with ajile', registry)), {'discord'})
        self.assertEqual(app_hints('set a timer for five minutes', registry), {})
        registry.windows = [dict(id='w2', process='msedge.exe', title='WhatsApp')]
        registry.desktop_state = dict(selected_window='w2')
        self.assertEqual(set(app_hints('open the chat with bacon', registry)), {'whatsapp', 'browser'})

    def test_hints_reach_the_model_context_only_when_relevant(self):
        import json
        two = lambda app: task(goal('Open ' + app, fixtures.step('open_app', {'app': app})),
                               goal('Open steam', fixtures.step('open_app', {'app': 'steam'}), id='g2'))
        provider = native_fixtures.NativePlanningTests.provider(
            self, native_fixtures.native('plan_task', two('discord')), native_fixtures.native('plan_task', two('steam')))
        provider.interpret('open discord and steam, not the browser', [], Registry(['discord', 'steam']), threading.Event())
        context = json.loads(self.session.request.call_args.kwargs['json']['contents'][0]['parts'][0]['text'])
        self.assertIn('ctrl+k', context['app_hints']['discord'])
        provider.interpret('open steam and then steam again, not in a window', [], Registry(['discord', 'steam']), threading.Event())
        context = json.loads(self.session.request.call_args.kwargs['json']['contents'][0]['parts'][0]['text'])
        self.assertNotIn('app_hints', context)


class AgentModelTests(unittest.TestCase):
    def test_agent_model_is_used_only_for_agent_turns_and_defaults_to_the_main_model(self):
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        from test_ai_layer import HTTPResponse
        session = Mock()
        session.request.side_effect = [HTTPResponse(payload=native_fixtures.native('task_complete', {})) for _ in range(2)]
        provider = GeminiProvider(AIConfig(mode='api', agent_model='gemini-test-pro'), Credentials(['placeholder']), session)
        self.addCleanup(provider.close)
        registry = Registry(['discord'])
        registry.agent_mode = True
        registry.desktop_state = dict(interface=dict(elements=[], view='v'))
        provider.interpret('open the chat with ajile', [], registry, threading.Event())
        self.assertIn('/gemini-test-pro:', session.request.call_args.args[1])
        from dataclasses import replace
        provider.config = replace(provider.config, agent_model='')
        provider.interpret('open the chat with ajile', [], registry, threading.Event())
        self.assertIn('/' + provider.config.model + ':', session.request.call_args.args[1])

    def test_chained_request_plans_with_agent_model_and_single_command_keeps_the_fast_model(self):
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        from test_ai_layer import HTTPResponse
        chained = task(goal('Open Discord', fixtures.step('open_app', {'app': 'discord'})),
                       goal('Open the chat with Ajile', observe(), id='g2', required=['observe_desktop', 'ui_action']))
        single = task(goal('Set volume', fixtures.step('set_volume', {'value': 40})))
        session = Mock()
        session.request.side_effect = [HTTPResponse(payload=native_fixtures.native('plan_task', chained)),
                                       HTTPResponse(payload=native_fixtures.native('plan_task', single))]
        provider = GeminiProvider(AIConfig(mode='api', agent_model='gemini-test-pro'), Credentials(['placeholder']), session)
        self.addCleanup(provider.close)
        provider.interpret('open discord and open the chat with ajile', [], Registry(['discord']), threading.Event())
        self.assertIn('/gemini-test-pro:', session.request.call_args.args[1])
        provider.interpret('set the volume to forty percent', [], Registry(['discord']), threading.Event())
        self.assertIn('/' + provider.config.model + ':', session.request.call_args.args[1])

    def test_invalid_agent_model_is_rejected(self):
        import json, os, tempfile
        from aidy.ai_provider import AIConfig
        from aidy.ai_tools import AIError
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'ai.local.json'), 'w', encoding='utf-8') as f:
                json.dump(dict(mode='api', agent_model='gpt-4o'), f)
            with self.assertRaises(AIError):
                AIConfig.load(folder)


class ObservationLoopTests(CompoundExecutionTests):
    def test_placeholder_observation_is_not_walked_twice(self):
        initial = planned(goal('Open Discord', fixtures.step('open_app', {'app': 'discord'})),
                          goal('Open the chat with Ajile', observe(), id='g2', required=['observe_desktop', 'ui_action']))
        click = fixtures.plan(fixtures.step('ui_action', dict(window='w1', element='e1', operation='invoke', text='')))
        engine = self.engine(initial, click, fixtures.plan(fixtures.step('task_complete', {})))
        def execute(step):
            if step['tool'] == 'observe_desktop':
                engine.registry.desktop_state = dict(selected_window='w1', interface=dict(elements=[], view='v'))
            return Result('verified', 'Done.')
        self.execute.side_effect = execute
        self.assertEqual(engine.run('open discord and open the chat with ajile', ('local', 'Admin')).status, 'verified')
        # The launch goal completes on app_open; only the click is followed by a refresh.
        self.assertEqual([c.args[0] for c in engine.refresh_agent.call_args_list], ['w1'])


if __name__ == '__main__':
    unittest.main()


class LoggedCompoundFailureTests(unittest.TestCase):
    """Failures from the 2026-11-24 log and its real-API replay."""

    def test_open_url_to_app_website_satisfies_app_open(self):
        from aidy.ai_tasks import criterion_satisfied
        criterion = {'kind': 'app_open', 'target': 'whatsapp_web'}
        row = lambda url: [{'step': {'tool': 'open_url', 'arguments': {'url': url}}, 'status': 'verified'}]
        hosts = {'whatsapp_web': 'web.whatsapp.com', 'youtube': 'youtube.com'}
        self.assertTrue(criterion_satisfied(criterion, row('https://web.whatsapp.com'), hosts))
        self.assertTrue(criterion_satisfied({'kind': 'app_open', 'target': 'youtube'},
                                            row('https://www.youtube.com/results?search_query=x'), hosts))
        self.assertFalse(criterion_satisfied(criterion, row('https://example.com'), hosts))
        self.assertFalse(criterion_satisfied(criterion, row('https://web.whatsapp.com')))

    def test_truncated_empty_string_argument_is_repaired(self):
        # Replay: the model cut {"window":""} to {"window": and the whole plan was rejected.
        from aidy.ai_tasks import repair_arguments, unpack_task
        self.assertEqual(repair_arguments('{"window":'), {'window': ''})
        self.assertEqual(repair_arguments('{"url":"https://x.com"'), {'url': 'https://x.com'})
        with self.assertRaises(ValueError):
            repair_arguments('{"a": [1,')
        with self.assertRaises(ValueError):
            repair_arguments('"text"')
        plan = unpack_task({'continuation': 'new_task', 'clarification': '', 'goals': [{
            'id': 'g1', 'description': 'Find the chat', 'requires_followup': True,
            'required_tools': ['observe_desktop'], 'criteria': [{'kind': 'tool_verified', 'target': 'observe_desktop', 'source_goal': ''}],
            'steps': [{'tool': 'observe_desktop', 'arguments_json': '{"window":', 'delay_seconds': 0}]}]},
            {'observe_desktop'})
        self.assertEqual(plan['steps'][0]['arguments'], {'window': ''})
        with self.assertRaises(ValueError):
            repair_arguments('{"window":"a","window":')

    def test_web_search_with_url_becomes_open_url(self):
        # Replay: the model put a GitHub search URL into web_search.query, which
        # would have searched Google for the URL text.
        url = 'https://github.com/search?q=oscar+hartman'
        step = lambda tool, **args: {'tool': tool, 'arguments': args, 'delay_seconds': 0}
        plan = Registry([]).plan({'clarification': '', 'steps': [step('web_search', query=' ' + url + ' ')]})
        self.assertEqual(plan['steps'], [step('open_url', url=url)])
        plan = Registry([]).plan({'clarification': '', 'steps': [step('web_search', query='oscar hartman')]})
        self.assertEqual(plan['steps'], [step('web_search', query='oscar hartman')])

    def test_web_search_url_rewrite_keeps_its_goal_completable(self):
        from aidy.ai_tasks import criterion_satisfied
        url = 'https://www.youtube.com/results?search_query=oscar+hartman'
        plan = Registry([]).plan({'clarification': '', 'continuation': 'new_task', 'steps': [
            {'tool': 'web_search', 'arguments': {'query': url}, 'delay_seconds': 0}], 'goals': [{
                'id': 'g1', 'description': 'Search YouTube', 'requires_followup': False, 'required_tools': ['web_search'],
                'criteria': [{'kind': 'tool_verified', 'target': 'web_search', 'source_goal': ''}],
                'step_start': 0, 'step_count': 1}]})
        goal = plan['goals'][0]
        self.assertEqual(goal['required_tools'], ['open_url'])
        done = [{'step': plan['steps'][0], 'status': 'verified'}]
        self.assertTrue(all(criterion_satisfied(c, done) for c in goal['criteria']))

    def test_scheduled_web_search_with_url_is_not_rewritten(self):
        # open_url cannot be scheduled; a delayed search keeps its old behaviour.
        url = 'https://github.com/search?q=cats'
        plan = Registry([]).plan({'clarification': '', 'steps': [
            {'tool': 'web_search', 'arguments': {'query': url}, 'delay_seconds': 600}]})
        self.assertEqual(plan['steps'][0]['tool'], 'web_search')
