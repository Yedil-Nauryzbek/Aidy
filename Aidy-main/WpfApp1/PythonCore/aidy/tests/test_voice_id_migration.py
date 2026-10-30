"""Whisper/Voice ID integration: real routing and capture, mocked OS actions."""
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from test_audio_pipeline import bare_aidy, speech_collector, FakeStream, VOICE_FRAME, SILENT_FRAME
import test_app_routing
from aidy import assistant as core, voice, voice_auth, system
from aidy.assistant import Aidy


class VoiceIDMigrationTests(unittest.TestCase):
    def secured(self):
        a = bare_aidy()
        a.voice_id_enabled = True
        a.voice_auth = Mock()
        a.voice_auth.has_admin.return_value = True
        a.voice_auth.extract_embedding.return_value = np.ones(192)
        a.voice_auth.authorize.return_value = (True, 'Admin', None)
        a._last_audio_buffer = b'new command audio'
        a._voice_auth_grant = Mock(return_value=None)
        a._authorization_intent = Mock(return_value='open_app')
        return a

    def test_only_accepted_utterance_becomes_speaker_audio(self):
        a = bare_aidy()
        a._last_audio_buffer = b'previous speaker'
        audio = np.array([-.5, 0, .5, 1], dtype=np.float32)
        a.speech.transcribe.return_value = 'open browser'
        self.assertEqual(a._transcribe_capture(audio), 'open browser')
        self.assertEqual(np.frombuffer(a._last_audio_buffer, '<i2').tolist(), [-16384, 0, 16384, 32767])
        a.speech.transcribe.return_value = None
        self.assertIsNone(a._transcribe_capture(audio))
        self.assertEqual(a._last_audio_buffer, b'')

    def test_interrupted_inference_cannot_leave_previous_identity(self):
        a = bare_aidy()
        a._last_audio_buffer = b'admin'
        a.speech.transcribe.side_effect = lambda *args, **kw: a._stop_event.set() or 'shutdown'
        self.assertIsNone(a._transcribe_capture(np.ones(16000, dtype=np.float32)))
        self.assertEqual(a._last_audio_buffer, b'')

    def test_one_embedding_shared_by_permission_and_grant_checks(self):
        a = self.secured()
        self.assertTrue(a._safe_process_command('open browser'))
        a.voice_auth.extract_embedding.assert_called_once_with(b'new command audio')
        self.assertIs(a.voice_auth.authorize.call_args.kwargs['precomputed_emb'],
                      a._voice_auth_grant.call_args.kwargs['precomputed_emb'])
        a.process_command.assert_called_once_with('open browser')

    def test_denied_speaker_cannot_execute_custom_modes_or_regular_commands(self):
        a = self.secured()
        a.voice_auth.authorize.return_value = (False, 'unknown', 'denied')
        with patch.object(core.time, 'sleep'):
            for command in ('open browser', 'custom mode', 'close everything'):
                self.assertFalse(a._safe_process_command(command))
        a.process_command.assert_not_called()

    def test_missing_audio_fails_closed(self):
        a = self.secured()
        a._last_audio_buffer = b''
        self.assertFalse(a._safe_process_command('open browser'))
        a.process_command.assert_not_called()

    def test_disabled_voice_id_preserves_current_behavior(self):
        a = self.secured()
        a.voice_id_enabled = False
        self.assertTrue(a._safe_process_command('open browser'))
        a.voice_auth.extract_embedding.assert_not_called()

    def test_negation_still_precedes_voice_id_and_execution(self):
        a = self.secured()
        self.assertFalse(a._safe_process_command("don't open browser"))
        a.voice_auth.extract_embedding.assert_not_called()
        a.process_command.assert_not_called()

    def test_enrollment_interrupts_wake_and_ignores_ptt(self):
        a = bare_aidy()
        a._pending_admin_enrollment = True
        self.assertEqual(a.wait_for_wake(), '__CONTROL_BREAK__')
        a.push_to_talk_enabled = True
        a._ptt_listening_flag = False
        a._enrollment_in_progress = True
        self.assertTrue(a._is_listening_allowed())
        a._enrollment_in_progress = False
        self.assertFalse(a._is_listening_allowed())

    def test_enrollment_cancel_interrupts_capture_without_opening_microphone(self):
        a = bare_aidy()
        a._enrollment_in_progress = True
        a._enrollment_cancel_requested = True
        a.start_stream = Mock()
        self.assertIsNone(a._capture_utterance(purpose='enrollment'))
        a.start_stream.assert_not_called()

    def test_ui_controls_preserve_label_colons_and_cancel_state(self):
        a = bare_aidy()
        a._handle_control_command('enroll_user_force:User:Alice: Work')
        self.assertEqual(a._pending_enrollment_role, 'User')
        self.assertEqual(a._pending_enrollment_label, 'Alice: Work')
        self.assertTrue(a._pending_admin_enrollment)
        a._handle_control_command('cancel_enrollment')
        self.assertFalse(a._pending_admin_enrollment)
        self.assertTrue(a._enrollment_cancel_requested)

    def test_enrollment_uses_whisper_capture_and_saves_all_five_segments(self):
        a = bare_aidy()
        a.voice_auth = Mock()
        a.voice_auth.user_count.return_value = 0
        a.voice_auth.enroll.return_value = (True, 'saved')
        a._send_voice_users = Mock()
        a._pending_enrollment_role = 'User'
        a._pending_enrollment_label = 'Alice'
        a._capture_utterance = Mock(return_value=np.full(16000, .2, dtype=np.float32))
        a.speech.transcribe.return_value = 'my spoken enrollment phrase'
        with tempfile.TemporaryDirectory() as folder, patch.object(core.time, 'sleep'), \
             patch.object(voice_auth, '_estimate_snr_db', return_value=20):
            a.base_dir = folder
            a._run_admin_enrollment()
        self.assertEqual(a._capture_utterance.call_count, 5)
        self.assertEqual(a.speech.transcribe.call_count, 5)
        segments = a.voice_auth.enroll.call_args.args[0]
        self.assertEqual(len(segments), 5)
        self.assertTrue(all(len(pcm) == 32000 for pcm in segments))
        self.assertEqual(a.voice_auth.enroll.call_args.kwargs, {'role': 'User', 'label': 'Alice'})
        self.assertFalse(a._enrollment_in_progress)
        self.assertEqual(a._last_audio_buffer, b'')

    def test_custom_slots_use_new_configuration_and_execute_once(self):
        a = bare_aidy()
        a._execute_custom_mode_slot = Mock()
        with tempfile.TemporaryDirectory() as folder:
            a.base_dir = folder
            Path(folder, 'config.json').write_text(json.dumps({'custom_mode': {'slots': [
                {'action_type':'open_app', 'target':'steam', 'display_name':'Steam'},
                {'action_type':'open_url', 'target':'https://example.com', 'display_name':'Site'},
                {'target':''}]}}), encoding='utf-8-sig')
            a._execute_all_custom_mode_slots()
        self.assertEqual(a._execute_custom_mode_slot.call_count, 2)
        a._handle_control_command('execute_slot:open_app|steam|Steam')
        self.assertEqual(a._execute_custom_mode_slot.call_count, 2)

    def test_new_commands_survive_non_action_filter(self):
        a = bare_aidy()
        for command in ('custom mode', 'local mode', 'mouse up', 'click', 'scroll down', 'list users'):
            with self.subTest(command=command):
                self.assertFalse(a._is_non_action_utterance(command))

    def test_mouse_actions_remain_mockable_and_settings_affect_distance(self):
        with patch.object(system, 'MOUSE_MOVE_PX', 123), patch.object(system.pyautogui, 'moveRel') as move:
            self.assertEqual(system.execute_mouse_command('mouse up'), 'mouse_up')
            move.assert_called_once_with(0, -123)

    def test_real_router_preserves_custom_mode_and_mouse_actions(self):
        a = test_app_routing.AppRoutingTests.make_assistant(self)
        a._execute_all_custom_mode_slots = Mock()
        a.api = Mock()
        a.api.get_intent.return_value = None
        with patch.object(core.time, 'sleep'):
            self.assertTrue(a._safe_process_command('custom mode'))
        a._execute_all_custom_mode_slots.assert_called_once()
        with patch.object(core, 'execute_mouse_command', return_value='mouse_up') as mouse:
            self.assertTrue(a._safe_process_command('mouse up'))
            mouse.assert_called_once_with('mouse up')

    def test_gain_changes_pcm_without_rewriting_original_clip(self):
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', ''))
            wav.writeframes(np.array([1000, -2000], dtype='<i2').tobytes())
        original = output.getvalue()
        with wave.open(io.BytesIO(voice._scale_wave(original, 50)), 'rb') as wav:
            self.assertEqual(np.frombuffer(wav.readframes(2), '<i2').tolist(), [500, -1000])
        self.assertEqual(original, output.getvalue())

    def test_normalized_shutdown_and_custom_mode_require_admin(self):
        a = bare_aidy()
        a.api = Mock()
        a.api.get_intent.return_value = None
        self.assertEqual(a._authorization_intent('shut down'), 'shutdown')
        self.assertEqual(a._authorization_intent('custom mode'), 'grant_access')


if __name__ == '__main__':
    unittest.main()
