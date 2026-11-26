"""Instant local answers (battery, volume, time), clause-first speech, slow-turn filler."""
from collections import OrderedDict
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.ai_tools import Registry, TOOLS
from aidy.ai_local_commands import local_plan
from aidy.ai_engine import Result


def tool_for(text):
    plan = local_plan(text, Registry(['discord']))
    return plan['steps'][0]['tool'] if plan else None


class LocalStatusQuestionTests(unittest.TestCase):
    def test_questions_map_to_read_tools(self):
        cases = {
            "what's my battery": 'battery_status', 'how much battery do i have left': 'battery_status',
            'battery percentage': 'battery_status', 'how many percent of my battery is here': 'battery_status',
            "what's the volume": 'volume_status', 'what is the volume level right now': 'volume_status',
            "what's the level of my volume right now": 'volume_status',
            'what time is it': 'current_time', "what's the date today": 'current_time', 'what day is it': 'current_time',
        }
        for text, tool in cases.items():
            with self.subTest(text=text):
                self.assertEqual(tool_for(text), tool)

    def test_commands_and_other_questions_are_not_status_reads(self):
        for text in ('open battery settings', 'volume up', 'battery saver on', 'set the volume to 30',
                     'how much time is left', 'what is my battery and volume', 'have a nice day'):
            with self.subTest(text=text):
                self.assertNotIn(tool_for(text), {'battery_status', 'volume_status', 'current_time'})

    def test_tools_are_declared_for_google_path(self):
        for name in ('battery_status', 'volume_status'):
            self.assertEqual(TOOLS[name].parameters['properties'], {})
            self.assertIn('never open Settings', TOOLS[name].description)


class BatteryReaderTests(unittest.TestCase):
    def read(self, ac, flag, percent):
        from aidy import system
        with patch.object(system, '_power_status', return_value=(ac, flag, percent)):
            return system.battery_status()

    def test_charging_and_discharging(self):
        self.assertEqual(self.read(1, 8, 72), {'percent': 72, 'charging': True, 'has_battery': True})
        self.assertEqual(self.read(0, 0, 40), {'percent': 40, 'charging': False, 'has_battery': True})

    def test_no_battery_and_unknown(self):
        self.assertFalse(self.read(1, 128, 255)['has_battery'])
        self.assertIsNone(self.read(1, 255, 255)['percent'])


