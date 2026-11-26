"""Keyboard and text by voice for users who cannot use their hands (group 2)."""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aidy.ai_keys import key_plan, normalize_keys


def only(plan):
    assert plan is not None and len(plan['steps']) == 1, plan
    step = plan['steps'][0]
    return step['tool'], step['arguments']


class KeyGrammarTests(unittest.TestCase):
    def test_normalize_keys(self):
        self.assertEqual(normalize_keys('control shift t'), 'ctrl+shift+t')
        self.assertEqual(normalize_keys('ctrl+shift+t'), 'ctrl+shift+t')
        self.assertEqual(normalize_keys('windows d'), 'win+d')
        self.assertEqual(normalize_keys('page down'), 'pagedown')
        self.assertEqual(normalize_keys('down arrow'), 'down')
        self.assertEqual(normalize_keys('f5'), 'f5')
        for spoken in ['windows r', 'alt f4', 'control alt delete', 'banana', '', 'control', 'shift shift']:
            with self.subTest(spoken=spoken):
                self.assertIsNone(normalize_keys(spoken))

    def test_press_phrases(self):
        self.assertEqual(only(key_plan('Press Enter.')), ('press_keys', {'keys': 'enter', 'times': 1}))
        self.assertEqual(only(key_plan('press down arrow 5 times')), ('press_keys', {'keys': 'down', 'times': 5}))
        self.assertEqual(only(key_plan('hit tab twice')), ('press_keys', {'keys': 'tab', 'times': 2}))
        self.assertEqual(only(key_plan('tap escape')), ('press_keys', {'keys': 'escape', 'times': 1}))
        self.assertEqual(only(key_plan('press the enter key')), ('press_keys', {'keys': 'enter', 'times': 1}))
        self.assertEqual(only(key_plan('press control s')), ('press_keys', {'keys': 'ctrl+s', 'times': 1}))
        self.assertEqual(only(key_plan('press backspace three times')), ('press_keys', {'keys': 'backspace', 'times': 3}))
        # Refused aloud by the runtime (review 2026-11-24), not sent to Google.
        self.assertEqual(only(key_plan('press tab 25 times')), ('press_keys', {'keys': 'tab', 'times': 25}))
        self.assertEqual(only(key_plan('press alt f4')), ('press_keys', {'keys': 'alt+f4', 'times': 1}))
        self.assertIsNone(key_plan('press tab 100 times'))

    def test_named_shortcuts(self):
        for text, keys in [('copy', 'ctrl+c'), ('Paste.', 'ctrl+v'), ('cut', 'ctrl+x'), ('select all', 'ctrl+a'),
                           ('redo', 'ctrl+y'), ('save', 'ctrl+s'), ('undo typing', 'ctrl+z'),
                           ('new line', 'enter'), ('delete last word', 'ctrl+backspace'),
                           ('select last word', 'ctrl+shift+left'), ('backspace', 'backspace'), ('delete', 'delete')]:
            with self.subTest(text=text):
                self.assertEqual(only(key_plan(text)), ('press_keys', {'keys': keys, 'times': 1}))
        self.assertEqual(only(key_plan('backspace 4 times')), ('press_keys', {'keys': 'backspace', 'times': 4}))

    def test_type_and_dictation(self):
        self.assertEqual(only(key_plan('Type hello world.')), ('type_into_focus', {'text': 'hello world'}))
        self.assertEqual(only(key_plan('type What time is it?')), ('type_into_focus', {'text': 'What time is it?'}))
        self.assertEqual(only(key_plan('start dictation')), ('dictation', {'action': 'start'}))
        self.assertEqual(only(key_plan('Dictation mode.')), ('dictation', {'action': 'start'}))
        self.assertEqual(only(key_plan('Stop dictation.')), ('dictation', {'action': 'stop'}))

    def test_other_phrases_are_not_keys(self):
        for text in ['undo', 'type', 'open steam', 'copy the file to documents', 'save the document as notes.txt',
                     'press', "don't press enter", 'delete the file', 'scroll down', 'mute']:
            with self.subTest(text=text):
                self.assertIsNone(key_plan(text))



