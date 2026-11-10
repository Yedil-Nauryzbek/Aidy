"""Closed, typed desktop vocabulary. No model text is executable code."""
from dataclasses import dataclass
import copy
from .ai_writing import length_tolerance
from .ai_tasks import validate_goals


class AIError(Exception):
    """Only fixed, secret-free messages may cross the UI/log boundary."""


class AIClarification(AIError):
    """A local target ambiguity, discovered after the required AI interpretation."""


class AIPlanError(AIError):
    """Invalid planning data; no desktop action has been dispatched."""


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def enum(*values):
    return dict(type='string', enum=list(values))


def integer(low, high):
    return dict(type='integer', minimum=low, maximum=high)


@dataclass(frozen=True)
class Tool:
    description: str
    parameters: dict
    permission: str
    confirm: bool = False
    schedulable: bool = False


TOOLS = {
    'recycle_bin': Tool('Windows Recycle Bin is a virtual shell folder, NEVER a file path. open opens it; list returns current item IDs and original locations; restore restores ONE exact item ID from list without overwriting; empty permanently empties the bin after confirmation. item is empty except for restore. Use this tool for recycle bin/trash/корзина, never file_action.', obj({'operation': enum('open', 'list', 'restore', 'empty'), 'item': {'type': 'string'}}), 'file_access'),
    'explorer_action': Tool('Work in Windows File Explorer: open a known folder or explicit task path; list/search its files; select a single existing file in Explorer. path uses an absolute path or desktop/documents/downloads/pictures/music/videos. query is empty except for search. Use file_action for mkdir/copy/rename/move/recycle within the user named folder.', obj({'operation': enum('open', 'list', 'search', 'select'), 'path': {'type': 'string'}, 'query': {'type': 'string'}}), 'file_access'),
    'calculator_action': Tool('Use the actual Windows Calculator. calculate enters a numeric arithmetic expression with + - * / parentheses and decimals and reads its display; read reads the display; clear clears the calculation. expression is empty except for calculate. No code, variables or functions.', obj({'operation': enum('calculate', 'read', 'clear'), 'expression': {'type': 'string'}}), 'desktop_control'),
    'notepad_action': Tool('Work inside an existing Notepad document: read full text, append literal text, replace all exact occurrences of query with text, or find and select the first exact query. window is an observed window ID or notepad/current. text is empty except append/replace; query is empty except find/replace. Use document_write to create a new draft and save_document to save it. Edits are read back and remain unsaved until requested.', obj({'window': {'type': 'string'}, 'operation': enum('read', 'append', 'replace', 'find'), 'query': {'type': 'string'}, 'text': {'type': 'string', 'maxLength': 8000, 'pattern': r'^[\s\S]*$'}}), 'desktop_control'),
    'settings_page': Tool('Open a fixed Windows Settings page: display, sound, bluetooth, wifi, network, apps, storage, notifications, accessibility, windows_update, date_time, personalization. This only navigates; use observed accessible controls for requested changes.', obj({'page': enum('display', 'sound', 'bluetooth', 'wifi', 'network', 'apps', 'storage', 'notifications', 'accessibility', 'windows_update', 'date_time', 'personalization')}), 'open_app'),
    'document_write': Tool('Create a new Notepad or Microsoft Word document and insert the complete supplied draft, with readback. For an essay, story or letter GENERATE its full text here, not instructions to write it. application=current targets the recent/current editor; new_document=true for open/create-and-write or a new draft. word_count is the requested target (within five percent for ordinary lengths, exact when explicitly requested), or 0 if unspecified. finish_task=true ONLY if creating/writing satisfies the WHOLE request; false when saving, formatting or any other action remains. No saving or sending unless separately requested.', obj({'application': enum('notepad', 'word', 'current'), 'new_document': {'type': 'boolean'}, 'text': {'type': 'string', 'maxLength': 16000, 'pattern': r'^[\s\S]*$'}, 'word_count': integer(0, 2000), 'finish_task': {'type':'boolean'}}), 'desktop_control'),
    'observe_desktop': Tool('Inspect current windows; window="" lists windows, an exact window ID inspects accessible controls. Do this before UI actions.', obj({'window': {'type': 'string'}}), 'desktop_read'),
    'window_action': Tool('Focus, minimize, maximize or restore an exact observed window.', obj({'window': {'type': 'string'}, 'operation': enum('focus', 'minimize', 'maximize', 'restore')}), 'switch_window'),
    'ui_action': Tool('Use an exact observed accessible element: invoke/select/toggle/expand/collapse/read/set_text. text is required, empty unless set_text. No invented element IDs.', obj({'window': {'type': 'string'}, 'element': {'type': 'string'}, 'operation': enum('invoke', 'select', 'toggle', 'expand', 'collapse', 'read', 'set_text'), 'text': {'type': 'string', 'maxLength': 8000, 'pattern': r'^[\s\S]*$'}}), 'desktop_control'),
    'key_input': Tool('Limited editing/navigation keys bound to an observed field. Save through save_document.', obj({'window': {'type': 'string'}, 'element': {'type': 'string'}, 'chord': enum('ctrl+a', 'ctrl+c', 'tab', 'shift+tab', 'escape', 'left', 'right', 'up', 'down', 'home', 'end', 'pageup', 'pagedown')}), 'desktop_control'),
    'mouse_input': Tool('One bounded click or scroll at a real observed element after hit-test and focus checks.', obj({'window': {'type': 'string'}, 'element': {'type': 'string'}, 'gesture': enum('click', 'right_click', 'scroll_up', 'scroll_down')}), 'desktop_control'),
    'browser_action': Tool('Operate one browser window ($browser resolves the current/unambiguous browser). tabs/read_page inspect; select_tab selects by actual tab ID or title. close_all_tabs closes every website in THIS window and keeps one blank tab; close_other_tabs keeps the selected tab; close_matching_tabs closes all title matches; close_tabs_except keeps all title matches and closes the others. Batch actions are complete semantic operations, not repeated close_tab guesses. new/next/previous/close/restore tab, back/forward/reload/find/navigate are supported. text is empty except tab title/query, find query or HTTP(S) address. Closing is confirmed and frozen to actual tab IDs.', obj({'window': {'type': 'string'}, 'operation': enum('tabs', 'new_tab', 'next_tab', 'previous_tab', 'close_tab', 'close_all_tabs', 'close_other_tabs', 'close_matching_tabs', 'close_tabs_except', 'select_tab', 'restore_tab', 'back', 'forward', 'reload', 'find', 'navigate', 'read_page'), 'text': {'type': 'string'}}), 'desktop_control'),
    'open_url': Tool('Open a user-requested HTTP/HTTPS address in the saved browser; never a shell/protocol command.', obj({'url': {'type': 'string'}}), 'open_app'),
    'file_action': Tool('Operate on an explicitly selected local path/folder, including named Desktop/Documents/Downloads resolved from known_folders. read/mkdir/write/list/search/copy/rename/move/recycle. list lists one folder; search recursively finds names/globs with text=query. copy copies one data file without overwriting. destination is used by copy/rename/move; text by write/search. New outputs only; no overwrite.', obj({'operation': enum('read', 'mkdir', 'write', 'list', 'search', 'copy', 'rename', 'move', 'recycle'), 'path': {'type': 'string'}, 'destination': {'type': 'string'}, 'text': {'type': 'string', 'maxLength': 8000, 'pattern': r'^[\s\S]*$'}}), 'file_access'),
    'save_document': Tool('Save text entered in this task using the application Save As dialog, to a new explicit user-selected text path; verify file content.', obj({'window': {'type': 'string'}, 'path': {'type': 'string'}}), 'file_access'),
    'task_complete': Tool('Finish the observed agent task only after its requested outcomes appear in verified results/current state. No desktop side effects.', obj({}), 'desktop_read'),
    'web_search': Tool('Search the web in the saved/default browser. Use one search action for open browser and search/type a query.', obj({'query': {'type': 'string'}}), 'open_app', schedulable=True),
    'open_app': Tool('Open a configured application or website. Use an exact catalog ID. For play music use music, which opens the saved preferred music app.', obj({'app': {'type': 'string'}}), 'open_app', schedulable=True),
    'close_app': Tool('Close a configured application. $last means last successfully referenced app, $active means foreground app.', obj({'app': {'type': 'string'}}), 'close_app', True, True),
    'volume_change': Tool('Adjust volume by 1 to 10 small steps; a little means 1.', obj({'direction': enum('up', 'down'), 'steps': integer(1, 10)}), 'volume_change', schedulable=True),
    'brightness_change': Tool('Adjust monitor brightness by 1 to 10 steps.', obj({'direction': enum('up', 'down'), 'steps': integer(1, 10)}), 'brightness_change', schedulable=True),
    'set_volume': Tool('Set exact system volume percent.', obj({'value': integer(0, 100)}), 'set_volume', schedulable=True),
    'system_action': Tool('Run one fixed Windows action. open_cmd opens an empty prompt, never executes code.', obj({'action': enum('shutdown', 'restart', 'lock', 'open_cmd', 'show_desktop', 'screenshot', 'task_manager')}), 'dynamic', schedulable=True),
    'mouse_action': Tool('Move cursor, click or scroll using saved mouse settings.', obj({'action': enum('mouse up', 'mouse down', 'mouse left', 'mouse right', 'scroll up', 'scroll down', 'click', 'double click', 'right click')}), 'mouse_control'),
    'window_switch': Tool('Start or adjust the existing Alt-Tab selection dialogue.', obj({'action': enum('start', 'left', 'right', 'done', 'cancel')}), 'switch_window'),
    'timer_start': Tool('Start or replace countdown timer, up to 12 hours. Ask if duration missing.', obj({'seconds': integer(1, 43200)}), 'timer', schedulable=True),
    'timer_cancel': Tool('Cancel the countdown timer.', obj({}), 'cancel_timer'),
    'timer_status': Tool('Report remaining countdown time.', obj({}), 'timer'),
    'study_mode': Tool('Start, stop or report the existing 45 minute study mode and workspace restoration workflow.', obj({'action': enum('start', 'stop', 'status')}), 'dynamic'),
    'custom_mode': Tool('Run saved custom/local mode slots, or disable mode. Only saved configuration, no new code.', obj({'action': enum('start', 'stop')}), 'grant_access', True),
    'assistant_audio': Tool('Mute or unmute Aidy voice feedback.', obj({'muted': {'type': 'boolean'}}), 'dynamic'),
    'close_everything': Tool('Close desktop windows, preserving except_apps (app IDs or current window IDs). For close everything except ChatGPT use except_apps=["gpt"]. An empty list closes all eligible windows.', obj({'except_apps': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8}}), 'close_everything', True),
    'close_window': Tool('Close one currently open desktop window by its window ID. Use $active for the foreground window.', obj({'window': {'type': 'string'}}), 'close_active', True),
    'exit_assistant': Tool('Close Aidy itself gracefully. Use for close yourself, quit Aidy, close Aidy. This must be the final step.', obj({}), 'close_app', True),
    'voice_access': Tool('List profiles, revoke expired access, or grant pending unknown speaker a role. duration_seconds=0 means permanent. For list/revoke use role=Guest, duration_seconds=0.', obj({'action': enum('list', 'revoke', 'grant'), 'role': enum('Admin', 'User', 'Guest'), 'duration_seconds': integer(0, 2592000)}), 'grant_access', True),
    'repeat': Tool('Repeat last executed action with fresh permission and confirmation checks.', obj({}), 'repeat'),
    'undo': Tool('Undo last action or last chain, where a safe inverse exists.', obj({'scope': enum('last', 'chain')}), 'undo_action'),
    'current_time': Tool('Read local computer date and time.', obj({}), 'timer'),
    'scheduled_status': Tool('List pending AI scheduled task IDs and remaining delays.', obj({}), 'timer'),
    'cancel_scheduled': Tool('Cancel an AI scheduled task; id=0 cancels all in this identity session.', obj({'id': integer(0, 2147483647)}), 'cancel_timer'),
}


