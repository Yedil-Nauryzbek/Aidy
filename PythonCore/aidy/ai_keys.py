"""Keyboard and text by voice for users who cannot use their hands (2026-11-24).

key_plan() turns a complete spoken key/typing/dictation phrase into one step for
press_keys, type_into_focus or dictation; anything else returns None and follows
the existing paths. Execution acts on the foreground window, never on terminals
or Aidy itself.
"""
import re

_NUMBERS = {w: i + 1 for i, w in enumerate(
    'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen '
    'seventeen eighteen nineteen twenty'.split())}
_NUMBERS.update(once=1, twice=2, thrice=3)
MAX_TIMES = 20

_MODIFIERS = {'control': 'ctrl', 'ctrl': 'ctrl', 'ctl': 'ctrl', 'shift': 'shift', 'alt': 'alt',
              'windows': 'win', 'win': 'win', 'window': 'win'}
_MODIFIER_ORDER = ('ctrl', 'shift', 'alt', 'win')
_KEYS = {'enter': 'enter', 'return': 'enter', 'tab': 'tab', 'escape': 'escape', 'esc': 'escape',
         'space': 'space', 'spacebar': 'space', 'backspace': 'backspace', 'delete': 'delete', 'del': 'delete',
         'insert': 'insert', 'home': 'home', 'end': 'end', 'pageup': 'pageup', 'pagedown': 'pagedown',
         'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right',
         **{f'f{i}': f'f{i}' for i in range(1, 13)},
         **{c: c for c in 'abcdefghijklmnopqrstuvwxyz0123456789'}}
_MULTIWORD = [(r'\bpage up\b', 'pageup'), (r'\bpage down\b', 'pagedown'), (r'\bback space\b', 'backspace'),
              (r'\bspace bar\b', 'spacebar'), (r'\b(up|down|left|right) arrow\b', r'\1'),
              (r'\barrow (up|down|left|right)\b', r'\1')]
# Run dialog / power menu / Task Manager ("Run new task") start arbitrary programs;
# alt+f4 closes without the close confirmation; ctrl+alt combinations reach system
# screens. A chord is blocked when it CONTAINS one of these (shift+alt+f4 too).
_BLOCKED = [frozenset({'win', 'r'}), frozenset({'win', 'x'}), frozenset({'alt', 'f4'}),
            frozenset({'ctrl', 'shift', 'escape'}), frozenset({'ctrl', 'alt'})]
MAX_SPOKEN_TIMES = 99  # parsed up to this; the runtime refuses more than MAX_TIMES aloud

_SHORTCUTS = {'copy': 'ctrl+c', 'copy that': 'ctrl+c', 'paste': 'ctrl+v', 'paste it': 'ctrl+v', 'cut': 'ctrl+x',
              'select all': 'ctrl+a', 'redo': 'ctrl+y', 'save': 'ctrl+s', 'save it': 'ctrl+s',
              'save this': 'ctrl+s', 'undo typing': 'ctrl+z', 'new line': 'enter',
              'delete last word': 'ctrl+backspace', 'delete the last word': 'ctrl+backspace',
              'select last word': 'ctrl+shift+left', 'select the last word': 'ctrl+shift+left',
              'backspace': 'backspace', 'delete': 'delete'}
_REPEATABLE = {'backspace', 'delete'}
# "type hello in notepad", "type hi and press enter", "type a letter to my boss".
_TYPE_TASK = re.compile(r'\s(?:and|then)\s|\s(?:in|into|inside) \S+(?: \S+)?$|'
                        r'^(?:a|an|the|my) (?:letter|email|e-mail|essay|message|note|story|poem|paragraph|reply|text)\b')


def _step(tool, arguments):
    return {'tool': tool, 'arguments': arguments, 'delay_seconds': 0}


def _plan(tool, arguments):
    return {'clarification': '', 'steps': [_step(tool, arguments)]}


def is_blocked(keys):
    parts = set(str(keys).split('+'))
    return any(blocked <= parts for blocked in _BLOCKED)


def normalize_keys(spoken):
    """'control shift t' / 'Ctrl+Shift+T' -> 'ctrl+shift+t'; unknown or blocked -> None."""
    keys = parse_keys(spoken)
    return None if keys is None or is_blocked(keys) else keys


def parse_keys(spoken):
    """A spoken chord in canonical form, blocked or not; unknown -> None."""
    text = ' '.join(re.sub(r'[+\-]', ' ', str(spoken).casefold()).split())
    for pattern, replacement in _MULTIWORD:
        text = re.sub(pattern, replacement, text)
    tokens = text.split()
    if not tokens:
        return None
    *modifiers, key = tokens
    mods = [_MODIFIERS.get(m) for m in modifiers]
    if None in mods or len(set(mods)) != len(mods) or key not in _KEYS:
        return None
    mods = [m for m in _MODIFIER_ORDER if m in mods]
    return '+'.join(mods + [_KEYS[key]])


def _split_times(text):
    match = re.fullmatch(r'(?P<body>.+?)\s+(?:(?P<n>\d+|[a-z]+) times?|(?P<w>once|twice|thrice))', text)
    if not match:
        return text, 1
    count = match['w'] or match['n']
    times = int(count) if count.isdigit() else _NUMBERS.get(count)
    return (match['body'], times) if times else (text, 1)


