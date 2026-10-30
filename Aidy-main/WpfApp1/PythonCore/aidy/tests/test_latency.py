"""Latency fixes must preserve utterances, recordings and confidence checks."""
from pathlib import Path
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from test_audio_pipeline import bare_aidy, speech_collector, FakeStream, VOICE_FRAME, SILENT_FRAME
from aidy import assistant as core
from aidy.audio_capture import BufferedInput
from aidy import voice
from aidy import system
from aidy.speech import SpeechRecognizer, _canonical_short_repeat


class LatencyTests(unittest.TestCase):
    def test_confirmation_uses_clean_clip_and_keeps_question_intonation(self):
        aidy = bare_aidy()
        aidy.voice.muted = False
        self.assertTrue(aidy._play_confirm_prompt_clip())
        aidy.voice.play_key.assert_called_once_with('are_you_sure_clean')
        self.assertEqual(voice.Voice._prepare_tts_text(None, 'Are you sure?'), 'Are you sure?')
        aidy.voice.play_key.return_value = False
        aidy._speak_confirm_prompt()
        aidy.voice.tts_blocking.assert_called_once_with('Are you sure?')

    def test_window_switch_capture_uses_navigation_context(self):
        aidy = bare_aidy()
        aidy.window_switch_active = True
        aidy._capture_utterance = Mock(return_value=np.ones(8000, np.float32))
        aidy.speech.transcribe.return_value = 'right'
        self.assertEqual(aidy.listen_command(speak_on_empty=False), 'right')
        self.assertEqual(aidy.speech.transcribe.call_args.kwargs['purpose'], 'navigation')

    def test_click_before_command_does_not_end_listening_window(self):
        aidy = bare_aidy()
        aidy.stream = FakeStream([VOICE_FRAME] * 4 + [SILENT_FRAME] * 20
                                 + [VOICE_FRAME] * 15 + [SILENT_FRAME] * 20)
        aidy.start_stream = Mock()
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            audio = aidy._capture_utterance(wait_seconds=.1)
        self.assertIsNotNone(audio)
        self.assertEqual(np.count_nonzero(audio), 15 * 320)

    def test_paused_app_name_is_kept_during_command_inference(self):
        aidy = bare_aidy()
        stream = BufferedInput(Mock())
        self.addCleanup(stream.close)
        aidy.stream = stream
        aidy.start_stream = Mock()
        def enqueue():
            for frame in [VOICE_FRAME] * 15 + [SILENT_FRAME] * 20:
                stream._callback(frame, 320, {}, 0)
        enqueue()
        def transcribe(audio, purpose):
            if aidy.speech.transcribe.call_count == 1:
                self.assertFalse(stream._closed)
                enqueue()
                return 'open'
            return 'steam'
        aidy.speech.transcribe.side_effect = transcribe
        with patch.object(core, 'UtteranceBuffer', side_effect=speech_collector):
            self.assertEqual(aidy.listen_command(speak_on_empty=False), 'open steam')
        self.assertTrue(stream._closed)
        aidy.process_command.assert_not_called()

    def test_short_window_repetition_and_token_limit_require_verification(self):
        meta = SimpleNamespace(duration_after_vad=1)
        def segment(text, tokens=()):
            return SimpleNamespace(text=text, tokens=tokens, avg_logprob=-.2,
                                   no_speech_prob=.01, compression_ratio=1)
        for text in ('five five', 'unknown app unknown app', 'yes yes yes'):
            self.assertTrue(SpeechRecognizer._needs_full_context([segment(text)], meta, 'command'))
        self.assertTrue(SpeechRecognizer._needs_full_context([segment('open steam', list(range(64)))], meta, 'command'))
        self.assertFalse(SpeechRecognizer._needs_full_context([segment("don't open steam")], meta, 'command'))
        self.assertEqual(_canonical_short_repeat('hey hey', 'wake'), 'hey')
        self.assertEqual(_canonical_short_repeat('open steam open steam', 'command'), 'open steam')
        self.assertEqual(_canonical_short_repeat("don't open steam don't open steam", 'command'), "don't open steam")
        self.assertEqual(_canonical_short_repeat('five five', 'command'), 'five five')
        self.assertEqual(_canonical_short_repeat('done done', 'navigation'), 'done')
        self.assertEqual(_canonical_short_repeat('cancel cancel', 'navigation'), 'cancel')
        for text in ('left left', 'right right', 'five five', 'yes yes'):
            self.assertEqual(_canonical_short_repeat(text, 'navigation'), text)
        self.assertEqual(_canonical_short_repeat('done done', 'command'), 'done done')

    def test_recording_keeps_quiet_ending_and_inner_pause_but_skips_silent_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reply.wav'
            rate = 16000
            tone = (2000 * np.sin(np.arange(3200) * .2)).astype(np.int16)
            samples = np.concatenate([np.zeros(1600, np.int16), tone,
                                      np.zeros(3200, np.int16), tone // 10,
                                      np.zeros(10400, np.int16)])
            with wave.open(str(path), 'wb') as output:
                output.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                output.writeframes(samples.tobytes())
            original = path.read_bytes()
            stat = path.stat()
            start, end = voice._wav_speech_bounds(str(path), stat.st_mtime_ns, stat.st_size)
            self.assertEqual(start, 70)
            self.assertEqual(end, 760)
            with patch.object(voice, 'mci', return_value=0) as mci, \
                 patch.object(voice.winsound, 'PlaySound') as playback:
                self.assertTrue(voice.play_audio(str(path), wait=True))
            mci.assert_not_called()
            with wave.open(voice.io.BytesIO(playback.call_args.args[0]), 'rb') as clipped:
                self.assertEqual(clipped.getnframes(), int(.69 * rate))
            self.assertEqual(path.read_bytes(), original)
            with patch.object(voice.winsound, 'PlaySound', side_effect=RuntimeError('device')), \
                 patch.object(voice, 'mci', return_value=0) as mci:
                self.assertTrue(voice.play_audio(str(path), wait=True))
            self.assertIn('play aidyvoice from 70 to 760 wait', [c.args[0] for c in mci.call_args_list])

    def test_playback_completion_clears_speaking_state(self):
        aidy = bare_aidy()
        aidy._playback_active.set()
        with patch.object(core, 'ui_state') as state:
            aidy._after_playback()
        state.assert_called_once_with('IDLE')
        self.assertFalse(aidy._playback_active.is_set())

    def test_volume_action_precedes_its_reply(self):
        aidy = bare_aidy()
        aidy.last_step_actions = Mock()
        aidy.last_step_actions.get_if_fresh.return_value = None
        events = []
        aidy._apply_volume_steps = Mock(side_effect=lambda **kw: events.append('action') or True)
        aidy.voice.play_or_tts.side_effect = lambda *args: events.append('reply')
        self.assertTrue(aidy._execute_intent('volume_change', {'direction': 'UP', 'steps': 2}))
        self.assertEqual(events, ['action', 'reply'])

    def test_volume_keys_have_no_per_key_pause(self):
        with patch.object(system.pyautogui, 'press') as press:
            system.volume_steps(True, 5)
        press.assert_called_once_with('volumeup', presses=10, interval=.01, _pause=False)

    def test_brightness_uses_direct_wmi_with_zero_transition_and_checks_result(self):
        level = SimpleNamespace(InstanceName='display', CurrentBrightness=50)
        monitor = Mock(InstanceName='display')
        properties = {'Timeout': SimpleNamespace(Value=None), 'Brightness': SimpleNamespace(Value=None)}
        parameters = Mock()
        parameters.Properties_.side_effect = properties.__getitem__
        monitor.Methods_.return_value.InParameters.SpawnInstance_.return_value = parameters
        result = Mock()
        code = SimpleNamespace(Value=0)
        result.Properties_.return_value = code
        monitor.ExecMethod_.return_value = result
        service = Mock()
        service.ExecQuery.side_effect = lambda query: [monitor] if 'Methods' in query else [level]
        with patch.object(system.win32com.client, 'Dispatch') as dispatch, \
             patch.object(system.pythoncom, 'CoInitialize'), \
             patch.object(system.pythoncom, 'CoUninitialize'), \
             patch.object(system.subprocess, 'run') as shell:
            dispatch.return_value.ConnectServer.return_value = service
            self.assertTrue(system.brightness_steps(False, 2))
            self.assertEqual(properties['Brightness'].Value, 30)
            self.assertEqual(properties['Timeout'].Value, 0)
            code.Value = 1
            self.assertFalse(system.brightness_steps(True, 10))
            self.assertEqual(properties['Brightness'].Value, 100)
            shell.assert_not_called()


if __name__ == '__main__':
    unittest.main()
