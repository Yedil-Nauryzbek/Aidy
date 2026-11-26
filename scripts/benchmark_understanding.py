"""Real Google planning latency and plan correctness for spoken-style requests.

Uses the real app catalog and visible windows like a live session. Every
desktop effect is mocked: no app is opened, closed or typed into. Run with
--label before/after and compare .validation/understanding-<label>.json.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'PythonCore'), str(ROOT/'scripts')]
from aidy.ai_engine import Result
from aidy.ai_provider import GeminiProvider

# (label, spoken request, acceptable first tools; None accepts any action plan)
CASES = [
    ('open_steam', 'open steam', [{'open_app'}]),
    ('open_discord', 'open discord', [{'open_app'}]),
    ('close_steam', 'close steam', [{'close_app', 'close_window'}]),
    # No WhatsApp desktop app is installed here: offering the website is correct.
    ('whatsapp_app_then_site', 'open the whatsapp app and then open the whatsapp website', 'clarify'),
    ('two_apps', 'open whatsapp and then open discord', [{'open_app'}, {'open_app'}]),
    ('essay_600', 'open notepad and write an essay about space in 600 words', [{'document_write', 'open_app', 'notepad_action'}]),
    ('recycle_bin', 'open the recycle bin', [{'recycle_bin'}]),
    ('trash_can', 'open the trash can', [{'recycle_bin'}]),
    ('cmd', 'can you open the cmd', [{'system_action'}]),
    ('bluetooth', 'open bluetooth settings', [{'settings_page'}]),
    ('volume', 'turn the volume up by three', [{'volume_change'}]),
    ('timer', 'set a timer for five minutes', [{'timer_start'}]),
    ('time', 'what time is it', [{'current_time'}]),
    ('screenshot', 'take a screenshot', [{'system_action'}]),
    ('youtube_search', 'open youtube and search for lofi music', [{'open_url'}]),
    ('url', 'go to github.com', [{'open_url'}]),
    ('lock', 'lock the computer', [{'system_action'}]),
    ('task_manager', 'open task manager', [{'system_action', 'open_app'}]),
    ('calculator', 'open the calculator and calculate 25 times 4', [{'calculator_action', 'open_app'}]),
    ('downloads', 'open my downloads folder', [{'explorer_action'}]),
    ('show_desktop', 'show me the desktop', [{'system_action'}]),
    ('uninstall', 'uninstall zoom', [{'settings_page'}]),
    ('asr_recycle', 'open the recycle bell', [{'recycle_bin'}]),
    ('asr_essay', 'open a note pad and right an essay about dogs in 300 words', [{'document_write', 'open_app', 'notepad_action'}]),
]


def make():
    from verify_document_workflows import make_runtime
    import win32gui
    from aidy.ai_windows import owner
    runtime = make_runtime()

    def visible():
        rows = []
        def collect(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) and owner(hwnd):
                rows.append(dict(hwnd=hwnd, pid=owner(hwnd), title=win32gui.GetWindowText(hwnd)))
        win32gui.EnumWindows(collect, None)
        return rows
    runtime.host._enum_visible_real_windows = visible
    return runtime


def prepare(runtime, text):
    """Same per-request registry preparation as AIRuntime.run."""
    host, engine = runtime.host, runtime.engine
    relevant = runtime.catalog.relevant(text)
    existing = {app['id'] for app in host.apps}
    host.apps.extend(app for app in relevant if app['id'] not in existing)
    registry = engine.registry
    registry.installed_ids = {app['id'] for app in relevant}
    registry.app_ids = frozenset(app['id'] for app in host.apps if not app['id'].startswith('installed_') or app['id'] in registry.installed_ids) | {'browser', 'music', 'aidy'}
    registry.installed_apps = [dict(id=app['id'], name=app['display_name'], source=app['type']) for app in relevant]
    registry.website_ids = frozenset(app['id'] for app in host.apps if app.get('type') == 'url')
    registry.windows = runtime.windows.snapshot()
    registry.active_window = runtime.windows.active_ref
    registry.active_app = runtime.windows.active_app
    registry.desktop_state = {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--pause', type=float, default=4.0, help='seconds between requests (free-tier rate limit)')
    options = parser.parse_args()
    cases = [c for c in CASES if not options.case or c[0] in options.case]
    calls = []
    original = GeminiProvider.interpret

    def timed(self, text, history, registry, cancel):
        started = time.perf_counter()
        try:
            plan = original(self, text, history, registry, cancel)
            calls.append(dict(ms=round((time.perf_counter()-started)*1000), error=''))
            return plan
        except Exception as exc:
            calls.append(dict(ms=round((time.perf_counter()-started)*1000), error=str(exc)))
            raise
    GeminiProvider.interpret = timed
    runtime = make()
    executed = []
    def execute(step):
        executed.append(step)
        return Result('verified', 'Verified benchmark effect.', data={})
    runtime.engine.execute = execute
    # Voice ID roles are not under test; the planner and executor contract are.
    runtime.engine.authorize = lambda *a: True
    runtime.engine.confirm = lambda *a: True
    runtime.engine.confirm_batch = lambda steps: True
    runtime.engine.start_agent = lambda text: None
    runtime.engine.refresh_agent = lambda ref: None
    runtime.engine.prepare_agent_step = lambda step: step
    runtime.engine.action_guard = lambda step: False
    rows = []
    try:
        for label, text, expected in cases:
            executed.clear(); calls.clear()
            runtime.engine.clear()
            prepare(runtime, text)
            started = time.perf_counter()
            try:
                result = runtime.engine.run(text, ('bench-' + label, 'Admin'))
                status, message = result.status, result.message
            except Exception as exc:
                status, message = 'exception', repr(exc)
            elapsed = round((time.perf_counter()-started)*1000)
            tools = [s['tool'] for s in executed]
            if expected == 'clarify':
                passed = status == 'clarification' and not tools
            else:
                passed = (status not in {'failed', 'exception', 'cancelled'} and bool(tools)) if expected is not None else status != 'exception'
            if expected not in (None, 'clarify') and passed:
                passed = len(tools) >= len(expected) and all(t in allowed for t, allowed in zip(tools, expected))
            row = dict(case=label, request=text, passed=passed, status=status, message=message[:160],
                       tools=tools, args=[s.get('arguments') for s in executed][:3],
                       first_plan_ms=calls[0]['ms'] if calls else None, model_calls=len(calls),
                       errors=[c['error'] for c in calls if c['error']], total_ms=elapsed)
            if any('quota' in e.lower() or 'rate limit' in e.lower() for e in row['errors']):
                print('Stopping: Google rate/quota limit reached.', flush=True)
                rows.append(row)
                break
            rows.append(row)
            print(json.dumps({k: row[k] for k in ('case', 'passed', 'status', 'tools', 'first_plan_ms', 'model_calls', 'total_ms')}), flush=True)
            time.sleep(options.pause)
    finally:
        runtime.engine.close()
    firsts = [r['first_plan_ms'] for r in rows if r['first_plan_ms']]
    summary = dict(label=options.label, n=len(rows), passed=sum(r['passed'] for r in rows),
                   median_first_plan_ms=statistics.median(firsts) if firsts else None,
                   max_first_plan_ms=max(firsts) if firsts else None)
    print(json.dumps(summary), flush=True)
    out = ROOT/f'.validation/understanding-{options.label}.json'
    out.write_text(json.dumps(dict(summary=summary, results=rows), indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
