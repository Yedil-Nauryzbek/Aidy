"""Questions about data (files, tabs, page, Notepad, Recycle Bin) against the real Gemini planner.

Stage 2 acceptance (2026-11-25): at least 18 of 20 requests use the right read tool and are
answered by a spoken answer that matches the data. Every read returns SYNTHETIC data in the real
executor's format and the windows are synthetic too: no real file, window or page reaches Google,
and nothing on the desktop is changed. Run with --label before/after.
"""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'PythonCore'), str(ROOT / 'scripts')]
import benchmark_understanding as bench  # noqa: E402
from aidy.ai_engine import Result  # noqa: E402
from aidy.ai_files import known_folders  # noqa: E402
from aidy.ai_provider import GeminiProvider  # noqa: E402
from aidy.ai_runtime import spoken_text  # noqa: E402

NOW = datetime.now()


def stamp(days=0, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).strftime('%Y-%m-%d %H:%M')


def folder(name):
    return str(known_folders().get(name, Path.home() / name.title()))


FILES = {  # folder -> rows newest first, as file_action/explorer_action list returns them
    'downloads': [('budget_2026.xlsx', 24_576, stamp(hours=2)), ('invoice_march.pdf', 184_320, stamp(days=5)),
                  ('holiday_video.mp4', 734_003_200, stamp(days=7)), ('README.txt', 2_048, stamp(days=15)),
                  ('setup_tool.exe', 5_242_880, stamp(days=23))],
    'desktop': [('todo.txt', 512, stamp(hours=5)), ('project', None, stamp(days=1)),
                ('photo.jpg', 2_097_152, stamp(days=3)), ('notes.docx', 15_360, stamp(days=9))],
    'documents': [('resume_2026.pdf', 98_304, stamp(days=2)), ('notes.txt', 1_024, stamp(days=4)),
                  ('meeting.txt', 800, stamp(days=6))],
}
TEXTS = {
    'todo.txt': 'Buy milk\nCall Anna about the trip\nFinish the quarterly report',
    'meeting.txt': 'Meeting on Monday: the budget was approved, the launch moved to January, '
                   'and Sara owns the design review.',
    'readme.txt': 'This archive contains the Aidy test assets. Unzip it to the Desktop before the demo.',
    'notes.txt': 'Pick up the parcel on Friday.',
}
TABS = '3 tabs: Install Python on Windows - guide, Inbox (4) - Mail, Weekly weather forecast'
PAGE = ('Install Python on Windows, step by step. Download the installer from python.org, run it, '
        'tick Add Python to PATH, then check it with python --version. The Pro course costs $49 per month.')
NOTEPAD = 'Ideas for the weekend: visit the lake, try the new pasta recipe, call grandma.'
RECYCLE = json.dumps(dict(count=2, items=[dict(id='r1', name='old_report.docx', original=folder('documents')),
                                          dict(id='r2', name='draft.txt', original=folder('desktop'))], truncated=False))
WINDOWS = [dict(id='w1', process='msedge.exe', title='Install Python on Windows - guide - Microsoft Edge'),
           dict(id='w2', process='notepad.exe', title='weekend.txt - Notepad')]

LIST = {'file_action': {'list', 'search'}, 'explorer_action': {'list', 'search'}}
READ = {'file_action': {'read'}}
CASES = [  # (label, request, accepted read tools {tool: operations}, words of which one must be spoken)
    ('downloads_list', "what's in my downloads folder", LIST, ['budget', 'invoice', 'video', 'readme', 'setup']),
    ('newest_download', "what's the newest file in my downloads", LIST, ['budget']),
    ('desktop_count', 'how many files are on my desktop', LIST, ['four', '4', 'three', '3']),
    ('find_resume', 'find my resume in documents', LIST, ['resume']),
    ('find_notes', 'is there a file called notes in my documents', LIST, ['notes']),
    ('read_todo', 'read the file todo.txt on my desktop', READ, ['milk', 'anna', 'report']),
    ('summarize_meeting', 'summarize the file meeting.txt in my documents', READ, ['budget', 'january', 'sara']),
    ('readme', 'what does the readme in my downloads say', {**LIST, **READ}, ['assets', 'unzip', 'demo']),
    ('video_size', 'how big is the video in my downloads', LIST, ['megabyte', '700', 'seven hundred', 'gigabyte', '734']),
    ('pdf_downloads', 'are there any pdf files in my downloads', LIST, ['invoice']),
    ('tabs', 'what tabs do I have open', {'browser_action': {'tabs'}}, ['python', 'inbox', 'mail', 'weather']),
    ('tab_count', 'how many tabs are open', {'browser_action': {'tabs'}}, ['three', '3']),
    ('page_about', 'what is this page about', {'browser_action': {'read_page'}}, ['python', 'install']),
    ('page_summary', 'summarize the article in my browser', {'browser_action': {'read_page'}}, ['python', 'install']),
    ('page_price', 'is there a price on this page', {'browser_action': {'read_page'}}, ['49', 'forty']),
    ('notepad_what', 'what did I write in notepad', {'notepad_action': {'read'}}, ['lake', 'pasta', 'grandma', 'weekend']),
    ('notepad_read', "read me what's in notepad", {'notepad_action': {'read'}}, ['lake', 'pasta', 'grandma']),
    ('recycle_what', "what's in the recycle bin", {'recycle_bin': {'list'}}, ['old_report', 'old report', 'draft']),
    ('recycle_count', 'how many items are in the trash', {'recycle_bin': {'list'}}, ['two', '2']),
    ('open_documents', 'open my documents folder', None, []),  # an action: no data answer
]


