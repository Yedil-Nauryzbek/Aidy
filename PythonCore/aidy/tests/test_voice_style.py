"""Synthetic speech clarity, consistent clip routing and speech-worker recovery."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from aidy.voice_style import DEFAULT_STYLE, VOICE_PRESETS, load_profile, phrase_file, speech_text, treated_audio, wave_bytes
from aidy import voice


def style(**changes):
    value=dict(DEFAULT_STYLE,keys={},phrases={})
    value.update(changes)
    return value


class VoiceStyleTests(unittest.TestCase):
    def test_numbers_questions_and_sentence_boundaries_are_not_flattened(self):
        text='**Volume:** 18.5 percent. Are you sure?\n[Details](https://example.test).'
        self.assertEqual(speech_text(text),'Volume: 18.5 percent. Are you sure?\nDetails.')
        self.assertEqual(speech_text(r'File C:\new\report.txt is ready.'),r'File C:\new\report.txt is ready.')

    def test_processed_audio_has_headroom_and_retains_duration(self):
        rate=24000
        x=(.4*np.sin(2*np.pi*160*np.arange(rate)/rate)).astype(np.float32)
        y=treated_audio(x,rate,.24)
        self.assertEqual(len(y),len(x))
        self.assertTrue(np.isfinite(y).all())
        self.assertLessEqual(float(np.abs(y).max()),.921)
        self.assertGreater(float(np.sqrt(np.mean((x-y)**2))),.03)
        self.assertAlmostEqual(float(y[0]),0,places=6)
        with wave.open(io.BytesIO(wave_bytes(y,rate)),'rb') as result:
            self.assertEqual((result.getnchannels(),result.getsampwidth(),result.getframerate()),(1,2,rate))
            self.assertEqual(result.getnframes(),rate)

    def test_invalid_audio_is_rejected_before_treatment(self):
        for samples,rate,strength in [(np.array([np.nan]*2000),24000,.2),(np.ones(2000),8000,.2),(np.ones(2000),24000,.9)]:
            with self.assertRaises(ValueError):treated_audio(samples,rate,strength)

    def test_every_preset_is_a_valid_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'profile.json'
            for name,preset in VOICE_PRESETS.items():
                p.write_text(json.dumps(style(preset=name,**preset)),encoding='utf8')
                with self.subTest(preset=name):self.assertIsNotNone(load_profile(folder))

    def test_profile_rejects_path_traversal_and_broken_types(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'profile.json'
            for payload in [[],style(keys={'wake_fast':'../old.wav'}),style(speed=9),style(blend={'af_nova':.5}),
                            style(blend={'../x':1.0}),dict(style(),version=2)]:
                p.write_text(json.dumps(payload),encoding='utf8')
                self.assertIsNone(load_profile(folder))
            p.write_text(json.dumps(style()),encoding='utf8')
            self.assertIsNotNone(load_profile(folder))

    def bare_voice(self,directory,profile):
        v=voice.Voice.__new__(voice.Voice)
        v.voice_dir=str(directory)
        v._style_profile=profile
        v.muted=False
        v.current_volume=100
        v._playback_lock=threading.RLock()
        v._playback_depth=0
        v.before_playback=Mock()
        v.after_playback=Mock()
        return v

    def test_missing_styled_clip_never_selects_an_old_male_or_female_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'wake_01.wav').write_bytes(b'old voice')
            v=self.bare_voice(folder,style())
            self.assertIsNone(v._pick_audio('wake'))

    def test_dynamic_amount_cannot_be_replaced_by_a_generic_cached_clip(self):
        with tempfile.TemporaryDirectory() as folder:
            name=phrase_file('Timer set.')
            (Path(folder)/name).write_bytes(b'cached')
            v=self.bare_voice(folder,style(keys={'timer_set':name},phrases={'Timer set.':name}))
            v._tts_blocking=Mock(return_value=True)
            with patch.object(voice,'play_audio') as playback:
                self.assertTrue(v.play_or_tts('timer_set','Timer set for five minutes.'))
            playback.assert_not_called()
            v._tts_blocking.assert_called_once_with('Timer set for five minutes.')
            v.before_playback.assert_called_once()
            v.after_playback.assert_called_once()

    def test_exact_phrase_uses_local_clip_and_mute_prevents_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            name=phrase_file('Done.')
            (Path(folder)/name).write_bytes(b'cached')
            v=self.bare_voice(folder,style(phrases={'Done.':name}))
            with patch.object(voice,'play_audio',return_value=True) as playback:
                self.assertTrue(v.play_or_tts('success','**Done.**'))
                v.muted=True
                self.assertFalse(v.play_or_tts('success','Done.'))
            playback.assert_called_once()
            v.before_playback.assert_called_once()
            v.after_playback.assert_called_once()

    def test_worker_synthesis_failure_releases_playback_and_accepts_the_next_reply(self):
        from aidy import voice_style
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)/'Assets/voice_jarvis'
            directory.mkdir(parents=True)
            (Path(folder)/'Assets/voice').mkdir()
            (directory/'profile.json').write_text(json.dumps(style()),encoding='utf8')
            neural=Mock()
            neural.chunks.side_effect=[RuntimeError('test synthesis failure'),iter([wave_bytes(np.zeros(200),24000)])]
            with patch.object(voice_style,'NeuralSpeaker',return_value=neural),patch.object(voice.winsound,'PlaySound') as playback:
                before,after=Mock(),Mock()
                v=voice.Voice(folder,before,after)
                try:
                    self.assertTrue(v._tts_ready.wait(2))
                    self.assertFalse(v.tts_blocking('First answer.'))
                    self.assertTrue(v.tts_blocking('Second answer.'))
                    self.assertEqual(before.call_count,2)
                    self.assertEqual(after.call_count,2)
                    playback.assert_called_once()
                finally:v.close()

    def test_jarvis_speaks_faster_and_common_confirmations_are_prerecorded(self):
        import importlib.util
        from aidy.voice_style import VOICE_PRESETS
        self.assertGreaterEqual(VOICE_PRESETS['jarvis']['speed'],1.15)
        path=Path(__file__).resolve().parents[3]/'scripts'/'prepare_jarvis_voice.py'
        spec=importlib.util.spec_from_file_location('prepare_jarvis_voice_under_test',path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _,phrases=module.static_phrases()
        for phrase in ['Done.','Permanently empty the Windows Recycle Bin. Are you sure?','Press Send. Are you sure?',
                       'Send the message. Are you sure?']:
            self.assertIn(phrase,phrases)

    def test_model_unavailable_uses_the_existing_windows_fallback_without_hanging(self):
        from aidy import voice_style
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)/'Assets/voice_jarvis';directory.mkdir(parents=True)
            (Path(folder)/'Assets/voice').mkdir()
            (directory/'profile.json').write_text(json.dumps(style()),encoding='utf8')
            engine=Mock()
            engine.getProperty.return_value=[SimpleNamespace(name='Microsoft Zira',id='female'),SimpleNamespace(name='Microsoft David',id='male')]
            with patch.object(voice_style,'NeuralSpeaker',side_effect=OSError('missing model')),patch.object(voice.pyttsx3,'init',return_value=engine):
                v=voice.Voice(folder)
                try:
                    self.assertTrue(v._tts_ready.wait(2))
                    self.assertTrue(v.tts_blocking('Ready.'))
                    self.assertEqual(v.backend_name,'windows-sapi')
                    self.assertIn((('voice','male'),),[(c.args,) for c in engine.setProperty.call_args_list])
                finally:v.close()


if __name__=='__main__':unittest.main()
