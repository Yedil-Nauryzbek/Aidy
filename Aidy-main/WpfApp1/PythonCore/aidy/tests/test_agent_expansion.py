"""Semantic browser scope, app shorthand, lazy optional models and file grants."""
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from aidy.ai_app_profile import app_chain
from aidy.ai_browser import tab_targets
from aidy.ai_tools import AIError,AIPlanError,AIClarification,Registry
from aidy.ai_automation import UIClient,DesktopTask
from aidy.ai_files import TaskFiles
from aidy.voice_auth import VoiceAuthManager
from aidy.ai_browser_profile import eligible


class AppShorthandTests(unittest.TestCase):
    def test_detailed_api_request_can_be_long_without_extending_the_idle_wait(self):
        from test_audio_pipeline import bare_aidy
        host=bare_aidy();host._ai=SimpleNamespace(enabled=True,awaiting_number=False)
        host._capture_utterance=Mock(return_value=object());host._transcribe_capture=Mock(return_value='open discord')
        host.listen_command(max_seconds=8,speak_on_empty=False)
        self.assertEqual(host._capture_utterance.call_args.kwargs['max_seconds'],45)
        self.assertEqual(host._capture_utterance.call_args.kwargs['wait_seconds'],8)

    def test_gpt_app_and_omitted_repeated_verbs_qualify_without_website_substitution(self):
        registry=Registry(['whatsapp_web','chatgpt_app','gpt','steam'])
        registry.app_names={'whatsapp_web':['whatsapp']}
        for phrase in ['Open WhatsApp and then GPT app','Open WhatsApp and then open GPT app',
                       'Open the WhatsApp and then open the chat with the app']:
            self.assertEqual(app_chain(phrase,registry),[('open_app','whatsapp_web'),('open_app','chatgpt_app')])
        self.assertEqual(app_chain('Open GPT app',registry),[('open_app','chatgpt_app')])
        for phrase in ['Open WhatsApp and then search GPT app','Open WhatsApp but not GPT app',
                       'Open WhatsApp and then the GPT website','Open WhatsApp and write to GPT app']:
            self.assertIsNone(app_chain(phrase,registry))
        missing=Registry(['whatsapp_web','gpt']);missing.app_names=registry.app_names
        self.assertIsNone(app_chain('Open WhatsApp and then GPT app',missing))


class BrowserScopeTests(unittest.TestCase):
    def test_close_protection_does_not_hide_an_editor_from_general_tasks(self):
        from aidy.ai_windows import Windows
        host=SimpleNamespace(_enum_visible_real_windows=lambda:[dict(hwnd=1,pid=10001,title='Editor'),dict(hwnd=2,pid=10002,title='Browser')],
                             _is_protected_process_for_close=lambda name:name=='code.exe')
        windows=Windows(host)
        with patch('aidy.ai_windows.process_name',side_effect=lambda pid:'code.exe' if pid==10001 else 'chrome.exe'):
            rows=windows.snapshot()
        self.assertEqual({row['process'] for row in rows},{'code.exe','chrome.exe'})
        editor=next(row['id'] for row in rows if row['process']=='code.exe')
        with self.assertRaises(AIError):windows.bind_one(editor)
        windows.bind_all([],{})
        self.assertEqual([row['process'] for row in windows._frozen],['chrome.exe'])

    def test_browser_contract_keeps_whole_browser_chains_and_defers_other_domains(self):
        registry=Registry([])
        for request in ['Close all websites in this browser','Close all tabs except YouTube',
                        'Select the YouTube tab and then close other tabs','Select tab two and close the others']:
            self.assertTrue(eligible(request,registry),request)
        for request in ['Open the browser','Close Chrome','Open Word page two',
                        'Close all tabs and shut down the computer','Select YouTube tab and write a message',
                        "Don't close browser tabs"]:
            self.assertFalse(eligible(request,registry),request)

    def test_effect_is_not_permitted_after_cancellation_and_requires_parent_ack(self):
        from aidy.ai_uia_worker import Automation
        automation=Automation.__new__(Automation);automation.request_id='test';automation.effect_started=False
        with patch('sys.stdin',io.StringIO('{"id":"different","permit_effect":true}\n')),patch('builtins.print'):
            with self.assertRaises(AIError):automation.effect()
        self.assertFalse(automation.effect_started)
        with patch('sys.stdin',io.StringIO('{"id":"test","permit_effect":true}\n')),patch('builtins.print'):
            automation.effect()
        self.assertTrue(automation.effect_started)

    def test_revoked_authority_stops_the_interface_worker_before_a_permit(self):
        client=UIClient();client.responses=queue.Queue();written=[]
        def write(line):written.append(json.loads(line))
        client.process=SimpleNamespace(poll=lambda:None,stdin=SimpleNamespace(write=write,flush=lambda:None))
        client.close=Mock()
        with self.assertRaisesRegex(AIError,'Access changed'):
            client.call({},'browser',threading.Event(),lambda:None,timeout=.1,authorize=lambda:False,action='close_all_tabs')
        self.assertEqual(len(written),1)
        self.assertNotIn('permit_effect',written[0])
        client.close.assert_called_once()

    def setUp(self):
        self.tabs=[dict(id='a',name='YouTube',selected=False),dict(id='b',name='Mail',selected=True),dict(id='c',name='YouTube music',selected=False)]

    def test_all_other_matching_and_exact_selection_have_distinct_scopes(self):
        self.assertEqual(tab_targets(self.tabs,'close_all_tabs'),['a','b','c'])
        self.assertEqual(tab_targets(self.tabs,'close_other_tabs'),['a','c'])
        self.assertEqual(tab_targets(self.tabs,'close_matching_tabs','YouTube'),['a','c'])
        self.assertEqual(tab_targets(self.tabs,'close_tabs_except','YouTube'),['b'])
        self.assertEqual(tab_targets(self.tabs,'select_tab','c'),['c'])
        self.assertEqual(tab_targets(self.tabs,'close_tab'),['b'])

    def test_ambiguous_selection_and_missing_selected_tab_cannot_close_arbitrary_targets(self):
        self.tabs[0]['name']='YouTube one'
        with self.assertRaises(AIClarification):tab_targets(self.tabs,'select_tab','YouTube')
        self.assertEqual(tab_targets(self.tabs,'close_matching_tabs','YouTube'),['a','c'])
        self.tabs[1]['selected']=False
        with self.assertRaises(AIClarification):tab_targets(self.tabs,'close_other_tabs')

    def test_browser_reference_uses_focus_then_recent_then_unique_and_never_a_random_window(self):
        runtime=SimpleNamespace(windows=SimpleNamespace(active_ref=None,targets={'w1':{'process':'chrome.exe'},'w2':{'process':'msedge.exe'}}),
                                engine=SimpleNamespace(last_browser_window=None))
        task=DesktopTask(runtime,'Close tabs in this browser')
        with self.assertRaises(AIClarification):task.reference('$browser')
        runtime.engine.last_browser_window='w1'
        self.assertEqual(task.reference('$browser'),'w1')
        runtime.windows.active_ref='w2'
        self.assertEqual(task.reference('$browser'),'w2')

    def test_native_calls_poll_controls_without_reopening_microphone(self):
        runtime=SimpleNamespace(engine=SimpleNamespace(_check=Mock(),task_deadline=time.monotonic()+10,cancel_event=threading.Event()),
                                uia=Mock(),progress=Mock(),poll_cancel_audio=Mock(side_effect=AssertionError('microphone reopened')))
        task=DesktopTask(runtime,'Inspect the app')
        task.call({'hwnd':1},'observe')
        self.assertIs(runtime.uia.call.call_args.args[3],runtime.progress)

    def test_timeout_before_input_can_replan_but_unknown_mutation_cannot(self):
        for effect in [False,True]:
            client=UIClient();client.responses=queue.Queue()
            def write(line):
                if effect:client.responses.put(dict(id=json.loads(line)['id'],phase='effect_started'))
            client.process=SimpleNamespace(poll=lambda:None,stdin=SimpleNamespace(write=write,flush=lambda:None))
            client.close=Mock()
            with self.assertRaises(AIError) as raised:
                client.call({},'browser',threading.Event(),lambda:None,timeout=.025,action='close_all_tabs')
            self.assertEqual(isinstance(raised.exception,AIPlanError),not effect)