class StatusExecutorTests(unittest.TestCase):
    def setUp(self):
        import os
        import test_app_routing
        self.env = patch.dict(os.environ, {'AIDY_INTERPRETATION_MODE': 'original'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.fixture = test_app_routing.AppRoutingTests()
        self.host = self.fixture.make_assistant()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.host._ai

    def run_tool(self, tool):
        return self.runtime.execute({'tool': tool, 'arguments': {}, 'delay_seconds': 0})

    def test_battery_messages(self):
        from aidy import system
        for info, message in (({'percent': 72, 'charging': True, 'has_battery': True}, 'Battery at 72 percent, charging.'),
                              ({'percent': 40, 'charging': False, 'has_battery': True}, 'Battery at 40 percent.'),
                              ({'percent': None, 'charging': None, 'has_battery': False}, 'This computer has no battery.')):
            with self.subTest(info=info), patch.object(system, 'battery_status', return_value=info):
                result = self.run_tool('battery_status')
                self.assertEqual((result.ok, result.message), (True, message))
        with patch.object(system, 'battery_status', return_value={'percent': None, 'charging': None, 'has_battery': True}):
            self.assertFalse(self.run_tool('battery_status').ok)

    def test_volume_messages(self):
        from aidy import system
        for value, message in (((40, False), 'Volume is at 40 percent.'), ((15, True), 'Volume is at 15 percent, muted.')):
            with self.subTest(value=value), patch.object(system, 'get_volume_percent', return_value=value):
                self.assertEqual(self.run_tool('volume_status').message, message)
        with patch.object(system, 'get_volume_percent', return_value=(None, None)):
            self.assertFalse(self.run_tool('volume_status').ok)


class ClauseFirstChunkTests(unittest.TestCase):
    def pieces(self, text):
        from aidy.voice_style import NeuralSpeaker
        speaker = object.__new__(NeuralSpeaker)
        speaker.render = lambda text, tone='neutral': text
        return list(speaker.chunks(text))

    def test_first_clause_then_one_sentence_at_a_time(self):
        self.assertEqual(self.pieces('I am quite well, thank you for asking. How may I assist you today?'),
                         ['I am quite well,', 'thank you for asking.', 'How may I assist you today?'])

    def test_short_or_comma_free_text_is_unchanged(self):
        self.assertEqual(self.pieces('Battery at 72 percent.'), ['Battery at 72 percent.'])
        self.assertEqual(self.pieces('Well, yes.'), ['Well, yes.'])
        self.assertEqual(self.pieces('Hello there. How are you?'), ['Hello there.', 'How are you?'])

    def test_long_sentence_is_split_near_160_chars(self):
        pieces = self.pieces(' '.join(['word'] * 80) + '.')
        self.assertTrue(all(len(p) <= 160 for p in pieces))
        self.assertEqual(' '.join(pieces), ' '.join(['word'] * 80) + '.')


class SlowTurnFillerTests(unittest.TestCase):
    def setUp(self):
        import os
        import test_app_routing
        from aidy.ai_provider import AIConfig
        self.env = patch.dict(os.environ, {'AIDY_INTERPRETATION_MODE': 'original'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.fixture = test_app_routing.AppRoutingTests()
        self.host = self.fixture.make_assistant()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.host._ai
        self.runtime.config = AIConfig(mode='api')
        self.runtime.reload = Mock()
        self.runtime.poll_cancel_audio = Mock()
        self.runtime.windows.snapshot = Mock(return_value=[])
        # The first catalog scan can take up to 1.5 s and counts toward the filler threshold.
        self.runtime.catalog.refresh = Mock()
        self.host.voice_id_enabled = False
        self.host.voice = Mock(muted=False)
        self.host._deafen_after_speak = Mock()

    def attach(self, delay):
        from aidy.ai_engine import AIEngine
        from test_conversation import ChatProvider, conv
        provider = ChatProvider(conv('Fine, thank you.', 'warm'))
        original = provider.interpret
        provider.interpret = lambda *args: (time.sleep(delay), original(*args))[1]
        self.runtime.engine = AIEngine(provider, Registry([a['id'] for a in self.host.apps]),
                                       self.runtime.authorize, self.runtime.confirm, self.runtime.execute)
        self.runtime.engine.local_commands = False

    def fillers(self):
        return [c for c in self.host.voice.play_or_tts.call_args_list if c.args == ('one_moment', 'One moment.')]

    def test_slow_turn_has_no_filler(self):
        # Owner 2026-11-24: "One moment." played on nearly every Google turn; removed.
        self.attach(1.7)
        self.assertTrue(self.runtime.run('how are you'))
        self.assertEqual(self.fillers(), [])


class ModelRaceTests(unittest.TestCase):
    """Measured 2026-11-24: Google answered one model with 503 or a 10 s stall while
    another answered in ~1 s; a retry on the same model after a backoff wasted 6-16 s."""

    def provider(self, *responses, **config):
        import threading
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        self.session = Mock()
        self.session.request.side_effect = list(responses)
        provider = GeminiProvider(AIConfig(mode='api', **config), Credentials(['placeholder']), self.session)
        self.addCleanup(provider.close)
        self.event = threading.Event()
        return provider

    def models(self):
        return [call.args[1].split('/models/')[1].split(':')[0] for call in self.session.request.call_args_list]

    def unavailable(self):
        import test_ai_layer as fixtures
        return fixtures.HTTPResponse(status=503, payload={'error': {'status': 'UNAVAILABLE', 'message': 'overloaded'}})

    def test_server_error_switches_to_fallback_model_at_once(self):
        import test_ai_layer as fixtures
        provider = self.provider(self.unavailable(), fixtures.HTTPResponse())
        started = time.monotonic()
        plan = provider.interpret('Open Discord.', [], Registry(['discord']), self.event)
        self.assertLess(time.monotonic() - started, .4)  # no backoff before the second request
        self.assertEqual(plan['steps'][0]['arguments'], {'app': 'discord'})
        self.assertEqual(self.models(), ['gemini-3.5-flash-lite', 'gemini-flash-lite-latest'])
        self.assertLessEqual(provider.credentials.cooldown_until, 0)

    def test_slow_request_is_raced_on_fallback_model(self):
        import test_ai_layer as fixtures
        from aidy.ai_provider import GeminiProvider
        provider = self.provider(fixtures.SlowHTTPResponse(), fixtures.HTTPResponse())
        with patch.object(GeminiProvider, 'hedge_delay', staticmethod(lambda timeout: .05)):
            started = time.monotonic()
            provider.interpret('Open Discord.', [], Registry(['discord']), self.event)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(self.models(), ['gemini-3.5-flash-lite', 'gemini-flash-lite-latest'])

    def test_client_error_is_not_raced(self):
        import test_ai_layer as fixtures
        from aidy.ai_tools import AIError
        provider = self.provider(fixtures.HTTPResponse(status=400, payload={'error': {'status': 'INVALID_ARGUMENT'}}))
        with self.assertRaises(AIError):
            provider.interpret('Open Discord.', [], Registry(['discord']), self.event)
        self.assertEqual(len(self.models()), 1)

    def test_empty_fallback_keeps_the_primary_model(self):
        import test_ai_layer as fixtures
        provider = self.provider(self.unavailable(), fixtures.HTTPResponse(), fallback_model='')
        provider.interpret('Open Discord.', [], Registry(['discord']), self.event)
        self.assertEqual(self.models(), ['gemini-3.5-flash-lite', 'gemini-3.5-flash-lite'])


if __name__ == '__main__':
    unittest.main()
