"""Regressions for reported pronouns, typed calls and composed writing tasks."""
from dataclasses import replace
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from aidy.ai_engine import AIEngine, Result
from aidy.ai_provider import GeminiProvider, AIConfig, Credentials, function_plan
from aidy.ai_tools import Registry, AIError, AIPlanError
from aidy.ai_automation import DesktopTask
from test_ai_layer import FakeProvider, plan, step, HTTPResponse


def native(name, args):
    return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'functionCall':{'name':name,'args':args}}]}}]}


class NativePlanningTests(unittest.TestCase):
    def provider(self, *payloads):
        self.session = Mock()
        self.session.request.side_effect = [HTTPResponse(payload=p) for p in payloads]
        provider = GeminiProvider(AIConfig(mode='api'),Credentials(['placeholder']),self.session)
        self.addCleanup(provider.close)
        return provider

    def test_close_it_uses_object_arguments_and_receives_exact_recent_window(self):
        provider = self.provider(native('close_window',dict(window='w7')))
        registry = Registry(['excel'])
        registry.windows = [dict(id='w7',title='Book1 - Excel',process='excel.exe')]
        registry.last_window,registry.last_app = 'w7','excel'
        result = provider.interpret('Close it',[],registry,threading.Event())
        self.assertEqual(result['steps'][0]['arguments'],{'window':'w7'})
        body = self.session.request.call_args.kwargs['json']
        self.assertNotIn('responseJsonSchema',body['generationConfig'])
        self.assertEqual(body['toolConfig']['functionCallingConfig']['mode'],'ANY')
        context = json.loads(body['contents'][0]['parts'][0]['text'])
        self.assertEqual(context['last_window'],'w7')

    def test_initial_phase_cannot_choose_terminal_or_unobserved_element_tools(self):
        provider = self.provider(native('observe_desktop',dict(window='')))
        provider.interpret('Write a draft in an editor',[],Registry([]),threading.Event())
        declarations = {v['name'] for v in self.session.request.call_args.kwargs['json']['tools'][0]['functionDeclarations']}
        self.assertNotIn('task_complete',declarations)
        self.assertNotIn('ui_action',declarations)
        self.assertIn('plan_task',declarations)
        schema=self.session.request.call_args.kwargs['json']['tools'][0]['functionDeclarations'][0]['parametersJsonSchema']
        self.assertEqual(schema['properties']['goals']['items']['properties']['steps']['items']['properties']['arguments_json']['type'],'string')
        self.assertIn('document_write',provider.available_tools(Registry([])))
        self.assertNotIn('task_complete',provider.available_tools(Registry([])))
        self.assertNotIn('ui_action',provider.available_tools(Registry([])))

    def test_word_display_name_resolves_only_to_a_real_unique_registration(self):
        provider=self.provider(native('open_app',{'app':'Word'}))
        registry=Registry(['installed_word'])
        registry.installed_apps=[dict(id='installed_word',name='Word',source='lnk')]
        result=provider.interpret('Open Word',[],registry,threading.Event())
        self.assertEqual(result['steps'][0]['arguments']['app'],'installed_word')
        provider=self.provider(native('open_app',{'app':'Notepad'}))
        registry=Registry(['notepad','installed_one','installed_two'])
        registry.installed_apps=[dict(id='installed_one',name='Notepad',source='shell'),dict(id='installed_two',name='Notepad',source='exe')]
        result=provider.interpret('Open the text editor',[],registry,threading.Event())
        self.assertEqual(result['steps'][0]['arguments']['app'],'notepad')

    def test_function_names_extra_arguments_and_clarification_mixed_with_effects_rejected(self):
        with self.assertRaises(AIPlanError):
            function_plan(native('python',{'code':'anything'}),{'close_window'})
        registry = Registry([])
        with self.assertRaises(AIError):
            registry.plan(function_plan(native('close_window',{'window':'$active','code':'anything'}),{'close_window'}))
        payload=native('ask_user',{'question':'Which editor?'})
        payload['candidates'][0]['content']['parts'].append(dict(functionCall=dict(name='open_app',args={'app':'notepad'})))
        with self.assertRaises(AIPlanError):
            function_plan(payload,{'ask_user','open_app'})

    def test_exact_length_insertion_is_counted_locally_before_any_document_action(self):
        provider=self.provider(native('insert_words',{'after_word':2,'words':['very','good']}))
        value=provider.fit_draft('A useful example.',5,threading.Event(),8)
        self.assertEqual(value,'A useful very good example.')
        self.assertEqual(len(value.split()),5)

    def test_exact_length_deletion_uses_distinct_in_range_word_indices(self):
        provider=self.provider(native('remove_words',{'indices':[2]}))
        self.assertEqual(provider.fit_draft('A very useful example.',3,threading.Event(),8),'A useful example.')
        provider=self.provider(native('remove_words',{'indices':[2,2]}))
        with self.assertRaises(AIPlanError):
            provider.fit_draft('A very useful nice example.',3,threading.Event(),8)

    def test_length_correction_cannot_insert_a_multiword_item_or_sentence_fragment(self):
        for args in [dict(after_word=2,words=['two words']),dict(after_word=3,words=['and','useful'])]:
            provider=self.provider(native('insert_words',args))
            with self.assertRaises(AIPlanError):
                provider.fit_draft('A useful example.',4 if len(args['words'])==1 else 5,threading.Event(),8)

    def test_native_draft_count_correction_is_part_of_bounded_model_budget(self):
        draft=dict(application='notepad',new_document=True,text='A useful example.',word_count=5)
        provider=self.provider(native('document_write',draft),native('insert_words',{'after_word':2,'words':['very','good']}))
        registry=Registry([])
        registry.planning_calls_remaining=2
        result=provider.interpret('Write a 5-word essay in Notepad',[],registry,threading.Event())
        self.assertEqual(len(result['steps'][0]['arguments']['text'].split()),5)
        self.assertEqual(provider.generation_calls,2)

    def test_no_budget_remains_means_no_untracked_length_repair_call(self):
        provider=self.provider(native('document_write',dict(application='notepad',new_document=True,text='A draft.',word_count=5)))
        registry=Registry([])
        registry.planning_calls_remaining=1
        with self.assertRaises(AIError):
            provider.interpret('Write a 5-word essay',[],registry,threading.Event())
        self.assertEqual(self.session.request.call_count,1)


