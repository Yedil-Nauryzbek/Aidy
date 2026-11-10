"""Whole utterances become persistent goals; no keyword-based task splitting."""
import copy
import json
import threading
import unittest
from unittest.mock import Mock

from aidy.ai_tasks import unpack_task,criterion_satisfied
from aidy.ai_tools import Registry,AIError,AIPlanError
from aidy.ai_engine import AIEngine,Result
import test_ai_layer as fixtures
import test_document_workflows as native_fixtures


def goal(description,*actions,followup=False,required=None,id='g1'):
    required=required if required is not None else list(dict.fromkeys(s['tool'] for s in actions))
    return dict(id=id,description=description,requires_followup=followup,
                required_tools=required,criteria=[dict(kind='tool_verified',target=t) for t in required],steps=list(actions))


def task(*goals,continuation='new_task'):
    value=dict(continuation=continuation,clarification='',goals=copy.deepcopy(list(goals)))
    for g in value['goals']:
        for action in g['steps']:
            action['arguments_json']=json.dumps(action.pop('arguments'))
    return value


def planned(*goals,**kwargs):
    return unpack_task(task(*goals,**kwargs),set(Registry([]).descriptions()))


class SemanticPlanTests(unittest.TestCase):
    provider=native_fixtures.NativePlanningTests.provider

    def test_one_native_call_contains_both_reported_app_goals(self):
        payload=task(goal('Open the ChatGPT desktop app',fixtures.step('open_app',{'app':'chatgpt_app'})),
                     goal('Then open WhatsApp',fixtures.step('open_app',{'app':'whatsapp_web'}),id='g2'))
        provider=self.provider(native_fixtures.native('plan_task',payload))
        plan=provider.interpret('open the chatgpt up and then open the whatsapp',[],Registry(['chatgpt_app','whatsapp_web']),threading.Event())
        self.assertEqual([s['arguments']['app'] for s in plan['steps']],['chatgpt_app','whatsapp_web'])
        self.assertEqual(len(plan['goals']),2)
        self.assertEqual(self.session.request.call_count,1)

    def test_entire_task_is_rejected_before_any_action_if_later_arguments_are_invalid(self):
        payload=task(goal('Open ChatGPT',fixtures.step('open_app',{'app':'chatgpt_app'})),
                     goal('Open WhatsApp',fixtures.step('open_app',{'app':'whatsapp_web','shell':'anything'}),id='g2'))
        provider=self.provider(native_fixtures.native('plan_task',payload))
        with self.assertRaises(AIPlanError):
            provider.interpret('Open ChatGPT and then WhatsApp',[],Registry(['chatgpt_app','whatsapp_web']),threading.Event())

    def test_unknown_unobserved_tools_and_duplicate_argument_keys_are_rejected(self):
        for name,args in [('powershell',{}),('ui_action',{}),('open_app',None)]:
            payload=task(goal('A task',fixtures.step(name,args or {})))
            if args is None:
                payload['goals'][0]['steps'][0]['arguments_json']='{"app":"one","app":"two"}'
            provider=self.provider(native_fixtures.native('plan_task',payload))
            with self.subTest(tool=name),self.assertRaises(AIError):
                provider.interpret('Do this task',[],Registry(['one','two']),threading.Event())

    def test_continuation_cannot_silently_discard_an_unfinished_goal(self):
        payload=task(goal('Answered goal',fixtures.step('open_app',{'app':'notepad'})),continuation='continue_task')
        provider=self.provider(native_fixtures.native('plan_task',payload))
        registry=Registry(['notepad']);registry.pending_task=dict(goals=[dict(id='g1'),dict(id='g2')])
        with self.assertRaises(AIPlanError):
            provider.interpret('Yes, that one',[],registry,threading.Event())

    def test_goal_ids_and_step_coverage_are_strict(self):
        value=planned(goal('First',fixtures.step()),goal('Second',fixtures.step(),id='g2'))
        value['goals'][1]['step_start']=0
        with self.assertRaises(AIPlanError):Registry(['discord']).plan(value)
        payload=task(goal('First',fixtures.step()),goal('Second',fixtures.step()))
        with self.assertRaises(ValueError):unpack_task(payload,{'open_app'})

    def test_saved_outcome_does_not_confuse_reading_with_writing(self):
        criterion=dict(kind='file_saved',target=r'C:\draft.txt')
        read=dict(step=fixtures.step('file_action',dict(operation='read',path=r'C:\draft.txt',destination='',text='')),status='verified')
        saved=dict(step=fixtures.step('save_document',dict(window='w1',path=r'C:\draft.txt')),status='verified')
        self.assertFalse(criterion_satisfied(criterion,[read]))
        self.assertTrue(criterion_satisfied(criterion,[saved]))
        saved['status']='dispatched'
        self.assertFalse(criterion_satisfied(criterion,[saved]))

    def test_model_quote_wrappers_do_not_change_explicit_file_grants(self):
        import tempfile
        from pathlib import Path
        from aidy.ai_files import TaskFiles
        with tempfile.TemporaryDirectory() as folder:
            selected=Path(folder)/'draft.txt'
            files=TaskFiles(f'Save it to "{selected}"')
            self.assertEqual(files.path('"'+str(selected)+'"',write=True),selected.resolve())
            self.assertEqual(files.path('\\"'+str(selected)+'\\"',write=True),selected.resolve())
            with self.assertRaises(AIError):files.path('"'+str(Path(folder)/'other.txt')+'"',write=True)

    def test_save_criterion_requires_the_correct_document_source(self):
        criterion=dict(kind='file_saved',target=r'C:\draft.txt',source_goal='g1')
        saved=dict(step=fixtures.step('save_document',dict(window='w1',path=r'C:\draft.txt')),status='verified',
                   verified_state=dict(source_goal='g2'))
        self.assertFalse(criterion_satisfied(criterion,[saved]))
        saved['verified_state']['source_goal']='g1'
        self.assertTrue(criterion_satisfied(criterion,[saved]))

    def test_save_reuses_verified_document_instead_of_stale_generated_file_text(self):
        from aidy.ai_automation import DesktopTask
        from types import SimpleNamespace
        from pathlib import Path
        task=DesktopTask.__new__(DesktopTask)
        task.runtime=SimpleNamespace(engine=SimpleNamespace(registry=SimpleNamespace(task_progress=dict(
            current_goal_spec=dict(criteria=[dict(kind='file_saved',target=r'C:\draft.txt',source_goal='g1')])))))
        task.typed={'w1':'The final length-corrected essay.'}
        task.documents_by_goal={'g1':'w1'}
        task.files=Mock();task.files.path.side_effect=lambda value,**kw:Path(value)
        window=dict(hwnd=1,pid=2);task.target=Mock(return_value=window)
        task.save=Mock(return_value=Result('verified','Saved.'))
        task.execute(fixtures.step('file_action',dict(operation='write',path=r'C:\draft.txt',destination='',text='Stale earlier draft.')))
        task.files.execute.assert_not_called()
        task.save.assert_called_once_with(window,'w1',r'C:\draft.txt')

    def test_compact_context_preserves_completed_goals_for_first_one_followups(self):
        from aidy.ai_provider import compact_history
        history=[dict(request='Open ChatGPT and WhatsApp',task_goals=[
            dict(id='g1',goal='Open ChatGPT',status='verified',criteria=[],verified_state=dict(window='w1')),
            dict(id='g2',goal='Open WhatsApp',status='dispatched',criteria=[])])]
        result=compact_history(history)
        self.assertEqual([g['id'] for g in result[0]['task_goals']],['g1','g2'])
        self.assertEqual(result[0]['task_goals'][0]['verified_state']['window'],'w1')