def key_plan(text):
    """One press_keys / type_into_focus / dictation step for a complete phrase, else None."""
    original = str(text).strip()
    typed = re.fullmatch(r'(?is)type\s+(.+)', original)
    if typed:
        value = typed[1].strip()
        if _TYPE_TASK.search(value.casefold().rstrip('.!?')):
            return None  # a target app, a follow-up action or a writing task: Google decides
        value = value[:-1] if value.endswith('.') and not value.endswith('..') else value
        return _plan('type_into_focus', {'text': value}) if value.strip() else None
    t = ' '.join(original.casefold().rstrip('.!?').split())
    t = re.sub(r'^please |,? please$', '', t)
    if re.fullmatch(r'(?:start |begin |turn on )?dictation(?: mode)?|start dictating', t):
        return _plan('dictation', {'action': 'start'})
    if re.fullmatch(r'(?:stop|end|finish|exit|cancel|turn off)(?: the)? dictation(?: mode)?|stop dictating|'
                    r'(?:turn )?dictation(?: mode)? off', t):
        return _plan('dictation', {'action': 'stop'})
    body, times = _split_times(t)
    if not 1 <= times <= MAX_SPOKEN_TIMES:
        return None
    if body in _SHORTCUTS and (times == 1 or _SHORTCUTS[body] in _REPEATABLE):
        return _plan('press_keys', {'keys': _SHORTCUTS[body], 'times': times})
    # "press the delete button" is a click on a control (ai_pointer), not the Delete key.
    press = re.fullmatch(r'(?:press|hit|tap|push)(?: the)? (?P<keys>.+?)(?: key| keys)?', body)
    # A blocked chord or too many presses still become a step: the runtime refuses
    # it aloud instead of sending the request to Google.
    keys = parse_keys(press['keys']) if press else None
    return _plan('press_keys', {'keys': keys, 'times': times}) if keys else None


# --- execution on the foreground window -------------------------------------

def send_unicode(text):
    from .ai_uia_worker import send_unicode as send
    send(text)


def foreground_guard(info):
    """Refuse when nothing is in front, a terminal is in front, or Aidy itself is."""
    from .ai_tools import AIError
    from .ai_uia_worker import TERMINALS
    process = str((info or {}).get('process', '')).casefold()
    if process and not process.endswith('.exe'):
        process += '.exe'  # system.get_active_window_info() strips the extension
    if not process:
        raise AIError('No window is in front to receive keys.')
    if process in TERMINALS:
        raise AIError('Typing into terminal or console windows is not supported.')
    if process in {'wpfapp1.exe', 'aidy.exe'}:
        raise AIError("Aidy's own window is in front. Switch to the app you want first.")


def press(keys, times):
    import pyautogui
    # The cursor may rest in a screen corner (pyautogui's fail-safe point): that
    # must not abort a key press for someone who moves the mouse by voice.
    failsafe, pyautogui.FAILSAFE = pyautogui.FAILSAFE, False
    try:
        for _ in range(times):
            pyautogui.hotkey(*keys.split('+'), interval=0.02)
    finally:
        pyautogui.FAILSAFE = failsafe


def _focused_is_password():
    """A classic Edit control with ES_PASSWORD has the keyboard focus."""
    import ctypes
    from ctypes import wintypes

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD), ('hwndActive', wintypes.HWND),
                    ('hwndFocus', wintypes.HWND), ('hwndCapture', wintypes.HWND), ('hwndMenuOwner', wintypes.HWND),
                    ('hwndMoveSize', wintypes.HWND), ('hwndCaret', wintypes.HWND), ('rcCaret', wintypes.RECT)]
    user32 = ctypes.windll.user32
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(0, ctypes.byref(info)) or not info.hwndFocus:
        return False
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(info.hwndFocus, name, 64)
    if not re.match(r'(?i)(?:edit|richedit)', name.value):
        return False  # style bit 0x20 means something else for other classes
    ES_PASSWORD, GWL_STYLE = 0x20, -16
    return bool(user32.GetWindowLongW(info.hwndFocus, GWL_STYLE) & ES_PASSWORD)


def type_text(text):
    from .ai_tools import AIError
    if _focused_is_password():
        raise AIError('The focused field is a password field. I will not type into it.')
    send_unicode(text)


# --- dictation -----------------------------------------------------------------

_DICTATION_KEYS = {'new line': ('enter', 1), 'new paragraph': ('enter', 2), 'undo': ('ctrl+z', 1),
                   'undo that': ('ctrl+z', 1), 'select all': ('ctrl+a', 1)}


def dictation_step(text):
    """One step for an utterance while dictating: an editing command, or the words typed."""
    plan = key_plan(text)
    if plan and plan['steps'][0]['tool'] == 'dictation':
        return plan['steps'][0]
    t = re.sub(r'^please |,? please$', '', ' '.join(str(text).casefold().rstrip('.!?').split()))
    if t in _DICTATION_KEYS:
        keys, times = _DICTATION_KEYS[t]
        return _step('press_keys', {'keys': keys, 'times': times})
    if plan and plan['steps'][0]['tool'] == 'press_keys':
        return plan['steps'][0]
    return _step('type_into_focus', {'text': ' '.join(str(text).split()) + ' '})
