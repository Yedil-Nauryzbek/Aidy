"""Local plans for common web chains (owner decision 2026-11-24).

"open youtube and search for X and then open github" took 5-16 s through Google,
timed out or lost a clause. Every clause must match this small grammar completely;
otherwise compound_plan returns None and the whole request goes to Google unchanged.
"""
import re
from urllib.parse import quote_plus

from .ai_app_profile import resolve_app_clauses

# key -> (home URL, search URL prefix, spoken names)
SITES = {
    'youtube': ('https://www.youtube.com', 'https://www.youtube.com/results?search_query=', ('youtube', 'you tube')),
    'google': ('https://www.google.com', 'https://www.google.com/search?q=', ('google',)),
    'github': ('https://github.com', 'https://github.com/search?q=', ('github', 'git hub')),
    'wikipedia': ('https://en.wikipedia.org', 'https://en.wikipedia.org/w/index.php?search=', ('wikipedia',)),
    'amazon': ('https://www.amazon.com', 'https://www.amazon.com/s?k=', ('amazon',)),
    'reddit': ('https://www.reddit.com', 'https://www.reddit.com/search/?q=', ('reddit',)),
    'stackoverflow': ('https://stackoverflow.com', 'https://stackoverflow.com/search?q=', ('stack overflow', 'stackoverflow')),
    'bing': ('https://www.bing.com', 'https://www.bing.com/search?q=', ('bing',)),
    'duckduckgo': ('https://duckduckgo.com', 'https://duckduckgo.com/?q=', ('duckduckgo', 'duck duck go')),
}
_NAMES = {name: key for key, (_, _, names) in SITES.items() for name in names}
# Whisper often ends each spoken command with a period: "Search for cats. Open Discord."
_SPLIT = re.compile(r'[.!?;]\s+|\s*,\s*(?:and then\s+|and\s+|then\s+)?|\s+(?:and then|and|then)\s+', re.I)
_SITE = r'(?:the )?(?P<site>[a-z .]+?)(?: website| site| page|\.com| dot com)?'
_OPEN_SITE = re.compile(r'(?:open(?: up)?|go to|visit|launch) ' + _SITE, re.I)
_SEARCH_ON = re.compile(r'(?:search|look up|find)(?: for)? (?P<q>.+) (?:on|in|at) ' + _SITE, re.I)
_SEARCH_SITE_FOR = re.compile(r'search ' + _SITE + r' for (?P<q>.+)', re.I)
# "google <q>" needs two or more words: "google chrome"/"google docs" name products.
_SEARCH = re.compile(r'(?:search(?: for)?|look up) (?P<q>.+)|google (?P<g>\S+(?: \S+)+)', re.I)
# "search whatsapp for bacon": search inside a named app or place, not the web.
_SEARCH_X_FOR = re.compile(r'search (?!for )(?:the )?(?P<x>.+?) for .+', re.I)
# A bare query naming a place, tool or time ("bacon in whatsapp", "my invoice",
# "cats after ten minutes") may mean in-app search or scheduling: Google decides.
_PLACE = re.compile(r'\b(?:in|on|at|inside|within|into|from|using|my|this|that|these|after|later|tonight|'
                    r'tomorrow|today|minutes?|hours?|seconds?)\b', re.I)
_BROWSERS = {'browser', 'chrome', 'opera', 'yandex_browser', 'edge', 'msedge', 'firefox', 'brave'}
_APP_VERB = re.compile(r'(?:please )?(?:open|launch|start|run|close|quit|exit)\b', re.I)


def _step(tool, arguments):
    return {'tool': tool, 'arguments': arguments, 'delay_seconds': 0}


def _site(name):
    return _NAMES.get(' '.join(name.casefold().split()))


def _query(text):
    query = ' '.join(text.split())
    if not query or query.casefold() == 'for' or re.search(r'[.!?;:]\s', query):
        return None
    return query


def _front_context(registry):
    """The app in front before this request: a known site, the web, or another app."""
    active = getattr(registry, 'active_app', None)
    if not active or active == 'aidy' or active in _BROWSERS or active == registry.aliases.get('browser'):
        return None
    return ('site', active) if active in SITES else ('app',)


