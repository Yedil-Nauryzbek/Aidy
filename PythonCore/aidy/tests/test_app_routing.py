"""App selection regressions; never open or close real applications."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))
from aidy import assistant as core
from aidy import apps as app_module
from aidy.apps import find_app, load_apps_config, normalize_app_command


class AppRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch('aidy.apps.default_browser_app', return_value=None):
            cls.apps = load_apps_config(str(CORE.parent))

    def test_browser_resolves_default_and_yes_closes_that_target_only(self):
        browser = dict(id='browser', aliases=['browser', 'the browser'], type='exe',
                       target=r'C:\Edge\msedge.exe', process='msedge.exe', args=[])
        with patch('aidy.apps.default_browser_app', return_value=browser):
            aidy = self.make_assistant()
        self.assertEqual(find_app(aidy.apps, 'browser')['process'], 'msedge.exe')
        self.assertEqual(find_app(aidy.apps, 'chrome')['process'], 'chrome.exe')
        aidy._confirm_close_request = core.Aidy._confirm_close_request.__get__(aidy)
        aidy._speak_confirm_prompt = Mock()
        aidy._listen_confirm_reply = Mock(return_value='yes')
        with patch.object(core, 'close_app', return_value=True) as close:
            self.assertTrue(aidy._safe_process_command('close browser'))
        close.assert_called_once_with(browser)
        aidy._listen_confirm_reply.return_value = 'no'
        with patch.object(core, 'close_app') as close:
            self.assertFalse(aidy._safe_process_command('close browser'))
        close.assert_not_called()

    def test_resolved_browser_process_is_used_by_the_close_implementation(self):
        with patch.object(app_module.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
             patch.object(app_module.time, 'sleep'):
            self.assertTrue(app_module.close_app({'id': 'browser', 'type': 'exe', 'process': 'msedge.exe'}))
        self.assertTrue(run.called)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][0:3], ['taskkill', '/IM', 'msedge.exe'])

    def test_partial_alias_cannot_pick_an_unrelated_app(self):
        for text in ('closed steam', 'close steam', 's', 'ds team', 'not steam',
                     'steam discord', 'unknown browser', 'discordant', 'steam and discord'):
            with self.subTest(text=text):
                self.assertIsNone(find_app(self.apps, text))

    def test_full_targets_and_spaced_names_still_work(self):
        for text, app_id in [('ds', 'discord'), ('steam', 'steam'),
                             ('visual studio code', 'vscode'), ('chat g p t', 'gpt'),
                             ('yandex music', 'yandex_music'), ('browser', 'chrome')]:
            self.assertEqual(find_app(self.apps, text)['id'], app_id)

    def test_ambiguous_alias_is_not_resolved_by_list_order(self):
        apps = [{'id': 'a', 'aliases': ['shared']}, {'id': 'b', 'aliases': ['shared']}]
        self.assertIsNone(find_app(apps, 'shared'))

    def test_spoken_close_variant_keeps_the_target_and_action(self):
        for text in ('close steam', 'closed steam', 'clothes steam',
                     'please close the steam app', 'could you close steam please'):
            self.assertEqual(normalize_app_command(text, self.apps), 'close steam')
        self.assertEqual(normalize_app_command('open up steam', self.apps), 'open steam')
        for text in ("don't close steam", 'i closed steam', 'closed unknown',
                     'open steam and close discord'):
            self.assertEqual(normalize_app_command(text, self.apps), text)

    def make_assistant(self):
        for name, kwargs in [('PyAudio', {} )]:
            patcher = patch.object(core.pyaudio, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('start_local_intent_api', 'stop_local_intent_api', 'Voice', 'VoiceAuthManager'):
            patcher = patch.object(core, name, return_value=Mock(muted=True) if name == 'Voice' else True)
            patcher.start()
            self.addCleanup(patcher.stop)
        aidy = core.Aidy(str(CORE.parent))
        self.addCleanup(aidy.close)
        aidy._confirm_close_request = Mock(return_value=True)
        aidy._sleep_success = Mock()
        return aidy

    def test_closed_steam_reaches_close_only(self):
        aidy = self.make_assistant()
        with patch.object(core, 'launch_app') as launch, patch.object(core, 'close_app', return_value=True) as close:
            self.assertTrue(aidy._safe_process_command('closed steam'))
            close.assert_called_once()
            self.assertEqual(close.call_args.args[0]['id'], 'steam')
            launch.assert_not_called()
            aidy._confirm_close_request.assert_called_once()

    def test_success_reply_follows_action_and_failure_has_no_success_reply(self):
        aidy = self.make_assistant()
        aidy._sleep_success = core.Aidy._sleep_success.__get__(aidy)
        aidy.voice.muted = False
        events = []
        aidy.voice.play_or_tts.side_effect = lambda key, text: events.append(key)
        with patch.object(core, 'launch_app', side_effect=lambda app: events.append('launched') or True):
            self.assertTrue(aidy._safe_process_command('open steam'))
        self.assertEqual(events, ['launched', 'success'])
        events.clear()
        with patch.object(core, 'launch_app', return_value=False):
            self.assertFalse(aidy._safe_process_command('open steam'))
        self.assertIn('open_app_fail', events)
        self.assertNotIn('success', events)

    def test_negation_unknown_and_multiple_targets_do_not_execute(self):
        aidy = self.make_assistant()
        aidy.api = Mock()
        aidy.api.get_intent.return_value = None
        with patch.object(core, 'launch_app') as launch, patch.object(core, 'close_app') as close:
            for text in ("don't open steam", 'open unknown', 'open steam and discord', 'closed unknown'):
                self.assertFalse(aidy._safe_process_command(text))
            launch.assert_not_called()
            close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