def rows_of(path):
    for name, rows in FILES.items():
        if Path(path).resolve() == Path(folder(name)).resolve() or Path(path).name.casefold() == name:
            return name, rows
    return None, []


def execute(step):
    """Synthetic outcome in the real executor's format; nothing on this computer is touched."""
    name, args = step['tool'], step['arguments']
    operation = args.get('operation') or args.get('action') or ''
    if name == 'observe_desktop':
        return Result('verified', json.dumps(dict(windows=WINDOWS)), data={})
    if name in LIST and operation in {'list', 'search'}:
        key, rows = rows_of(args.get('path', ''))
        if key is None:
            return Result('failed', 'Select an existing folder to list or search.')
        query = (args.get('text') or args.get('query') or '').strip('*').casefold().replace('*', '')
        base = folder(key)
        items = [dict(path=str(Path(base) / n), kind='folder' if size is None else 'file', size=size, modified=m)
                 for n, size, m in rows if operation == 'list' or query in n.casefold()]
        return Result('verified', json.dumps(dict(items=items, truncated=False)), data=dict(items=items))
    if name == 'file_action' and operation == 'read':
        text = TEXTS.get(Path(args.get('path', '')).name.casefold())
        return Result('verified', text) if text else Result('failed', 'The selected path does not exist.')
    if name == 'browser_action' and operation == 'tabs':
        return Result('verified', TABS)
    if name == 'browser_action' and operation == 'read_page':
        return Result('verified', PAGE)
    if name == 'notepad_action' and operation == 'read':
        return Result('verified', NOTEPAD)
    if name == 'recycle_bin' and operation == 'list':
        return Result('verified', RECYCLE)
    return Result('verified', 'Verified benchmark effect.', data={})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--pause', type=float, default=4.0, help='seconds between requests (free-tier rate limit)')
    options = parser.parse_args()
    calls, errors = [], []
    for method in ('interpret', 'answer_data'):
        original = getattr(GeminiProvider, method, None)
        if original:
            def counted(self, *args, _original=original, _name=method, **kwargs):
                calls.append(_name)
                try:
                    return _original(self, *args, **kwargs)
                except Exception as exc:  # record why a plan was rejected, then let the engine handle it
                    errors.append(str(exc)[:200])
                    raise
            setattr(GeminiProvider, method, counted)
    runtime = bench.make()
    executed = []
    runtime.engine.execute = lambda step: executed.append(step) or execute(step)
    runtime.engine.authorize = lambda *a: True
    runtime.engine.confirm = lambda *a: True
    runtime.engine.confirm_batch = lambda steps: True
    runtime.engine.start_agent = lambda text: None
    runtime.engine.refresh_agent = lambda ref: None
    runtime.engine.prepare_agent_step = lambda step: step
    runtime.engine.action_guard = lambda step: False
    rows = []
    try:
        for label, text, accepted, words in [c for c in CASES if not options.case or c[0] in options.case]:
            executed.clear()
            calls.clear()
            errors.clear()
            runtime.engine.clear()
            bench.prepare(runtime, text)
            registry = runtime.engine.registry
            registry.windows, registry.active_window, registry.active_app = list(WINDOWS), 'w1', None
            started = time.perf_counter()
            try:
                result = runtime.engine.run(text, ('bench-' + label, 'Admin'))
            except Exception as exc:
                result = Result('failed', repr(exc))
            elapsed = round((time.perf_counter() - started) * 1000)
            data = result.data or {}
            if result.status == 'answered':
                spoken = spoken_text(result.message, bool(data.get('detail')) or result.message.rstrip().endswith('?'))
            else:
                spoken = spoken_text(result.spoken_message or result.message, not result.ok)
            used = [(s['tool'], s['arguments'].get('operation', '')) for s in executed]
            if accepted is None:
                passed = result.ok and 'answer_data' not in calls and any(t == 'explorer_action' for t, _ in used)
            else:
                read = any(op in accepted.get(tool, ()) for tool, op in used)
                passed = (read and result.status == 'answered' and '{"' not in spoken
                          and any(w in spoken.casefold() for w in words))
            row = dict(case=label, passed=passed, status=result.status, tools=used, model_calls=len(calls),
                       answer_calls=calls.count('answer_data'), total_ms=elapsed, spoken=spoken[:240],
                       errors=list(errors))
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            time.sleep(options.pause)
    finally:
        runtime.engine.close()
    times = [r['total_ms'] for r in rows]
    summary = dict(label=options.label, n=len(rows), passed=sum(r['passed'] for r in rows),
                   median_ms=statistics.median(times) if times else None, max_ms=max(times) if times else None)
    print(json.dumps(summary), flush=True)
    (ROOT / f'.validation/data-answers-{options.label}.json').write_text(
        json.dumps(dict(summary=summary, results=rows), indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
