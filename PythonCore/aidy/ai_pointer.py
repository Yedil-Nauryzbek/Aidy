"""Click anything by voice for users who cannot use their hands (2026-11-24).

pointer_plan() turns "click Send", "right click 7", "show numbers" into one step for
the local-only tools click_target / show_labels; anything else returns None. The
runtime clicks through the UIA worker's checked mouse operation, never raw points.
"""
import re

MAX_LABELS = 150
_WORDS = ('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen '
          'seventeen eighteen nineteen twenty').split()
_TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
_GESTURES = {'click': 'click', 'double click': 'double_click', 'double-click': 'double_click',
             'right click': 'right_click', 'right-click': 'right_click'}
_TIME = re.compile(r'\b(?:in|after) \S+ (?:seconds?|minutes?|hours?)\b|\b(?:later|tomorrow|tonight)\b')
# Labelled screen controls are found by their accessible names; these words only
# describe the control, they are not part of its name.
_NOUNS = r'(?: button| link| tab| icon| menu| item| field| box| checkbox| option)?'


def _step(tool, arguments):
    return {'tool': tool, 'arguments': arguments, 'delay_seconds': 0}


def _plan(tool, arguments):
    return {'clarification': '', 'steps': [_step(tool, arguments)]}


# Whisper hears a lone number as a homophone ("2" -> "to").
_HOMOPHONES = {'to': 2, 'too': 2, 'for': 4, 'won': 1, 'ate': 8}


def _number(word):
    word = word.casefold()
    if word.isdigit():
        return int(word)
    if word in _HOMOPHONES:
        return _HOMOPHONES[word]
    if word in _WORDS:
        return _WORDS.index(word) + 1
    parts = re.fullmatch(r'(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[ -](one|two|three|four|five|six|seven|eight|nine))?', word)
    if parts:
        return _TENS[parts[1]] + (_WORDS.index(parts[2]) + 1 if parts[2] else 0)
    return None


def label_number(text):
    """'7', 'Seven.', 'number 12', 'click 3' -> the label number 1..150, else None."""
    t = ' '.join(str(text).casefold().strip().rstrip('.!?').split())
    match = re.fullmatch(r'(?:(?:click|choose|select|pick) )?(?:(?:number|label) )?(.+)', t)
    value = _number(match[1]) if match else None
    return value if value and 1 <= value <= MAX_LABELS else None


def pointer_plan(text):
    """One click_target / show_labels step for a complete pointer phrase, else None."""
    t = ' '.join(str(text).strip().rstrip('.!?').split())
    t = re.sub(r'(?i)^please |,? please$', '', t)
    low = t.casefold()
    if re.fullmatch(r'(?:show|display)(?: me)?(?: the)? (?:numbers|labels)|number the (?:buttons|controls|links)', low):
        return _plan('show_labels', {'action': 'show'})
    if re.fullmatch(r'(?:hide|remove|clear)(?: the)? (?:numbers|labels)', low):
        return _plan('show_labels', {'action': 'hide'})
    if _TIME.search(low):
        return None  # scheduled clicks are not supported locally
    match = re.fullmatch(r'(?P<gesture>double[ -]click|right[ -]click|click)(?: on)?(?: the)? (?P<target>.+?)' + _NOUNS,
                         t, re.I)
    if not match:
        match = re.fullmatch(r'(?:press|hit|tap|push) (?:the )?(?P<target>.+?) button', t, re.I)
        gesture = 'click'
    else:
        gesture = _GESTURES[match['gesture'].casefold()]
    if not match or not match['target'].strip() or match['target'].casefold() in {
            'on', 'the', 'it', 'that', 'this', 'here', 'there', 'twice', 'again', 'once'}:
        return None
    number = label_number(match['target'])
    target = str(number) if number else match['target'].strip()
    return _plan('click_target', {'target': target[:80], 'gesture': gesture})


def risky(target):
    """A control whose name or automation ID says send/buy/delete (also Russian UI): ask first.

    Same words as the agent's effect guard (ai_automation.EFFECTS), plus a few more."""
    from .ai_automation import EFFECTS
    pattern = re.compile(EFFECTS + r'|\b(?:checkout|erase|uninstall|format)\b', re.I)
    return any(pattern.search(str(target.get(key) or '')) for key in ('name', 'automation_id'))


def number_word(n):
    return _WORDS[n - 1] if 1 <= n <= len(_WORDS) else str(n)


def foreground_window():
    """The window in front as the UIA worker expects it, or None."""
    import ctypes
    from .ai_windows import owner, process_name
    hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    pid = owner(hwnd) if hwnd else 0
    return dict(hwnd=hwnd, pid=pid, process=process_name(pid)) if pid else None


def match_targets(rows, name):
    """Controls named exactly like the spoken name, else those containing it as whole words."""
    want = ' '.join(str(name).casefold().split())
    exact = [row for row in rows if ' '.join(row['name'].casefold().split()) == want]
    if exact:
        return exact
    words = re.compile(r'(?<!\w)' + re.escape(want) + r'(?!\w)')
    return [row for row in rows if words.search(row['name'].casefold())]


def overlay_line(labels):
    """stdout line for the WPF label overlay; [] hides it. Pixels are physical."""
    import json
    return 'OVERLAY:' + json.dumps({'labels': labels})
