"""Ordered typed editor plans and the observed Save As filename boundary."""
import copy
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from aidy.ai_notepad_profile import eligible, workflow_plan
from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
from aidy.ai_tools import AIError, AIPlanError, Registry
from aidy.ai_engine import AIEngine, Result
from aidy.ai_system_apps import SystemApps
from aidy.ai_uia_worker import Automation
from test_ai_layer import HTTPResponse, FakeProvider, response


def action(operation, text='', query='', path='', window=''):
    return dict(operation=operation,text=text,query=query,path=path,window=window)


def workflow(*actions):
    return dict(supported=True,clarification='',actions=list(actions))


class NotepadWorkflows(unittest.TestCase):
    def payload(self):
        return workflow(action('create',text='Hello "Aidy"\nSecond line'),action('append',text='\nExtra 🚀'),
            action('replace',query='Hello',text='Hi'),action('save',path=r'C:\Documents\new draft.txt'))

    def test_quotes_newlines_unicode_and_whitespace_remain_actual_typed_text(self):
        value=workflow_plan(self.payload(),Registry([]))
        self.assertEqual([s['tool'] for s in value['steps']],['document_write','notepad_action','notepad_action','save_document'])
        self.assertEqual(value['steps'][0]['arguments']['text'],'Hello "Aidy"\nSecond line')
        self.assertEqual(value['steps'][1]['arguments']['text'],'\nExtra 🚀')
        self.assertEqual(value['steps'][3]['arguments']['path'],r'C:\Documents\new draft.txt')
        self.assertEqual(value['goals'][3]['criteria'][0]['source_goal'],'g3')
        self.assertTrue(all(s['arguments'].get('window','$last')=='$last' for s in value['steps']))

    def test_quoted_app_names_or_edits_cannot_select_the_profile(self):
        registry=Registry([])
        for text in ['Write "Notepad append hello" in Word', 'Write in Notepad "replace Hello"',
                     'Write a 500 word essay in Notepad and replace Hello']:
            self.assertFalse(eligible(text,registry),text)
        self.assertTrue(eligible('Create a Notepad document and append " test"',registry))
        for text in ['Read the current Notepad document','Find beta in Notepad','Save the Notepad document']:
            self.assertTrue(eligible(text,registry))

    def test_invalid_unused_fields_and_tools_fail_before_any_action(self):
        for bad in [action('execute',text='code'),action('append',text='x',path=r'C:\bad.txt'),
                    action('read',text='unrequested text'),action('save',path=r'C:\draft.txt')]:
            with self.subTest(operation=bad['operation']),self.assertRaises(AIError):
                workflow_plan(workflow(bad),Registry([]))

    def test_a_later_invalid_action_rejects_the_whole_plan(self):
        value=self.payload()
        value['actions'][-1]['code']='untrusted'
        with self.assertRaises(AIPlanError):workflow_plan(value,Registry([]))

    def test_window_assignment_text_is_not_a_window_reference(self):
        value=self.payload()
        value['actions'][1]['window']='window=$last'
        with self.assertRaisesRegex(AIPlanError,'exact string'):
            workflow_plan(value,Registry([]))

    def test_full_task_fallback_cannot_also_contain_executable_actions(self):
        self.assertIsNone(workflow_plan(dict(supported=False,clarification='',actions=[]),Registry([])))
        with self.assertRaises(AIPlanError):
            workflow_plan(dict(supported=False,clarification='',actions=[action('append',text='x')]),Registry([]))

    def test_clarification_has_no_actions(self):
        result=workflow_plan(dict(supported=True,clarification='Which new path?',actions=[]),Registry([]))
        self.assertEqual(result['steps'],[])

    def test_api_receives_typed_text_schema_and_entire_original_request(self):
        session=Mock()
        session.request.return_value=HTTPResponse(payload=response(self.payload()))
        provider=GeminiProvider(AIConfig(mode='api'),Credentials(['test-placeholder']),session)
        text='Create Notepad with "Hello". Append " test". Replace "Hello" with "Hi". Save to "C:\\Documents\\new draft.txt".'
        try:
            value=provider.interpret(text,[],Registry([]),threading.Event())
            self.assertEqual(len(value['steps']),4)
            body=session.request.call_args.kwargs['json']
            schema=body['generationConfig']['responseJsonSchema']
            self.assertEqual(schema['properties']['actions']['items']['properties']['text']['type'],'string')
            self.assertNotIn('arguments_json',str(schema))
            import json
            self.assertEqual(json.loads(body['contents'][0]['parts'][0]['text'])['request'],text)
            self.assertIn('save_document',provider.available_tools(Registry([])))
        finally:provider.close()

    def test_engine_runs_every_typed_action_once_without_an_extra_model_request(self):
        provider=FakeProvider(workflow_plan(self.payload(),Registry([])))
        execute=Mock(return_value=Result('verified','Done.'))
        # A save criterion must be satisfied by its real source binding.
        execute.side_effect=[Result('verified','Written.'),Result('verified','Appended.'),Result('verified','Replaced.'),
                             Result('verified','Saved.',data=dict(source_goal='g3'))]
        engine=AIEngine(provider,Registry([]),lambda *a:True,lambda *a:True,execute)
        engine.start_agent=lambda t:None
        result=engine.run('Create and edit a Notepad document',('local','Admin'))
        self.assertTrue(result.ok,result.message)
        self.assertEqual(execute.call_count,4)
        self.assertEqual(len(provider.seen),1)

    def test_failed_edit_stops_before_save(self):
        provider=FakeProvider(workflow_plan(self.payload(),Registry([])))
        execute=Mock(side_effect=[Result('verified','Written.'),Result('failed','Readback mismatch.')])
        engine=AIEngine(provider,Registry([]),lambda *a:True,lambda *a:True,execute)
        engine.start_agent=lambda t:None
        self.assertFalse(engine.run('Create and edit Notepad',('local','Admin')).ok)
        self.assertEqual([c.args[0]['tool'] for c in execute.call_args_list],['document_write','notepad_action'])

    def test_read_answer_is_preserved_when_a_later_goal_saves_the_document(self):
        value=workflow_plan(workflow(action('read'),action('save',path=r'C:\Documents\new.txt')),Registry([]))
        provider=FakeProvider(value)
        execute=Mock(side_effect=[Result('verified','Alpha beta gamma'),Result('verified','Saved.',data=dict(source_goal='g1'))])
        engine=AIEngine(provider,Registry([]),lambda *a:True,lambda *a:True,execute)
        engine.start_agent=lambda t:None
        result=engine.run('Read the Notepad document and save a copy',('local','Admin'))
        self.assertTrue(result.ok,result.message)
        self.assertIn('Alpha beta gamma',result.message)

    def rejected_before_effect_engine(self, preparation=False):
        from test_ai_layer import step, plan
        edit=step('notepad_action',dict(window='w1',operation='append',query='',text=' test'))
        provider=FakeProvider(plan(edit),plan(copy.deepcopy(edit)),plan(step('task_complete',{})))
        execute=Mock(return_value=Result('verified','Edited.'))
        engine=AIEngine(provider,Registry([]),lambda *a:True,lambda *a:True,execute)
        engine.start_agent=lambda t:None
        engine.refresh_agent=Mock()
        if preparation:
            engine.prepare_agent_step=Mock(side_effect=[AIPlanError('Stale reference before input.'),copy.deepcopy(edit)])
        else:
            execute.side_effect=[AIPlanError('Rejected before input.'),Result('verified','Edited.')]
        return engine,execute

    def test_preparation_rejection_does_not_consume_a_never_sent_action(self):
        engine,execute=self.rejected_before_effect_engine(preparation=True)
        result=engine.run('Append to the selected editor',('local','Admin'))
        self.assertTrue(result.ok,result.message)
        execute.assert_called_once()

    def test_executor_pre_action_rejection_allows_the_corrected_identical_action(self):
        engine,execute=self.rejected_before_effect_engine()
        result=engine.run('Append to the selected editor',('local','Admin'))
        self.assertTrue(result.ok,result.message)
        self.assertEqual(execute.call_count,2)

    def test_verified_read_or_edit_binds_the_correct_source_document(self):
        for operation in ['read','append','replace']:
            window=dict(hwnd=1,pid=2,process='notepad.exe')
            task=SimpleNamespace(reference=lambda r:'w2',target=lambda r:window,
                engine=SimpleNamespace(registry=SimpleNamespace(task_progress=dict(current_goal='g3'))),
                call=Mock(return_value=dict(status='verified',message='Read back.',document_text='Hi',data=dict(characters=2))),
                typed={},documents_by_goal={},views={},input_window=None)
            result=SystemApps(task).execute(dict(tool='notepad_action',arguments=dict(window='$last',operation=operation,query='',text='')))
            self.assertTrue(result.ok)
            self.assertEqual(task.typed,{'w2':'Hi'})
            self.assertEqual(task.documents_by_goal,{'g3':'w2'})

    def filename_automation(self, value):
        field=SimpleNamespace(CurrentControlType=50004,CurrentAutomationId='1001',SetFocus=Mock())
        worker=Automation.__new__(Automation)
        worker.client=SimpleNamespace(GetFocusedElement=lambda:field,CompareElements=lambda a,b:a is b)
        worker.check=Mock()
        worker.effect=Mock()
        worker.set_text=Mock()
        worker.keys=Mock()
        worker.text=Mock(return_value=value)
        return worker,field

    def test_filename_requires_real_input_focus_commit_and_exact_readback(self):
        worker,field=self.filename_automation(r'C:\Drafts\new.txt')
        worker.set_filename(dict(hwnd=1,pid=2),field,r'C:\Drafts\new.txt')
        worker.set_text.assert_called_once_with(dict(hwnd=1,pid=2),field,r'C:\Drafts\new.txt')
        worker.keys.assert_called_once_with(dict(hwnd=1,pid=2),'tab')
        worker.effect.assert_called_once()

    def test_changed_filename_field_is_rejected_without_input(self):
        worker,field=self.filename_automation('')
        field.CurrentAutomationId='other'
        with self.assertRaises(AIError):worker.set_filename(dict(hwnd=1,pid=2),field,r'C:\Drafts\new.txt')
        worker.set_text.assert_not_called()


if __name__=='__main__':unittest.main()
