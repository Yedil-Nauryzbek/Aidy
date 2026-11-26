"""Conversational Aidy: converse tool, grounded answers, runtime speech and window."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.ai_tools import AIError, AIPlanError, Registry
from aidy.ai_provider import AIConfig, Credentials, GeminiProvider, function_plan
from aidy.ai_local_commands import local_plan
import test_ai_layer as fixtures
from test_task_understanding import goal, task


def calls(*items):
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
        {'functionCall': {'name': name, 'args': args}} for name, args in items]}}]}


def chat(reply='Quite well, thank you.', tone='warm', live_query=''):
    return ('converse', {'reply': reply, 'tone': tone, 'live_query': live_query})


ALLOWED = {'open_app', 'plan_task', 'ask_user', 'converse'}


class ConversationConfigTests(unittest.TestCase):
    def load(self, **values):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ai.local.json'
            path.write_text(json.dumps(values), encoding='utf-8')
            with patch.dict(os.environ, {'AIDY_AI_CONFIG': str(path), 'AIDY_INTERPRETATION_MODE': 'api'}):
                return AIConfig.load(folder)

    def test_defaults(self):
        cfg = AIConfig()
        self.assertIs(cfg.conversation_enabled, True)
        self.assertEqual(cfg.conversation_followup_seconds, 8)
        self.assertIs(cfg.live_search_enabled, True)

    def test_load_rejects_bad_values(self):
        for bad in ({'conversation_followup_seconds': 31}, {'conversation_followup_seconds': -1},
                    {'conversation_followup_seconds': '8'}, {'conversation_enabled': 'yes'},
                    {'live_search_enabled': 1}):
            with self.subTest(bad=bad), self.assertRaises(AIError):
                self.load(**bad)

    def test_load_accepts_zero_window(self):
        cfg = self.load(conversation_followup_seconds=0, conversation_enabled=False, live_search_enabled=False)
        self.assertEqual(cfg.conversation_followup_seconds, 0)
        self.assertIs(cfg.conversation_enabled, False)
        self.assertIs(cfg.live_search_enabled, False)


class ConversationPlanTests(unittest.TestCase):
    def provider(self, payload, **config):
        self.session = Mock()
        self.session.request.side_effect = [fixtures.HTTPResponse(payload=payload)]
        provider = GeminiProvider(AIConfig(mode='api', **config), Credentials(['placeholder']), self.session)
        self.addCleanup(provider.close)
        return provider

    def body(self):
        return self.session.request.call_args.kwargs['json']

    def declared(self):
        return [d['name'] for tool in self.body().get('tools', []) for d in tool.get('functionDeclarations', [])]

    def test_converse_alone(self):
        p = function_plan(calls(chat()), ALLOWED)
        self.assertEqual(p['conversation'], {'reply': 'Quite well, thank you.', 'tone': 'warm', 'live_query': '', 'detail': False})
        self.assertEqual(p['steps'], [])
        self.assertEqual(p['clarification'], '')

    def test_converse_with_ask_user_invalid(self):
        with self.assertRaises(AIPlanError):
            function_plan(calls(chat(), ('ask_user', {'question': 'Which one?'})), ALLOWED)

    def test_empty_reply_and_query_invalid(self):
        with self.assertRaises(AIPlanError):
            function_plan(calls(chat(reply='  ', live_query='')), ALLOWED)

    def test_reply_too_long_or_bad_tone_invalid(self):
        for bad in (chat(reply='x' * 1001), chat(tone='angry'), chat(live_query='q' * 201),
                    chat(reply='Hi\x07 there.'), ('converse', {'reply': 'Hi.', 'tone': 'warm', 'extra': ''})):
            with self.subTest(bad=bad[1]), self.assertRaises(AIPlanError):
                function_plan(calls(bad), ALLOWED)

    def test_reply_limit_is_1000_chars(self):
        self.assertEqual(len(function_plan(calls(chat(reply='x' * 1000)), ALLOWED)['conversation']['reply']), 1000)

    def test_detail_defaults_false_and_must_be_boolean(self):
        self.assertIs(function_plan(calls(chat()), ALLOWED)['conversation']['detail'], False)
        detailed = ('converse', {'reply': 'A long story.', 'tone': 'warm', 'live_query': '', 'detail': True})
        self.assertIs(function_plan(calls(detailed), ALLOWED)['conversation']['detail'], True)
        for bad in ('yes', 1, None):
            with self.subTest(detail=bad), self.assertRaises(AIPlanError):
                function_plan(calls(('converse', {'reply': 'Hi.', 'tone': 'warm', 'live_query': '', 'detail': bad})), ALLOWED)

    def test_live_query_with_empty_reply_valid(self):
        p = function_plan(calls(chat(reply='', tone='neutral', live_query='usd to kzt today')), ALLOWED)
        self.assertEqual(p['conversation']['live_query'], 'usd to kzt today')

    def test_mixed_plan_task_and_converse(self):
        payload = task(goal('Open Discord', fixtures.step('open_app', {'app': 'discord'})))
        provider = self.provider(calls(('plan_task', payload), chat(reply='Why did the robot cross the road?', tone='amused')))
        p = provider.interpret('open discord and tell me a joke', [], Registry(['discord']), threading.Event())
        self.assertEqual([s['tool'] for s in p['steps']], ['open_app'])
        self.assertEqual(p['conversation']['tone'], 'amused')

    def test_dropped_talk_clause_retry_points_to_converse(self):
        # Observed with the real API: "open notepad and tell me a joke" came back as
        # plan_task alone; the correction must steer the retry to converse.
        payload = task(goal('Open Discord', fixtures.step('open_app', {'app': 'discord'})))
        provider = self.provider(calls(('plan_task', payload)))
        with self.assertRaises(AIPlanError) as caught:
            provider.interpret('open discord and tell me a joke', [], Registry(['discord']), threading.Event())
        self.assertIn('converse', str(caught.exception))
        self.assertIn('tell me a joke', self.body()['systemInstruction']['parts'][0]['text'])

    def test_converse_cannot_stand_in_for_a_dropped_action_clause(self):
        # Review finding: "play some jazz" was dropped while converse said "Enjoy the jazz."
        payload = task(goal('Open Discord', fixtures.step('open_app', {'app': 'discord'})))
        provider = self.provider(calls(('plan_task', payload), chat(reply='Enjoy the jazz.', tone='upbeat')))
        with self.assertRaises(AIPlanError):
            provider.interpret('open discord and play some jazz', [], Registry(['discord']), threading.Event())

    def test_routing_keeps_facts_off_memory_and_tool_questions_on_tools(self):
        for live in (True, False):
            provider = self.provider(calls(chat()), live_search_enabled=live)
            provider.interpret('how are you doing today', [], Registry(['discord']), threading.Event())
            text = self.body()['systemInstruction']['parts'][0]['text']
            self.assertIn('never answer current facts from memory', text)
            self.assertIn('answered by a listed tool', text)
            if not live:
                self.assertIn('current fact', text)
                self.assertIn('web_search', text.split('CONVERSATION:')[1])

    def test_registry_accepts_conversation_only(self):
        conversation = {'reply': 'Hi.', 'tone': 'neutral', 'live_query': '', 'detail': False}
        p = Registry(['discord']).plan({'clarification': '', 'steps': [], 'conversation': conversation})
        self.assertEqual(p['conversation'], conversation)
        with self.assertRaises(AIError):  # an empty plan without conversation stays invalid
            Registry(['discord']).plan({'clarification': '', 'steps': []})

    def test_interpret_returns_conversation_and_declares_converse(self):
        provider = self.provider(calls(chat()))
        p = provider.interpret('how are you doing today', [], Registry(['discord']), threading.Event())
        self.assertEqual(p['conversation']['reply'], 'Quite well, thank you.')
        self.assertIn('converse', self.declared())
        self.assertIn('live_query', json.dumps(self.body()['tools']))
        self.assertIn('converse', self.body()['systemInstruction']['parts'][0]['text'])

    def test_no_declaration_when_disabled(self):
        provider = self.provider(calls(('ask_user', {'question': 'What should I do?'})), conversation_enabled=False)
        provider.interpret('how are you doing today', [], Registry(['discord']), threading.Event())
        self.assertNotIn('converse', self.declared())
        self.assertNotIn('converse', self.body()['systemInstruction']['parts'][0]['text'])

    def test_no_declaration_in_agent_mode(self):
        provider = self.provider(calls(('ask_user', {'question': 'What should I do?'})))
        registry = Registry(['discord'])
        registry.agent_mode = True
        provider.interpret('how are you doing today', [], registry, threading.Event())
        self.assertNotIn('converse', self.declared())

    def test_live_rule_absent_when_search_disabled(self):
        provider = self.provider(calls(chat()), live_search_enabled=False)
        provider.interpret('how are you doing today', [], Registry(['discord']), threading.Event())
        self.assertIn('converse', self.declared())
        self.assertNotIn('live_query', json.dumps(self.body()['tools']))
        self.assertNotIn('live_query', self.body()['systemInstruction']['parts'][0]['text'])

    def test_app_mention_is_not_explicit_app_request(self):
        registry = Registry(['chrome'])
        self.assertFalse(GeminiProvider._explicit_app_request('what do you think about Chrome', registry))
        self.assertIsNone(local_plan('what do you think about Chrome', registry))


def grounded(text='About five hundred and twelve tenge today.', finish='STOP'):
    return {'candidates': [{'finishReason': finish, 'content': {'parts': [{'text': text}]},
            'groundingMetadata': {'webSearchQueries': ['usd to kzt today'],
                                  'groundingChunks': [{'web': {'title': f's{i}', 'uri': f'https://x/{i}'}} for i in range(5)]}}]}


class LiveAnswerTests(unittest.TestCase):
    def provider(self, *responses):
        self.session = Mock()
        self.session.request.side_effect = list(responses)
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder-one']), self.session)
        self.addCleanup(provider.close)
        return provider

    def ask(self, provider, history=()):
        return provider.answer_live('usd to kzt today', list(history), threading.Event())

    def test_body_has_only_search_tool(self):
        from datetime import datetime
        self.ask(self.provider(fixtures.HTTPResponse(payload=grounded())),
                 [{'request': 'hi', 'aidy_reply': 'Hello there.', 'grounded': False}])
        body = self.session.request.call_args.kwargs['json']
        self.assertEqual(body['tools'], [{'googleSearch': {}}])
        self.assertNotIn('toolConfig', body)
        self.assertNotIn('functionDeclarations', json.dumps(body))
        self.assertIn('British butler', body['systemInstruction']['parts'][0]['text'])
        user = body['contents'][0]['parts'][0]['text']
        self.assertIn(datetime.now().strftime('%Y-%m-%d'), user)
        self.assertIn('Hello there.', user)
        self.assertIn('usd to kzt today', user)

    def test_reply_and_sources_capped(self):
        answer = self.ask(self.provider(fixtures.HTTPResponse(payload=grounded())))
        self.assertEqual(answer['reply'], 'About five hundred and twelve tenge today.')
        self.assertEqual(len(answer['sources']), 3)
        self.assertEqual(answer['sources'][0], {'title': 's0', 'uri': 'https://x/0'})
        self.assertEqual(answer['queries'], ['usd to kzt today'])

    def test_blocked_or_empty_raises(self):
        for payload in (grounded(finish='SAFETY'), grounded(text='  '), {'candidates': []}):
            with self.subTest(payload=payload), self.assertRaises(AIError):
                self.ask(self.provider(fixtures.HTTPResponse(payload=payload)))

    def test_credentials_redacted(self):
        answer = self.ask(self.provider(fixtures.HTTPResponse(payload=grounded('Key placeholder-one here.'))))
        self.assertNotIn('placeholder-one', answer['reply'])

    def test_search_quota_error_does_not_block_other_requests(self):
        # Observed 2026-11-23: free-tier keys answer plain requests but 429 every
        # grounded one; that must not put the shared credentials into cooldown.
        quota = fixtures.HTTPResponse(status=429, payload={'error': {'status': 'RESOURCE_EXHAUSTED', 'message': 'quota',
            'details': [{'@type': 'type.googleapis.com/google.rpc.Help'}]}})
        provider = self.provider(quota)
        with self.assertRaises(AIError):
            self.ask(provider)
        self.assertFalse(provider.credentials.quota_exhausted)
        self.assertLessEqual(provider.credentials.cooldown_until, 0)

    def test_grounded_call_is_never_hedged(self):
        # A duplicate grounded request would run (and bill) every search twice.
        provider = self.provider(fixtures.SlowHTTPResponse(payload=grounded()), fixtures.HTTPResponse(payload=grounded()))
        with patch.object(GeminiProvider, 'hedge_delay', staticmethod(lambda timeout: .05)):
            self.ask(provider)
        self.assertEqual(self.session.request.call_count, 1)


class ChatProvider(fixtures.FakeProvider):
    def __init__(self, *plans, answers=()):
        super().__init__(*plans)
        self.answers = list(answers)
        self.live_seen = []

    def answer_live(self, query, history, cancel):
        self.live_seen.append(query)
        value = self.answers.pop(0)
        if callable(value):
            return value(query, history, cancel)
        if isinstance(value, Exception):
            raise value
        return value


def conv(reply='Splendid, thank you.', tone='upbeat', live_query='', steps=(), detail=False):
    return {'clarification': '', 'steps': list(steps),
            'conversation': {'reply': reply, 'tone': tone, 'live_query': live_query, 'detail': detail}}


LIVE = {'reply': 'About five hundred and twelve tenge today.', 'sources': [{'title': 's', 'uri': 'https://s'}],
        'queries': ['usd kzt']}


class EngineConversationTests(unittest.TestCase):
    def make(self, *plans, answers=(), allowed=True):
        from aidy.ai_engine import AIEngine, Result
        self.provider = ChatProvider(*plans, answers=answers)
        self.execute = Mock(return_value=Result('dispatched', 'Dispatched; final state unverified.'))
        self.authorize = Mock(return_value=allowed)
        self.engine = AIEngine(self.provider, Registry(['discord', 'steam']), self.authorize, Mock(return_value=True), self.execute)
        self.engine.local_commands = False
        self.engine.announce = Mock()
        return self.engine

    def run_text(self, text):
        return self.engine.run(text, ('local', 'Admin'))

    def test_chat_only_answers_without_actions(self):
        from aidy.ai_tools import permission
        self.make(conv())
        result = self.run_text('how are you')
        self.assertEqual((result.status, result.ok, result.message), ('answered', True, 'Splendid, thank you.'))
        self.assertEqual(result.data, {'tone': 'upbeat', 'sources': [], 'detail': False})
        self.execute.assert_not_called()
        self.authorize.assert_called_once_with(('local', 'Admin'), permission({'tool': 'current_time', 'arguments': {}}))
        self.assertEqual(self.engine.history[-1], {'request': 'how are you', 'aidy_reply': 'Splendid, thank you.', 'grounded': False})
        self.engine.announce.assert_not_called()

    def test_answered_result_carries_detail(self):
        self.make(conv(reply='Once upon a time a robot learned to sing.', detail=True))
        self.assertIs(self.run_text('tell me a story').data['detail'], True)

    def test_live_query_calls_grounded_answer_and_announces(self):
        self.make(conv(reply='', tone='neutral', live_query='usd kzt'), answers=[LIVE])
        result = self.run_text("what's the dollar rate today")
        self.assertEqual(result.status, 'answered')
        self.assertEqual(result.message, LIVE['reply'])
        self.assertEqual(result.data['sources'], LIVE['sources'])
        self.assertEqual(self.provider.live_seen, ['usd kzt'])
        self.engine.announce.assert_called_once()
        self.assertTrue(self.engine.history[-1]['grounded'])

    def test_live_failure_speaks_fallback(self):
        self.make(conv(reply='', live_query='usd kzt'), answers=[AIError('Google returned no usable answer.')])
        result = self.run_text("what's the dollar rate today")
        self.assertEqual((result.status, result.message), ('failed', "I can't check live information right now."))

    def test_cancel_during_live_call(self):
        def cancelled(query, history, cancel):
            self.engine.cancel()
            cancel.wait(1)
            raise AIError('Cancelled.')
        self.make(conv(reply='', live_query='usd kzt'), answers=[cancelled])
        result = self.run_text("what's the dollar rate today")
        self.assertEqual(result.status, 'cancelled')
        self.assertFalse(any('aidy_reply' in row for row in self.engine.history))

    def test_unauthorized_identity_cannot_chat(self):
        self.make(conv(live_query='usd kzt'), answers=[LIVE], allowed=False)
        result = self.run_text('how are you')
        self.assertEqual(result.status, 'failed')
        self.assertEqual(self.provider.live_seen, [])

    def test_mixed_plan_records_turn_conversation(self):
        mixed = conv(reply='Here is one.', tone='amused', steps=[fixtures.step()])
        self.make(mixed, fixtures.plan(fixtures.step()))
        self.assertTrue(self.run_text('open discord and tell me a joke').ok)
        self.execute.assert_called_once()
        self.assertEqual(self.engine.turn_conversation, mixed['conversation'])
        self.run_text('open discord')
        self.assertIsNone(self.engine.turn_conversation)

    def test_answer_after_mixed_run_uses_fresh_cancellation(self):
        mixed = conv(reply='', live_query='weather london', steps=[fixtures.step()])
        self.make(mixed, answers=[LIVE])
        self.run_text('open discord and tell me the weather in london')
        reply, sources, grounded = self.engine.answer(self.engine.turn_conversation)
        self.assertEqual((reply, grounded), (LIVE['reply'], True))
        self.assertEqual(self.engine.history[-1]['aidy_reply'], LIVE['reply'])

    def test_compact_history_includes_reply(self):
        from aidy.ai_provider import compact_history
        rows = compact_history([{'request': 'hi', 'aidy_reply': 'Hello.', 'grounded': False}])
        self.assertEqual(rows[0]['aidy_reply'], 'Hello.')
        self.assertIs(rows[0]['grounded'], False)


class RuntimeConversationTests(unittest.TestCase):
    def setUp(self):
        import test_app_routing
        self.env = patch.dict(os.environ, {'AIDY_INTERPRETATION_MODE': 'original'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.fixture = test_app_routing.AppRoutingTests()
        self.host = self.fixture.make_assistant()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.host._ai
        self.runtime.config = AIConfig(mode='api')
        self.runtime.reload = Mock()
        self.runtime.poll_cancel_audio = Mock(side_effect=self.runtime.progress)
        self.runtime.windows.snapshot = Mock(return_value=[])
        self.host.voice_id_enabled = False
        self.host.voice = Mock(muted=False)
        self.host._deafen_after_speak = Mock()

    def attach(self, *plans, answers=()):
        from aidy.ai_engine import AIEngine
        self.provider = ChatProvider(*plans, answers=answers)
        self.runtime.engine = AIEngine(self.provider, Registry([a['id'] for a in self.host.apps]),
                                       self.runtime.authorize, self.runtime.confirm, self.runtime.execute)
        self.runtime.engine.local_commands = False

    def answered(self, text='Two short sentences. Here.', tone='warm', sources=()):
        from aidy.ai_engine import Result
        return Result('answered', text, data={'tone': tone, 'sources': list(sources)})

    # Speech cleanup and length tests moved to test_assistant_voice.SpokenTextTests (2026-11-25).

    def test_reply_with_nothing_speakable_opens_no_window(self):
        self.runtime.report(self.answered('https://example.com 😀'))
        self.host.voice.tts_blocking.assert_not_called()
        self.assertFalse(self.runtime.pending)

    def test_answered_speaks_full_reply_with_tone_and_opens_window(self):
        self.attach(conv('Two short sentences. Here.', 'warm'))
        self.assertTrue(self.runtime.run('how are you'))
        self.host.voice.tts_blocking.assert_called_once_with('Two short sentences. Here.', tone='warm')
        self.assertTrue(self.runtime.pending)

    def test_action_result_opens_no_window(self):
        from aidy.ai_engine import Result
        self.runtime.report(self.answered())
        self.runtime.report(Result('verified', 'Opening discord.'))
        self.assertFalse(self.runtime.pending)

    def test_window_disabled_by_config(self):
        self.runtime.config = AIConfig(mode='api', conversation_followup_seconds=0)
        self.runtime.report(self.answered())
        self.assertFalse(self.runtime.pending)

    def test_end_listening_closes_window(self):
        self.runtime.report(self.answered())
        self.runtime.end_listening()
        self.assertFalse(self.runtime.pending)

    def test_sources_shown_not_spoken(self):
        import contextlib
        import io
        out = io.StringIO()
        with patch('aidy.ai_runtime.UI_MODE', True), contextlib.redirect_stdout(out):
            self.runtime.report(self.answered('About five hundred tenge.', 'neutral', [{'title': 'XE', 'uri': 'https://xe'}]))
        self.assertIn('AI_RESPONSE:About five hundred tenge. Sources: XE', out.getvalue())
        self.host.voice.tts_blocking.assert_called_once_with('About five hundred tenge.', tone='neutral')

    def test_mixed_reply_spoken_after_success_only(self):
        from aidy.ai_engine import Result
        self.attach()
        self.runtime.engine.turn_conversation = {'reply': 'Here is a joke.', 'tone': 'amused', 'live_query': '', 'detail': True}
        failed = self.runtime._attach_reply(self.runtime.engine, Result('failed', 'Could not open Discord.'))
        self.assertNotIn('reply', failed.data or {})
        done = self.runtime._attach_reply(self.runtime.engine, Result('verified', 'Opening discord.'))
        self.assertEqual(done.data['reply'], 'Here is a joke.')
        self.assertIs(done.data['detail'], True)
        self.runtime.report(done)
        self.host.voice.tts_blocking.assert_called_with('Here is a joke.', tone='amused')
        self.assertFalse(self.runtime.pending)  # acted: the wake word is needed again

    def test_live_answer_announces_let_me_check(self):
        self.attach(conv(reply='', tone='neutral', live_query='usd kzt'), answers=[LIVE])
        self.assertTrue(self.runtime.run("what's the dollar rate today"))
        self.host.voice.play_or_tts.assert_any_call('let_me_check', 'Let me check.')
        self.host.voice.tts_blocking.assert_called_once_with(LIVE['reply'], tone='neutral')


if __name__ == '__main__':
    unittest.main()
