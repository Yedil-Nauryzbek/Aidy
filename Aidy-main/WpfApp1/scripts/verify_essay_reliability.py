"""Real Google + editor replay of reported ASR failures; fresh test drafts only."""
import json
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'PythonCore'))
from verify_document_workflows import make_runtime, close_owned_notepad
from aidy.ai_writing import writing_hints, length_tolerance


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',action='append',default=[],help='Run selected test labels only.')
    parser.add_argument('--exact',action='store_true',help='Also validate an explicit exactly-500-word request.')
    options=parser.parse_args()
    cases = [
        ('reported_google_500', 'open a notepad in the right in the say about google for 500 words', 'google', 500),
        ('reported_google_spoken_250', 'write and i say you about google two hundred and fifty words', 'google', 250),
        ('reported_space_500', 'write in a saying about space 500 world', 'space', 500),
        ('clean_economics_500', 'Open Notepad and write a 500-word essay about economics.', 'economic', 500),
        ('word_space_500', 'Open Microsoft Word, create a new document, and write a 500-word essay about space.', 'space', 500),
    ]
    if options.exact:
        cases.append(('exact_notepad_500','Open Notepad and write an essay about space in exactly 500 words.','space',500))
    if options.case:
        cases=[case for case in cases if case[0] in options.case]
        if not cases:
            parser.error('No selected cases match.')
    rows=[]
    report_path=ROOT/('.validation/essay-reliability-exact.json' if options.exact and options.case else '.validation/essay-reliability.json')
    # Shared runtime gives follow-up requests their real recent editor context.
    runtime=make_runtime()
    try:
        for label,request,topic,count in cases:
            started=time.perf_counter()
            result=runtime.engine.run(request,('local','Admin'))
            history=list(runtime.engine.history)
            written=[r for turn in history if turn['request']==request for r in turn.get('results',[])
                     if r['step']['tool']=='document_write' and r['status']=='verified']
            draft=written[-1]['step']['arguments']['text'] if written else ''
            state=written[-1].get('verified_state',{}) if written else {}
            actual=len(draft.split())
            row=dict(test=label,request=request,interpreted=writing_hints(request).context(),
                passed=result.status=='verified' and abs(actual-count)<=(0 if writing_hints(request).exact else length_tolerance(count)) and topic in draft.lower()
                       and state.get('word_count')==actual and r'\n\n' not in draft,
                status=result.status,message=result.message,actual_words=actual,target_words=count,
                elapsed_ms=round((time.perf_counter()-started)*1000,2),timings=dict(runtime.engine.timings),
                tools=[r['step']['tool'] for turn in history if turn['request']==request for r in turn.get('results',[])],
                verified_state=state)
            rows.append(row)
            print(json.dumps(row),flush=True)
            (ROOT/f'.validation/essay-{label}-result.json').write_text(json.dumps(row,indent=2),encoding='utf-8')
            (ROOT/f'.validation/essay-{label}-history.json').write_text(json.dumps(history,indent=2),encoding='utf-8')
            if draft:
                (ROOT/f'.validation/essay-{label}.txt').write_text(draft,encoding='utf-8')
            # Keep recent test documents open during the chain to check that a new
            # essay preserves previously filled documents. Cleanup only our exact
            # verified drafts once every case has finished.
            report_path.write_text(json.dumps(dict(
                scope='Real Google and real UI/COM readback, text input from reported transcripts. No microphone/accent claim.',
                results=rows),indent=2),encoding='utf-8')
            if not row['passed']:
                break
    finally:
        for row in rows:
            state=row['verified_state']
            path=ROOT/f'.validation/essay-{row["test"]}.txt'
            if state.get('app')=='notepad' and path.exists():
                close_owned_notepad(state,path.read_text(encoding='utf-8'))
        runtime.close()
    if len(rows)!=len(cases) or not all(r['passed'] for r in rows):
        raise SystemExit(1)


if __name__=='__main__':
    main()