class OptionalEncoderTests(unittest.TestCase):
    def test_disabled_voice_id_does_not_start_a_speaker_embedding_model(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(VoiceAuthManager,'_load_encoder') as load:
            manager=VoiceAuthManager(directory,defer_encoder=True)
            self.assertFalse(manager._encoder_started)
            load.assert_not_called()
            with patch.object(manager,'_migrate_embedding_dimensions'):
                manager.start_encoder(asynchronous=False)
                manager.start_encoder(asynchronous=False)
            load.assert_called_once()
            if manager._conn is not None:manager._conn.close()

    def test_importing_command_runtime_does_not_import_speechbrain_or_torch(self):
        core=Path(__file__).resolve().parents[2]
        program='import sys;sys.path.insert(0,'+repr(str(core))+');import aidy.ai_runtime,aidy.voice_auth;assert "torch" not in sys.modules;assert "speechbrain" not in sys.modules'
        result=subprocess.run([sys.executable,'-c',program],capture_output=True,text=True,timeout=12)
        self.assertEqual(result.returncode,0,result.stderr)


class GeneralFileTests(unittest.TestCase):
    def test_list_search_copy_are_scoped_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'nested').mkdir();(root/'nested/report.txt').write_text('test',encoding='utf8')
            task=TaskFiles('Find reports in "'+str(root)+'" and copy them within that folder')
            args=dict(path=str(root),destination='',text='',operation='list')
            self.assertIn('nested',task.execute(args).message)
            args.update(operation='search',text='*.txt')
            self.assertIn('report.txt',task.execute(args).message)
            args.update(operation='copy',path=str(root/'nested/report.txt'),destination=str(root/'copy.txt'),text='')
            self.assertTrue(task.execute(args).ok)
            self.assertEqual((root/'copy.txt').read_text(),'test')
            with self.assertRaises(AIError):task.execute(args)
            with self.assertRaises(AIError):task.path(str(root.parent/'outside.txt'))

    def test_named_known_folder_is_granted_but_other_folders_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch('aidy.ai_files.known_folders',return_value={'desktop':str(root)}):
                task=TaskFiles('Create notes on my desktop')
            self.assertEqual(task.path(str(root/'notes.txt')),root/'notes.txt')
            with self.assertRaises(AIError):task.path(str(root.parent/'notes.txt'))


if __name__=='__main__':unittest.main()
