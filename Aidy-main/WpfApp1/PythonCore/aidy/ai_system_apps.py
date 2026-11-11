"""Semantic Windows app operations; effects run in the bounded UI worker."""
from pathlib import Path
import re

from .ai_engine import Result
from .ai_tools import AIError
from .ai_files import known_folders


SYSTEM_TOOLS = {'recycle_bin', 'explorer_action', 'calculator_action', 'notepad_action', 'settings_page'}
SETTINGS = dict(display='display', sound='sound', bluetooth='bluetooth', wifi='network-wifi',
                network='network', apps='appsfeatures', storage='storagesense', notifications='notifications',
                accessibility='easeofaccess', windows_update='windowsupdate', date_time='dateandtime',
                personalization='personalization')


def arithmetic(value):
    """Validate keystrokes, not executable code. Calculator performs the math."""
    value = value.strip().replace('×', '*').replace('÷', '/').replace('−', '-')
    if not value or len(value) > 200 or not re.fullmatch(r'[0-9+*/%().\s-]+', value):
        raise AIError('Use a numeric arithmetic expression with + - * / % and parentheses.')
    # Reject malformed input before changing the current calculation.
    import ast
    try:
        tree = ast.parse(value, mode='eval')
    except (SyntaxError, ValueError):
        raise AIError('The arithmetic expression is incomplete.') from None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub,
               ast.Mult, ast.Div, ast.UAdd, ast.USub)
    if any(not isinstance(node, allowed) or isinstance(node, ast.Constant) and
           (type(node.value) not in {int, float} or len(str(node.value)) > 100) for node in ast.walk(tree)):
        raise AIError('Use ordinary arithmetic; powers, functions and code are unavailable.')
    return ''.join(value.split())


class SystemApps:
    def __init__(self, task):
        self.task = task
        self.bin_bindings = {}

    def folder_path(self, value):
        names = known_folders()
        if value.casefold().strip() in names:
            value = names[value.casefold().strip()]
        return self.task.files.path(value)

    def guard(self, step):
        name, args = step['tool'], step['arguments']
        if name == 'explorer_action':
            self.folder_path(args['path'])
        if name == 'calculator_action' and args['operation'] == 'calculate':
            arithmetic(args['expression'])
        if name == 'recycle_bin' and args['operation'] in {'empty', 'restore'}:
            if not re.search(r'\b(?:empty|clear|clean|restore|recover)\b|очист|восстанов', self.task.request, re.I):
                raise AIError('Request emptying or restoring the Recycle Bin explicitly first.')
            state = self.task.call({}, 'system_app', tool='recycle_bin', arguments={'operation': 'list', 'item': ''})
            key = self.task.runtime._confirmation_key(step)
            signature = state['fingerprint']
            if key in self.bin_bindings and self.bin_bindings[key] != signature:
                raise AIError('The Recycle Bin changed during confirmation. Nothing was changed.')
            self.bin_bindings[key] = signature
        return False

    def execute(self, step):
        name, args = step['tool'], dict(step['arguments'])
        task = self.task
        window = {}
        if name == 'explorer_action':
            path = self.folder_path(args['path'])
            args['path'] = str(path)
            if args['operation'] in {'list', 'search'}:
                return task.files.execute(dict(operation=args['operation'], path=str(path), text=args['query'], destination=''))
        elif name == 'notepad_action':
            ref = task.reference(args['window'])
            window = task.target(ref)
            if window['process'] != 'notepad.exe':
                raise AIError('Select a Notepad document first.')
            if args['operation'] in {'append', 'replace'}:
                task.input_window = dict(window)
        elif name == 'calculator_action' and args['operation'] == 'calculate':
            args['expression'] = arithmetic(args['expression'])
        if name == 'recycle_bin' and args['operation'] in {'empty', 'restore'}:
            args['expected_fingerprint'] = self.bin_bindings.get(task.runtime._confirmation_key(step), '')
            if not args['expected_fingerprint']:
                raise AIError('Inspect and confirm the Recycle Bin operation first.')
        result = task.call(window, 'system_app', tool=name, arguments=args)
        if name == 'notepad_action' and 'document_text' in result:
            task.typed[ref] = result.pop('document_text')
            current_goal=task.engine.registry.task_progress.get('current_goal')
            if current_goal:
                task.documents_by_goal[current_goal]=ref
            task.views.pop(ref, None)
        if name != 'notepad_action':
            task.input_window = None  # This semantic operation explicitly changes focus.
        return Result(result['status'], result['message'], data=result.get('data'))


