"""Local plans for simple, context-free commands (owner decision 2026-11-23).

Only a complete whole-phrase match qualifies; anything else returns None and
goes to Google as before. The returned plan has the provider's format, so
registry validation, amount clarification, permissions and confirmations
still apply unchanged.
"""
import re

from .ai_app_profile import resolve_app_clauses, validate_chain

_NUMBERS = 'one two three four five six seven eight nine ten'.split()
_AMOUNT = r'(?: (?:by )?(?P<amount>10|[1-9]|' + '|'.join(_NUMBERS) + r')(?: steps?)?)?'
_VOLUME = {
    'up': r'(?:volume up|increase (?:the )?volume|turn up the volume|turn the volume up|louder|make it louder|sound up)',
    'down': r'(?:volume down|decrease (?:the )?volume|lower (?:the )?volume|turn down the volume|turn the volume down'
            r'|quieter|make it quieter|sound down)',
}
_FIXED = {
    'scroll up': ('mouse_action', {'action': 'scroll up'}),
    'scroll down': ('mouse_action', {'action': 'scroll down'}),
    **{name: ('system_action', {'action': 'screenshot'})
       for name in ('screenshot', 'screen shot', 'take a screenshot', 'take screenshot', 'take a screen shot')},
}
_NEVER_LOCAL_APPS = {'aidy', 'gpt', 'chatgpt', 'chatgpt_app', 'everything', 'all'}


def _norm(name):
    return re.sub(r'[^0-9a-z]+', '', name.casefold())


def _website_shadows_installed_app(app, registry):
    """A configured website named like an installed desktop app needs Google's app-vs-site rules."""
    if app not in getattr(registry, 'website_ids', ()):
        return False
    names = {_norm(n) for n in (app.replace('_', ' '), re.sub(r'_?web$', '', app),
                                *getattr(registry, 'app_names', {}).get(app, []))} - {''}
    installed = [_norm(a.get('name', '')) for a in getattr(registry, 'installed_apps', [])]
    return any(item.startswith(name) for item in installed for name in names)


def _step(tool, arguments):
    return {'tool': tool, 'arguments': arguments, 'delay_seconds': 0}


# Status questions answered instantly from this computer. Every word must come
# from this small vocabulary, so "volume up", "open battery settings" or
# "how much time is left" (a timer question) never match.
_STATUS_WORDS = frozenset("""what what's whats is the my current currently level percent percentage of how
    much many do i have left it there here now right at check tell me a s remaining battery charge life
    status volume sound time date day today today's""".split())
_STATUS_TARGETS = (('battery_status', r'\b(?:battery|charge)\b'), ('volume_status', r'\b(?:volume|sound)\b'),
                   ('current_time', r'\b(?:time|date|day)\b'))


def _status_step(text):
    words = re.findall(r"[a-z']+", text)
    if not 1 <= len(words) <= 12 or any(word not in _STATUS_WORDS for word in words):
        return None
    tools = [tool for tool, pattern in _STATUS_TARGETS if re.search(pattern, text)]
    if len(tools) != 1 or tools[0] == 'current_time' and re.search(r'\b(?:left|remaining)\b', text):
        return None
    return _step(tools[0], {})


def _fixed_step(text):
    t = ' '.join(text.casefold().strip().strip('.!?,').split())
    t = re.sub(r'^please |,? please$', '', t)
    if t in _FIXED:
        tool, arguments = _FIXED[t]
        return _step(tool, dict(arguments))
    status = _status_step(t)
    if status:
        return status
    for direction, phrase in _VOLUME.items():
        match = re.fullmatch(phrase + _AMOUNT, t)
        if match:
            amount = match['amount']
            steps = 1 if amount is None else int(amount) if amount.isdigit() else _NUMBERS.index(amount) + 1
            return _step('volume_change', {'direction': direction, 'steps': steps})
    return None


def local_plan(text, registry):
    """A provider-format plan for a simple whole-phrase command, else None."""
    if registry.agent_mode or registry.pending_task or registry.plan_feedback:
        return None
    fixed = _fixed_step(text)
    if fixed:
        return {'clarification': '', 'steps': [fixed]}
    from .ai_keys import key_plan
    keys = key_plan(text)
    if keys:
        return keys
    from .ai_pointer import pointer_plan
    pointer = pointer_plan(text)
    if pointer:
        return pointer
    expected = resolve_app_clauses(text, registry)
    if not expected:
        from .ai_local_web import compound_plan
        return compound_plan(text, registry)
    if any(app in _NEVER_LOCAL_APPS or app not in registry.app_ids
                           or _website_shadows_installed_app(app, registry) for _, app in expected):
        return None
    plan = {'clarification': '', 'steps': [_step(tool, {'app': app}) for tool, app in expected]}
    return validate_chain(plan, expected, registry) if len(expected) > 1 else plan
