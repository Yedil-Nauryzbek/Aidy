"""Stage 3 of the assistant core (2026-11-26): what the user asked Aidy to remember."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.ai_tools import AIError


class StoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'memory.json'

    def store(self):
        from aidy.ai_memory import MemoryStore
        return MemoryStore(self.path)

    def test_notes_survive_a_new_store(self):
        first = self.store().add('local', "Rami doesn't care about setup files.")
        second = self.store().add('local', 'Rami prefers short answers.')
        self.assertGreater(second['id'], first['id'])
        self.assertEqual([n['text'] for n in self.store().items(('local',))],
                         ["Rami doesn't care about setup files.", 'Rami prefers short answers.'])

    def test_secrets_are_refused(self):
        for text in ('my wifi password is tiger42', 'remember my api key sk-live-9f8e7d6c5b4a',
                     'the door pin is 4821', 'card 4111 1111 1111 1111', 'token A7f9K2m4P8q1Z6x3C5v0B9n2'):
            with self.subTest(text=text), self.assertRaises(AIError):
                self.store().add('local', text)
        self.assertFalse(self.path.exists())

    # Review 26.11: plurals and everyday names of secrets passed; a paraphrase dropped the word.
    def test_more_secrets_are_refused(self):
        for text in ("Rami's passwords are tiger42 and lion7", 'API keys are kept in the drawer: abc123',
                     'wifi key is tiger42', 'the door code is 4821', 'pincode is 1234', 'pass word is tiger42',
                     "Rami's Wi-Fi key: tiger42"):
            with self.subTest(text=text), self.assertRaises(AIError):
                self.store().add('local', text)
        with self.assertRaises(AIError):  # the planner's note lost the word; the user's request still has it
            self.store().add('local', 'Rami uses tiger42 at home.', said='remember my wifi password is tiger42')

    def test_wrong_shape_reads_as_unreadable(self):
        for content in ('{"next_id": 2, "notes": [{"id": 1, "text": "x"}]}', '[1]', '{"next_id": 2, "notes": [5]}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding='utf-8')
                self.assertEqual(self.store().items(('local',)), [])  # every command keeps working
                with self.assertRaises(AIError):
                    self.store().add('local', 'Rami likes jazz.')
                self.assertEqual(self.path.read_text(encoding='utf-8'), content)

    def test_limits(self):
        from aidy.ai_memory import MAX_NOTES, MAX_TEXT
        with self.assertRaises(AIError):
            self.store().add('local', 'x' * (MAX_TEXT + 1))
        store = self.store()
        for index in range(MAX_NOTES):
            store.add('local', f'Note number {index}.')
        with self.assertRaises(AIError) as caught:
            store.add('local', 'One more note.')
        self.assertIn('full', str(caught.exception))

    def test_owners(self):
        from aidy.ai_memory import owners
        self.assertEqual(owners(('local', 'Admin')), ('local',))
        self.assertEqual(owners((5, 'Admin')), ('5', 'local'))
        self.assertEqual(owners((7, 'User')), ('7',))
        self.assertEqual(owners((9, 'Guest')), ())

    def test_forget_is_scoped(self):
        store = self.store()
        note = store.add('local', 'Rami likes jazz.')
        self.assertFalse(store.forget(('7',), note['id']))  # a User cannot touch the local admin's notes
        self.assertFalse(store.forget(('5', 'local'), 999))
        self.assertTrue(store.forget(('5', 'local'), note['id']))
        self.assertEqual(store.items(('local',)), [])
        store.add('7', 'A.')
        store.add('7', 'B.')
        self.assertEqual(store.forget_all(('7',)), 2)

    def test_corrupt_file_is_kept(self):
        self.path.write_text('{not json', encoding='utf-8')
        with self.assertRaises(AIError):
            self.store().add('local', 'Rami likes jazz.')
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{not json')
        self.assertEqual(self.store().items(('local',)), [])  # ordinary commands keep working


def memory_step(operation, text='', note_id=0):
    return {'tool': 'memory', 'arguments': {'operation': operation, 'text': text, 'id': note_id}, 'delay_seconds': 0}


class ToolTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'memory.json'

    def test_memory_tool_contract(self):
        from aidy.ai_tools import DATA_READS, TOOLS, needs_confirmation, permission
        from aidy.voice_auth import ADMIN_ONLY_INTENTS, GUEST_ALLOWED_INTENTS
        properties = TOOLS['memory'].parameters['properties']
        self.assertEqual(properties['operation']['enum'], ['remember', 'list', 'forget', 'forget_all'])
        self.assertEqual(properties['text']['maxLength'], 300)
        self.assertEqual(properties['id'], {'type': 'integer', 'minimum': 0, 'maximum': 1000000})
        self.assertEqual(permission(memory_step('remember')), 'memory')
        self.assertNotIn('memory', GUEST_ALLOWED_INTENTS)  # a Guest has no memory
        self.assertNotIn('memory', ADMIN_ONLY_INTENTS)     # a User keeps their own notes
        self.assertTrue(needs_confirmation(memory_step('forget_all')))
        self.assertFalse(needs_confirmation(memory_step('remember')))
        self.assertIn(('memory', 'list'), DATA_READS)

    def runtime(self, identity=('local', 'Admin')):
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from aidy.ai_memory import MemoryStore
        from aidy.ai_runtime import AIRuntime
        runtime = AIRuntime.__new__(AIRuntime)
        runtime.memory = MemoryStore(self.path)
        runtime.engine = SimpleNamespace(identity=identity, cancel_event=threading.Event())
        runtime.host = SimpleNamespace(stop_stream=Mock())
        runtime._confirmed_actions = {}
        runtime.windows = SimpleNamespace(_frozen=None)
        runtime._confirmed_slots = None
        return runtime

    def test_runtime_executes_memory_for_the_current_speaker(self):
        import json
        from unittest.mock import patch
        runtime = self.runtime()
        with patch('aidy.ai_runtime.ui_state'):
            saved = runtime.execute(memory_step('remember', 'Rami likes jazz.'))
            listed = runtime.execute(memory_step('list'))
            note = json.loads(listed.message)['notes'][0]
            missing = runtime.execute(memory_step('forget', note_id=note['id'] + 100))
            refused = runtime.execute(memory_step('remember', 'my wifi password is tiger42'))
            forgotten = runtime.execute(memory_step('forget', note_id=note['id']))
        self.assertEqual((saved.status, saved.message), ('verified', "I'll remember that."))
        self.assertEqual(note['text'], 'Rami likes jazz.')
        self.assertEqual((missing.status, missing.message), ('failed', "I couldn't find that note."))
        self.assertEqual((refused.status, refused.message), ('failed', "I don't store passwords or keys."))
        # Review 26.11: a misheard "forget that" must say which note went (there is no undo).
        self.assertEqual((forgotten.status, forgotten.message), ('verified', 'Forgotten: Rami likes jazz.'))

    def test_memory_is_not_offered_in_agent_mode(self):
        # Review 26.11: page or file text read by the agent could otherwise plant a lasting note.
        from types import SimpleNamespace
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']))
        self.addCleanup(provider.close)
        for agent_mode, offered in ((False, True), (True, False)):
            registry = SimpleNamespace(agent_mode=agent_mode, desktop_state={}, screen_image=None)
            self.assertEqual('memory' in provider.available_tools(registry), offered)

    def test_forget_all_has_a_clear_confirmation(self):
        _, spoken, _ = self.runtime()._prepare_confirmation(memory_step('forget_all'))
        self.assertEqual(spoken, 'Forget everything I remember')


class DirectAnswerTests(unittest.TestCase):
    """memory is not an agent tool: its list read takes the engine's direct path."""
    def run_memory(self, operation, message):
        from unittest.mock import Mock
        import test_ai_layer as fixtures
        from aidy.ai_engine import AIEngine, Result
        from aidy.ai_tools import Registry
        answers = []

        class Provider(fixtures.FakeProvider):
            def answer_data(self, request, reads, history, cancel, memory=()):
                answers.append(reads)
                return {'reply': 'I remember that you like jazz.', 'tone': 'warm', 'detail': False}

        step = memory_step(operation, 'Rami likes jazz.' if operation == 'remember' else '')
        engine = AIEngine(Provider(fixtures.plan(step)), Registry([]), lambda *a: True, lambda *a: True,
                          Mock(return_value=Result('verified', message)))
        return engine.run('what do you remember', ('local', 'Admin')), answers

    def test_memory_list_is_answered_aloud(self):
        result, answers = self.run_memory('list', '{"notes": [{"id": 1, "text": "Rami likes jazz."}]}')
        self.assertEqual((result.status, result.message), ('answered', 'I remember that you like jazz.'))
        self.assertEqual(answers[0][0]['operation'], 'list')

    def test_remember_makes_no_second_call(self):
        result, answers = self.run_memory('remember', "I'll remember that.")
        self.assertEqual((result.status, result.message), ('verified', "I'll remember that."))
        self.assertEqual(answers, [])


