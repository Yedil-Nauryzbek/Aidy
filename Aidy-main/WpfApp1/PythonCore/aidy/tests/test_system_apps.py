"""System-app workflows, confirmation binding and recovery after failed turns."""
import copy
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.ai_engine import AIEngine, Result
from aidy.ai_tools import AIError, Registry, needs_confirmation
from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
from aidy.ai_system_apps import SystemApps, arithmetic, worker_run
from aidy.ai_system_profile import target
from test_ai_layer import step, plan, FakeProvider, HTTPResponse, response
from test_task_understanding import goal, planned


class SystemAppTests(unittest.TestCase):
    def test_arithmetic_accepts_numeric_input_and_rejects_code_or_unsupported_math(self):
        for text in ['2+3*4', '(2 + 3) ÷ 4', '-5+2', '0.25*8']:
            self.assertTrue(arithmetic(text))
        for text in ['__import__("os")', '2**8', '1//2', 'a+1', '1;2', '5%', '', '1+', '9'*201]:
            with self.subTest(text=text), self.assertRaises(AIError):
                arithmetic(text)

    def test_read_open_are_unconfirmed_but_empty_and_restore_require_confirmation(self):
        for operation in ['open', 'list', 'empty', 'restore']:
            self.assertEqual(needs_confirmation(step('recycle_bin', dict(operation=operation,item=''))),
                             operation in {'empty','restore'})

    def test_compound_negated_and_editor_requests_keep_full_planner(self):
        registry=Registry([])
        self.assertEqual(target('open recycle bin',registry),'recycle_bin')
        self.assertEqual(target('calculate 2 plus 3',registry),'calculator_action')
        for text in ['open recycle bin and open Steam','do not empty recycle bin','write about Calculator',
                     'open calculator then save a file','открой корзину затем блокнот']:
            self.assertIsNone(target(text,registry))

    def test_empty_binds_contents_and_rechecks_after_confirmation(self):
        task=SimpleNamespace(request='empty the recycle bin',call=Mock(),runtime=SimpleNamespace(_confirmation_key=lambda s:'key'))
        apps=SystemApps(task)
        operation=step('recycle_bin',dict(operation='empty',item=''))
        task.call.return_value=dict(fingerprint='before')
        apps.guard(operation)
        task.call.return_value=dict(fingerprint='after')
        with self.assertRaisesRegex(AIError,'changed during confirmation'):
            apps.guard(operation)

    def test_open_request_cannot_be_promoted_to_empty(self):
        task=SimpleNamespace(request='open the recycle bin',call=Mock())
        with self.assertRaisesRegex(AIError,'explicitly'):
            SystemApps(task).guard(step('recycle_bin',dict(operation='empty',item='')))
        task.call.assert_not_called()

    def test_worker_rejects_changed_bin_before_permanent_effect(self):
        automation=Mock()
        with patch('win32com.client.Dispatch'), patch('aidy.ai_system_apps.recycle_state',return_value=([],{},'new')):
            with self.assertRaisesRegex(AIError,'changed after confirmation'):
                worker_run(automation,{},'recycle_bin',dict(operation='empty',item='',expected_fingerprint='old'))
        automation.effect.assert_not_called()

    def test_restore_does_not_overwrite_an_existing_destination(self):
        automation=Mock()
        item=Mock()
        rows=[dict(id='one',name='exists.txt',original_folder='C:\\Example')]
        with (patch('win32com.client.Dispatch'), patch('aidy.ai_system_apps.recycle_state',return_value=(rows,{'one':item},'same')),
                patch('aidy.ai_files.TaskFiles.canonical',return_value=SimpleNamespace(parent=SimpleNamespace(is_dir=lambda:True),exists=lambda:True))):
            with self.assertRaisesRegex(AIError,'already exists'):
                worker_run(automation,{},'recycle_bin',dict(operation='restore',item='one',expected_fingerprint='same'))
        automation.effect.assert_not_called()
        item.InvokeVerb.assert_not_called()

    def test_system_queries_and_compound_tasks_return_actual_result_without_replanning(self):
        payload=planned(goal('List the Recycle Bin',step('recycle_bin',dict(operation='list',item=''))),
                        goal('Calculate',step('calculator_action',dict(operation='calculate',expression='2+3*4')),id='g2'))
        provider=FakeProvider(payload)
        execute=Mock(side_effect=[Result('verified','71 items',data=dict(count=71)),
                                  Result('verified','Display is 14',data=dict(calculator_display='14'))])
        engine=AIEngine(provider,Registry([]),lambda *a:True,lambda *a:True,execute)
        engine.start_agent=lambda t:None
        result=engine.run('list the recycle bin and calculate 2+3*4',('local','Admin'))
        self.assertTrue(result.ok,result.message)
        self.assertEqual(execute.call_count,2)
        self.assertEqual(len(provider.seen),1)

    def test_rejected_confirmation_prevents_bin_empty(self):
        provider=FakeProvider(planned(goal('Empty the bin',step('recycle_bin',dict(operation='empty',item='')))))
        execute=Mock()
        engine=AIEngine(provider,Registry([]),lambda *a:True,lambda *a:False,execute)
        engine.start_agent=lambda t:None
        result=engine.run('empty the recycle bin',('local','Admin'))
        self.assertFalse(result.ok)
        execute.assert_not_called()
        self.assertFalse(engine.busy)

    def test_failure_then_steam_is_an_independent_turn(self):
        provider=FakeProvider(plan(step('recycle_bin',dict(operation='open',item=''))),plan(step('open_app',{'app':'steam'})))
        execute=Mock(return_value=Result('verified','Opened.'))
        engine=AIEngine(provider,Registry(['steam']),lambda *a:True,lambda *a:True,execute)
        engine.start_agent=Mock(side_effect=AIError('Unsupported provider.'))
        first=engine.run('open recycle bin',('local','Admin'))
        second=engine.run('open steam',('local','Admin'))
        self.assertFalse(first.ok)
        self.assertTrue(second.ok,second.message)
        self.assertFalse(engine.busy)
        execute.assert_called_once()

    def test_timeout_late_response_cannot_pollute_or_execute_next_turn(self):
        release=threading.Event()
        started=threading.Event()
        class Provider:
            config=AIConfig(mode='api')
            planning_timeout=lambda *a:.06
            def interpret(self,text,history,registry,cancel):
                if text=='stuck':
                    started.set()
                    release.wait(2)
                    registry.pending_task={'poison':True}
                    registry.plan_feedback='late state'
                    return plan(step('open_app',{'app':'discord'}))
                return plan(step('open_app',{'app':'steam'}))
        execute=Mock(return_value=Result('verified','Opened.'))
        registry=Registry(['steam','discord'])
        engine=AIEngine(Provider(),registry,lambda *a:True,lambda *a:True,execute)
        try:
            self.assertFalse(engine.run('stuck',('local','Admin')).ok)
            self.assertTrue(started.is_set())
            self.assertTrue(engine.run('open steam',('local','Admin')).ok)
            release.set()
            time.sleep(.03)
            self.assertIsNone(registry.pending_task)
            self.assertEqual(registry.plan_feedback,'')
            execute.assert_called_once()
            self.assertEqual(execute.call_args.args[0]['arguments']['app'],'steam')
        finally:release.set()

    def test_single_bin_profile_makes_api_request_and_uses_semantic_tool(self):
        native={'candidates':[{'finishReason':'STOP','content':{'parts':[{'functionCall':{
            'name':'recycle_bin','args':{'operation':'open','item':''}}}]}}]}
        session=Mock()
        session.request.return_value=HTTPResponse(payload=native)
        provider=GeminiProvider(AIConfig(mode='api'),Credentials(['test-placeholder']),session)
        try:
            result=provider.interpret('open recycle bin',[],Registry([]),threading.Event())
            self.assertEqual(result['steps'][0]['tool'],'recycle_bin')
            session.request.assert_called_once()
            declarations=session.request.call_args.kwargs['json']['tools'][0]['functionDeclarations']
            self.assertEqual({d['name'] for d in declarations},{'recycle_bin','ask_user'})
        finally:provider.close()

    def test_stuck_google_socket_is_retired_before_next_command(self):
        release=threading.Event()
        slow=Mock()
        ready=Mock()
        slow.request.side_effect=lambda *a,**kw: (release.wait(2) or True) and HTTPResponse(
            payload=response(plan(step('open_app',{'app':'discord'}))))
        ready.request.return_value=HTTPResponse(payload=response(plan(step('open_app',{'app':'steam'}))))
        credentials=Credentials(['test-placeholder'])
        provider=GeminiProvider(AIConfig(mode='api'),credentials,slow)
        provider.planning_timeout=lambda *a:.05
        execute=Mock(return_value=Result('verified','Opened.'))
        engine=AIEngine(provider,Registry(['steam','discord']),lambda *a:True,lambda *a:True,execute)
        try:
            with patch('aidy.ai_provider.requests.Session',return_value=ready):
                failed=engine.run('open discord',('local','Admin'))
            self.assertIn('timed out',failed.message)
            self.assertTrue(provider.closed)
            self.assertIsNot(engine.provider,provider)
            self.assertIs(engine.provider.credentials,credentials)
            self.assertTrue(engine.run('open steam',('local','Admin')).ok)
            ready.request.assert_called_once()
            execute.assert_called_once()
        finally:
            release.set()
            engine.close()


if __name__=='__main__':
    unittest.main()