class KeyExecutionTests(unittest.TestCase):
    def test_foreground_guard(self):
        from aidy.ai_keys import foreground_guard
        from aidy.ai_tools import AIError
        for info in [None, {'process': 'cmd.exe'}, {'process': 'WindowsTerminal.exe'}, {'process': 'WpfApp1.exe'}]:
            with self.subTest(info=info), self.assertRaises(AIError):
                foreground_guard(info)
        foreground_guard({'process': 'chrome.exe', 'title': 'YouTube'})

    def test_press_sends_the_chord_times(self):
        from unittest.mock import patch
        from aidy import ai_keys
        with patch('pyautogui.hotkey') as hotkey:
            ai_keys.press('ctrl+shift+t', 2)
        self.assertEqual([c.args for c in hotkey.call_args_list], [('ctrl', 'shift', 't')] * 2)

    def test_type_refuses_password_edit(self):
        from unittest.mock import patch
        from aidy import ai_keys
        from aidy.ai_tools import AIError
        with patch.object(ai_keys, '_focused_is_password', return_value=True),                 patch.object(ai_keys, 'send_unicode') as send, self.assertRaises(AIError):
            ai_keys.type_text('secret')
        send.assert_not_called()
        with patch.object(ai_keys, '_focused_is_password', return_value=False),                 patch.object(ai_keys, 'send_unicode') as send:
            ai_keys.type_text('hello')
        send.assert_called_once_with('hello')



class KeyRoutingTests(unittest.TestCase):
    def test_key_plans_are_local_and_valid(self):
        from aidy.ai_local_commands import local_plan
        from aidy.ai_tools import Registry
        r = Registry(['steam'])
        for text in ['press enter', 'copy', 'type hello', 'start dictation', 'press control shift t 2 times']:
            with self.subTest(text=text):
                plan = local_plan(text, r)
                self.assertEqual(plan, key_plan(text))
                r.plan(plan)
        self.assertEqual(local_plan('open steam', r)['steps'][0]['tool'], 'open_app')

    def test_google_never_sees_local_key_tools(self):
        from aidy.ai_provider import AIConfig, GeminiProvider, Credentials
        from aidy.ai_tools import Registry
        from unittest.mock import Mock
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), Mock())
        self.addCleanup(provider.close)
        for agent in (False, True):
            r = Registry([])
            r.agent_mode = agent
            with self.subTest(agent=agent):
                self.assertFalse({'press_keys', 'type_into_focus', 'dictation'} & set(provider.available_tools(r)))


class KeyRuntimeTests(unittest.TestCase):
    def setUp(self):
        import os
        from unittest.mock import Mock, patch
        import test_app_routing
        from aidy.ai_provider import AIConfig
        env = patch.dict(os.environ, {'AIDY_INTERPRETATION_MODE': 'original'})
        env.start()
        self.addCleanup(env.stop)
        fixture = test_app_routing.AppRoutingTests()
        self.host = fixture.make_assistant()
        self.addCleanup(fixture.doCleanups)
        self.runtime = self.host._ai
        self.runtime.config = AIConfig(mode='api')
        self.runtime.reload = Mock()
        self.runtime.poll_cancel_audio = Mock()
        self.runtime.windows.snapshot = Mock(return_value=[])
        self.runtime.catalog.refresh = Mock()
        self.host.voice_id_enabled = False
        self.host.voice = Mock(muted=False)
        self.host._deafen_after_speak = Mock()
        # Offline provider: a test must never reach the real Google API.
        from aidy.ai_engine import AIEngine
        from aidy.ai_provider import Credentials, GeminiProvider
        from aidy.ai_tools import Registry
        self.session = Mock()
        self.session.request.side_effect = OSError('offline')
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), self.session)
        self.runtime.engine = AIEngine(provider, Registry([a['id'] for a in self.host.apps]),
                                       self.runtime.authorize, self.runtime.confirm, self.runtime.execute)

    def front(self, process):
        from unittest.mock import patch
        return patch('aidy.system.get_active_window_info', return_value={'process': process, 'title': 'x', 'pid': 1})

    def test_press_enter_acts_on_front_app_silently(self):
        from unittest.mock import patch
        with self.front('chrome.exe'), patch('aidy.ai_keys.press') as press:
            self.assertTrue(self.runtime.run('press enter'))
        press.assert_called_once_with('enter', 1)
        self.host.voice.tts_blocking.assert_not_called()
        self.host.voice.play_or_tts.assert_not_called()

    def test_typing_goes_to_front_app(self):
        from unittest.mock import patch
        with self.front('notepad.exe'), patch('aidy.ai_keys.type_text') as typed:
            self.assertTrue(self.runtime.run('type hello world'))
        typed.assert_called_once_with('hello world')

    def test_terminal_in_front_is_refused_and_spoken(self):
        from unittest.mock import patch
        with self.front('cmd.exe'), patch('aidy.ai_keys.press') as press:
            self.assertFalse(self.runtime.run('press enter'))
        press.assert_not_called()
        self.assertTrue(self.host.voice.tts_blocking.called or self.host.voice.play_or_tts.called)