class ContextTests(unittest.TestCase):
    NOTES = [{'id': i, 'text': f'Note {i}: ' + 'x' * 140, 'created': '2026-11-26 10:00'} for i in range(30)]

    def provider(self, payload):
        from unittest.mock import Mock
        import test_ai_layer as fixtures
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        self.session = Mock()
        self.session.request.side_effect = [fixtures.HTTPResponse(payload=payload)]
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), self.session)
        self.addCleanup(provider.close)
        return provider

    def sent(self):
        import json
        body = self.session.request.call_args.kwargs['json']
        return body['systemInstruction']['parts'][0]['text'], json.loads(body['contents'][0]['parts'][0]['text'])

    def test_planner_sees_the_speakers_notes(self):
        import json
        import threading
        from test_conversation import calls, chat
        from aidy.ai_tools import Registry
        registry = Registry(['discord'])
        registry.user_memory = self.NOTES
        self.provider(calls(chat())).interpret('how are you', [], registry, threading.Event())
        system, context = self.sent()
        notes = context['user_memory']
        self.assertLessEqual(len(notes), 20)
        self.assertLessEqual(len(json.dumps(notes)), 2000)
        self.assertEqual(notes[-1]['id'], 29)  # the newest notes are kept
        self.assertIn('never permission', system)
        self.assertIn('only when the user explicitly asks', system)

    def test_no_notes_no_context_key(self):
        import threading
        from test_conversation import calls, chat
        from aidy.ai_tools import Registry
        self.provider(calls(chat())).interpret('how are you', [], Registry(['discord']), threading.Event())
        self.assertNotIn('user_memory', self.sent()[1])

    def test_answer_request_sees_notes(self):
        import threading
        import test_ai_layer as fixtures
        provider = self.provider(fixtures.response({'reply': 'Two files.', 'tone': 'neutral', 'detail': False}))
        provider.answer_data("what's in my downloads", [{'tool': 'file_action', 'operation': 'list', 'target': '', 'text': '{}'}],
                             [], threading.Event(), memory=[{'id': 1, 'text': "Rami doesn't care about setup files."}])
        system, context = self.sent()
        self.assertEqual(context['user_memory'], [{'id': 1, 'text': "Rami doesn't care about setup files."}])
        # Live 26.11: the notes reached the answer request, but its rules never mentioned them.
        self.assertIn('user_memory', system)

    def test_run_sets_notes_for_the_identity(self):
        import test_conversation
        from aidy.ai_memory import MemoryStore
        case = test_conversation.RuntimeConversationTests('test_window_disabled_by_config')
        case.setUp()
        self.addCleanup(case.doCleanups)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        case.runtime.memory = MemoryStore(Path(folder.name) / 'memory.json')
        case.runtime.memory.add('local', 'Rami likes jazz.')
        case.attach(test_conversation.conv())
        self.assertTrue(case.runtime.run('how are you'))
        self.assertEqual([n['text'] for n in case.runtime.engine.registry.user_memory], ['Rami likes jazz.'])


if __name__ == '__main__':
    unittest.main()