def _installed_shadow(key, registry):
    """An installed desktop app named like the site (GitHub Desktop): Google picks app or site."""
    return any(str(app.get('name', '')).casefold().replace(' ', '').startswith(name.replace(' ', ''))
               for app in getattr(registry, 'installed_apps', []) for name in SITES[key][2])


def compound_plan(text, registry):
    """A provider-format plan when every clause is a known app/site/search action, else None."""
    from .ai_local_commands import _NEVER_LOCAL_APPS, _website_shadows_installed_app
    clean = re.sub(r'^(?:please )|,? please$', '', text.strip().rstrip('.!?'), flags=re.I).strip()
    clauses = [c.strip() for c in _SPLIT.split(clean)]
    if not 1 <= len(clauses) <= 6 or not all(clauses):
        return None
    steps, bare_open = [], None
    context = _front_context(registry)  # ('site', key) | ('web',) | ('app',) | None
    for clause in clauses:
        clause = re.sub(r'^please |,? please$', '', clause, flags=re.I)
        app = resolve_app_clauses(clause, registry) if _APP_VERB.match(clause) else None
        if app and len(app) == 1:
            operation, app_id = app[0]
            if app_id in _NEVER_LOCAL_APPS or app_id not in registry.app_ids or \
                    _website_shadows_installed_app(app_id, registry):
                return None
            steps.append(_step(operation, {'app': app_id}))
            opened_site = operation == 'open_app' and app_id in SITES
            context, bare_open = (('site', app_id), len(steps) - 1) if opened_site else (('app',), None)
            continue
        match = _OPEN_SITE.fullmatch(clause)
        if match and _site(match['site']):
            key = _site(match['site'])
            if _installed_shadow(key, registry):
                return None
            steps.append(_step('open_url', {'url': SITES[key][0]}))
            context, bare_open = ('site', key), len(steps) - 1
            continue
        match = _SEARCH_SITE_FOR.fullmatch(clause)
        if not (match and _site(match['site'])):
            match = _SEARCH_ON.fullmatch(clause)
        if match and _site(match['site']) and _query(match['q']):
            key = _site(match['site'])
            steps.append(_step('open_url', {'url': SITES[key][1] + quote_plus(_query(match['q']))}))
            context, bare_open = ('site', key), None
            continue
        other = _SEARCH_X_FOR.fullmatch(clause)
        if other and not _site(other['x']):
            return None
        match = _SEARCH.fullmatch(clause)
        query = _query(match['q'] or match['g']) if match else None
        if not query or _PLACE.search(query) or context == ('app',):
            return None
        if context and context[0] == 'site':
            search = _step('open_url', {'url': SITES[context[1]][1] + quote_plus(query)})
            if bare_open == len(steps) - 1:
                steps[-1] = search  # the search page opens the site itself
            else:
                steps.append(search)
            bare_open = None
        else:
            steps.append(_step('web_search', {'query': query}))
            context = ('web',)
    if not any(s['tool'] in {'open_url', 'web_search'} for s in steps):
        return None  # pure app chains stay with the existing app-chain rules
    # One complete goal per step, so the agent path verifies each without a model call.
    goals = [dict(id='g' + str(index + 1), description=_describe(step), requires_followup=False,
                  required_tools=[step['tool']],
                  criteria=[dict(kind='app_open', target=step['arguments']['app'], source_goal='')
                            if step['tool'] == 'open_app' else
                            dict(kind='tool_verified', target=step['tool'], source_goal='')],
                  step_start=index, step_count=1) for index, step in enumerate(steps)]
    return {'clarification': '', 'steps': steps, 'goals': goals, 'continuation': 'new_task'}


def _describe(step):
    args = step['arguments']
    if step['tool'] == 'web_search':
        return 'Search the web for ' + args['query']
    return ('Close ' if step['tool'] == 'close_app' else 'Open ') + (args.get('url') or args.get('app'))
