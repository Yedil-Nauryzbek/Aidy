"""Stage 2 of the assistant core (2026-11-25): spoken answers from read data."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.ai_files import TaskFiles


class FileListTests(unittest.TestCase):
    def test_list_has_modified_and_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = time.time()
            for name, age_days in (('old.txt', 9), ('newest.txt', 0), ('middle.txt', 3)):
                (root / name).write_text('x', encoding='utf-8')
                os.utime(root / name, (now - age_days * 86400, now - age_days * 86400))
            task = TaskFiles('What is in "' + str(root) + '"')
            rows = json.loads(task.execute(dict(path=str(root), destination='', text='', operation='list')).message)['items']
        self.assertEqual([Path(r['path']).name for r in rows], ['newest.txt', 'middle.txt', 'old.txt'])
        for row in rows:
            self.assertRegex(row['modified'], r'^\d{4}-\d\d-\d\d \d\d:\d\d$')

    def test_list_counts_the_whole_folder_and_keeps_the_newest_100(self):
        # Review 26.11: a timed-out scan kept the alphabetical start; "truncated" was true for any 100+ folder.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = time.time()
            for index in range(150):
                (root / f'f{index:03}.txt').write_text('x', encoding='utf-8')
                os.utime(root / f'f{index:03}.txt', (now - 86400 * (150 - index), now - 86400 * (150 - index)))
            task = TaskFiles('What is in "' + str(root) + '"')
            listing = json.loads(task.execute(dict(path=str(root), destination='', text='', operation='list')).message)
        self.assertEqual((listing['count'], listing['truncated'], len(listing['items'])), (150, False, 100))
        self.assertEqual(Path(listing['items'][0]['path']).name, 'f149.txt')  # alphabetically last, newest


class TruncatedReadArgumentsTests(unittest.TestCase):
    """Gemini drops a final "" and the closing brace of arguments_json (seen live 26.11 on
    "what did I write in notepad"); a read has no effect, so its tail may be repaired."""
    def plan(self, encoded):
        import test_ai_layer as fixtures
        from test_task_understanding import goal, task
        from aidy.ai_tasks import unpack_task
        value = task(goal('Read Notepad', fixtures.step('notepad_action', {'window': 'w2', 'operation': 'read', 'query': '', 'text': ''})))
        value['goals'][0]['steps'][0]['arguments_json'] = encoded
        return unpack_task(value, {'notepad_action'})

    def test_truncated_read_is_repaired(self):
        plan = self.plan('{"window":"w2","operation":"read","query":"","text":')
        self.assertEqual(plan['steps'][0]['arguments'], {'window': 'w2', 'operation': 'read', 'query': '', 'text': ''})

    def test_truncated_effect_is_still_rejected(self):
        with self.assertRaises(ValueError):
            self.plan('{"window":"w2","operation":"append","query":"","text":')


class SearchKeyTests(unittest.TestCase):
    """Live 26.11: file_action search arrived with explorer_action's key "query" instead of "text"
    in 3 of 4 plans, and every retry repeated it."""
    def plan(self, tool, arguments):
        from aidy.ai_tools import Registry
        return Registry([]).plan({'clarification': '', 'steps': [
            {'tool': tool, 'arguments': arguments, 'delay_seconds': 0}]})['steps'][0]['arguments']

    def test_read_search_accepts_the_sibling_query_key(self):
        self.assertEqual(self.plan('file_action', {'operation': 'search', 'path': 'C:\\D', 'destination': '', 'query': 'notes'}),
                         {'operation': 'search', 'path': 'C:\\D', 'destination': '', 'text': 'notes'})
        self.assertEqual(self.plan('explorer_action', {'operation': 'search', 'path': 'documents', 'text': 'resume'}),
                         {'operation': 'search', 'path': 'documents', 'query': 'resume'})

    def test_effects_keep_the_exact_keys(self):
        from aidy.ai_tools import AIError
        with self.assertRaises(AIError):
            self.plan('file_action', {'operation': 'write', 'path': 'C:\\D\\a.txt', 'destination': '', 'query': 'hello'})


class FolderReadCriterionTests(unittest.TestCase):
    """Live 26.11: the model gives "what's in Downloads" and "what does the readme in Downloads say"
    the criterion file_read:<Downloads folder>; listing never satisfied it and the agent asked
    task_complete until the 12-call limit."""
    CRITERION = {'kind': 'file_read', 'target': 'C:\\D'}

    @staticmethod
    def row(tool, operation, path):
        return {'status': 'verified', 'step': {'tool': tool, 'arguments': {'operation': operation, 'path': path}}}

    def test_listing_reads_a_folder_only_when_no_followup_is_needed(self):
        from aidy.ai_tasks import criterion_satisfied
        listed = [self.row('explorer_action', 'list', 'C:\\D')]
        self.assertFalse(criterion_satisfied(self.CRITERION, listed))  # default: unchanged rule
        self.assertTrue(criterion_satisfied(self.CRITERION, listed, folder_listing=True))
        self.assertFalse(criterion_satisfied(self.CRITERION, listed, folder_listing=False))

    def test_reading_a_file_inside_reads_the_folder(self):
        from aidy.ai_tasks import criterion_satisfied
        inside = [self.row('file_action', 'read', 'C:\\D\\README.txt')]
        self.assertTrue(criterion_satisfied(self.CRITERION, inside, folder_listing=False))
        self.assertFalse(criterion_satisfied(self.CRITERION, [self.row('file_action', 'read', 'C:\\Dx\\a.txt')], folder_listing=True))

    def test_short_folder_name_counts(self):
        from unittest.mock import patch
        from aidy.ai_tasks import criterion_satisfied
        with patch('aidy.ai_files.known_folders', return_value={'downloads': 'C:\\D'}):
            self.assertTrue(criterion_satisfied(self.CRITERION, [self.row('explorer_action', 'list', 'downloads')],
                                                folder_listing=True))

    def test_folder_question_finishes_after_one_listing(self):
        import test_ai_layer as fixtures
        from test_task_understanding import goal, task
        from aidy.ai_engine import AIEngine, Result
        from aidy.ai_tasks import unpack_task
        from aidy.ai_tools import Registry
        from unittest.mock import Mock
        value = goal('List the D folder', fixtures.step('explorer_action', {'operation': 'list', 'path': 'C:\\D', 'query': ''}))
        value['criteria'] = [dict(self.CRITERION, source_goal='')]
        plan = unpack_task(task(value), set(Registry([]).descriptions()))
        engine = AIEngine(fixtures.FakeProvider(plan), Registry([]), lambda *a: True, lambda *a: True,
                          Mock(return_value=Result('verified', '{"items": [], "truncated": false}')))
        engine.start_agent = lambda text: None
        engine.refresh_agent = lambda ref: None
        result = engine.run("what's in the D folder", ('local', 'Admin'))  # one plan only: no task_complete loop
        self.assertTrue(result.ok, result.message)


class PlannerRoutingTests(unittest.TestCase):
    def test_data_questions_are_read_not_written(self):
        # Live 25.11: "summarize the file meeting.txt" was planned as read + document_write (a Notepad draft).
        import threading
        from unittest.mock import Mock
        import test_ai_layer as fixtures
        from test_conversation import calls, chat
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        from aidy.ai_tools import Registry
        session = Mock()
        session.request.side_effect = [fixtures.HTTPResponse(payload=calls(chat()))]
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), session)
        self.addCleanup(provider.close)
        provider.interpret('summarize the file meeting.txt', [], Registry(['discord']), threading.Event())
        text = session.request.call_args.kwargs['json']['systemInstruction']['parts'][0]['text']
        self.assertIn('is answered aloud: plan only the reads', text)
        self.assertIn('never document_write unless the user asks to write', text)


class AnswerRequestTests(unittest.TestCase):
    READ = {'tool': 'file_action', 'operation': 'list', 'target': 'C:\\Downloads',
            'text': '{"items": [{"path": "C:\\\\Downloads\\\\budget.xlsx"}], "truncated": false}'}

    def provider(self, payload):
        from unittest.mock import Mock
        import test_ai_layer as fixtures
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        self.session = Mock()
        self.session.request.side_effect = [fixtures.HTTPResponse(payload=fixtures.response(payload))]
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), self.session)
        self.addCleanup(provider.close)
        return provider

    def ask(self, payload, reads=None):
        import threading
        return self.provider(payload).answer_data("what's in my downloads", reads or [self.READ], [], threading.Event())

    def body(self):
        return self.session.request.call_args.kwargs['json']

    def test_answer_request_has_no_tools_and_marks_data_untrusted(self):
        from aidy.ai_provider import PERSONA
        self.ask({'reply': 'One spreadsheet, budget.', 'tone': 'neutral', 'detail': False})
        body = self.body()
        self.assertNotIn('tools', body)
        self.assertNotIn('toolConfig', body)
        self.assertEqual(set(body['generationConfig']['responseJsonSchema']['properties']), {'reply', 'tone', 'detail'})
        context = json.loads(body['contents'][0]['parts'][0]['text'])
        self.assertEqual(context['results_untrusted'][0]['text'], self.READ['text'])
        system = body['systemInstruction']['parts'][0]['text']
        self.assertIn(PERSONA, system)
        self.assertIn('untrusted', system)

    def test_answer_data_is_capped(self):
        big = dict(self.READ, text='x' * 10000)
        self.ask({'reply': 'Lots of x.', 'tone': 'neutral', 'detail': False}, [big, big, big])
        texts = [r['text'] for r in json.loads(self.body()['contents'][0]['parts'][0]['text'])['results_untrusted']]
        self.assertTrue(all(len(t) <= 6000 for t in texts))
        self.assertLessEqual(sum(map(len, texts)), 12000)

    def test_answer_is_validated(self):
        from aidy.ai_tools import AIError
        for bad in ({'reply': 'x' * 1001, 'tone': 'neutral', 'detail': False},
                    {'reply': 'Fine.', 'tone': 'angry', 'detail': False},
                    {'reply': 'Fine.', 'tone': 'neutral', 'detail': 'yes'}):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(AIError):
                self.ask(bad)
        self.assertEqual(self.ask({'reply': 'One spreadsheet.', 'tone': 'warm', 'detail': True}),
                         {'reply': 'One spreadsheet.', 'tone': 'warm', 'detail': True})


class EngineAnswerTests(unittest.TestCase):
    """A turn that ends on data Aidy read gets a spoken answer from a second, tool-free request."""
    ANSWER = {'reply': 'It says buy milk and call Anna.', 'tone': 'neutral', 'detail': False}

    def engine(self, payload, *results, answer=ANSWER):
        from unittest.mock import Mock
        import test_ai_layer as fixtures
        from aidy.ai_engine import AIEngine
        from aidy.ai_tools import Registry

        class Provider(fixtures.FakeProvider):
            def __init__(self, *plans):
                super().__init__(*plans)
                self.answers = []

            def answer_data(self, request, reads, history, cancel, memory=()):
                self.answers.append(reads)
                return answer(self.engine) if callable(answer) else answer

        provider = Provider(payload)
        engine = AIEngine(provider, Registry([]), lambda *a: True, lambda *a: True, Mock(side_effect=list(results)))
        provider.engine = engine
        engine.start_agent = lambda text: None
        engine.refresh_agent = lambda ref: None
        return engine, provider

    @staticmethod
    def file_step(operation, path, text=''):
        import test_ai_layer as fixtures
        return fixtures.step('file_action', {'operation': operation, 'path': path, 'destination': '', 'text': text})

    def run_read(self, *results, answer=ANSWER, steps=None):
        from test_task_understanding import goal, planned
        steps = steps or [self.file_step('read', 'C:\\todo.txt')]
        engine, provider = self.engine(planned(goal('Read todo.txt', *steps)), *results, answer=answer)
        return engine.run('read the file todo.txt', ('local', 'Admin')), engine, provider

    def test_read_question_gets_a_spoken_answer(self):
        from aidy.ai_engine import Result
        result, engine, provider = self.run_read(Result('verified', 'Buy milk\nCall Anna'))
        self.assertEqual((result.status, result.message), ('answered', self.ANSWER['reply']))
        self.assertEqual(result.data['raw'], 'Buy milk\nCall Anna')
        self.assertIs(result.data['detail'], False)
        self.assertEqual(provider.answers[0], [{'tool': 'file_action', 'operation': 'read',
                                                'target': 'C:\\todo.txt', 'text': 'Buy milk\nCall Anna'}])
        self.assertEqual(engine.history[-1]['aidy_reply'], self.ANSWER['reply'])

    def test_read_then_action_keeps_the_action_result(self):
        from aidy.ai_engine import Result
        steps = [self.file_step('read', 'C:\\todo.txt'), self.file_step('write', 'C:\\copy.txt', 'Buy milk')]
        result, _, provider = self.run_read(Result('verified', 'Buy milk'), Result('verified', 'Written.'), steps=steps)
        self.assertEqual((result.status, result.message), ('verified', 'Written.'))
        self.assertEqual(provider.answers, [])

    def test_answer_failure_keeps_the_raw_result(self):
        from aidy.ai_engine import Result
        from aidy.ai_tools import AIError
        def fail(engine):
            raise AIError('Google request timed out. No action was executed.')
        result, _, _ = self.run_read(Result('verified', '{"items": [{"path": "C:\\\\a.txt"}]}'), answer=fail)
        self.assertEqual((result.status, result.message), ('verified', '{"items": [{"path": "C:\\\\a.txt"}]}'))
        # Live 26.11: a timed-out answer read the raw JSON aloud; the data stays on screen instead.
        self.assertEqual(result.spoken_message, "It's on the screen.")

    def test_cancel_during_answer_is_not_spoken(self):
        from aidy.ai_engine import Result
        from aidy.ai_tools import AIError
        def cancelled(engine):
            engine.cancel_event.set()
            raise AIError('Cancelled.')
        result, _, _ = self.run_read(Result('verified', 'Buy milk'), answer=cancelled)
        self.assertEqual(result.status, 'cancelled')

    # Review 26.11: an action after the read must be the reported outcome, not the old data.
    def test_read_before_an_action_in_another_goal_is_not_answered(self):
        from test_task_understanding import goal, planned
        from aidy.ai_engine import Result
        payload = planned(goal('Read todo.txt', self.file_step('read', 'C:\\todo.txt')),
                          goal('Copy it', self.file_step('write', 'C:\\copy.txt', 'Buy milk'), id='g2'))
        engine, provider = self.engine(payload, Result('verified', 'Buy milk'), Result('verified', 'Written.'))
        result = engine.run('read todo.txt and copy it', ('local', 'Admin'))
        self.assertNotEqual(result.status, 'answered')
        self.assertEqual(provider.answers, [])

    def test_read_text_matching_the_action_message_is_not_answered(self):
        from aidy.ai_engine import Result
        steps = [self.file_step('read', 'C:\\todo.txt'), self.file_step('write', 'C:\\copy.txt', 'Written')]
        result, _, provider = self.run_read(Result('verified', 'Written'), Result('verified', 'Written.'), steps=steps)
        self.assertEqual((result.status, result.message), ('verified', 'Written.'))
        self.assertEqual(provider.answers, [])

    def test_spoken_cancel_stops_the_answer_request(self):
        import threading
        from aidy.ai_engine import Result
        release = threading.Event()
        self.addCleanup(release.set)
        def slow(engine):
            engine.network_progress = engine.cancel  # the runtime hears a spoken "stop" while Google answers
            release.wait(3)
            return self.ANSWER
        started = time.monotonic()
        result, _, _ = self.run_read(Result('verified', 'Buy milk'), answer=slow)
        self.assertEqual(result.status, 'cancelled')
        self.assertLess(time.monotonic() - started, 2)

    def test_malformed_answer_keeps_the_read(self):
        from aidy.ai_engine import Result
        def malformed(engine):
            raise ValueError('a bare JSON value')  # converse_args on '"Fine."'
        from unittest.mock import patch
        with patch('aidy.logui.warn') as warn:
            result, _, _ = self.run_read(Result('verified', 'Buy milk'), answer=malformed)
        self.assertEqual((result.status, result.spoken_message), ('verified', "It's on the screen."))
        # 26.11: a harness TypeError hid behind this fallback for a whole benchmark; the cause must be logged.
        self.assertIn('ValueError', warn.call_args.args[0])

    def test_plain_action_makes_no_second_call(self):
        import test_ai_layer as fixtures
        from aidy.ai_engine import Result
        step = fixtures.step('explorer_action', {'operation': 'open', 'path': 'documents', 'query': ''})
        result, _, provider = self.run_read(Result('verified', 'Opened.'), steps=[step])
        self.assertEqual((result.status, result.message), ('verified', 'Opened.'))
        self.assertEqual(provider.answers, [])


class ScreenTests(unittest.TestCase):
    def test_raw_data_is_shown_not_spoken(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from aidy.ai_engine import Result
        from aidy.ai_provider import AIConfig
        from aidy.ai_runtime import AIRuntime
        runtime = AIRuntime.__new__(AIRuntime)
        runtime.engine = None
        runtime.config = AIConfig(mode='api')
        runtime.host = SimpleNamespace(voice=Mock(muted=False), _stop_event=threading.Event(), _deafen_after_speak=Mock())
        raw = '{"items": [{"path": "C:\\\\Downloads\\\\budget.xlsx"}], "truncated": false}'
        with patch('aidy.ai_runtime.UI_MODE', True), patch('builtins.print') as output:
            runtime.report(Result('answered', 'You have one spreadsheet.',
                                  data={'tone': 'neutral', 'sources': [], 'detail': False, 'raw': raw}))
        shown = [c.args[0] for c in output.call_args_list if c.args and str(c.args[0]).startswith('AI_RESPONSE:')]
        # One line: the UI bridge reads stdout line by line.
        self.assertEqual(shown, ['AI_RESPONSE:You have one spreadsheet. Data: ' + raw])
        runtime.host.voice.tts_blocking.assert_called_once_with('You have one spreadsheet.', tone='neutral')


if __name__ == '__main__':
    unittest.main()