def recycle_state(shell):
    import hashlib
    import json
    namespace = shell.NameSpace(10)
    items = namespace.Items()
    if items.Count > 4000:
        raise AIError('The Recycle Bin has too many items for a bounded operation. Use Windows directly.')
    rows, objects = [], {}
    for item in items:
        ident = hashlib.sha256(item.Path.encode('utf-8')).hexdigest()[:24]
        row = dict(id=ident, name=str(item.ExtendedProperty('System.FileName') or item.Name),
                   original_folder=str(item.ExtendedProperty('System.Recycle.DeletedFrom') or ''),
                   size=str(item.ExtendedProperty('System.Size') or ''),
                   deleted=str(item.ExtendedProperty('System.Recycle.DateDeleted') or ''))
        rows.append(row)
        objects[ident] = item
    fingerprint = hashlib.sha256(json.dumps(sorted(rows, key=lambda r: r['id']), sort_keys=True).encode()).hexdigest()
    return rows, objects, fingerprint


def worker_run(automation, window, name, args):
    """Only fixed shell APIs and app-bound UIA input; never shell command text."""
    import json
    import time
    import win32com.client
    shell = win32com.client.Dispatch('Shell.Application') if name in {'recycle_bin', 'explorer_action'} else None
    if name == 'recycle_bin':
        action = args['operation']
        if action == 'open':
            automation.effect()
            shell.Explore('shell:RecycleBinFolder')
            target = shell.NameSpace(10).Self.Path.casefold()
            end = time.monotonic()+3
            while time.monotonic()<end:
                for browser in shell.Windows():
                    try:
                        if browser.Document.Folder.Self.Path.casefold() == target:
                            return dict(status='verified', message='Recycle Bin opened in File Explorer.')
                    except Exception:
                        continue
                time.sleep(.05)
            return dict(status='dispatched', message='Recycle Bin navigation requested; its window could not be verified.')
        rows, objects, fingerprint = recycle_state(shell)
        if action == 'list':
            return dict(status='verified', message=json.dumps(dict(count=len(rows), items=rows[:20], truncated=len(rows)>20), ensure_ascii=False),
                        data=dict(count=len(rows), items=rows[:20], truncated=len(rows)>20), fingerprint=fingerprint)
        if not args.get('expected_fingerprint') or args['expected_fingerprint'] != fingerprint:
            raise AIError('The Recycle Bin changed after confirmation. Nothing was changed.')
        if action == 'empty':
            import ctypes
            from ctypes import wintypes
            empty = ctypes.WinDLL('shell32').SHEmptyRecycleBinW
            empty.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
            empty.restype = ctypes.c_long
            automation.effect()
            if empty(None, None, 7) < 0:
                raise AIError('Windows could not empty the Recycle Bin.')
            remaining, _, _ = recycle_state(shell)
            if remaining:
                raise AIError('Some Recycle Bin items remain. The operation will not be repeated automatically.')
            return dict(status='verified', message='Recycle Bin emptied.', data=dict(count=0))
        if action == 'restore':
            item = objects.get(args['item'])
            row = next((r for r in rows if r['id'] == args['item']), None)
            if not item or not row or not row['original_folder']:
                raise AIError('Select one actual item ID from the Recycle Bin list.')
            destination = Path(row['original_folder']) / row['name']
            from .ai_files import TaskFiles
            destination = TaskFiles.canonical(str(destination))
            if not destination.parent.is_dir() or destination.exists():
                raise AIError('The original folder is missing or the destination already exists. Nothing was restored.')
            automation.effect()
            item.InvokeVerb('undelete')
            end = time.monotonic()+3
            while time.monotonic()<end:
                _, current, _ = recycle_state(shell)
                if args['item'] not in current and destination.exists():
                    return dict(status='verified', message='Item restored.', data=dict(path=str(destination)))
                time.sleep(.05)
            raise AIError('Restore was requested, but its outcome could not be verified. It will not be repeated.')
        raise AIError('Unsupported Recycle Bin operation.')
    if name == 'explorer_action':
        path = Path(args['path'])
        if not path.exists():
            raise AIError('The selected path does not exist.')
        folder = path if path.is_dir() else path.parent
        automation.effect()
        shell.Explore(str(folder))
        end = time.monotonic()+3
        while time.monotonic()<end:
            for browser in shell.Windows():
                try:
                    if Path(browser.Document.Folder.Self.Path).resolve() != folder.resolve():
                        continue
                    if args['operation'] == 'select':
                        item = browser.Document.Folder.ParseName(path.name)
                        if item is None:
                            raise AIError('The file no longer exists in this folder.')
                        automation.effect()
                        browser.Document.SelectItem(item, 0x1D)
                        if not any(Path(i.Path).resolve() == path.resolve() for i in browser.Document.SelectedItems()):
                            continue
                    return dict(status='verified', message='Selected in File Explorer.' if args['operation']=='select' else 'Folder opened in File Explorer.', data=dict(path=str(path)))
                except AIError:
                    raise
                except Exception:
                    continue
            time.sleep(.05)
        return dict(status='dispatched', message='Explorer navigation requested; its view could not be verified.')
    if name == 'settings_page':
        import os
        page = SETTINGS.get(args['page'])
        if page is None:
            raise AIError('Unsupported Windows Settings page.')
        automation.effect()
        os.startfile('ms-settings:'+page)
        return dict(status='dispatched', message='Windows Settings page requested.')
    if name == 'calculator_action':
        return calculator(automation, args)
    if name == 'notepad_action':
        return notepad(automation, window, args)
    raise AIError('Unsupported system application operation.')


