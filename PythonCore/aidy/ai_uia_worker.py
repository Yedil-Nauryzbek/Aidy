"""Fixed UI Automation RPC worker. Never evaluates model-supplied code.

Separate process bounds unresponsive accessibility providers. A timed-out mutation
is not retried. One persistent COM client serves one authenticated task at a time.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import sys
import time
from collections import deque

import comtypes.client
from .ai_windows import owner, process_name
from .ai_tools import AIError, AIPlanError

U = ctypes.WinDLL('user32', use_last_error=True)
U.GetForegroundWindow.restype = wintypes.HWND
U.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
U.GetAncestor.restype = wintypes.HWND
U.GetLastActivePopup.argtypes = [wintypes.HWND]
U.GetLastActivePopup.restype = wintypes.HWND
U.SetForegroundWindow.argtypes = [wintypes.HWND]
U.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
U.IsWindowEnabled.argtypes = [wintypes.HWND]
U.IsIconic.argtypes = [wintypes.HWND]
U.IsZoomed.argtypes = [wintypes.HWND]
U.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
U.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
U.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
U.GetWindow.restype = wintypes.HWND
U.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
U.SendMessageTimeoutW.restype = ctypes.c_ssize_t
U.GetWindowTextLengthW.argtypes = [wintypes.HWND]
U.GetWindowTextLengthW.restype = ctypes.c_int
U.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
U.WindowFromPoint.argtypes = [wintypes.POINT]
U.WindowFromPoint.restype = wintypes.HWND

# Added for app navigation (search boxes, quick switchers, back/forward).
# Destructive or sending chords (alt+f4, ctrl+enter, delete...) are absent.
NAVIGATION_KEYS = {'enter', 'space', 'backspace', 'ctrl+f', 'ctrl+k', 'ctrl+l', 'ctrl+n', 'ctrl+t',
                   'ctrl+tab', 'ctrl+shift+tab', 'alt+left', 'alt+right', 'f5'}
TERMINALS = {'cmd.exe', 'powershell.exe', 'powershell_ise.exe', 'pwsh.exe', 'windowsterminal.exe',
             'conhost.exe', 'mintty.exe', 'wsl.exe', 'bash.exe'}


def moved(bounds, rect, tolerance=4):
    """The control is no longer where its number was drawn ([x, y, w, h] in pixels)."""
    x, y, w, h = rect
    return (abs(bounds.left - x) > tolerance or abs(bounds.top - y) > tolerance or
            abs(bounds.right - bounds.left - w) > tolerance or abs(bounds.bottom - bounds.top - h) > tolerance)


def send_unicode(text):
    """One real Unicode key burst into the current focus (no Enter, no shortcuts)."""
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class UNION(ctypes.Union):
        _fields_ = [('key', KEYBDINPUT), ('padding', ctypes.c_byte * (32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24))]
    class INPUT(ctypes.Structure):
        _fields_ = [('type', wintypes.DWORD), ('data', UNION)]
    U.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    encoded = text.encode('utf-16-le')
    units = [int.from_bytes(encoded[i:i+2], 'little') for i in range(0, len(encoded), 2)]
    items = (INPUT * (len(units) * 2))()
    for i, unit in enumerate(units):
        items[2*i] = INPUT(1, UNION(key=KEYBDINPUT(0, unit, 4, 0, 0)))
        items[2*i+1] = INPUT(1, UNION(key=KEYBDINPUT(0, unit, 6, 0, 0)))
    if items and U.SendInput(len(items), items, ctypes.sizeof(INPUT)) != len(items):
        raise AIError('Windows blocked text input; the field may be partially filled.')


class Automation:
    def __init__(self):
        self.types = comtypes.client.GetModule('UIAutomationCore.dll')
        self.client = comtypes.client.CreateObject(self.types.CUIAutomation, interface=self.types.IUIAutomation)
        self.elements = {}
        self.views = {}
        self.observations = {}
        self.effect_started = False
        self.request_id = None
        self.word = None

    def effect(self):
        if self.request_id is not None:
            # Permission precedes every semantic input, including each tab in a
            # batch. The parent records uncertainty before sending its permit.
            print(json.dumps(dict(id=self.request_id, phase='ready_for_effect')), flush=True)
            try:permit=json.loads(sys.stdin.readline())
            except (ValueError,TypeError):raise AIError('Cancelled before mutation.') from None
            if permit != dict(id=self.request_id,permit_effect=True):
                raise AIError('Cancelled before mutation.')
        self.effect_started = True

    def check(self, window, foreground=False):
        hwnd, pid = window['hwnd'], window['pid']
        if owner(hwnd) != pid or process_name(pid) != window['process']:
            raise AIError('The target window or process changed. Select it again.')
        popup = int(U.GetLastActivePopup(hwnd) or 0)
        modal = False
        if popup != hwnd and popup and U.IsWindowVisible(popup):
            # Browsers also own nonmodal tooltips/find popovers. Only a real
            # modal window or disabled owner blocks an otherwise bound target.
            candidate = self.client.ElementFromHandle(popup)
            pattern = self.pattern(candidate, 10009, 'IUIAutomationWindowPattern')
            modal = bool(pattern and pattern.CurrentIsModal)
        if not U.IsWindowEnabled(hwnd) or modal:
            raise AIError('An unexpected dialog is open. Resolve it or select it explicitly.')
        if foreground and int(U.GetForegroundWindow() or 0) != hwnd:
            raise AIError('The foreground window changed. No input was sent.')
        return self.client.ElementFromHandle(hwnd)

    def pattern(self, element, number, interface):
        try:
            return element.GetCurrentPattern(number).QueryInterface(getattr(self.types, interface))
        except Exception:
            return None

    def text(self, element, limit=4000):
        if element.CurrentIsPassword:
            return ''
        pattern = self.pattern(element, 10002, 'IUIAutomationValuePattern')
        if pattern:
            return pattern.CurrentValue[:limit]
        pattern = self.pattern(element, 10014, 'IUIAutomationTextPattern')
        return pattern.DocumentRange.GetText(limit) if pattern else ''

    def entered_prefix(self, element):
        # Rich editors may expose a final paragraph marker even when it is not
        # part of saved text. The range before the caret proves accepted input
        # and, unlike rstrip(), distinguishes a processed Enter from a pending one.
        if element.CurrentControlType == 50030:
            pattern = self.pattern(element, 10014, 'IUIAutomationTextPattern')
            if pattern:
                selection = pattern.GetSelection()
                if selection.Length == 1:
                    prefix = pattern.DocumentRange.Clone()
                    prefix.MoveEndpointByRange(1, selection.GetElement(0), 0)
                    return prefix.GetText(4000)
        return self.text(element)

    def snapshot(self, window, chrome_only=False):
        root = self.check(window)
        cache = self.client.CreateCacheRequest()
        cache.TreeScope = 1
        for prop in (30002, 30003, 30004, 30005, 30010, 30011, 30019, 30022):
            cache.AddProperty(prop)
        root = root.BuildUpdatedCache(cache)
        queue = deque([(root, 0)])
        rows, references = [], {}
        walker = self.client.ControlViewWalker
        scanned = 0
        end = time.monotonic() + (1.1 if chrome_only else 1.6)
        from .apps import DEFAULT_BROWSER_PROCESSES
        browser = window['process'] in DEFAULT_BROWSER_PROCESSES
        # Collect more, then keep interactive controls first: web/Electron apps
        # bury their inputs below many static text rows.
        row_limit=256 if chrome_only else 400
        process_cache={window['pid']:window['process']}
        while queue and scanned < (1000 if chrome_only else 2200) and len(rows) < row_limit and time.monotonic() < end:
            element, depth = queue.popleft()
            scanned += 1
            try:
                pid=element.CachedProcessId
                if pid not in process_cache:process_cache[pid]=process_name(pid)
                # WebView2 apps (e.g. WhatsApp desktop) host their page in an embedded child process.
                # UWP apps (Calculator, Settings) draw their content from the app's own
                # process inside an ApplicationFrameHost window.
                if (process_cache[pid] not in {window['process'], 'msedgewebview2.exe'}
                        and window['process'] != 'applicationframehost.exe') or element.CachedIsPassword:
                    continue
                runtime = list(element.GetRuntimeId())
                ref = 'e' + hashlib.sha256(json.dumps([window['hwnd'], window['pid'], runtime]).encode()).hexdigest()[:16]
                kind = int(element.CachedControlType)
                if not element.CachedIsOffscreen or chrome_only and kind==50019:
                    row = dict(id=ref, name=element.CachedName[:160], automation_id=element.CachedAutomationId[:100],
                               control=kind, enabled=bool(element.CachedIsEnabled),
                               value=self.text(element)[:1000] if kind==50004 or kind==50030 and not browser else '')
                    row['control_type'] = {50000: 'Button', 50003: 'ComboBox', 50004: 'Edit', 50008: 'List',
                        50011: 'MenuItem', 50019: 'TabItem', 50020: 'Text', 50030: 'Document',
                        50032: 'Window', 50033: 'Pane'}.get(kind, element.CachedLocalizedControlType)
                    selection = self.pattern(element, 10010, 'IUIAutomationSelectionItemPattern') if kind in {50007,50013,50019} else None
                    if selection:
                        row['selected'] = bool(selection.CurrentIsSelected)
                    toggle = self.pattern(element, 10015, 'IUIAutomationTogglePattern') if kind==50002 else None
                    if toggle:
                        row['toggle'] = int(toggle.CurrentToggleState)
                    rows.append(row)
                    references[ref] = element
                if depth < (14 if chrome_only else 24) and not (chrome_only and kind in {50030,50019}):
                    child = walker.GetFirstChildElementBuildCache(element, cache)
                    while child and len(queue) < 1200:
                        queue.append((child, depth + 1))
                        child = walker.GetNextSiblingElementBuildCache(child, cache)
            except Exception:
                continue
        truncated = bool(queue)
        if not chrome_only:
            interactive = {50000, 50002, 50003, 50004, 50005, 50007, 50011, 50013, 50019, 50024, 50029, 50030, 50031}
            rows = sorted(rows, key=lambda r: r['control'] not in interactive)  # stable: tree order kept
            truncated = truncated or len(rows) > 220
            rows = rows[:220]
            references = {r['id']: references[r['id']] for r in rows}
        signature = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        self.elements[window['hwnd']] = references
        self.views[window['hwnd']] = signature
        self.observations[window['hwnd']] = {r['id']:r for r in rows}
        return dict(elements=rows, view=signature, truncated=truncated, foreground=int(U.GetForegroundWindow() or 0) == window['hwnd'])

    # Button, CheckBox, ComboBox, Edit, Hyperlink, ListItem, MenuItem, RadioButton,
    # TabItem, TreeItem, SplitButton, DataItem: what a person would click.
    TARGET_CONTROLS = frozenset({50000, 50002, 50003, 50004, 50005, 50007, 50011, 50013, 50019, 50024, 50031, 50029})

    def _hit_belongs(self, element, hit):
        """The element under a point is the control itself or one of its children
        (a button's icon or text): clicking it activates that control."""
        current = hit
        for _ in range(7):
            if not current:
                return False
            if self.client.CompareElements(current, element):
                return True
            current = self.client.ControlViewWalker.GetParentElement(current)
        return False

    def targets(self, window, limit=220):
        """Visible, enabled, clickable controls of the window with screen rectangles
        (physical pixels), for voice clicks and numbered labels. Read-only."""
        try:
            U.SetProcessDPIAware()  # the same pixels the mouse operation clicks
        except (AttributeError, OSError):
            pass
        state = self.snapshot(window)
        references = self.elements.get(window['hwnd'], {})
        found = []
        for row in state['elements']:
            if row['control'] not in self.TARGET_CONTROLS or not row['enabled']:
                continue
            try:
                bounds = references[row['id']].CurrentBoundingRectangle
            except Exception:
                continue
            width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
            if width <= 2 or height <= 2:
                continue
            center = wintypes.POINT(bounds.left + width // 2, bounds.top + height // 2)
            if int(U.GetAncestor(U.WindowFromPoint(center), 2) or 0) != window['hwnd']:
                continue  # covered by another window
            try:
                if not self._hit_belongs(references[row['id']], self.client.ElementFromPoint(center)):
                    continue  # clipped or covered inside the window: a click would be refused
            except Exception:
                continue
            found.append(dict(id=row['id'], name=row['name'], automation_id=row['automation_id'],
                              control_type=row['control_type'], x=bounds.left, y=bounds.top, w=width, h=height))
            if len(found) >= limit:
                break
        # A cut-off scan may hide a second control with the same name.
        return dict(view=state['view'], targets=found, truncated=bool(state['truncated']) or len(found) >= limit)

    def focus(self, window):
        root = self.check(window)
        if U.IsIconic(window['hwnd']):
            U.ShowWindow(window['hwnd'], 9)
        try:
            root.SetFocus()
        except Exception:
            pass
        if not U.SetForegroundWindow(window['hwnd']):
            # This worker is disposable and bounded by its parent even if an app
            # stops servicing its input queue. Always detach; no system setting
            # or foreground-lock timeout is modified.
            current = ctypes.WinDLL('kernel32').GetCurrentThreadId()
            foreground = U.GetWindowThreadProcessId(U.GetForegroundWindow(), None)
            attached = foreground != current and U.AttachThreadInput(current, foreground, True)
            try:
                U.SetForegroundWindow(window['hwnd'])
            finally:
                if attached:
                    U.AttachThreadInput(current, foreground, False)
        end = time.monotonic() + 1.5
        unlocked = False
        while time.monotonic() < end:
            if int(U.GetForegroundWindow() or 0) == window['hwnd']:
                self.check(window, True)
                return
            if not unlocked and time.monotonic() > end - 1.2:
                # Foreground lock: a background helper may activate a window only
                # after it produced the last input event. One Alt tap is the
                # standard minimal input for that; no text or shortcut is sent.
                unlocked = True
                U.keybd_event(0x12, 0, 0, 0)
                U.keybd_event(0x12, 0, 2, 0)
                U.SetForegroundWindow(window['hwnd'])
            time.sleep(.02)
        raise AIError('Windows refused focus. Activate the target window and try again.')

    def element(self, window, ref, view):
        self.check(window, True)
        seen = self.observations.get(window['hwnd'], {}).get(ref)
        current = self.snapshot(window)
        fresh = self.observations[window['hwnd']].get(ref)
        # Live apps change elsewhere (typing indicators, clocks). Proceed when the
        # chosen control itself is unchanged; any change to it still stops input.
        if view != current['view'] and (seen is None or fresh is None or any(
                seen.get(key) != fresh.get(key) for key in ('name', 'control', 'automation_id', 'value', 'enabled'))):
            raise AIPlanError('The interface changed before input. No action was sent; choose a control from the refreshed observation.')
        element = self.elements[window['hwnd']].get(ref)
        if element is None or not element.CurrentIsEnabled or element.CurrentIsPassword or element.CurrentIsOffscreen:
            raise AIError('The selected element is unavailable or protected.')
        previous = seen or fresh
        if (element.CurrentName[:160] != previous['name'] or element.CurrentControlType != previous['control']
                or element.CurrentAutomationId[:100] != previous['automation_id']
                or previous['control']==50004 and self.text(element)[:1000]!=previous['value']):
            raise AIPlanError('The selected control changed before input. No action was sent; inspect it again.')
        return element

    def keys(self, window, chord):
        import pyautogui
        pyautogui.PAUSE = 0
        allowed = {'ctrl+s', 'ctrl+shift+s', 'ctrl+a', 'ctrl+c', 'tab', 'shift+tab', 'escape',
                   'left', 'right', 'up', 'down', 'home', 'end', 'pageup', 'pagedown'} | NAVIGATION_KEYS
        if chord not in allowed:
            raise AIError('This key combination is not available.')
        self.check(window, True)
        pyautogui.hotkey(*chord.split('+'))

    def focus_key(self, element):
        return 'f' + hashlib.sha256(json.dumps(list(element.GetRuntimeId())).encode()).hexdigest()[:16]

    def focused_target(self, window, expected=''):
        """The keyboard focus inside the bound foreground window, never a password."""
        self.check(window, True)
        element = self.client.GetFocusedElement()
        if element is None or element.CurrentIsPassword:
            raise AIPlanError('The keyboard focus is missing or a protected field. No input was sent.')
        if expected and self.focus_key(element) != expected:
            raise AIPlanError('The keyboard focus changed after it was checked. No input was sent.')
        return element

    def describe(self, element):
        return dict(id=self.focus_key(element), name=element.CurrentName[:160],
                    automation_id=element.CurrentAutomationId[:100], control=int(element.CurrentControlType))

    def window_rect(self, window):
        U.SetProcessDPIAware()  # screenshot, hit-test and click share physical pixels
        rect = wintypes.RECT()
        if not U.GetWindowRect(window['hwnd'], ctypes.byref(rect)) or rect.right <= rect.left or rect.bottom <= rect.top:
            raise AIPlanError('The window has no visible area. No input was sent.')
        return [rect.left, rect.top, rect.right, rect.bottom]

    def screenshot(self, window):
        """Downscaled JPEG of the bound foreground window, for poor accessibility trees."""
        import base64
        import io
        from PIL import ImageGrab
        self.check(window, True)  # foreground: the captured pixels are this window
        rect = self.window_rect(window)
        image = ImageGrab.grab(bbox=tuple(rect), all_screens=True).convert('RGB')
        scale = min(1.0, 1024 / max(image.size))
        if scale < 1:
            image = image.resize((max(1, round(image.size[0] * scale)), max(1, round(image.size[1] * scale))))
        for quality in (60, 40, 25):
            buffer = io.BytesIO()
            image.save(buffer, 'JPEG', quality=quality)
            data = base64.b64encode(buffer.getvalue()).decode('ascii')
            if len(data) <= 200000:  # parent reader accepts 256 KiB lines
                break
        else:
            raise AIPlanError('The window screenshot is too large. No input was sent.')
        return dict(status='verified', message='Screenshot captured.', image=data, width=image.size[0],
                    height=image.size[1], rect=rect)

    def hit(self, window, request):
        """Element under a screenshot point; the point must belong to this window."""
        rect = self.window_rect(window)
        if request.get('rect') and list(request['rect']) != rect:
            raise AIPlanError('The window moved or resized after the screenshot. No click was sent; observe it again.')
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        x = min(rect[2] - 2, max(rect[0] + 1, rect[0] + round(request['x'] / 1000 * width)))
        y = min(rect[3] - 2, max(rect[1] + 1, rect[1] + round(request['y'] / 1000 * height)))
        point = wintypes.POINT(x, y)
        if int(U.GetAncestor(U.WindowFromPoint(point), 2) or 0) != window['hwnd']:
            raise AIPlanError('That point is covered by another window. No click was sent.')
        element = self.client.ElementFromPoint(point)
        labels, current = [], element
        for _ in range(4):  # a Send icon is often an unnamed child of a named button
            if not current:
                break
            labels.append(current.CurrentName[:120] + ' ' + current.CurrentAutomationId[:60])
            current = self.client.ControlViewWalker.GetParentElement(current)
        return dict(id=self.focus_key(element) + '%d,%d' % (x, y), label=' '.join(labels)[:400],
                    name=element.CurrentName[:160], control=int(element.CurrentControlType),
                    password=bool(element.CurrentIsPassword), x=x, y=y)

    def no_terminal(self, window, element):
        if window['process'] in TERMINALS:
            raise AIError('Typing into command interpreters is not supported.')
        ancestor = element
        for _ in range(12):
            if not ancestor:
                break
            label = (ancestor.CurrentName + ' ' + ancestor.CurrentAutomationId + ' ' + ancestor.CurrentClassName).casefold()
            if any(word in label for word in ('terminal', 'console', 'powershell', 'repl')):
                raise AIError('Typing into terminal or console controls is not supported.')
            ancestor = self.client.ControlViewWalker.GetParentElement(ancestor)

    def invoke(self, element, operation):
        pairs = {'invoke': (10000, 'IUIAutomationInvokePattern', 'Invoke'),
                 'select': (10010, 'IUIAutomationSelectionItemPattern', 'Select'),
                 'toggle': (10015, 'IUIAutomationTogglePattern', 'Toggle'),
                 'expand': (10005, 'IUIAutomationExpandCollapsePattern', 'Expand'),
                 'collapse': (10005, 'IUIAutomationExpandCollapsePattern', 'Collapse')}
        number, interface, method = pairs[operation]
        pattern = self.pattern(element, number, interface)
        if not pattern:
            error = AIError if self.effect_started else AIPlanError
            raise error('This control does not support '+operation+'. No activation was sent; inspect it and choose a supported operation.')
        self.effect()
        getattr(pattern, method)()

    def set_text(self, window, element, text):
        if window['process'] in {'cmd.exe', 'powershell.exe', 'powershell_ise.exe', 'pwsh.exe', 'windowsterminal.exe', 'conhost.exe', 'mintty.exe', 'wsl.exe', 'bash.exe'}:
            raise AIError('Typing into command interpreters is not supported.')
        if element.CurrentControlType not in {50004, 50030}:
            raise AIError('Select an editable text field first.')
        ancestor = element
        for _ in range(12):
            if not ancestor:
                break
            label = (ancestor.CurrentName + ' ' + ancestor.CurrentAutomationId + ' ' + ancestor.CurrentClassName).casefold()
            if any(word in label for word in ('terminal', 'console', 'powershell', 'repl')):
                raise AIError('Typing into terminal or console controls is not supported.')
            ancestor = self.client.ControlViewWalker.GetParentElement(ancestor)
        self.check(window, True)
        element.SetFocus()
        focused = self.client.GetFocusedElement()
        if not self.client.CompareElements(focused, element):
            raise AIError('The text field did not receive focus.')
        native = int(element.CurrentNativeWindowHandle or 0)
        if (window['process'] == 'notepad.exe' and native and owner(native) == window['pid']
                and int(U.GetAncestor(native, 2) or 0) == window['hwnd']
                and element.CurrentClassName.casefold().startswith('richedit')):
            # Documented RichEdit text operation: one undoable insertion with
            # real EN_CHANGE notifications. No clipboard and no per-character
            # input for long drafts. The native handle is from this exact UIA node.
            self.check(window, True)
            result = ctypes.c_size_t()
            if not U.SendMessageTimeoutW(native, 0xB1, 0, -1, 2, 800, ctypes.byref(result)):
                raise AIError('The editor did not accept text selection. No text was inserted.')
            self.check(window, True)
            value = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r')
            buffer = ctypes.create_unicode_buffer(value)
            if not U.SendMessageTimeoutW(native, 0xC2, 1, ctypes.cast(buffer, ctypes.c_void_p).value,
                                        2, 1500, ctypes.byref(result)):
                raise AIError('Editor insertion timed out. It will not be repeated.')
            end = time.monotonic() + .8
            while time.monotonic() < end:
                actual = self.text(element, len(text) + 2).replace('\r\n', '\n').replace('\r', '\n')
                expected = text.replace('\r\n', '\n').replace('\r', '\n')
                if actual == expected or actual == expected + '\n':
                    return
                time.sleep(.02)
            raise AIError('The document readback did not match. Remaining actions stopped.')
        value = self.pattern(element, 10002, 'IUIAutomationValuePattern')
        # Some XAML Document providers and common-dialog filename edits expose
        # ValuePattern without updating the application's document/selection model.
        # Use real focused Unicode input for these; retain ValuePattern on edits
        # whose provider implements editable values (e.g. standard Win32 fields).
        if value and not value.CurrentIsReadOnly and element.CurrentControlType == 50004 and element.CurrentAutomationId != '1001':
            value.SetValue(text)
        else:
            # Unicode SendInput fallback, with exact element/window checks per chunk.
            self.keys(window, 'ctrl+a')
            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                            ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
            class UNION(ctypes.Union):
                _fields_ = [('key', KEYBDINPUT), ('padding', ctypes.c_byte * (32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24))]
            class INPUT(ctypes.Structure):
                _fields_ = [('type', wintypes.DWORD), ('data', UNION)]
            U.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
            expected = ''
            # A common-dialog filename edit autocompletes between bursts. Send
            # its complete bounded path as one real input burst, then read back
            # the entire value before any Save. XAML documents keep prefix checks.
            chunk_size = max(1,len(text)) if element.CurrentControlType == 50004 and element.CurrentAutomationId == '1001' else 1 if element.CurrentControlType == 50030 else 24
            normalized = text.replace('\r\n', '\n').replace('\r', '\n')
            for offset in range(0, len(normalized), chunk_size):
                self.check(window, True)
                if not self.client.CompareElements(self.client.GetFocusedElement(), element):
                    raise AIError('The text field changed. Remaining input was cancelled.')
                character = normalized[offset:offset + chunk_size]
                editor_break = character == '\n' and window['process'] in {'notepad.exe', 'notepad++.exe', 'wordpad.exe', 'winword.exe', 'swriter.exe'}
                if editor_break:
                    # XAML Notepad requires a real editor line-break key; a
                    # Unicode CR alone can be ignored. Never expose Enter as
                    # arbitrary form/chat input through this fallback.
                    import pyautogui
                    pyautogui.PAUSE = 0
                    pyautogui.keyDown('enter')
                    try:
                        time.sleep(.02)  # Key dwell, not a UI readiness delay.
                    finally:
                        pyautogui.keyUp('enter')
                encoded = b'' if editor_break else character.replace('\n', '\r').encode('utf-16-le')
                units = [int.from_bytes(encoded[i:i+2], 'little') for i in range(0, len(encoded), 2)]
                items = (INPUT * (len(units) * 2))()
                for i, unit in enumerate(units):
                    items[2*i] = INPUT(1, UNION(key=KEYBDINPUT(0, unit, 4, 0, 0)))
                    items[2*i+1] = INPUT(1, UNION(key=KEYBDINPUT(0, unit, 6, 0, 0)))
                if items and U.SendInput(len(items), items, ctypes.sizeof(INPUT)) != len(items):
                    raise AIError('Windows blocked text input; the field may be partially filled.')
                expected += character
                # XAML editors can coalesce/drop a burst of Unicode events.
                # Observe each accepted prefix before sending more; no blind
                # retry and no long fixed typing delay.
                end = time.monotonic() + .35
                while time.monotonic() < end:
                    actual = self.entered_prefix(element).replace('\r\n', '\n').replace('\r', '\n')
                    if actual == expected.replace('\r\n', '\n').replace('\r', '\n'):
                        break
                    time.sleep(.005)
                else:
                    raise AIError('Text input stopped at an unverified prefix. It will not be repeated.')
        end = time.monotonic() + .7
        while time.monotonic() < end:
            actual = self.text(element).replace('\r\n', '\n').replace('\r', '\n')
            wanted = text.replace('\r\n', '\n').replace('\r', '\n')
            if actual == wanted or (element.CurrentControlType == 50030 and actual == wanted + '\n'):
                return
            time.sleep(.025)
        raise AIError('Text was sent, but readback did not match. It will not be retried.')

    def set_filename(self, window, field, text):
        """Set only the observed common-dialog filename Edit, with readback.

        Real input commits the dialog's filename model. ValuePattern/WM_SETTEXT
        can change the visible edit while leaving the dialog's chosen name stale.
        """
        if field.CurrentControlType != 50004 or field.CurrentAutomationId != '1001':
            raise AIError('Select the exact Save As filename field.')
        self.check(window, True)
        field.SetFocus()
        if not self.client.CompareElements(self.client.GetFocusedElement(), field):
            raise AIError('The filename field did not receive focus.')
        self.effect()
        self.set_text(window, field, text)
        self.keys(window, 'tab')
        end = time.monotonic()+.7
        while time.monotonic()<end:
            self.check(window, True)
            if self.text(field, len(text)+2) == text:
                return
            time.sleep(.02)
        raise AIError('The filename did not match the requested path. No Save was sent.')

    def run(self, request):
        self.request_id = request.get('id')
        self.effect_started = False
        self.browser_progress = None
        window, operation = request['window'], request['operation']
        if operation == 'system_app':
            from .ai_system_apps import worker_run
            return worker_run(self, window, request['tool'], request['arguments'])
        if operation == 'word_document':
            from .ai_documents import WordDocuments
            if self.word is None:
                self.word = WordDocuments()
            return self.word.write(window or None, request['text'], request['new_document'])
        if operation == 'notepad_new_document':
            if window['process'] != 'notepad.exe':
                raise AIError('The selected application is not Notepad.')
            import win32gui
            previous = set()
            win32gui.EnumWindows(lambda hwnd, _: previous.add(hwnd), None)
            self.focus(window)
            # A Notepad launched earlier in the same task builds its menu bar
            # a moment after the window appears; wait for it instead of failing.
            end = time.monotonic() + 4
            while True:
                root = self.check(window, True)
                menus = root.FindAll(4, self.client.CreatePropertyCondition(30003, 50011))
                files = [menus.GetElement(i) for i in range(menus.Length) if
                         menus.GetElement(i).CurrentAutomationId == 'File' or
                         menus.GetElement(i).CurrentName.replace('&', '').casefold() in {'file', 'файл'}]
                if len(files) == 1 or time.monotonic() >= end:
                    break
                time.sleep(.1)
            if len(files) != 1:
                raise AIError('Notepad does not expose its File menu. No existing text was changed.')
            self.invoke(files[0], 'invoke')
            end = time.monotonic() + 1
            item = None
            while time.monotonic() < end:
                items = root.FindAll(4, self.client.CreatePropertyCondition(30003, 50011))
                matches = [items.GetElement(i) for i in range(items.Length) if
                           items.GetElement(i).CurrentName.replace('&', '').casefold() in {'new window', 'новое окно'}]
                if len(matches) == 1:
                    item = matches[0]
                    break
                time.sleep(.02)
            if item is None:
                raise AIError('Notepad does not expose New window. Existing documents were preserved.')
            self.check(window, True)
            self.invoke(item, 'invoke')
            end = time.monotonic() + 3
            pending_created = None
            while time.monotonic() < end:
                candidates = []
                win32gui.EnumWindows(lambda hwnd, _: candidates.append(hwnd) if hwnd not in previous
                    and win32gui.IsWindowVisible(hwnd) and owner(hwnd) == window['pid']
                    and win32gui.GetClassName(hwnd) == 'Notepad' else None, None)
                if len(candidates) == 1:
                    hwnd = candidates[0]
                    created = dict(hwnd=hwnd, pid=window['pid'], process='notepad.exe', title=win32gui.GetWindowText(hwnd))
                    if pending_created is None:
                        self.focus(created)
                        pending_created = created
                    else:
                        self.check(created, True)
                    tree = self.snapshot(created)
                    editors = [e for e in tree['elements'] if e['control'] == 50030]
                    if len(editors) == 1 and not editors[0]['value'].strip():
                        return dict(status='verified', message='New document created.', window=created, interface=tree)
                time.sleep(.025)
            if pending_created is not None:
                raise AIError('The new window did not expose one empty editor. Existing text was preserved.')
            raise AIError('A fresh Notepad window did not appear. No text was inserted.')
        if operation == 'observe':
            return self.snapshot(window)
        if operation == 'targets':
            return self.targets(window)
        if operation == 'save_as':
            from pathlib import Path
            root = self.check(window, True)
            def find(control, automation_id):
                condition = self.client.CreateAndCondition(self.client.CreatePropertyCondition(30003, control),
                    self.client.CreatePropertyCondition(30011, automation_id))
                matches = root.FindAll(4, condition)
                if matches.Length != 1:
                    raise AIError('The Save As dialog does not expose one exact filename field and Save button.')
                return matches.GetElement(0)
            field, button = find(50004, '1001'), find(50000, '1')
            if button.CurrentName.casefold().strip('&') not in {'save', 'сохранить'}:
                raise AIError('The owned dialog is not a supported Save As dialog.')
            value = request['path']
            if Path(value).exists():
                raise AIError('The output path already exists. No overwrite was attempted.')
            self.set_filename(window, field, value)
            # Leave the edit by actual UIA focus so common-dialog state commits.
            button.SetFocus()
            self.check(window, True)
            if (not self.client.CompareElements(find(50004, '1001'), field)
                    or self.text(field) != value or Path(value).exists()):
                raise AIError('The filename field or output path changed. Saving stopped.')
            self.invoke(find(50000, '1'), 'invoke')
            return dict(status='dispatched', message='Save requested; verify the file content.')
        if operation in {'focus', 'minimize', 'maximize', 'restore'}:
            self.check(window)
            if operation == 'focus':
                self.focus(window)
            else:
                U.ShowWindow(window['hwnd'], {'minimize': 6, 'maximize': 3, 'restore': 9}[operation])
                end = time.monotonic() + .5
                while time.monotonic() < end:
                    good = bool(U.IsIconic(window['hwnd'])) if operation == 'minimize' else bool(U.IsZoomed(window['hwnd'])) if operation == 'maximize' else not U.IsIconic(window['hwnd']) and not U.IsZoomed(window['hwnd'])
                    if good:
                        break
                    time.sleep(.02)
                if not good:
                    raise AIError('The window state change could not be verified.')
            return dict(status='verified', message='Done.')
        if operation == 'browser':
            from .apps import DEFAULT_BROWSER_PROCESSES
            if window['process'] not in DEFAULT_BROWSER_PROCESSES:
                raise AIPlanError('Select a supported browser window.')
            from .ai_browser import BrowserAutomation
            result=BrowserAutomation(self).run(window,request)
            if result is not None:return result
        self.check(window, True)
        if operation == 'browser':
            from .apps import DEFAULT_BROWSER_PROCESSES
            if window['process'] not in DEFAULT_BROWSER_PROCESSES:
                raise AIError('The target is not a supported browser process.')
            import pyautogui
            pyautogui.PAUSE = 0
            action = request['action']
            chords = {'new_tab': ('ctrl', 't'), 'next_tab': ('ctrl', 'tab'),
                      'previous_tab': ('ctrl', 'shift', 'tab'), 'close_tab': ('ctrl', 'w'), 'find': ('ctrl', 'f')}
            if action not in chords or (action == 'find' and not request['text'].strip()):
                raise AIError('Choose a supported browser action and nonempty find text.')
            before = self.snapshot(window,chrome_only=True)
            self.check(window, True)
            self.effect()
            pyautogui.hotkey(*chords[action])
            if action == 'find':
                end = time.monotonic() + .7
                field = None
                input_window = window
                while time.monotonic() < end:
                    candidate = self.client.GetFocusedElement()
                    if candidate.CurrentControlType == 50004 and candidate.CurrentProcessId == window['pid']:
                        foreground = int(U.GetForegroundWindow() or 0)
                        current = foreground
                        for _ in range(5):
                            if current == window['hwnd']:
                                break
                            current = int(U.GetWindow(current, 4) or 0)  # GW_OWNER
                        if current != window['hwnd'] or owner(foreground) != window['pid']:
                            raise AIError('Focus moved outside the browser while opening Find. No text was sent.')
                        input_window = dict(window, hwnd=foreground)
                        field = candidate
                        break
                    time.sleep(.025)
                if field is None:
                    raise AIError('The browser did not expose a focused Find field.')
                self.set_text(input_window, field, request['text'])
                return dict(status='verified', message='Find text entered; matching page results depend on the browser.')
            end = time.monotonic() + .8
            while time.monotonic() < end:
                if owner(window['hwnd']) is None and action == 'close_tab':
                    return dict(status='verified', message='Closed.')
                state = self.snapshot(window,chrome_only=True)
                if state['view'] != before['view']:
                    return dict(status='verified', message='Browser interface changed.')
                time.sleep(.03)
            return dict(status='dispatched', message='Browser key sent; no state change was observed.')
        if operation == 'focused':
            return dict(status='verified', message='Focus inspected.', focus=self.describe(self.focused_target(window)))
        if operation == 'screenshot':
            return self.screenshot(window)
        if operation == 'point':
            return dict(status='verified', message='Point inspected.', hit=self.hit(window, request))
        if operation == 'click_point':
            import pyautogui
            pyautogui.PAUSE = 0
            target = self.hit(window, request)
            if target['password'] or target['id'] != request.get('expected_hit'):
                raise AIPlanError('The control under that point changed after it was checked. No click was sent.')
            self.check(window, True)
            self.effect()
            if request['gesture'] == 'double_click':
                pyautogui.doubleClick(target['x'], target['y'])
            else:
                pyautogui.click(target['x'], target['y'], button='right' if request['gesture'] == 'right_click' else 'left')
            time.sleep(.35)  # let the app repaint before the verifying screenshot
            return dict(status='dispatched', message='Clicked; inspect the new screenshot for the result.')
        if operation == 'type_text':
            if request['element']:
                element = self.element(window, request['element'], request['view'])
                element.SetFocus()
                if not self.client.CompareElements(self.client.GetFocusedElement(), element):
                    raise AIError('The selected field did not receive focus. No text was sent.')
            else:
                element = self.focused_target(window, request.get('expected_focus', ''))
            self.no_terminal(window, element)
            label = element.CurrentName[:160] + ' ' + element.CurrentAutomationId[:100]
            self.effect()
            send_unicode(request['text'])
            end = time.monotonic() + .6
            while time.monotonic() < end:
                try:
                    if request['text'].casefold() in self.text(self.client.GetFocusedElement()).casefold():
                        return dict(status='verified', message='Text typed.', label=label)
                except Exception:
                    break
                time.sleep(.03)
            return dict(status='dispatched', message='Text typed; the field does not expose its value. Inspect before continuing.', label=label)
        if operation == 'keys' and not request['element']:
            # Current focus of the bound window (e.g. after ctrl+k); bound to the
            # exact element the guard inspected before any confirmation.
            element = self.focused_target(window, request.get('expected_focus', ''))
            self.no_terminal(window, element)
            self.effect()
            self.keys(window, request['chord'])
            return dict(status='dispatched', message='Key sent; inspect the resulting interface.')
        if operation == 'keys':
            element = self.element(window, request['element'], request['view'])
            element.SetFocus()
            if not self.client.CompareElements(self.client.GetFocusedElement(), element):
                raise AIError('The selected field did not receive focus.')
            if request['chord'] in NAVIGATION_KEYS:
                self.no_terminal(window, element)
            self.effect()
            self.keys(window, request['chord'])
            return dict(status='dispatched', message='Key sent; inspect the resulting interface.')
        if operation == 'mouse':
            import pyautogui
            element = self.element(window, request['element'], request['view'])
            bounds = element.CurrentBoundingRectangle
            if request.get('rect') and moved(bounds, request['rect']):
                raise AIPlanError('The control moved. Say show numbers again.')
            x, y = int((bounds.left + bounds.right) / 2), int((bounds.top + bounds.bottom) / 2)
            hit = self.client.ElementFromPoint(wintypes.POINT(x, y))
            if not self._hit_belongs(element, hit):
                raise AIError('The selected element is obscured. No mouse input was sent.')
            self.check(window, True)
            pyautogui.PAUSE = 0
            self.effect()
            if request['gesture'] == 'double_click':
                pyautogui.doubleClick(x, y)
            elif request['gesture'] in {'click', 'right_click'}:
                pyautogui.click(x, y, button='right' if request['gesture'] == 'right_click' else 'left')
            else:
                pyautogui.moveTo(x, y)
                pyautogui.scroll(3 if request['gesture'] == 'scroll_up' else -3)
            return dict(status='dispatched', message='Input sent; inspect the resulting interface.')
        element = self.element(window, request['element'], request['view'])
        if operation == 'read':
            return dict(status='verified', message=self.text(element)[:4000] or element.CurrentName[:500])
        if operation == 'set_text':
            if request.get('require_empty') is True:
                native = int(element.CurrentNativeWindowHandle or 0)
                if native and element.CurrentClassName.casefold().startswith(('richedit','edit')):
                    empty = U.GetWindowTextLengthW(native) == 0
                else:
                    empty = self.text(element,16001) in {'','\r','\n','\r\n'}
                if not empty:
                    raise AIError('This document already contains text. Create a new document before entering the draft.')
            self.effect()
            self.set_text(window, element, request['text'])
            return dict(status='verified', message='Text entered.')
        self.invoke(element, operation)
        # A click alone is not proof; wait for an observable accessibility change.
        end = time.monotonic() + .8
        while time.monotonic() < end:
            try:
                state = self.snapshot(window)
                if state['view'] != request['view']:
                    return dict(status='verified', message='Interface changed.')
            except AIError:
                return dict(status='dispatched', message='A dialog or window change occurred. Inspect before continuing.')
            time.sleep(.04)
        return dict(status='dispatched', message='Activation sent; no state change was observed.')


def main():
    automation = Automation()
    for line in sys.stdin:
        request = None
        try:
            if len(line) > 65536:
                raise AIError('Automation request is too large.')
            request = json.loads(line)
            result = automation.run(request)
            response = dict(id=request['id'], result=result)
        except AIPlanError as exc:
            response = dict(id=request.get('id') if request else None, error=str(exc), rejected_before_action=True)
        except AIError as exc:
            progress=getattr(automation,'browser_progress',None)
            detail=(f'Closed {progress[0]} of {progress[1]} selected tabs. Remaining closures stopped. ' if progress and progress[0] else '')
            response = dict(id=request.get('id') if request else None, error=detail+str(exc))
        except Exception:
            response = dict(id=request.get('id') if request else None, error='UI Automation is unavailable for this element. Inspect the window or use its interface manually.')
        print(json.dumps(response, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