class GoalExecutionTests(unittest.TestCase):
    def engine(self,*plans):
        self.provider=fixtures.FakeProvider(*plans)
        self.execute=Mock(return_value=Result('verified','Done.'))
        self.confirm=Mock(return_value=True)
        registry=Registry(['chatgpt_app','whatsapp_web','notepad','discord'])
        engine=AIEngine(self.provider,registry,lambda *a:True,self.confirm,self.execute)
        engine.start_agent=Mock()
        engine.refresh_agent=Mock()
        return engine

    def test_both_app_goals_execute_in_order_without_a_second_model_call(self):
        value=planned(goal('Open ChatGPT',fixtures.step('open_app',{'app':'chatgpt_app'})),
                      goal('Open WhatsApp',fixtures.step('open_app',{'app':'whatsapp_web'}),id='g2'))
        engine=self.engine(value)
        result=engine.run('Open ChatGPT and then WhatsApp',('local','Admin'))
        self.assertEqual(result.status,'verified')
        self.assertIn('2 of 2',result.message)
        self.assertEqual([c.args[0]['arguments']['app'] for c in self.execute.call_args_list],['chatgpt_app','whatsapp_web'])
        self.assertEqual(engine.timings['model_calls'],1)
        self.assertEqual(engine.start_agent.call_count,1)
        self.assertEqual(len([key for key in engine.completed_ids if ':agent:' in key]),2)

    def test_atomic_writing_cannot_finish_the_whole_task_before_another_app_goal(self):
        write=fixtures.step('document_write',dict(application='notepad',new_document=True,text='A useful essay.',word_count=3,finish_task=True))
        value=planned(goal('Write an essay in Notepad',write),
                      goal('Open Discord',fixtures.step('open_app',{'app':'discord'}),id='g2'))
        engine=self.engine(value)
        self.assertEqual(engine.run('Write an essay and open Discord',('local','Admin')).status,'verified')
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['document_write','open_app'])

    def test_required_save_prevents_premature_goal_completion(self):
        write=fixtures.step('document_write',dict(application='notepad',new_document=True,text='A useful essay.',word_count=3,finish_task=True))
        save=fixtures.step('save_document',dict(window='w1',path='C:\\draft.txt'))
        value=planned(goal('Write an essay and save it',write,followup=True,required=['document_write','save_document']),
                      goal('Open Discord',fixtures.step('open_app',{'app':'discord'}),id='g2'))
        engine=self.engine(value,fixtures.plan(fixtures.step('task_complete',{})),fixtures.plan(save),fixtures.plan(fixtures.step('task_complete',{})))
        self.assertEqual(engine.run('Write, save, and open Discord',('local','Admin')).status,'verified')
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['document_write','save_document','open_app'])

    def test_bound_tail_within_one_goal_is_retained(self):
        value=planned(goal('Open two apps',fixtures.step('open_app',{'app':'chatgpt_app'}),fixtures.step('open_app',{'app':'whatsapp_web'})))
        engine=self.engine(value)
        self.assertTrue(engine.run('Open these two apps',('local','Admin')).ok)
        self.assertEqual(self.execute.call_count,2)
        self.assertEqual(engine.timings['model_calls'],1)

    def test_partial_failure_reports_remaining_goal_and_does_not_dispatch_it_twice(self):
        value=planned(goal('Open ChatGPT',fixtures.step('open_app',{'app':'chatgpt_app'})),
                      goal('Open WhatsApp',fixtures.step('open_app',{'app':'whatsapp_web'}),id='g2'))
        engine=self.engine(value)
        self.execute.side_effect=[Result('verified','Opened.'),Result('failed','Application unavailable.')]
        result=engine.run('Open both apps',('local','Admin'))
        self.assertEqual(result.status,'failed')
        self.assertIn('1 of 2',result.message)
        self.assertIn('WhatsApp',result.message)
        self.assertEqual(self.execute.call_count,2)

    def test_one_task_action_limit_covers_all_goals(self):
        from dataclasses import replace
        value=planned(goal('First',fixtures.step('open_app',{'app':'notepad'})),goal('Second',fixtures.step('open_app',{'app':'discord'}),id='g2'))
        engine=self.engine(value);engine.provider.config=replace(engine.provider.config,max_actions=1)
        self.assertFalse(engine.run('Open both',('local','Admin')).ok)
        self.execute.assert_not_called()

    def test_missing_amount_blocks_the_whole_mixed_task(self):
        value=planned(goal('Open Notepad',fixtures.step('open_app',{'app':'notepad'})),
                      goal('Increase volume',fixtures.step('volume_change',{'direction':'up','steps':1}),id='g2'))
        engine=self.engine(value)
        result=engine.run('Open Notepad and increase volume',('local','Admin'))
        self.assertEqual(result.status,'clarification')
        self.execute.assert_not_called()

    def test_clarification_retains_later_goal_and_replies_continue_it(self):
        initial=planned(goal('Open the requested editor',fixtures.step('observe_desktop',{'window':''}),followup=True,required=['open_app']),
                        goal('Open Discord afterwards',fixtures.step('open_app',{'app':'discord'}),id='g2'))
        resumed=planned(goal('Open Notepad',fixtures.step('open_app',{'app':'notepad'})),
                        goal('Open Discord afterwards',fixtures.step('open_app',{'app':'discord'}),id='g2'),continuation='continue_task')
        engine=self.engine(initial,fixtures.plan(question='Which editor?'),resumed)
        self.assertEqual(engine.run('Open my editor and then Discord',('local','Admin')).status,'clarification')
        self.assertEqual([g['id'] for g in engine.pending_task['goals']],['g1','g2'])
        self.assertEqual(engine.run('Notepad',('local','Admin')).status,'verified')
        self.assertEqual([c.args[0]['arguments'].get('app') for c in self.execute.call_args_list if c.args[0]['tool']=='open_app'],['notepad','discord'])

    def test_other_identity_cannot_resume_a_pending_task(self):
        value=planned(goal('Open Notepad',fixtures.step('open_app',{'app':'notepad'})),continuation='continue_task')
        engine=self.engine(value);engine.identity=('owner','Admin')
        engine.pending_task=dict(original_request='Open Notepad',goals=[dict(id='g1')])
        engine.pending_until=__import__('time').monotonic()+20
        self.assertFalse(engine.run('Yes',('different','Admin')).ok)
        self.execute.assert_not_called()

    def test_unknown_editor_effect_stops_later_goal(self):
        write=fixtures.step('document_write',dict(application='notepad',new_document=True,text='Draft.',word_count=0,finish_task=False))
        value=planned(goal('Write in Notepad',write),goal('Open Discord',fixtures.step('open_app',{'app':'discord'}),id='g2'))
        engine=self.engine(value)
        self.execute.return_value=Result('dispatched','Unknown final text.')
        result=engine.run('Write then open Discord',('local','Admin'))
        self.assertEqual(result.status,'dispatched')
        self.assertIn('remaining goals stopped',result.message)
        self.assertEqual(self.execute.call_count,1)

    def test_verified_save_finishes_goal_even_if_planner_named_another_file_tool(self):
        first=goal('Save the draft',fixtures.step('observe_desktop',{'window':''}),followup=True,required=['file_action'])
        first['criteria']=[dict(kind='file_saved',target=r'C:\draft.txt')]
        second=goal('Open Discord',fixtures.step('open_app',{'app':'discord'}),id='g2')
        save=fixtures.step('save_document',dict(window='w1',path=r'C:\draft.txt'))
        engine=self.engine(planned(first,second),fixtures.plan(save))
        self.assertEqual(engine.run('Save and open Discord',('local','Admin')).status,'verified')
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['observe_desktop','save_document','open_app'])
        self.assertEqual(engine.timings['model_calls'],2)

    def test_unverified_new_app_does_not_retain_an_unrelated_close_target(self):
        import time
        engine=self.engine(planned(goal('Open WhatsApp',fixtures.step('open_app',{'app':'whatsapp_web'}))))
        engine.identity=('local','Admin');engine.context_until=time.monotonic()+300
        engine.last_app='notepad';engine.last_window='w1'
        engine.registry.windows=[dict(id='w1',process='notepad.exe',title='User document')]
        self.execute.return_value=Result('dispatched','Opening WhatsApp.')
        self.assertTrue(engine.run('Open WhatsApp',('local','Admin')).ok)
        self.assertEqual(engine.last_app,'whatsapp_web')
        self.assertIsNone(engine.last_window)

    def test_atomic_effect_does_not_replan_into_a_future_timer_goal(self):
        value=planned(goal('Set volume',fixtures.step('set_volume',{'value':25}),followup=True),
                      goal('Start timer',fixtures.step('timer_start',{'seconds':600}),id='g2'))
        engine=self.engine(value)
        self.assertTrue(engine.run('Set volume to 25 and start a timer for ten minutes',('local','Admin')).ok)
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['set_volume','timer_start'])
        self.assertEqual(engine.timings['model_calls'],1)

    def test_replan_cannot_execute_a_later_goal_before_current_ui_work(self):
        value=planned(goal('Finish current interface task',fixtures.step('observe_desktop',{'window':''}),followup=True,required=['ui_action']),
                      goal('Open Discord afterwards',fixtures.step('open_app',{'app':'discord'}),id='g2'))
        ui=fixtures.step('ui_action',dict(window='w1',element='e1',operation='invoke',text=''))
        engine=self.engine(value,fixtures.plan(fixtures.step('open_app',{'app':'discord'})),fixtures.plan(ui),fixtures.plan(fixtures.step('task_complete',{})))
        self.assertTrue(engine.run('Do the interface task and then open Discord',('local','Admin')).ok)
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['observe_desktop','ui_action','open_app'])

    def test_fixed_important_operation_in_observed_task_keeps_confirmation(self):
        restart=fixtures.step('system_action',{'action':'restart'})
        value=planned(goal('Inspect desktop',fixtures.step('observe_desktop',{'window':''})),
                      goal('Restart Windows',restart,id='g2'))
        engine=self.engine(value)
        self.assertTrue(engine.run('Inspect desktop and restart Windows',('local','Admin')).ok)
        self.confirm.assert_called_once_with(restart,('local','Admin'))
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['observe_desktop','system_action'])


if __name__=='__main__':unittest.main()
