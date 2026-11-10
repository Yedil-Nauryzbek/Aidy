"""Switch dialogue regressions; all keyboard, model and audio IO are mocked."""
import unittest
from unittest.mock import Mock, patch

import numpy as np

from test_audio_pipeline import bare_aidy
from aidy import assistant as core
from aidy.assistant import Aidy


class WindowSwitchTests(unittest.TestCase):
    def run_dialogue(self, replies):
        aidy = bare_aidy()
        aidy.start_stream = Mock()
        aidy._key_down = Mock()
        aidy._key_up = Mock()
        aidy._press = Mock()
        aidy._deafen_after_speak = Mock()
        aidy.last_step_actions = []
        states = []
        captures = []
        utterances = iter(['hey aidy switch', *replies])

        def process(text):
            if text == 'switch' and not aidy.window_switch_active:
                aidy.start_window_switch()
                return True
            if aidy.window_switch_active:
                return Aidy.process_command(aidy, text)
            self.fail(f'Unexpected command executed outside switch dialogue: {text}')

        def capture(**kwargs):
            try:
                text = next(utterances)
            except StopIteration:
                aidy._stop_event.set()
                return None
            captures.append((kwargs['purpose'], states[-1]))
            if text is None:
                return None
            aidy.speech.transcribe.return_value = text
            return np.ones(3200, dtype=np.float32)

        aidy.process_command = Mock(side_effect=process)
        aidy._capture_utterance = Mock(side_effect=capture)
        with patch.object(core, 'SpeechRecognizer', return_value=aidy.speech), \
             patch.object(core, 'ui_state', side_effect=states.append):
            aidy.run()
        return aidy, states, captures

    def test_switch_next_done_releases_alt_and_requires_wake_again(self):
        for ending in ('done', 'down', 'cancel'):
            with self.subTest(ending=ending):
                aidy, states, captures = self.run_dialogue(['next', ending, 'next', 'open browser'])
                self.assertFalse(aidy.window_switch_active)
                aidy._key_up.assert_called_once_with(0x12)
                self.assertEqual(aidy._press.call_count, 2)  # initial Tab + next
                self.assertEqual([c.args[0] for c in aidy.process_command.call_args_list],
                                 ['switch', 'next', ending])
                aidy._play_wake_ack.assert_called_once()
                self.assertEqual(captures, [('wake', 'LISTENING'),
                    ('navigation', 'COMMAND_LISTENING'), ('navigation', 'COMMAND_LISTENING'),
                    ('wake', 'LISTENING'), ('wake', 'LISTENING')])
                self.assertNotIn('IDLE', states[:states.index('EXECUTING') + 2])

    def test_silence_and_unrelated_speech_cannot_keep_switch_alive(self):
        for replies in ([None, None, None], ['thank you', 'hello', 'open browser']):
            with self.subTest(replies=replies):
                aidy, states, captures = self.run_dialogue([*replies, 'next'])
                self.assertFalse(aidy.window_switch_active)
                aidy._key_up.assert_called_once_with(0x12)
                aidy._press.assert_called_once_with(0x09)
                aidy.process_command.assert_called_once_with('switch')
                aidy._play_wake_ack.assert_called_once()
                keys = [c.args[0] for c in aidy.voice.play_or_tts.call_args_list]
                self.assertEqual(keys, ['window_switch_mode', 'window_switch_cancel'])
                # No Idle or reprompt between the three quiet/rejected windows.
                start = states.index('COMMAND_LISTENING')
                end = states.index('SPEAKING', start)
                self.assertNotIn('IDLE', states[start:end])
                self.assertEqual(captures[-1], ('wake', 'LISTENING'))

    def test_navigation_rejects_wake_like_text_without_ack_or_help(self):
        aidy = bare_aidy()
        aidy.window_switch_active = True
        aidy._capture_utterance = Mock(return_value=np.ones(3200, dtype=np.float32))
        aidy.speech.transcribe.return_value = 'hey'
        with patch.object(core, 'ui_state') as state:
            self.assertIsNone(aidy.listen_command())
        self.assertEqual([c.args[0] for c in state.call_args_list], ['COMMAND_LISTENING'])
        aidy.voice.play_or_tts.assert_not_called()
        aidy._play_wake_ack.assert_not_called()


if __name__ == '__main__':
    unittest.main()