class DocumentLoopTests(unittest.TestCase):
    def engine(self,*plans):
        self.provider=FakeProvider(*plans)
        self.execute=Mock(return_value=Result('verified','Done.'))
        engine=AIEngine(self.provider,Registry(['notepad']),lambda *a:True,lambda *a:True,self.execute)
        engine.start_agent=Mock()
        engine.refresh_agent=Mock(side_effect=lambda ref: engine.registry.desktop_state.update(interface={'view':str(self.execute.call_count)}))
        self.engine_=engine
        return engine

    def test_open_only_cannot_drop_the_rest_of_a_writing_request(self):
        write=step('document_write',dict(application='notepad',new_document=True,text='A useful essay.',word_count=3))
        engine=self.engine(plan(step('open_app',{'app':'notepad'})),plan(step('task_complete',{})),plan(write),plan(step('task_complete',{})))
        result=engine.run('Open Notepad and write an essay',('local','Admin'))
        self.assertEqual(result.status,'verified')
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['open_app','document_write'])

    def test_recent_document_result_pins_followup_close_to_one_window(self):
        engine=self.engine(plan(step('close_app',{'app':'$last'})))
        engine.identity=('local','Admin');engine.context_until=time.monotonic()+300
        engine.last_app='notepad';engine.last_window='w8'
        engine.registry.windows=[dict(id='w8',title='Untitled',process='notepad.exe')]
        self.assertTrue(engine.run('Close it',('local','Admin')).ok)
        self.assertEqual(self.execute.call_args.args[0]['tool'],'close_window')
        self.assertEqual(self.execute.call_args.args[0]['arguments'],{'window':'w8'})

    def test_validation_rejection_can_replan_without_a_desktop_side_effect(self):
        engine=self.engine(AIPlanError('Use a typed window reference.'),plan(step('open_app',{'app':'notepad'})))
        result=engine.run('Open Notepad',('local','Admin'))
        self.assertTrue(result.ok)
        self.assertEqual(self.execute.call_count,1)
        self.assertEqual(engine.timings['model_calls'],2)

    def test_stale_before_input_can_refresh_and_continue_but_unknown_outcome_stops(self):
        action=step('ui_action',dict(window='w1',element='e1',operation='set_text',text='Draft'))
        engine=self.engine(plan(action),plan(action),plan(step('task_complete',{})))
        engine.registry.desktop_state={'interface':{'view':'old'}}
        self.execute.side_effect=[AIPlanError('Stale before action.'),Result('verified','Focused.')]
        self.assertEqual(engine.run('Enter the draft',('local','Admin')).status,'verified')
        self.assertEqual(self.execute.call_count,2)

    def test_permissions_precede_prerequisite_observation(self):
        engine=self.engine(plan(step('observe_desktop',{'window':''})))
        engine.authorize=lambda *a:False
        engine.prepare_agent_step=Mock(side_effect=AssertionError('unauthorized read'))
        self.assertFalse(engine.run('Inspect editor',('guest','Guest')).ok)
        engine.prepare_agent_step.assert_not_called()