if __name__ == '__main__':
    unittest.main()


class DictationTests(KeyRuntimeTests):
    def test_dictation_step(self):
        from aidy.ai_keys import dictation_step
        step = lambda tool, **a: {'tool': tool, 'arguments': a, 'delay_seconds': 0}
        self.assertEqual(dictation_step('Hello there.'), step('type_into_focus', text='Hello there. '))
        self.assertEqual(dictation_step('new line'), step('press_keys', keys='enter', times=1))
        self.assertEqual(dictation_step('New paragraph.'), step('press_keys', keys='enter', times=2))
        self.assertEqual(dictation_step('undo'), step('press_keys', keys='ctrl+z', times=1))
        self.assertEqual(dictation_step('delete last word'), step('press_keys', keys='ctrl+backspace', times=1))
        self.assertEqual(dictation_step('Stop dictation.'), step('dictation', action='stop'))

    def test_dictation_types_every_utterance_without_google(self):
        from unittest.mock import patch
        with self.front('notepad.exe'), patch('aidy.ai_keys.type_text') as typed, patch('aidy.ai_keys.press') as press:
            self.assertTrue(self.runtime.run('start dictation'))
            self.assertTrue(self.runtime.pending)
            self.host.voice.reset_mock()
            self.assertTrue(self.runtime.run('Open Steam and buy everything.'))
            self.assertTrue(self.runtime.run('new line'))
            self.runtime.end_listening()  # 8 s of silence in the listening loop
            self.assertTrue(self.runtime.pending)
            self.assertTrue(self.runtime.run('stop dictation'))
        typed.assert_called_once_with('Open Steam and buy everything. ')
        press.assert_called_once_with('enter', 1)
        self.assertFalse(self.runtime.pending)
        self.session.request.assert_not_called()
        spoken = [c.args for c in self.host.voice.tts_blocking.call_args_list + self.host.voice.play_or_tts.call_args_list]
        self.assertEqual(len(spoken), 1, spoken)  # only "Dictation off."

    def test_dictation_ends_after_idle_time_wake_or_cancel(self):
        from unittest.mock import patch
        from aidy import ai_runtime
        with self.front('notepad.exe'):
            with patch.object(ai_runtime, 'DICTATION_IDLE_SECONDS', 0.05):
                self.runtime.run('start dictation')
                time.sleep(0.1)
                self.runtime.end_listening()  # expiry is checked between utterances
            self.assertFalse(self.runtime.pending)
            self.runtime.run('start dictation')
            self.host._play_wake_ack = Mock()
            self.runtime.accept_wake()
            self.assertFalse(self.runtime.dictating)
            self.runtime.run('start dictation')
            self.runtime.cancel()
            self.assertFalse(self.runtime.dictating)

    def test_dictation_refuses_terminal_and_stops_on_failure(self):
        from unittest.mock import patch
        with self.front('cmd.exe'):
            self.assertFalse(self.runtime.run('start dictation'))
        self.assertFalse(self.runtime.dictating)
        with self.front('notepad.exe'):
            self.runtime.run('start dictation')
        with self.front('cmd.exe'), patch('aidy.ai_keys.type_text') as typed:
            self.assertFalse(self.runtime.run('hello'))
        typed.assert_not_called()
        self.assertFalse(self.runtime.dictating)

    def test_wake_word_with_a_command_leaves_dictation(self):
        from unittest.mock import patch
        with self.front('notepad.exe'):
            self.runtime.run('start dictation')
        self.runtime.reload = Mock()
        with patch.object(self.runtime, 'run', return_value=True) as run:
            self.host._safe_process_command('Aidy, open Steam.')
        self.assertFalse(self.runtime.dictating)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('aidy', run.call_args.args[0].casefold())