def calculator(automation, args):
    import os
    import time
    import win32gui
    from .ai_windows import owner, process_name
    def windows():
        found=[]
        def visit(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                pid=owner(hwnd)
                if pid and process_name(pid) in {'calculatorapp.exe','calculator.exe','calc.exe'}:
                    found.append(dict(hwnd=hwnd,pid=pid,process=process_name(pid),title=win32gui.GetWindowText(hwnd)))
        win32gui.EnumWindows(visit,None)
        return found
    matches=windows()
    if not matches:
        automation.effect()
        os.startfile('calculator:')
        end=time.monotonic()+4
        while not matches and time.monotonic()<end:
            time.sleep(.05)
            matches=windows()
    if len(matches)!=1:
        raise AIError('Open exactly one Windows Calculator window and try again.')
    window=matches[0]
    automation.focus(window)
    root=automation.check(window,True)
    def element(ident):
        found=root.FindAll(4,automation.client.CreatePropertyCondition(30011,ident))
        if found.Length!=1:
            raise AIError('Calculator does not expose the required control. Choose Standard or Scientific mode.')
        return found.GetElement(0)
    def press(ident):
        automation.check(window,True)
        automation.invoke(element(ident),'invoke')
    if args['operation'] in {'calculate','clear'}:
        expression=arithmetic(args['expression']) if args['operation']=='calculate' else ''
        keys={'+':'plusButton','-':'minusButton','*':'multiplyButton','/':'divideButton',
              '.':'decimalSeparatorButton','(':'openParenthesisButton',')':'closeParenthesisButton'}
        if expression:
            # Standard mode evaluates left-to-right; Scientific mode is required
            # to preserve arithmetic precedence and parentheses.
            # Alt+2 is Calculator's documented Scientific-mode shortcut.
            import pyautogui
            automation.check(window,True)
            automation.effect()
            pyautogui.hotkey('alt','2')
            end=time.monotonic()+2
            while True:
                root=automation.check(window,True)
                try:
                    control=element('openParenthesisButton')
                    if control.CurrentIsEnabled and not control.CurrentIsOffscreen:
                        break
                except AIError:
                    pass
                if time.monotonic()>=end:
                    raise AIError('Scientific Calculator mode could not be verified. No expression was entered.')
                time.sleep(.04)
        # Build and validate the complete input sequence before clearing.
        ids=[]
        tokens=re.findall(r'\d+(?:\.\d*)?|\.\d+|[()+*/-]',expression)
        previous=None
        negative=False
        for token in tokens:
            if token in {'+','-'} and (previous is None or previous in {'(','+','-','*','/'}):
                if negative:
                    raise AIError('Repeated unary signs are unsupported.')
                negative=token=='-'
                continue
            if token[0].isdigit() or token[0]=='.':
                ids.extend('num'+c+'Button' if c.isdigit() else 'decimalSeparatorButton' for c in token)
                if negative:
                    ids.append('negateButton')
                    negative=False
            else:
                if negative:
                    raise AIError('Use a negative number instead of negating a parenthesized expression.')
                ids.append(keys[token])
            previous=token
        for ident in {'clearButton','equalButton',*ids}:
            element(ident)
        press('clearButton')
        for ident in ids:
            press(ident)
        if expression:
            press('equalButton')
    display=element('CalculatorResults')
    value=automation.text(display) or display.CurrentName
    if not value.strip():
        raise AIError('Calculator result could not be read.')
    if args['operation']=='calculate' and re.search(r'cannot|undefined|overflow|invalid|error|делени|ошиб|невозможно',value,re.I):
        raise AIError('Calculator reported an arithmetic error: '+value[:200])
    return dict(status='verified',message=value,data=dict(calculator_display=value))


def notepad(automation, window, args):
    if window.get('process')!='notepad.exe':
        raise AIError('Select a Notepad document first.')
    automation.focus(window)
    state=automation.snapshot(window)
    editors=[e for e in state['elements'] if e['control'] in {50004,50030} and e['enabled']]
    if len(editors)!=1:
        raise AIError('Notepad must expose exactly one document editor.')
    editor=automation.element(window,editors[0]['id'],state['view'])
    text=automation.text(editor,16002).replace('\r\n','\n').replace('\r','\n')
    if len(text)>16000:
        raise AIError('This document exceeds the supported 16000-character editing limit.')
    action=args['operation']
    if action=='read':
        return dict(status='verified',message=text or '(Empty document)',document_text=text,
                    data=dict(characters=len(text)))
    query=args['query']
    if action in {'find','replace'} and not query:
        raise AIError('Provide the exact text to find.')
    if action in {'find','replace'} and query not in text:
        return dict(status='verified',message='Text was not found; the document was unchanged.',data=dict(matches=0))
    if action=='find':
        pattern=automation.pattern(editor,10014,'IUIAutomationTextPattern')
        if not pattern:
            raise AIError('This Notepad editor does not support text selection.')
        match=pattern.DocumentRange.FindText(query,False,False)
        if not match:
            raise AIError('The matching text range could not be selected.')
        automation.check(window,True)
        automation.effect()
        match.Select()
        selected=pattern.GetSelection()
        if selected.Length!=1 or selected.GetElement(0).GetText(-1)!=query:
            raise AIError('The selected text could not be verified.')
        return dict(status='verified',message='Matching text selected.',data=dict(matches=text.count(query)))
    updated=text+args['text'] if action=='append' else text.replace(query,args['text']) if action=='replace' else None
    if updated is None or len(updated)>16000:
        raise AIError('Unsupported edit or document size limit exceeded.')
    automation.check(window,True)
    automation.effect()
    automation.set_text(window,editor,updated)
    return dict(status='verified',message='Document edited and read back; changes are unsaved.',document_text=updated,
                data=dict(characters=len(updated)))
