"""Bounded persistent accessibility transport and task-scoped desktop executor."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

from .ai_engine import Result
from .ai_tools import AIError, AIPlanError, AIClarification
from .ai_files import TaskFiles


class UIClient:
    def __init__(self):
        self.process = None
        self.responses = None

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.responses = None

    def call(self, window, operation, cancel, progress, timeout=3, authorize=None, **arguments):
        if self.process is None or self.process.poll() is not None:
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONIOENCODING='utf-8')
            self.process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'aidy.ai_uia_worker'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            self.responses = queue.Queue()
            output, result_queue = self.process.stdout, self.responses
            def reader():
                for line in output:
                    if len(line) <= 262144:
                        try:
                            result_queue.put(json.loads(line))
                        except ValueError:
                            pass
            threading.Thread(target=reader, name='aidy-uia-results', daemon=True).start()
        request_id = uuid.uuid4().hex
        if cancel.is_set():
            raise AIError('Cancelled.')
        try:
            self.process.stdin.write(json.dumps(dict(id=request_id, window=window, operation=operation, **arguments)) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.close()
            raise AIPlanError('The accessibility worker stopped before input. Refresh the target and try again.') from None
        deadline = time.monotonic() + timeout
        effect_started = False
        next_authorization=0
        while time.monotonic() < deadline:
            progress()
            if authorize is not None and time.monotonic()>=next_authorization:
                if not authorize():
                    self.close()
                    raise AIError('Access changed. Remaining interface actions stopped.')
                next_authorization=time.monotonic()+.25
            if cancel.is_set():
                self.close()
                raise AIError('Cancelled. Remaining actions stopped; input already sent is not undone.')
            try:
                response = self.responses.get(timeout=.02)
                if response.get('id') != request_id:
                    continue
                if response.get('phase') in {'ready_for_effect','effect_started'}:
                    if cancel.is_set():
                        self.close()
                        raise AIError('Cancelled before mutation.')
                    if authorize is not None and not authorize():
                        self.close()
                        raise AIError('Access changed before input. Remaining interface actions stopped.')
                    effect_started = True
                    if response['phase']=='ready_for_effect':
                        self.process.stdin.write(json.dumps(dict(id=request_id,permit_effect=True))+'\n')
                        self.process.stdin.flush()
                    continue
                if 'error' in response:
                    if response.get('rejected_before_action') is True:
                        raise AIPlanError(response['error'])
                    raise AIError(response['error'])
                return response['result']
            except queue.Empty:
                if self.process.poll() is not None:
                    break
        self.close()
        if not effect_started and operation in {'observe','focus','browser','read'}:
            raise AIPlanError('Accessibility timed out before any task input was sent. Refresh the target and choose a supported semantic operation.')
        raise AIError('UI Automation timed out. The action may have been sent and will not be retried.')


class DesktopTask:
    def __init__(self, runtime, request):
        self.runtime = runtime
        self.request = request
        self.files = TaskFiles(request)
        self.views = {}
        self.typed = {}
        self.documents_by_goal = {}
        self.input_window = None
        self.recycle_bindings = {}
        self.browser_bindings = {}
        from .ai_system_apps import SystemApps
        self.system_apps = SystemApps(self)

    @property
    def engine(self):
        return self.runtime.engine

    def call(self, window, operation, **args):
        self.engine._check(self.engine.task_deadline)
        allowance = (20 if operation == 'system_app' else
                     min(25, 4 + len(args.get('target_tabs', []))*.3) if operation=='browser' and args.get('target_tabs') else
                     min(30, 3 + len(args.get('text', '')) * .04) if operation == 'set_text' else
                     15 if operation == 'word_document' else 6 if operation == 'notepad_new_document' else 3)
        permission=('file_access' if operation=='system_app' and args.get('tool') in {'recycle_bin','explorer_action'} else
                    'open_app' if operation=='system_app' and args.get('tool')=='settings_page' else
                    'desktop_read' if operation in {'observe','read'} or operation=='browser' and args.get('action') in {'tabs','read_page'} else
                    'switch_window' if operation in {'focus','window'} else 'file_access' if operation=='save_as' else 'desktop_control')
        return self.runtime.uia.call(window, operation, self.engine.cancel_event,
            self.runtime.progress, timeout=min(allowance, max(.05, self.engine.task_deadline - time.monotonic())),
            authorize=lambda:self.runtime.authorize(self.engine.identity,permission), **args)

    def target(self, ref):
        ref = self.reference(ref)
        return self.runtime.windows.targets[ref]

    def reference(self, ref):
        if ref == '$browser':
            from .apps import DEFAULT_BROWSER_PROCESSES
            matches=[key for key,window in self.runtime.windows.targets.items() if window['process'] in DEFAULT_BROWSER_PROCESSES]
            active=self.runtime.windows.active_ref
            recent=getattr(self.engine,'last_browser_window',None)
            if active in matches:return active
            if recent in matches:return recent
            if len(matches)==1:return matches[0]
            raise AIClarification('Activate the browser window you mean, then repeat the request.')
        if ref == '$active':
            ref = self.runtime.windows.active_ref
        elif ref in {'$last', 'current'}:
            ref = self.engine.last_window or self.runtime.windows.active_ref
        if ref in self.runtime.windows.targets:
            return ref
        target = str(ref or '').casefold().strip()
        matches = [key for key, window in self.runtime.windows.targets.items() if
                   window['title'].casefold() == target or window['process'].casefold().removesuffix('.exe') == target
                   or self.runtime.windows.matches(window, target)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            if self.engine.last_window in matches:
                return self.engine.last_window
            raise AIClarification('Which window should I use? Activate it and repeat the task.')
        raise AIPlanError('The window reference did not match any current window. Choose an ID from the refreshed window list.')

    def observe(self, ref=''):
        runtime = self.runtime
        windows = runtime.windows.snapshot()
        registry = self.engine.registry
        registry.windows = windows
        registry.active_window = runtime.windows.active_ref
        registry.active_app = runtime.windows.active_app
        state = dict(windows=windows, active_window=registry.active_window)
        if ref:
            ref = self.reference(ref)
            view = self.call(self.target(ref), 'observe')
            self.views[ref] = view
            state['selected_window'] = ref
            state['interface'] = view
        registry.desktop_state = state
        return state

    def prepare(self, step):
        step = json.loads(json.dumps(step))
        name, args = step['tool'], step['arguments']
        if name=='save_document':
            source=self.save_source(args['path'])
            if source:
                args['window']=source
        if name == 'observe_desktop' and not args['window']:
            return step
        if 'window' in args:
            args['window'] = self.reference(args['window'])
        if name in {'ui_action', 'key_input', 'mouse_input'}:
            ref = args['window']
            if ref not in self.views:
                self.observe(ref)
            rows = self.views[ref]['elements']
            exact = [e for e in rows if e['id'] == args['element']]
            if not exact:
                candidates = [e for e in rows if args['element'] and args['element'].casefold() in
                              {e['name'].casefold(), e['automation_id'].casefold()}]
                if len(candidates) != 1:
                    raise AIPlanError('That element was not observed. Select its actual current ID from the refreshed interface; do not invent one.')
                args['element'] = candidates[0]['id']
        return step

    def guard(self, step):
        name, args = step['tool'], step['arguments']
        from .ai_system_apps import SYSTEM_TOOLS
        if name in SYSTEM_TOOLS:
            self.system_apps.guard(step)
        if self.input_window and name != 'observe_desktop':
            from .ai_windows import _user
            if int(_user.GetForegroundWindow() or 0) != self.input_window['hwnd']:
                raise AIError('The foreground window changed. Remaining actions stopped.')
        if name=='browser_action':
            from .ai_browser import CLOSING,tab_targets
            if args['operation'] in CLOSING:
                window=self.target(args['window'])
                state=self.call(window,'browser',action='tabs',text='')
                tabs=state['tabs']
                targets=tab_targets(tabs,args['operation'],args['text'])
                key=self.runtime._confirmation_key(step)
                self.browser_bindings[key]=dict(expected_tabs=[t['id'] for t in tabs],target_tabs=targets)
                self.browser_bindings[key]['expected_names']={t['id']:t['name'] for t in tabs if t['id'] in targets}
                return bool(targets)
        if name in {'file_action', 'save_document'}:
            path = self.files.path(args['path'], write=name == 'save_document' or args.get('operation') == 'write')
            if name == 'file_action' and args['operation'] == 'recycle':
                stat = path.stat()
                fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if str(path) in self.recycle_bindings and self.recycle_bindings[str(path)] != fingerprint:
                    raise AIError('The file changed after it was selected for recycling. Nothing was deleted.')
                self.recycle_bindings[str(path)] = fingerprint
        if name in {'ui_action', 'key_input', 'mouse_input'}:
            view = self.views.get(args['window'])
            if view is None:
                raise AIError('Inspect the target window before selecting an element.')
            element = next((e for e in view['elements'] if e['id'] == args['element']), None)
            if element is None:
                raise AIError('Choose an element from the current interface observation.')
            if (name == 'ui_action' and args['operation'] == 'set_text' and element['control'] == 50030
                    and element.get('value', '').strip() and args['window'] not in self.typed
                    and not re.search(r'\b(replace|overwrite|edit|change the text)\b', self.request, re.I)):
                raise AIError('This document already contains text. Create a new document before entering the draft.')
            # External effects need explicit user intent and fresh confirmation.
            label = element['name'] + ' ' + element['automation_id']
            sensitive = re.search(r'send|submit|post|publish|buy|purchase|pay|order|delete|remove|отправ|купить|оплат|удал|опубли', label, re.I)
            activating = name == 'mouse_input' and args['gesture'] in {'click', 'right_click'} or name == 'ui_action' and args['operation'] == 'invoke'
            negated_effect = re.search(r"(?:do not|don't|never|without|not)\s+(?:\w+\s+){0,3}(?:send|submit|post|publish|buy|purchase|pay|delet|remov)", self.request, re.I)
            if activating and sensitive and (negated_effect or not re.search(r'send|submit|post|publish|buy|purchase|pay|delete|remove|отправ|купить|оплат|удал|опубли', self.request, re.I)):
                raise AIError('Your request did not authorize sending, purchasing, publishing or deleting.')
            if activating:
                # Most arbitrary app buttons have unknowable effects. Confirm these,
                # except a small set of known reversible navigation controls.
                safe = re.fullmatch(r'back|forward|next|previous|cancel|help|search|find|new(?: tab| window)?|open|назад|далее|отмена|поиск', element['name'].strip(), re.I)
                if element['name'].strip().lower() == 'apply' and re.search(r'\b(?:click|press|choose)\s+apply\b', self.request, re.I):
                    safe = True
                return not bool(safe)
        return name == 'file_action' and args['operation'] == 'recycle'

    def execute(self, step):
        name, args = step['tool'], step['arguments']
        from .ai_system_apps import SYSTEM_TOOLS
        if name in SYSTEM_TOOLS:
            return self.system_apps.execute(step)
        if name == 'observe_desktop':
            self.observe(args['window'])
            return Result('verified', 'Desktop inspected.')
        if name == 'file_action':
            if args['operation']=='write':
                source=self.save_source(args['path'])
                if source:
                    return self.save(self.target(source),source,args['path'])
            return self.files.execute(args)
        if name == 'document_write':
            return self.write_document(args)
        window = self.target(args['window'])
        if name in {'ui_action', 'key_input', 'mouse_input', 'browser_action', 'save_document'} and self.input_window is None:
            # Focus+field validation is one semantic operation. The planner need
            # not spend a model call on this mechanical prerequisite. Once bound,
            # a later foreground change stops the task instead of reclaiming it.
            self.call(window, 'focus')
            self.input_window = dict(window)
        if name == 'window_action':
            result = self.call(window, args['operation'])
            if args['operation'] == 'focus':
                self.input_window = dict(window)
            elif args['operation'] == 'minimize' and self.input_window and self.input_window['hwnd'] == window['hwnd']:
                self.input_window = None  # This focus loss was explicitly requested.
        elif name in {'ui_action', 'key_input', 'mouse_input'}:
            self.guard(step)
            view = self.views[args['window']]
            common = dict(element=args['element'], view=view['view'])
            if name == 'ui_action':
                require_empty = (args['operation'] == 'set_text' and args['window'] not in self.typed
                                 and not re.search(r'\b(replace|overwrite|edit|change the text)\b', self.request, re.I)
                                 and any(e['id'] == args['element'] and e['control'] == 50030 for e in view['elements']))
                result = self.call(window, args['operation'], text=args['text'], require_empty=require_empty, **common)
                if args['operation'] == 'set_text' and result['status'] == 'verified':
                    self.typed[args['window']] = args['text']
                    current_goal=self.engine.registry.task_progress.get('current_goal')
                    if current_goal:self.documents_by_goal[current_goal]=args['window']
            elif name == 'key_input':
                # Saving to an unknown path / implicit overwrites require save_document.
                if args['chord'] in {'ctrl+s', 'ctrl+shift+s'}:
                    raise AIError('Use save_document with an explicit task path to save.')
                result = self.call(window, 'keys', chord=args['chord'], **common)
            else:
                result = self.call(window, 'mouse', gesture=args['gesture'], **common)
        elif name == 'save_document':
            return self.save(window, args['window'], args['path'])
        elif name == 'browser_action':
            return self.browser(window, args)
        else:
            raise AIError('Unsupported desktop action.')
        return Result(result['status'], result['message'])

    def write_document(self, args):
        application = args['application']
        ref = self.engine.last_window if self.engine.last_window in self.runtime.windows.targets else self.runtime.windows.active_ref
        current = self.runtime.windows.targets.get(ref)
        if application == 'current':
            if current and current['process'] in {'notepad.exe', 'winword.exe'}:
                application = 'word' if current['process'] == 'winword.exe' else 'notepad'
            else:
                raise AIClarification('Which editor should I use: Notepad or Word?')
        if application == 'word':
            selected = current if current and current['process'] == 'winword.exe' else None
            result = self.call(selected or {}, 'word_document', text=args['text'], new_document=args['new_document'])
        else:
            matches = [w for w in self.runtime.windows.targets.values() if w['process'] == 'notepad.exe']
            selected = current if current and current['process'] == 'notepad.exe' else matches[0] if len(matches) == 1 else None
            if not selected and matches and args['new_document']:
                selected = matches[0]  # Only uses File > New window, never this document's text.
            if not selected:
                from .apps import launch_app
                app = next((a for a in self.runtime.host.apps if a['id'] == 'notepad'), None)
                app = app or next((a for a in self.runtime.catalog.entries if a['display_name'].casefold() == 'notepad'), None)
                if not app or not launch_app(app):
                    raise AIError('Notepad could not be opened from its Windows registration.')
                end = time.monotonic() + 4
                while time.monotonic() < end:
                    self.engine._check(self.engine.task_deadline)
                    self.runtime.windows.snapshot()
                    selected = next((w for w in self.runtime.windows.targets.values() if w['process'] == 'notepad.exe'), None)
                    if selected:
                        break
                    self.engine.cancel_event.wait(.025)
            if not selected:
                raise AIError('The Notepad editor window did not appear.')
            if args['new_document']:
                created = self.call(selected, 'notepad_new_document')
                selected = created['window']
            self.call(selected, 'focus')
            state = self.call(selected, 'observe')
            editors = [e for e in state['elements'] if e['control'] in {50004, 50030} and e['enabled']]
            if len(editors) != 1:
                raise AIError('The document does not expose one editable text area.')
            if editors[0]['value'].strip():
                raise AIPlanError('No text was inserted: the selected document already contains user text. For this new draft use document_write with new_document=true; preserve the existing document.')
            self.call(selected, 'set_text', element=editors[0]['id'], view=state['view'], text=args['text'], require_empty=True)
            result = dict(status='verified', message='Written.', window=selected, word_count=len(args['text'].split()))
        self.runtime.windows.snapshot()
        window = result['window']
        ref = next((key for key, row in self.runtime.windows.targets.items() if
                    row['hwnd'] == window['hwnd'] and row['pid'] == window['pid']), None)
        if ref is None:
            raise AIError('Text was written, but the document window could not be tracked. Check the editor.')
        self.typed[ref] = args['text']
        current_goal=self.engine.registry.task_progress.get('current_goal')
        if current_goal:self.documents_by_goal[current_goal]=ref
        self.input_window = dict(window)
        self.engine.registry.windows = [{'id': key, 'process': row['process'], 'title': row['title'][:100]}
                                       for key, row in self.runtime.windows.targets.items()][:24]
        return Result('verified', f'Written ({result["word_count"]} words).', data=dict(window=ref, app='word' if application == 'word' else 'notepad',
                      word_count=result['word_count'], hwnd=window['hwnd'], pid=window['pid']))

    def save_source(self,value):
        spec=self.engine.registry.task_progress.get('current_goal_spec',{})
        for criterion in spec.get('criteria',[]):
            source=criterion.get('source_goal')
            if criterion['kind']=='file_saved' and source and self.files.path(criterion['target'],write=True)==self.files.path(value,write=True):
                ref=self.documents_by_goal.get(source)
                if ref not in self.typed:
                    raise AIPlanError('The source document has not been written and verified. Complete its document goal first.')
                return ref
        return None

    def save(self, window, ref, value):
        path = self.files.path(value, write=True)
        if path.exists():
            raise AIError('The output file already exists. Choose a new file path.')
        if ref not in self.typed:
            raise AIError('Only a document entered and verified in this task can be saved automatically.')
        self.call(window,'focus')
        self.input_window=dict(window)
        # Only documented Save As sequence; never blind coordinates or guessed labels.
        view = self.call(window, 'observe')
        editable = [e for e in view['elements'] if e['control'] in {50004, 50030}]
        if len(editable) != 1:
            raise AIError('The document field is ambiguous. Select it before saving.')
        self.call(window, 'keys', chord='ctrl+shift+s', element=editable[0]['id'], view=view['view'])
        end = time.monotonic() + 2
        dialog = None
        while time.monotonic() < end:
            self.engine._check(self.engine.task_deadline)
            self.runtime.windows.snapshot()
            from .ai_windows import owned_by
            candidates = [w for w in self.runtime.windows.targets.values() if w['pid'] == window['pid']
                          and owned_by(w['hwnd'], window['hwnd'])]
            if len(candidates) == 1:
                dialog = candidates[0]
            if dialog:
                break
            self.engine.cancel_event.wait(.04)
        if not dialog:
            raise AIError('The application did not expose a supported Save As dialog. No file was reported saved.')
        self.call(dialog, 'save_as', path=str(path))
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            self.engine._check(self.engine.task_deadline)
            if path.is_file():
                try:
                    actual = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
                    if actual == self.typed[ref].replace('\r\n', '\n').replace('\r', '\n'):
                        criteria=self.engine.registry.task_progress.get('current_goal_spec',{}).get('criteria',[])
                        if not isinstance(criteria,list):
                            criteria=[]
                        source=next((c['source_goal'] for c in criteria if c['kind']=='file_saved' and c.get('source_goal')
                            and self.documents_by_goal.get(c['source_goal'])==ref),
                            next((goal for goal,document in self.documents_by_goal.items() if document==ref),''))
                        return Result('verified', 'Saved.',data=dict(window=ref,path=str(path),source_goal=source,effect='document_saved'))
                except (OSError, UnicodeError):
                    pass
            self.engine.cancel_event.wait(.025)
        raise AIError('Save could not be verified by file readback. Check the dialog; the save will not be repeated.')

    def browser(self, window, args):
        from .apps import DEFAULT_BROWSER_PROCESSES
        if window['process'] not in DEFAULT_BROWSER_PROCESSES:
            raise AIError('Select a browser window first.')
        step=dict(tool='browser_action',arguments=args)
        binding=self.browser_bindings.pop(self.runtime._confirmation_key(step),{})
        result=self.call(window,'browser',action=args['operation'],text=args['text'],**binding)
        self.engine.last_browser_window=args['window']
        return Result(result['status'],result['message'],data=dict(window=args['window'],effect='browser_'+args['operation'],
            **{key:result[key] for key in ('closed_count','tabs') if key in result}))
