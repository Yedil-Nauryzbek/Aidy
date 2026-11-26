"""Tone-aware Kokoro prosody for conversational replies. No audio device or model."""
from collections import OrderedDict
from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.voice_style import DEFAULT_STYLE, NeuralSpeaker, TONE_PROSODY, tone_prosody


class ToneProsodyTests(unittest.TestCase):
    def test_tone_speeds_within_bounds(self):
        for base in (1.15, 1.25, .85):
            for tone in TONE_PROSODY:
                speed, sentence_pause, clause_pause = tone_prosody(base, tone)
                self.assertTrue(.85 <= speed <= 1.25, (base, tone, speed))
                self.assertGreater(sentence_pause, 0)
                self.assertGreater(clause_pause, 0)

    def test_unknown_tone_is_neutral(self):
        self.assertEqual(tone_prosody(1.15, 'furious'), tone_prosody(1.15, 'neutral'))
        self.assertEqual(tone_prosody(1.15, 'neutral'), (1.15, .20, .075))

    def test_render_uses_tone_and_caches_per_tone(self):
        speaker = object.__new__(NeuralSpeaker)
        speaker.profile = dict(DEFAULT_STYLE, speed=1.15, effect_strength=.12)
        speaker.cache, speaker.cache_bytes, speaker.model_voice = OrderedDict(), 0, None
        speaker.engine = Mock(create=Mock(return_value=(np.full(2400, .1, np.float32), 24000)))
        speaker.render('Hi there.', 'warm')
        speaker.render('Hi there.', 'neutral')
        speaker.render('Hi there.', 'warm')  # cached
        speeds = [call.kwargs['speed'] for call in speaker.engine.create.call_args_list]
        self.assertEqual(len(speeds), 2)
        self.assertAlmostEqual(speeds[0], 1.15 * TONE_PROSODY['warm'][0])
        self.assertAlmostEqual(speeds[1], 1.15)
        self.assertGreater(speaker.engine.create.call_args_list[0].kwargs['sentence_pause'], .20)

    def test_default_preset_valid(self):
        from aidy.voice_style import DEFAULT_PRESET, VOICE_PRESETS
        self.assertEqual(DEFAULT_PRESET, 'jarvis_live')
        self.assertIn('jarvis', VOICE_PRESETS)  # one-line rollback
        style = DEFAULT_STYLE
        self.assertEqual(style['preset'], 'jarvis_live')
        self.assertTrue(.85 <= style['speed'] <= 1.25 and 0 <= style['effect_strength'] <= .4)
        self.assertLess(style['effect_strength'], VOICE_PRESETS['jarvis']['effect_strength'])
        self.assertTrue(1 <= len(style['blend']) <= 4 and abs(sum(style['blend'].values()) - 1) <= .01)
        self.assertIn(style['lang'], {'en-us', 'en-gb'})

    def test_tts_queue_carries_tone(self):
        from aidy.voice import Voice
        voice = object.__new__(Voice)
        voice.muted, voice._style_profile, voice._tts_queue = False, None, queue.Queue()
        worker = threading.Thread(target=voice._tts_blocking, args=('Hello there.', 'warm'), daemon=True)
        worker.start()
        text, tone, done, result = voice._tts_queue.get(timeout=2)
        self.assertEqual((text, tone), ('Hello there.', 'warm'))
        result.append(True)
        done.set()
        worker.join(2)


if __name__ == '__main__':
    unittest.main()
