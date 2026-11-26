"""Short per-app hints for the planner, added only for the app the task is about.

Hints describe where things are; the model still observes, chooses and verifies
every action, and every executor guard still applies.
"""

EMOJI_CONTACT = ('A contact described by an emoji ("the one with the bacon emoji") is searched by typing that emoji '
                 'character itself (🥓), not its English name.')
BROWSERS = ('chrome', 'msedge', 'edge', 'firefox', 'opera', 'brave', 'yandex', 'browser', 'youtube', 'google')

RECIPES = {
    'whatsapp': (('whatsapp',),
        'WhatsApp (web or desktop): to open a chat, focus the search box at the top of the chat list '
        '("Search or start new chat", Russian "Поиск или новый чат"), type_text the name, then select/invoke '
        'the matching chat row. Never use browser find or the address bar for this. The message box is at the '
        'bottom; enter there sends, so press it only when the user asked to send. ' + EMOJI_CONTACT),
    'telegram': (('telegram',),
        'Telegram Desktop: the search field is at the top of the chat list; type_text the name and open the '
        'matching result. The message box is at the bottom; enter there sends. ' + EMOJI_CONTACT),
    'discord': (('discord',),
        'Discord: key_input ctrl+k (element "") opens the Quick Switcher; type_text the friend or channel name; '
        'key_input enter opens the top match. The message box is at the bottom; enter there sends.'),
    'browser': (BROWSERS,
        'Browser: on a results page open a result by invoking its link (Hyperlink) or click_point; '
        'ctrl+l focuses the address bar, alt+left goes back. A web app inside a tab (WhatsApp Web, YouTube) '
        'is operated through its page controls.'),
    'explorer': (('explorer', 'downloads', 'documents', 'desktop folder', 'file '),
        'File Explorer: find files with explorer_action search, then select the file; open it with '
        'key_input enter or mouse_input double_click on its observed row.'),
    'settings': (('settings', 'systemsettings', 'bluetooth', 'wi-fi', 'wifi'),
        'Windows Settings: settings_page opens the page; switches such as Bluetooth are toggle buttons; use '
        'ui_action toggle on the observed switch and check its toggle state in the next observation.'),
}


def app_hints(text, registry):
    """Hints whose app is named in the request or is the observed/active window."""
    haystack = str(text or '').casefold()
    state = getattr(registry, 'desktop_state', None) or {}
    ref = state.get('selected_window') or getattr(registry, 'active_window', None)
    window = next((w for w in getattr(registry, 'windows', []) if w.get('id') == ref), None)
    if window:
        haystack += ' ' + (window.get('process', '') + ' ' + window.get('title', '')).casefold()
    return {app: hint for app, (keys, hint) in RECIPES.items() if any(key in haystack for key in keys)}
