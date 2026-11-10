"""Real UIA/browser tests, only in a newly created isolated local test profile.

Serves three harmless local pages. The user's browser profile is never attached.
CDP is used only to independently read results from this disposable profile.
"""
import http.server
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'PythonCore'))
from aidy.ai_automation import UIClient
from aidy.ai_windows import owner,process_name


def main():
    import win32gui
    parser=argparse.ArgumentParser();parser.add_argument('--browser',choices=['edge','chrome'],default='edge');parser.add_argument('--api',action='store_true');parser.add_argument('--api-chain',action='store_true')
    options=parser.parse_args()
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            title='AidyBatch'+self.path.strip('/').title()
            data=f'<html><head><title>{title}</title></head><body><h1>{title}</h1><input aria-label="Test text"><button>Test button</button></body></html>'.encode()
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    profile=ROOT/'.validation'/('browser-batch-profile-'+str(time.time_ns()))
    assert profile.resolve().is_relative_to((ROOT/'.validation').resolve())
    profile.mkdir()
    browser=(Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe' if options.browser=='edge' else
             Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Google/Chrome/Application/chrome.exe')
    urls=[f'http://127.0.0.1:{server.server_port}/{name}' for name in ('alpha','beta','gamma')]
    command=[str(browser),'--guest','--user-data-dir='+str(profile),'--no-first-run','--no-default-browser-check',
             '--remote-debugging-port=0','--new-window',*urls]
    child=subprocess.Popen(command,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    ui=UIClient();cancel=threading.Event();rows=[];windows=[]
    try:
        end=time.monotonic()+15
        while time.monotonic()<end:
            windows=[]
            win32gui.EnumWindows(lambda hwnd,_:windows.append(dict(hwnd=hwnd,pid=child.pid,process=browser.name.lower(),title=win32gui.GetWindowText(hwnd)))
                if win32gui.IsWindowVisible(hwnd) and owner(hwnd)==child.pid and 'AidyBatch' in win32gui.GetWindowText(hwnd) else None,None)
            if len(windows)==1 and (profile/'DevToolsActivePort').exists():break
            time.sleep(.1)
        assert len(windows)==1,'The isolated browser did not expose one test window.'
        window=windows[0]
        port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
        def pages():
            with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=2) as response:
                return [row for row in json.load(response) if row['type']=='page']
        def call(action,text='',**kwargs):
            start=time.perf_counter()
            result=ui.call(window,'browser',cancel,lambda:None,timeout=12,action=action,text=text,**kwargs)
            rows.append(dict(operation=action,elapsed_ms=round((time.perf_counter()-start)*1000,2),status=result['status'],message=result['message']))
            return result
        ui.call(window,'focus',cancel,lambda:None,timeout=4)
        state=call('tabs')
        assert len(state['tabs'])>=3,state
        api_chain_record=None
        if options.api_chain:
            from verify_document_workflows import make_runtime
            from aidy.ai_tools import AIError
            runtime=make_runtime(allowed_pids={child.pid});real_execute=runtime.execute;effects=[]
            def execute_chain(step):
                if step['tool']!='browser_action' or step['arguments']['operation'] not in {'select_tab','close_other_tabs'} or runtime.desktop_task.target(step['arguments']['window'])['hwnd']!=window['hwnd']:
                    raise AIError('The isolated test permits only selecting and closing its own test tabs.')
                result=real_execute(step);effects.append(dict(step=step,status=result.status,data=result.data));return result
            runtime.engine.execute=execute_chain
            try:
                start=time.perf_counter()
                outcome=runtime.engine.run('Select the AidyBatchAlpha tab and then close other tabs',('local','Admin'))
                api_chain_record=dict(status=outcome.status,message=outcome.message,elapsed_ms=round((time.perf_counter()-start)*1000,2),timings=runtime.engine.timings,effects=effects)
                assert outcome.status=='verified' and [r['step']['arguments']['operation'] for r in effects]==['select_tab','close_other_tabs'],api_chain_record
                assert runtime.engine.timings['model_calls']==1,api_chain_record
                (ROOT/f'.validation/browser-chain-{options.browser}-api.json').write_text(json.dumps(api_chain_record,indent=2),encoding='utf8')
            finally:runtime.close()
        else:call('select_tab','AidyBatchAlpha')
        state=call('tabs')
        active=[r for r in state['tabs'] if r.get('selected')]
        assert len(active)==1 and 'Alpha' in active[0]['name'],state
        from aidy.ai_browser import tab_targets
        targets=tab_targets(state['tabs'],'close_other_tabs')
        call('close_other_tabs',expected_tabs=[r['id'] for r in state['tabs']],target_tabs=targets)
        assert [p['title'] for p in pages()]==['AidyBatchAlpha'],pages()
        call('new_tab')
        call('navigate',urls[1])
        end=time.monotonic()+3
        while time.monotonic()<end and not any(p['title']=='AidyBatchBeta' for p in pages()):time.sleep(.05)
        state=call('tabs')
        targets=tab_targets(state['tabs'],'close_matching_tabs','Beta')
        call('close_matching_tabs','Beta',expected_tabs=[r['id'] for r in state['tabs']],target_tabs=targets)
        assert [p['title'] for p in pages()]==['AidyBatchAlpha'],pages()
        for url in urls[1:]:
            call('new_tab');call('navigate',url)
            end=time.monotonic()+3
            while time.monotonic()<end and not any(p['url']==url for p in pages()):time.sleep(.04)
        other_url=f'http://127.0.0.1:{server.server_port}/delta'
        second=subprocess.Popen(command[:-3]+[other_url],creationflags=subprocess.CREATE_NO_WINDOW,
                                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        second.wait(timeout=5)
        end=time.monotonic()+4
        while time.monotonic()<end and not any(p['title']=='AidyBatchDelta' for p in pages()):time.sleep(.05)
        extra=[]
        end=time.monotonic()+4
        while not extra and time.monotonic()<end:
            win32gui.EnumWindows(lambda hwnd,_:extra.append(dict(hwnd=hwnd,pid=child.pid,process=browser.name.lower(),title=win32gui.GetWindowText(hwnd)))
                if win32gui.IsWindowVisible(hwnd) and owner(hwnd)==child.pid and 'AidyBatchDelta' in win32gui.GetWindowText(hwnd) else None,None)
            if not extra:time.sleep(.05)
        assert len(extra)==1,'Second isolated browser window did not appear.'
        windows.extend(extra)
        ui.call(window,'focus',cancel,lambda:None,timeout=4)
        state=call('tabs')
        assert len(state['tabs'])==3,state
        api_record=None
        if options.api:
            from verify_document_workflows import make_runtime
            from aidy.ai_tools import AIError
            runtime=make_runtime(allowed_pids={child.pid})
            real_execute=runtime.execute;effects=[]
            def execute(step):
                if step['tool']!='browser_action' or step['arguments']['operation']!='close_all_tabs' or runtime.desktop_task.target(step['arguments']['window'])['hwnd']!=window['hwnd']:
                    raise AIError('The isolated test accepts only closing tabs in its own selected test window.')
                result=real_execute(step);effects.append(dict(step=step,status=result.status,data=result.data));return result
            runtime.engine.execute=execute
            try:
                start=time.perf_counter()
                outcome=runtime.engine.run('Close all the websites in this browser',('local','Admin'))
                api_record=dict(status=outcome.status,message=outcome.message,elapsed_ms=round((time.perf_counter()-start)*1000,2),timings=runtime.engine.timings,effects=effects,
                                scope='Real Google planning and native tab closures; explicit yes confirmation simulated for these disposable test tabs only.')
                assert outcome.status=='verified' and len(effects)==1,api_record
            finally:runtime.close()
        else:
            call('close_all_tabs',expected_tabs=[r['id'] for r in state['tabs']],target_tabs=[r['id'] for r in state['tabs']])
        remaining=pages()
        assert len(remaining)==2 and any(p['title']=='AidyBatchDelta' for p in remaining),remaining
        assert sum(not p['url'].startswith(f'http://127.0.0.1:{server.server_port}') for p in remaining)==1,remaining
        assert owner(window['hwnd'])==child.pid,'The original browser window was not preserved.'
        report=dict(passed=True,scope=f'Real {options.browser}, native UIA selection, frozen tab closures, local HTTP pages, new isolated guest profile, independent read-only CDP result checks. No personal profile or user tab was touched.',rows=rows,api=api_record,api_chain=api_chain_record)
        suffix='-api' if options.api or options.api_chain else ''
        (ROOT/f'.validation/browser-batches-{options.browser}{suffix}.json').write_text(json.dumps(report,indent=2),encoding='utf8')
        print(json.dumps(report),flush=True)
    except Exception:
        import ctypes
        u=ctypes.WinDLL('user32');u.GetLastActivePopup.restype=ctypes.c_void_p
        details=[]
        for window in windows:
            popup=int(u.GetLastActivePopup(window['hwnd']) or 0)
            details.append(dict(window=window['hwnd'],enabled=bool(u.IsWindowEnabled(window['hwnd'])),popup=popup,
                                popup_title=win32gui.GetWindowText(popup),popup_class=win32gui.GetClassName(popup),pages=pages()))
        print(json.dumps(dict(failure_windows=details,completed=rows)),flush=True)
        raise
    finally:
        ui.close();server.shutdown();server.server_close()
        for window in windows:
            if owner(window['hwnd'])==child.pid:
                win32gui.PostMessage(window['hwnd'],0x10,0,0)
        try:child.wait(timeout=4)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=4)


if __name__=='__main__':main()
