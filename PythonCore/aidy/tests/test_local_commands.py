"""Simple whole-phrase commands run locally; everything else still reaches Google."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aidy.ai_local_commands import local_plan
from aidy.ai_tools import Registry


def registry(apps=('discord', 'steam', 'whatsapp_web', 'chrome'), aliases=None):
    r = Registry(list(apps), {'browser': 'chrome'} if aliases is None else aliases)
    r.app_names = {'whatsapp_web': ['whatsapp', 'whats app']}
    return r


def step(tool, **arguments):
    return {'tool': tool, 'arguments': arguments, 'delay_seconds': 0}


class LocalPlanTests(unittest.TestCase):
    def test_single_explicit_app_is_local(self):
        r = registry()
        self.assertEqual(local_plan('Open Steam.', r)['steps'], [step('open_app', app='steam')])
        self.assertEqual(local_plan('close discord', r)['steps'], [step('close_app', app='discord')])
        self.assertEqual(local_plan('open the browser', r)['steps'], [step('open_app', app='chrome')])

    def test_chain_is_local_with_goals(self):
        plan = local_plan('open whatsapp and then discord', registry())
        self.assertEqual([s['arguments']['app'] for s in plan['steps']], ['whatsapp_web', 'discord'])
        self.assertEqual([g['id'] for g in plan['goals']], ['g1', 'g2'])

    def test_fixed_commands(self):
        r = registry()
        self.assertEqual(local_plan('scroll down', r)['steps'], [step('mouse_action', action='scroll down')])
        self.assertEqual(local_plan('Screen shot', r)['steps'], [step('system_action', action='screenshot')])
        self.assertEqual(local_plan('increase volume', r)['steps'], [step('volume_change', direction='up', steps=1)])
        self.assertEqual(local_plan('volume down five steps', r)['steps'], [step('volume_change', direction='down', steps=5)])

    def test_everything_else_goes_to_google(self):
        r = registry()
        for text in ["don't open steam", 'open steam and search cats', 'open steam in ten seconds', 'open it',
                     'close everything', 'close yourself', 'open chatgpt', 'open unknown app',
                     'actually open steam instead', 'scroll down please now', 'volume up eleven', 'mute']:
            with self.subTest(text=text):
                self.assertIsNone(local_plan(text, r))
        self.assertIsNone(local_plan('open chatgpt', registry(('steam', 'chatgpt_app'))))

    def test_context_states_disable_local(self):
        for field, value in [('pending_task', {'goals': []}), ('plan_feedback', 'x'), ('agent_mode', True)]:
            r = registry()
            setattr(r, field, value)
            with self.subTest(field=field):
                self.assertIsNone(local_plan('open steam', r))
                self.assertIsNone(local_plan('scroll down', r))

    def test_close_everything_or_all_is_never_local_even_with_such_an_app(self):
        r = registry(('discord', 'steam', 'everything', 'all'))
        for text in ['close everything', 'close all', 'close all and steam', 'open steam and close everything']:
            with self.subTest(text=text):
                self.assertIsNone(local_plan(text, r))

    def test_website_sharing_an_installed_app_name_goes_to_google(self):
        r = registry(('discord', 'whatsapp_web', 'telegram_web'))
        r.app_names['telegram_web'] = ['telegram']
        r.website_ids = frozenset({'whatsapp_web', 'telegram_web'})
        self.assertEqual(local_plan('open whatsapp', r)['steps'], [step('open_app', app='whatsapp_web')])
        r.installed_apps = [{'id': 'installed_1', 'name': 'WhatsApp', 'source': 'lnk'},
                            {'id': 'installed_2', 'name': 'Telegram Desktop', 'source': 'lnk'}]
        for text in ['open whatsapp', 'open telegram', 'open discord and whatsapp']:
            with self.subTest(text=text):
                self.assertIsNone(local_plan(text, r))
        self.assertEqual(local_plan('open discord', r)['steps'], [step('open_app', app='discord')])

    def test_target_not_in_catalog_is_not_local(self):
        # 'browser'/'music' are always generic catalog IDs; an alias to a missing app is not.
        self.assertIsNone(local_plan('open browser', registry(('discord', 'steam'), aliases={'browser': 'firefox'})))

    def test_local_plan_passes_registry_validation(self):
        r = registry()
        for text in ['open steam', 'close discord', 'open whatsapp and then discord', 'scroll up',
                     'take a screenshot', 'volume up', 'turn the volume down by 3']:
            with self.subTest(text=text):
                r.plan(local_plan(text, r))


class EngineLocalCommandTests(unittest.TestCase):
    def engine(self):
        from aidy.ai_engine import AIEngine, Result
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        self.session = Mock()
        self.session.request.side_effect = OSError('offline')
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), self.session)
        self.addCleanup(provider.close)
        self.executed = Mock(return_value=Result('verified', 'Done.'))
        return AIEngine(provider, registry(), lambda *a: True, lambda *a: True, self.executed)

    def test_engine_runs_local_command_without_provider(self):
        engine = self.engine()
        result = engine.run('open steam', ('local', 'Admin'))
        self.assertTrue(result.ok)
        self.assertEqual([c.args[0]['arguments']['app'] for c in self.executed.call_args_list], ['steam'])
        self.session.request.assert_not_called()
        self.assertEqual(engine.timings['local_plan'], 1)

    def test_stale_feedback_from_previous_task_does_not_block_local(self):
        engine = self.engine()
        engine.registry.plan_feedback = 'The current goal is unfinished.'
        self.assertTrue(engine.run('open steam', ('local', 'Admin')).ok)
        self.session.request.assert_not_called()

    def test_engine_volume_without_amount_still_asks(self):
        engine = self.engine()
        result = engine.run('increase volume', ('local', 'Admin'))
        self.assertEqual(result.status, 'clarification')
        self.assertEqual(result.message, 'By how many steps, from one to ten?')
        self.executed.assert_not_called()
        self.session.request.assert_not_called()

    def test_amount_answer_after_local_volume_needs_no_google(self):
        engine = self.engine()
        self.assertEqual(engine.run('increase volume', ('local', 'Admin')).status, 'clarification')
        result = engine.run('five', ('local', 'Admin'))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.executed.call_args.args[0]['arguments'], {'direction': 'up', 'steps': 5})
        self.session.request.assert_not_called()

    def test_engine_non_local_request_calls_provider(self):
        engine = self.engine()
        engine.run("don't open steam", ('local', 'Admin'))
        self.session.request.assert_called()
        self.executed.assert_not_called()


YT = 'https://www.youtube.com/results?search_query='


def web_registry():
    r = registry(('discord', 'steam', 'whatsapp_web', 'chrome', 'youtube'))
    r.app_names['youtube'] = ['youtube', 'you tube']
    r.website_ids = frozenset({'whatsapp_web', 'youtube'})
    return r


class LocalWebCompoundTests(unittest.TestCase):
    """Common web/app chains from the 2026-11-24 log run without Google."""

    def steps(self, text):
        plan = local_plan(text, web_registry())
        return None if plan is None else plan['steps']

    def test_open_site_then_search_then_open_other_site(self):
        self.assertEqual(self.steps('open youtube and search for oscar hartman and then open github'),
                         [step('open_url', url=YT + 'oscar+hartman'), step('open_url', url='https://github.com')])
        self.assertEqual(self.steps('Open YouTube, then search for Oscar Hartman.'),
                         [step('open_url', url=YT + 'Oscar+Hartman')])

    def test_search_on_named_sites(self):
        self.assertEqual(self.steps('search for oscar hartman on youtube and then search for claude skills on github'),
                         [step('open_url', url=YT + 'oscar+hartman'),
                          step('open_url', url='https://github.com/search?q=claude+skills')])
        self.assertEqual(self.steps('search wikipedia for world war one'),
                         [step('open_url', url='https://en.wikipedia.org/w/index.php?search=world+war+one')])

    def test_plain_search_and_site_open(self):
        self.assertEqual(self.steps('search for oscar hartman'), [step('web_search', query='oscar hartman')])
        self.assertEqual(self.steps('google best way to use claude code'),
                         [step('web_search', query='best way to use claude code')])
        self.assertEqual(self.steps('go to wikipedia'), [step('open_url', url='https://en.wikipedia.org')])
        self.assertEqual(self.steps('search for cats and then search for dogs'),
                         [step('web_search', query='cats'), step('web_search', query='dogs')])

    def test_app_then_site_search(self):
        self.assertEqual(self.steps('open steam and search for oscar hartman on youtube'),
                         [step('open_app', app='steam'), step('open_url', url=YT + 'oscar+hartman')])
        self.assertEqual(self.steps('open youtube and then open discord'),
                         [step('open_app', app='youtube'), step('open_app', app='discord')])

    def test_ambiguous_or_in_app_work_goes_to_google(self):
        for text in ['open steam and search cats', 'open steam and search for cats', 'search for salt and pepper',
                     'search for bacon in whatsapp', 'open the hub', 'open youtube and then open the chat with bacon',
                     'open whatsapp and search for bacon', 'search for', "don't search for cats",
                     'search for cats in ten minutes', 'open github and close it']:
            with self.subTest(text=text):
                self.assertIsNone(self.steps(text))

    def test_search_inside_an_app_or_place_goes_to_google(self):
        # Review 2026-11-24: "search whatsapp for bacon" became a web search.
        for text in ['search whatsapp for bacon', 'search spotify for jazz', 'search my documents for invoice',
                     'search this page for price', 'search the recycle bin for photo', 'search for my invoice']:
            with self.subTest(text=text):
                self.assertIsNone(self.steps(text))
        self.assertEqual(self.steps('search for best tools for python'),
                         [step('web_search', query='best tools for python')])

    def test_sentence_punctuation_separates_commands(self):
        self.assertEqual(self.steps('Search for cats. Open Discord.'),
                         [step('web_search', query='cats'), step('open_app', app='discord')])
        self.assertEqual(self.steps('search youtube for cats. open discord'),
                         [step('open_url', url=YT + 'cats'), step('open_app', app='discord')])

    def test_bare_search_follows_the_app_in_front(self):
        for active, expected in [('whatsapp_web', None), ('discord', None), ('steam', None),
                                 ('chrome', [step('web_search', query='bacon')]),
                                 (None, [step('web_search', query='bacon')]),
                                 ('aidy', [step('web_search', query='bacon')]),
                                 ('youtube', [step('open_url', url=YT + 'bacon')])]:
            r = web_registry()
            r.active_app = active
            with self.subTest(active=active):
                plan = local_plan('search for bacon', r)
                self.assertEqual(None if plan is None else plan['steps'], expected)

    def test_google_verb_needs_a_real_query(self):
        for text in ['google chrome', 'google docs', 'google classroom']:
            with self.subTest(text=text):
                self.assertIsNone(self.steps(text))

    def test_time_words_go_to_google(self):
        for text in ['search for cats after ten minutes', 'search for cats later', 'search for cats tomorrow',
                     'search for news tonight', 'search for cats in five minutes']:
            with self.subTest(text=text):
                self.assertIsNone(self.steps(text))

    def test_installed_app_named_like_a_site_goes_to_google(self):
        r = web_registry()
        r.installed_apps = [{'id': 'installed_1', 'name': 'GitHub Desktop', 'source': 'lnk'}]
        self.assertIsNone(local_plan('open github', r))
        self.assertEqual(local_plan('search for cats on github', r)['steps'],
                         [step('open_url', url='https://github.com/search?q=cats')])

    def test_web_plans_pass_registry_validation(self):
        r = web_registry()
        for text in ['open youtube and search for oscar hartman and then open github', 'search for oscar hartman',
                     'search for oscar hartman on youtube and then search for claude skills on github',
                     'open steam and search for oscar hartman on youtube', 'go to wikipedia']:
            with self.subTest(text=text):
                r.plan(local_plan(text, r))

    def test_engine_runs_web_chain_without_google(self):
        from aidy.ai_engine import AIEngine, Result
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        session = Mock()
        session.request.side_effect = OSError('offline')
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), session)
        self.addCleanup(provider.close)
        executed = Mock(return_value=Result('verified', 'Done.'))
        engine = AIEngine(provider, web_registry(), lambda *a: True, lambda *a: True, executed)
        engine.start_agent = lambda text: None  # set by AIRuntime in the app
        engine.refresh_agent = lambda ref: None
        result = engine.run('open youtube and search for cats and then open github', ('local', 'Admin'))
        self.assertTrue(result.ok, result.message)
        self.assertEqual([c.args[0]['arguments']['url'] for c in executed.call_args_list],
                         [YT + 'cats', 'https://github.com'])
        session.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