def validate(value, schema):
    kind = schema['type']
    if kind == 'object':
        if type(value) is not dict or set(value) != set(schema['properties']):
            raise AIError('Invalid tool arguments.')
        for key, child in schema['properties'].items():
            validate(value[key], child)
    elif kind == 'integer':
        if type(value) is not int or not schema['minimum'] <= value <= schema['maximum']:
            raise AIError('Numeric argument is outside the allowed range.')
    elif kind == 'boolean':
        if type(value) is not bool:
            raise AIError('Invalid boolean argument.')
    elif kind == 'string':
        multiline = schema.get('pattern') == r'^[\s\S]*$'
        if type(value) is not str or len(value) > schema.get('maxLength', 500) or any(ord(c) < 32 and not (multiline and c in '\r\n\t') for c in value):
            raise AIError('Invalid text argument.')
        if 'enum' in schema and value not in schema['enum']:
            raise AIError('Unsupported action or target.')
    elif kind == 'array':
        if type(value) is not list or len(value) > schema['maxItems']:
            raise AIError('Invalid exception list.')
        for item in value:
            validate(item, schema['items'])


class Registry:
    def __init__(self, app_ids, aliases=None):
        self.app_ids = frozenset(app_ids) | {'browser', 'music', 'aidy'}
        self.aliases = dict(aliases or {})
        self.windows = []
        self.active_window = None
        self.active_app = None
        self.desktop_state = {}
        self.installed_apps = []
        self.agent_mode = False
        self.last_app = None
        self.last_window = None
        self.plan_feedback = ''
        self.task_progress = {}
        self.pending_task = None

    def schema(self, tool_names=None):
        selected = {name: TOOLS[name] for name in tool_names} if tool_names else TOOLS
        properties = {}
        for name, tool in selected.items():
            for key, schema in tool.parameters['properties'].items():
                if key in properties and 'enum' in schema:
                    properties[key]['enum'] = sorted(set(properties[key]['enum']) | set(schema['enum']))
                else:
                    properties[key] = copy.deepcopy(schema)
        # One small schema avoids the exponential decoder-state growth of a
        # discriminated union repeated six times. Tool-specific required keys
        # and constraints remain authoritative in plan(), before ANY action.
        args = {'type': 'object', 'properties': properties, 'additionalProperties': False}
        return obj({'clarification': {'type': 'string'}, 'steps': {
            'type': 'array', 'maxItems': 6,
            'items': obj({'tool': enum(*selected), 'arguments': args, 'delay_seconds': integer(0, 3600)})}})

    def descriptions(self):
        return {name: {'description': tool.description, 'arguments': tool.parameters['properties']}
                for name, tool in TOOLS.items()}

    def plan(self, payload):
        payload = copy.deepcopy(payload)
        if type(payload) is not dict or not {'clarification','steps'} <= set(payload) or set(payload)-{'clarification','steps','goals','continuation'}:
            raise AIError('The model returned an invalid plan.')
        if 'goals' in payload or 'continuation' in payload:
            try:
                validate_goals(payload)
                if any(t not in TOOLS or t=='task_complete' for g in payload['goals'] for t in g['required_tools']):
                    raise ValueError()
                for goal in payload['goals']:
                    for criterion in goal['criteria']:
                        if criterion['kind']=='app_open':
                            criterion['target']=self.aliases.get(criterion['target'],criterion['target'])
                            if criterion['target'] not in self.app_ids:
                                raise ValueError()
                        if criterion['kind']=='tool_verified' and criterion['target'] not in TOOLS:
                            raise ValueError()
            except (ValueError,KeyError,TypeError):
                raise AIPlanError('Return every task goal with its complete ordered typed action plan.') from None
        question, steps = payload['clarification'], payload['steps']
        validate(question, {'type': 'string'})
        if type(steps) is not list or len(steps) > (20 if payload.get('goals') else 6) or bool(question.strip()) == bool(steps):
            raise AIError('Expected a clarification or a bounded ordered action plan.')
        for index, step in enumerate(steps):
            if type(step) is not dict or set(step) != {'tool', 'arguments', 'delay_seconds'}:
                raise AIError('Invalid action structure.')
            name = step['tool']
            if type(name) is not str or name not in TOOLS:
                raise AIError('Unsupported tool.')
            if name == 'close_everything' and step['arguments'] == {}:
                step = dict(step, arguments={'except_apps': []})
                steps[index] = step
            if name == 'document_write' and type(step['arguments']) is dict:
                # Older recorded plans keep the conservative replan behavior.
                step['arguments'].setdefault('finish_task', False)
            try:
                validate(step['arguments'], TOOLS[name].parameters)
            except AIError:
                raise AIPlanError('Use ONLY the exact required argument keys and types for '+name+'. No action was executed for this invalid plan.') from None
            if name == 'document_write':
                text, count = step['arguments']['text'], step['arguments']['word_count']
                if not text.strip():
                    raise AIPlanError('Generate the actual document text before calling document_write.')
                actual = len(text.split())
                if count and abs(actual-count) > length_tolerance(count):
                    raise AIPlanError(f'The requested draft targets {count} whitespace-separated words (within five percent); your text has {actual}. Correct the draft before calling document_write.')
            if name == 'web_search' and not step['arguments']['query'].strip():
                raise AIError('What should I search for?')
            validate(step['delay_seconds'], integer(0, 3600))
            if step['delay_seconds'] and (not TOOLS[name].schedulable or len(steps) != 1):
                raise AIError('Schedule one supported action at a time.')
            if name in ('open_app', 'close_app'):
                allowed = self.app_ids | {'$last'} | ({'$active'} if name == 'close_app' else set())
                if step['arguments']['app'] not in allowed:
                    raise AIPlanError('Choose an actual app_catalog ID or the document_write tool for a Notepad/Word document. The application name was not a registered catalog ID.')
            window_ids = {w['id'] for w in self.windows}
            if name == 'close_window' and step['arguments']['window'] not in window_ids | {'$active'}:
                raise AIError('The selected window is no longer available.')
            if name == 'close_everything':
                if any(target not in self.app_ids | window_ids for target in step['arguments']['except_apps']):
                    raise AIError('Unknown application in the exception list.')
            exits = (name == 'exit_assistant' or (name == 'close_app' and step['arguments']['app'] == 'aidy') or
                     (name == 'close_window' and step['arguments']['window'] == '$active' and self.active_app == 'aidy'))
            if exits and index != len(steps) - 1:
                raise AIError('Closing Aidy must be the final action.')
            if name == 'close_app' and step['arguments']['app'] == 'aidy' and step['delay_seconds']:
                raise AIError('Closing Aidy cannot be scheduled.')
            if name == 'voice_access' and step['arguments']['action'] != 'grant':
                if step['arguments']['role'] != 'Guest' or step['arguments']['duration_seconds'] != 0:
                    raise AIError('Invalid access-management arguments.')
        return copy.deepcopy(payload)


AGENT_TOOLS = frozenset({'recycle_bin', 'explorer_action', 'calculator_action', 'notepad_action', 'settings_page', 'document_write', 'observe_desktop', 'window_action', 'ui_action', 'key_input', 'mouse_input',
                         'browser_action', 'open_url', 'file_action', 'save_document', 'task_complete'})


def permission(step):
    name, args = step['tool'], step['arguments']
    if name == 'system_action':
        return args['action']
    if name == 'study_mode':
        return 'study_mode_' + args['action']
    if name == 'assistant_audio':
        return 'mute' if args['muted'] else 'unmute'
    if name == 'close_app' and args['app'] == '$active':
        return 'close_active'
    return TOOLS[name].permission


def needs_confirmation(step):
    return (TOOLS[step['tool']].confirm or permission(step) in {'shutdown', 'restart', 'study_mode_start', 'study_mode_stop'}
            or step['tool'] == 'recycle_bin' and step['arguments']['operation'] in {'empty', 'restore'})
