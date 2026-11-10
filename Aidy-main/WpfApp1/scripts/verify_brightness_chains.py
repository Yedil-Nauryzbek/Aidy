"""Real WMI readback and cloud-planned brightness -> application/search chains."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'PythonCore'))
from aidy import system
from verify_document_workflows import make_runtime


def levels():
    system.pythoncom.CoInitialize()
    service=None
    try:
        service=system.win32com.client.Dispatch('WbemScripting.SWbemLocator').ConnectServer('.',r'root\WMI')
        return {str(m.InstanceName):int(m.CurrentBrightness) for m in service.ExecQuery('SELECT * FROM WmiMonitorBrightness WHERE Active=TRUE')}
    finally:
        service=None
        system.pythoncom.CoUninitialize()


def main():
    baseline=levels()
    if not baseline:raise SystemExit('No native brightness provider.')
    # Move only one percentage point and restore immediately, including on failure.
    up=all(value<100 for value in baseline.values())
    rows=[]
    try:
        started=time.perf_counter()
        adjusted=system.adjust_brightness(up,1,step_percent=1)
        actual=levels()
        rows.append(dict(test='one_percent_readback',passed=adjusted.ok and actual=={k:v+(1 if up else -1) for k,v in baseline.items()},
                         before=baseline,after=actual,message=adjusted.message,elapsed_ms=round((time.perf_counter()-started)*1000,2)))
    finally:
        if levels()!=baseline:system.adjust_brightness(not up,1,step_percent=1)
    rows.append(dict(test='baseline_restored',passed=levels()==baseline))
    if not all(row['passed'] for row in rows):raise RuntimeError(rows)
    runtime=make_runtime()
    try:
        for label,request,expected in [
            ('brightness_then_app','brightness up for 5 and then open Notepad',['brightness_change','open_app']),
            ('reported_brightness_then_search','brightness up for 5 and then search for a date of world war i',['brightness_change','web_search'])]:
            calls=[]
            def execute(step):
                result=runtime.execute(step)
                calls.append(dict(tool=step['tool'],arguments=step['arguments'],status=result.status,message=result.message))
                return result
            runtime.engine.execute=execute
            result=runtime.engine.run(request,('local','Admin'))
            rows.append(dict(test=label,request=request,passed=result.ok and [call['tool'] for call in calls]==expected,
                             status=result.status,message=result.message,actions=calls,timings=dict(runtime.engine.timings)))
    finally:runtime.close()
    document=dict(scope='Real Google planning and Windows actions; text input, no microphone verification.',results=rows)
    (ROOT/'.validation/brightness-desktop.json').write_text(json.dumps(document,indent=2),encoding='utf-8')
    for row in rows:print(json.dumps(row),flush=True)
    if not all(row['passed'] for row in rows):raise SystemExit(1)


if __name__=='__main__':main()
