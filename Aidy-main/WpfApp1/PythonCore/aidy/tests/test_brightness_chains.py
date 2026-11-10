"""Windows brightness readback and independent, ordered follow-up actions."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from aidy import system
from aidy.ai_engine import Result
import test_task_understanding as tasks
from test_task_understanding import goal,planned
import test_ai_layer as fixtures


class BrightnessTests(unittest.TestCase):
    def run_provider(self,initial=50,up=True,steps=1,code=None,apply=True,present=True):
        level=SimpleNamespace(InstanceName='panel',CurrentBrightness=initial)
        monitor=Mock(InstanceName='panel')
        props={name:SimpleNamespace(Value=None) for name in ['Timeout','Brightness']}
        params=Mock();params.Properties_.side_effect=props.__getitem__
        monitor.Methods_.return_value.InParameters.SpawnInstance_.return_value=params
        def change(*args):
            if apply and code in {None,0}:level.CurrentBrightness=props['Brightness'].Value
            if code is None:return None
            value=Mock();value.Properties_.return_value=SimpleNamespace(Value=code)
            return value
        monitor.ExecMethod_.side_effect=change
        service=Mock()
        service.ExecQuery.side_effect=lambda query:([monitor] if 'Methods' in query else [level]) if present else []
        with patch.object(system.win32com.client,'Dispatch') as dispatch,patch.object(system.pythoncom,'CoInitialize'),patch.object(system.pythoncom,'CoUninitialize'):
            dispatch.return_value.ConnectServer.return_value=service
            result=system.adjust_brightness(up,steps)
        return result,monitor,props

    def test_empty_com_return_is_verified_from_actual_brightness(self):
        result,monitor,props=self.run_provider()
        self.assertTrue(result.ok)
        self.assertEqual(result.levels,{'panel':60})
        self.assertEqual(props['Timeout'].Value,0)
        monitor.ExecMethod_.assert_called_once()

    def test_limits_are_verified_noop_and_do_not_invoke_setter(self):
        for initial,up in [(100,True),(0,False)]:
            result,monitor,_=self.run_provider(initial,up,5)
            self.assertTrue(result.ok)
            self.assertIn('limit',result.message)
            monitor.ExecMethod_.assert_not_called()

    def test_unavailable_provider_is_distinguished_from_uncertain_effect(self):
        unavailable,_,_=self.run_provider(present=False)
        self.assertFalse(unavailable.ok);self.assertTrue(unavailable.not_executed)
        unknown,_,_=self.run_provider(apply=False)
        self.assertFalse(unknown.ok);self.assertFalse(unknown.not_executed)
        rejected,_,_=self.run_provider(code=1)
        self.assertFalse(rejected.ok);self.assertFalse(rejected.not_executed)


class IndependentChainTests(unittest.TestCase):
    engine=tasks.GoalExecutionTests.engine

    def test_brightness_and_app_finish_in_order_with_one_planning_call(self):
        value=planned(goal('Increase brightness',fixtures.step('brightness_change',{'direction':'up','steps':5})),
                      goal('Open Notepad',fixtures.step('open_app',{'app':'notepad'}),id='g2'))
        engine=self.engine(value)
        result=engine.run('brightness up for 5 and then open Notepad',('local','Admin'))
        self.assertTrue(result.ok)
        self.assertEqual([c.args[0]['tool'] for c in self.execute.call_args_list],['brightness_change','open_app'])
        self.assertEqual(engine.timings['model_calls'],1)

    def test_unsupported_brightness_does_not_block_independent_app_goal(self):
        value=planned(goal('Increase brightness',fixtures.step('brightness_change',{'direction':'up','steps':5})),
                      goal('Open Notepad',fixtures.step('open_app',{'app':'notepad'}),id='g2'))
        engine=self.engine(value)
        self.execute.side_effect=[Result('failed','No brightness control.',data={'independent_control_unavailable':True}),Result('verified','Notepad opened.')]
        result=engine.run('brightness up for 5 and then open Notepad',('local','Admin'))
        self.assertEqual(self.execute.call_count,2)
        self.assertEqual(result.status,'failed')
        self.assertIn('1 of 2',result.message)
        self.assertIn('brightness',result.message)

    def test_unsupported_brightness_does_not_block_legacy_search(self):
        engine=self.engine(fixtures.plan(fixtures.step('brightness_change',{'direction':'up','steps':5}),
                                         fixtures.step('web_search',{'query':'date of World War I'})))
        self.execute.side_effect=[Result('failed','No brightness control.',data={'independent_control_unavailable':True}),Result('dispatched','Search opened.')]
        result=engine.run('brightness up for 5 and then search for the date of World War I',('local','Admin'))
        self.assertEqual(self.execute.call_count,2)
        self.assertIn('1 of 2',result.message)

    def test_uncertain_brightness_stops_later_goal(self):
        value=planned(goal('Increase brightness',fixtures.step('brightness_change',{'direction':'up','steps':5})),
                      goal('Open Notepad',fixtures.step('open_app',{'app':'notepad'}),id='g2'))
        engine=self.engine(value);self.execute.return_value=Result('failed','Readback failed.')
        self.assertFalse(engine.run('brightness up for 5 and then open Notepad',('local','Admin')).ok)
        self.assertEqual(self.execute.call_count,1)


if __name__=='__main__':unittest.main()