class ReviewFixGrammarTests(unittest.TestCase):
    """Group 2 review, 2026-11-24: real input shapes from production."""

    def test_guard_uses_real_process_names_without_exe(self):
        from aidy.ai_keys import foreground_guard
        from aidy.ai_tools import AIError
        # system.get_active_window_info() strips ".exe" (system.py splitext).
        for process in ['cmd', 'WpfApp1', 'WindowsTerminal', 'powershell', 'pwsh', 'conhost']:
            with self.subTest(process=process), self.assertRaises(AIError):
                foreground_guard({'process': process, 'title': 'x'})
        foreground_guard({'process': 'chrome', 'title': 'x'})

    def test_blocked_chords_cannot_be_reached_with_extra_modifiers(self):
        for spoken in ['shift alt f4', 'win shift r', 'control windows r', 'windows shift x',
                       'control shift escape', 'control shift esc', 'shift control escape']:
            with self.subTest(spoken=spoken):
                self.assertIsNone(normalize_keys(spoken))
        self.assertEqual(normalize_keys('control shift t'), 'ctrl+shift+t')

    def test_blocked_or_too_many_presses_are_refused_locally_not_sent_to_google(self):
        self.assertEqual(only(key_plan('press windows r')), ('press_keys', {'keys': 'win+r', 'times': 1}))
        self.assertEqual(only(key_plan('press enter 25 times')), ('press_keys', {'keys': 'enter', 'times': 25}))

    def test_type_leaves_requests_with_a_target_or_a_task_to_google(self):
        for text in ['type hello world in notepad', 'type hello and press enter', 'type hi then send it',
                     'type a letter to my boss', 'type an email to john', 'type my essay about dogs',
                     'type the message for anna']:
            with self.subTest(text=text):
                self.assertIsNone(key_plan(text))
        self.assertEqual(only(key_plan('type hello world')), ('type_into_focus', {'text': 'hello world'}))

    def test_more_stop_phrases(self):
        for text in ['stop the dictation', 'cancel dictation', 'dictation off', 'turn dictation off',
                     'end the dictation']:
            with self.subTest(text=text):
                self.assertEqual(only(key_plan(text)), ('dictation', {'action': 'stop'}))

    def test_press_survives_cursor_in_a_screen_corner(self):
        from aidy import ai_keys
        import pyautogui
        seen = []
        with patch('pyautogui.hotkey', side_effect=lambda *a, **k: seen.append(pyautogui.FAILSAFE)):
            ai_keys.press('enter', 1)
        self.assertEqual(seen, [False])
        self.assertTrue(pyautogui.FAILSAFE)


