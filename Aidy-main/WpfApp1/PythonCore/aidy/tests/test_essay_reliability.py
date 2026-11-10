"""Reported accented-ASR transcripts and length policy across planning/execution."""
import json
import threading
import unittest

from aidy.ai_writing import writing_hints, fit_near_length
from aidy.ai_tools import Registry, AIPlanError
from aidy.ai_engine import AIEngine, Result
import test_document_workflows as fixtures

native = fixtures.native


REPORTED = [
    ('open a notepad in the right in the say about google for 500 words', 500, 'and write an essay about google'),
    ('write and i say you about google two hundred and fifty words', 250, 'write an essay about google 250 words'),
    ('write in a saying about space 500 world', 500, 'write an essay about space 500 words'),
    ('writing a save for 500 words about economics', 500, 'write an essay for 500 words about economics'),
    ('writing a say about space for 500 words', 500, 'write an essay about space for 500 words'),
]


class WritingHintTests(unittest.TestCase):
    def test_reported_phrases_preserve_topic_count_and_writing_goal(self):
        for raw,count,expected in REPORTED:
            with self.subTest(raw=raw):
                hints=writing_hints(raw)
                self.assertEqual(hints.requested_words,count)
                self.assertIn(expected,hints.normalized)
                self.assertTrue(AIEngine.document_goal(raw))

    def test_negations_literal_dictation_saving_search_and_position_survive(self):
        for raw in ['Write "in a saying about space 500 world" in Notepad.',
                    'Save the essay to "C:\\Drafts\\a save.txt".',
                    'Search for writing a say about space for 500 words',
                    'Move Notepad to the right and say hello']:
            hints=writing_hints(raw)
            # Search requests may be interpreted as prose, but must not be locally
            # rewritten into a new command. Quoted dictation stays literal.
            self.assertEqual(hints.normalized,raw)
        raw="Don't open Notepad; write an essay about space in this empty document, 500 words."
        hints=writing_hints(raw)
        self.assertIn("Don't open Notepad",hints.normalized)
        self.assertFalse(hints.fresh_document)
        self.assertEqual(hints.requested_words,500)
        self.assertIsNone(writing_hints('Write an essay in 250 or 500 words').requested_words)

    def test_spoken_and_hyphenated_lengths_and_exactness(self):
        for raw,count in [('Write a five hundred word essay about space',500),
                          ('Compose a 500-word essay',500),
                          ('Write a one thousand two hundred and fifty word essay',1250)]:
            self.assertEqual(writing_hints(raw).requested_words,count)
        self.assertTrue(writing_hints('Write exactly 500 words about space').exact)
        self.assertFalse(writing_hints('Write 500 words about space').exact)

    def test_length_selection_keeps_whole_sentences_opening_and_conclusion(self):
        sentences=['Opening explains the main idea clearly.'] + [f'Example {i} provides another useful detail about this topic.' for i in range(10)] + ['The conclusion brings these ideas together.']
        draft='\n\n'.join(sentences)
        fitted=fit_near_length(draft,75)
        self.assertLessEqual(abs(len(fitted.split())-75),3)
        self.assertTrue(fitted.startswith(sentences[0]))
        self.assertTrue(fitted.endswith(sentences[-1]))
        self.assertTrue(all(s in sentences for s in fitted.split('\n\n')))
        self.assertEqual(fit_near_length('One indivisible sentence without a suitable removable body.',5),
                         'One indivisible sentence without a suitable removable body.')