class ElementPreparationTests(unittest.TestCase):
    def test_friendly_window_and_field_references_are_bound_to_real_observed_elements(self):
        runtime=Mock()
        runtime.engine.last_window=None
        runtime.windows.active_ref='w3'
        runtime.windows.targets={'w3':dict(hwnd=30,pid=3,process='notepad.exe',title='Untitled - Notepad')}
        runtime.windows.matches.side_effect=lambda window,target:target=='notepad'
        task=DesktopTask(runtime,'Type a draft')
        task.observe=Mock(side_effect=lambda ref:task.views.update({ref:dict(elements=[dict(id='real',name='Text editor',automation_id='101',control=50030)])}))
        value=task.prepare(step('ui_action',dict(window='notepad',element='Text editor',operation='set_text',text='Draft')))
        self.assertEqual(value['arguments']['window'],'w3')
        self.assertEqual(value['arguments']['element'],'real')
        task.observe.assert_called_once_with('w3')

    def test_invented_element_reference_is_replanned_before_input(self):
        runtime=Mock();runtime.engine.last_window=None
        runtime.windows.targets={'w1':dict(hwnd=1,pid=1,process='notepad.exe',title='Untitled')}
        task=DesktopTask(runtime,'Type a draft')
        task.views['w1']=dict(elements=[dict(id='actual',name='Text editor',automation_id='101',control=50030)])
        with self.assertRaises(AIPlanError):
            task.prepare(step('ui_action',dict(window='w1',element='invented',operation='set_text',text='Draft')))


class WordBoundaryTests(unittest.TestCase):
    def test_new_word_document_uses_an_isolated_registered_instance(self):
        from aidy.ai_documents import WordDocuments
        document=SimpleNamespace(ActiveWindow=SimpleNamespace(Hwnd=17),ProtectionType=-1,Content=SimpleNamespace(Text=''),Activate=Mock(),Name='Test')
        app=Mock();app.Documents.Add.return_value=document
        with patch('win32com.client.DispatchEx',return_value=app) as create, patch('win32com.client.GetActiveObject') as active, patch('aidy.ai_documents.owner',return_value=10),patch('aidy.ai_documents.process_name',return_value='winword.exe'):
            result=WordDocuments().write(None,'A new draft.',True)
        self.assertEqual(result['status'],'verified')
        self.assertEqual(result['window']['hwnd'],17)
        create.assert_called_once_with('Word.Application')
        active.assert_not_called()

    def test_existing_word_instance_must_match_the_selected_native_window(self):
        from aidy.ai_documents import WordDocuments
        document=SimpleNamespace(Content=SimpleNamespace(Text='Existing user draft.'))
        app=SimpleNamespace(ActiveWindow=SimpleNamespace(Hwnd=222),ActiveDocument=document)
        with patch('win32com.client.GetActiveObject',return_value=app),patch('aidy.ai_documents.owner',return_value=10),patch('aidy.ai_documents.process_name',return_value='winword.exe'),self.assertRaises(AIError):
            WordDocuments().write(dict(hwnd=17,pid=10,process='winword.exe'),'Replacement',False)
        self.assertEqual(document.Content.Text,'Existing user draft.')

    def test_long_document_with_blank_prefix_is_not_treated_as_an_empty_editor(self):
        from aidy.ai_uia_worker import Automation
        automation=Automation.__new__(Automation)
        automation.check=Mock()
        automation.element=Mock(return_value=SimpleNamespace(CurrentNativeWindowHandle=17,CurrentClassName='RichEditD2DPT'))
        automation.set_text=Mock()
        with patch('aidy.ai_uia_worker.U.GetWindowTextLengthW',return_value=5000),self.assertRaises(AIError):
            automation.run(dict(window=dict(hwnd=1,pid=10,process='notepad.exe'),operation='set_text',element='real',view='current',text='Draft',require_empty=True))
        automation.set_text.assert_not_called()


if __name__=='__main__':unittest.main()