class ReviewFixRuntimeTests(KeyRuntimeTests):
    """Group 2 review, 2026-11-24: runtime behaviour on production input shapes."""

    def spoken(self):
        return [c.args[-1] for c in self.host.voice.tts_blocking.call_args_list + self.host.voice.play_or_tts.call_args_list]

    def raw(self, text):
        from types import SimpleNamespace
        self.host.speech = SimpleNamespace(last_raw_text=text)

    def test_real_terminal_name_is_refused(self):
        with self.front('cmd'), patch('aidy.ai_keys.press') as press:
            self.assertFalse(self.runtime.run('press enter'))
        press.assert_not_called()

    def test_typing_keeps_whispers_case_and_punctuation(self):
        self.raw('Type Hello, World.')
        with self.front('notepad'), patch('aidy.ai_keys.type_text') as typed:
            self.assertTrue(self.runtime.run('type hello world'))
        typed.assert_called_once_with('Hello, World')

    def test_dictation_keeps_punctuation_and_strips_a_wake_prefix_from_raw(self):
        with self.front('notepad'), patch('aidy.ai_keys.type_text') as typed:
            self.runtime.run('start dictation')
            self.raw('Hello, how are you? I am fine.')
            self.runtime.run('hello how are you i am fine')
            self.raw('Something unrelated.')  # stale raw text is never used
            self.runtime.run('see you soon')
        self.assertEqual([c.args[0] for c in typed.call_args_list], ['Hello, how are you? I am fine. ', 'see you soon '])

    def test_greeting_while_dictating_is_typed_not_a_command(self):
        with self.front('notepad'):
            self.runtime.run('start dictation')
        with patch.object(self.runtime, 'run', return_value=True) as run:
            self.host._safe_process_command('hey john are you coming tonight')
            self.host._safe_process_command('no')
        self.assertTrue(self.runtime.dictating)
        self.assertEqual([c.args[0] for c in run.call_args_list], ['hey john are you coming tonight', 'no'])

    def test_idle_expiry_happens_between_utterances_and_is_spoken(self):
        from aidy import ai_runtime
        import time as clock
        with self.front('notepad'), patch.object(ai_runtime, 'DICTATION_IDLE_SECONDS', 0.05):
            self.runtime.run('start dictation')
            clock.sleep(0.1)
            self.assertTrue(self.runtime.dictating)  # speech captured now is still dictation
            self.host.voice.reset_mock()
            self.runtime.end_listening()  # the loop heard 8 s of silence
        self.assertFalse(self.runtime.dictating)
        self.assertIn('Dictation off.', self.spoken())

    def test_unexpected_error_never_stops_aidy(self):
        with self.front('notepad'), patch('aidy.ai_keys.press', side_effect=RuntimeError('boom')):
            self.assertFalse(self.runtime.run('press enter'))
            self.runtime.run('start dictation')
            self.assertFalse(self.runtime.run('new line'))
        self.assertFalse(self.runtime.dictating)

    def test_blocked_and_too_many_are_refused_aloud(self):
        with self.front('notepad'), patch('aidy.ai_keys.press') as press:
            self.assertFalse(self.runtime.run('press windows r'))
            self.assertFalse(self.runtime.run('press enter 25 times'))
        press.assert_not_called()
        spoken = ' '.join(self.spoken())
        self.assertIn('blocked', spoken)
        self.assertIn('20', spoken)
        self.session.request.assert_not_called()

    def test_key_presses_do_not_replace_the_undo_chain(self):
        import time as clock
        engine = self.runtime.engine
        engine.identity, engine.context_until = ('local', 'Admin'), clock.monotonic() + 300  # same session
        engine.undo_chain = [{'tool': 'open_app', 'arguments': {'app': 'steam'}, 'delay_seconds': 0}]
        with self.front('notepad'), patch('aidy.ai_keys.press'), patch('aidy.ai_keys.type_text'):
            self.runtime.run('press enter')
            self.runtime.run('type hello')
        self.assertEqual(engine.undo_chain[0]['arguments'], {'app': 'steam'})

    def test_key_command_during_a_pending_question_is_a_new_command(self):
        import time as clock
        engine = self.runtime.engine
        engine.identity = ('local', 'Admin')  # the same speaker answers
        engine.pending_until = clock.monotonic() + 20
        engine.pending_request_text = 'open whatsapp and message anna'
        with self.front('notepad'), patch('aidy.ai_keys.press') as press:
            self.assertTrue(self.runtime.run('press escape'))
        press.assert_called_once_with('escape', 1)
        self.session.request.assert_not_called()

    def test_dictation_belongs_to_the_speaker_who_started_it(self):
        speaker = ['u1']
        with self.front('notepad'), patch.object(self.runtime, 'identity', side_effect=lambda: (speaker[0], 'User')), \
                patch.object(self.runtime, 'authorize', return_value=True), \
                patch.object(self.runtime.engine, 'authorize', return_value=True), \
                patch('aidy.ai_keys.type_text') as typed:
            self.runtime.run('start dictation')
            self.runtime.run('first words')
            speaker[0] = 'u2'
            self.assertFalse(self.runtime.run('someone else'))
        self.assertEqual([c.args[0] for c in typed.call_args_list], ['first words '])
        self.assertFalse(self.runtime.dictating)