class EssayPlanningTests(unittest.TestCase):
    provider = fixtures.NativePlanningTests.provider

    def test_close_length_draft_does_not_make_a_count_repair_call(self):
        text=' '.join(['A coherent draft.']*165) # 495 words
        provider=self.provider(native('document_write',dict(application='notepad',new_document=False,text=text,word_count=0)))
        registry=Registry([])
        raw=REPORTED[0][0]
        result=provider.interpret(raw,[],registry,threading.Event())
        args=result['steps'][0]['arguments']
        self.assertEqual(args['word_count'],500)
        self.assertTrue(args['new_document'])
        self.assertEqual(args['text'],text)
        self.assertEqual(self.session.request.call_count,1)
        context=json.loads(self.session.request.call_args.kwargs['json']['contents'][0]['parts'][0]['text'])
        self.assertEqual(context['request'],raw)
        self.assertIn('write an essay',context['speech_interpretation_hints']['normalized_request'])

    def test_spoken_count_overrides_incorrect_model_count(self):
        provider=self.provider(native('document_write',dict(application='current',new_document=True,text=' '.join(['word']*250),word_count=0)))
        args=provider.interpret(REPORTED[1][0],[],Registry([]),threading.Event())['steps'][0]['arguments']
        self.assertEqual(args['word_count'],250)
        self.assertEqual(self.session.request.call_count,1)

    def test_exact_request_still_corrects_one_word_difference(self):
        provider=self.provider(native('document_write',dict(application='notepad',new_document=True,text=' '.join(['word']*499),word_count=500)),
                               native('insert_words',dict(after_word=2,words=['extra'])))
        args=provider.interpret('Write exactly 500 words about space in Notepad',[],Registry([]),threading.Event())['steps'][0]['arguments']
        self.assertEqual(len(args['text'].split()),500)
        self.assertEqual(self.session.request.call_count,2)

    def test_large_exact_gap_is_expanded_as_prose_before_small_exact_edit(self):
        provider=self.provider(native('document_write',dict(application='notepad',new_document=True,
                               text=' '.join(['word']*450),word_count=500)),
                               native('draft_extension',dict(text=' '.join(['extra']*40))),
                               native('insert_words',dict(after_word=2,words=['added']*10)))
        args=provider.interpret('Write exactly 500 words about space',[],Registry([]),threading.Event())['steps'][0]['arguments']
        self.assertEqual(len(args['text'].split()),500)
        self.assertEqual(self.session.request.call_count,3)

    def test_short_draft_revises_prose_once_and_validates_before_dispatch(self):
        provider=self.provider(native('document_write',dict(application='notepad',new_document=True,text='Too short.',word_count=500)),
                               native('draft_extension',dict(text=' '.join(['word']*488))))
        args=provider.interpret('Write 500 words about space',[],Registry([]),threading.Event())['steps'][0]['arguments']
        self.assertEqual(len(args['text'].split()),490)
        self.assertEqual(self.session.request.call_count,2)
        provider=self.provider(native('document_write',dict(application='notepad',new_document=True,text='Too short.',word_count=500)),
                               native('draft_extension',dict(text='Still too short.')),
                               native('draft_extension',dict(text='Still too short.')))
        with self.assertRaises(AIPlanError):
            provider.interpret('Write 500 words about space',[],Registry([]),threading.Event())

    def test_quoted_count_in_literal_dictation_does_not_generate_an_essay(self):
        provider=self.provider(native('document_write',dict(application='notepad',new_document=True,text='500 words',word_count=500)))
        args=provider.interpret('Write "500 words" in Notepad.',[],Registry([]),threading.Event())['steps'][0]['arguments']
        self.assertEqual(args['text'],'500 words')
        self.assertEqual(args['word_count'],0)
        self.assertEqual(self.session.request.call_count,1)

    def test_generated_paragraphs_are_real_newlines_before_count_and_insertion(self):
        provider=self.provider(native('document_write',dict(application='notepad',new_document=True,
                               text=r'One sentence.\n\nAnother sentence.',word_count=0)))
        args=provider.interpret('Write an essay about space',[],Registry([]),threading.Event())['steps'][0]['arguments']
        self.assertEqual(args['text'],'One sentence.\n\nAnother sentence.')

    def test_five_hundred_word_generation_has_bounded_scaled_deadline(self):
        provider=self.provider()
        self.assertEqual(provider.planning_timeout('Close it',Registry([])),8)
        self.assertEqual(provider.planning_timeout(REPORTED[2][0],Registry([])),45)
        self.assertLessEqual(provider.planning_timeout('Write 2000 words about space',Registry([])),60)


class EssayCompletionTests(unittest.TestCase):
    engine = fixtures.DocumentLoopTests.engine

    def test_verified_atomic_write_finishes_without_another_google_call(self):
        write=fixtures.step('document_write',dict(application='notepad',new_document=True,
                            text='A complete draft.',word_count=3,finish_task=True))
        engine=self.engine(fixtures.plan(write))
        self.execute.return_value=Result('verified','Written (3 words).',data=dict(window='w3',app='notepad',word_count=3))
        result=engine.run('Write an essay in Notepad',('local','Admin'))
        self.assertEqual(result.status,'verified')
        self.assertEqual(result.message,'Written (3 words).')
        self.assertEqual(engine.timings['model_calls'],1)
        self.assertEqual(engine.last_window,'w3')
        engine.refresh_agent.assert_not_called()

    def test_known_remaining_plan_tail_cannot_be_skipped_by_finish_flag(self):
        write=fixtures.step('document_write',dict(application='notepad',new_document=True,
                            text='A complete draft.',word_count=3,finish_task=True))
        save=fixtures.step('save_document',dict(window='w3',path='C:\\draft.txt'))
        engine=self.engine(fixtures.plan(write,save),fixtures.plan(save),fixtures.plan(fixtures.step('task_complete',{})))
        result=engine.run('Write an essay and save it',('local','Admin'))
        self.assertEqual(result.status,'verified')
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['document_write','save_document'])

    def test_failed_editor_action_cannot_finish(self):
        write=fixtures.step('document_write',dict(application='notepad',new_document=True,
                            text='A complete draft.',word_count=3,finish_task=True))
        engine=self.engine(fixtures.plan(write))
        self.execute.return_value=Result('failed','Readback did not match.')
        self.assertEqual(engine.run('Write an essay in Notepad',('local','Admin')).status,'failed')


if __name__ == '__main__':
    unittest.main()
