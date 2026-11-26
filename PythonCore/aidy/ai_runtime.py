"""Aidy adapter: identity, confirmation, result-bearing existing Windows actions."""
import json
import copy
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlencode

from .ai_engine import AIEngine, Result
from .ai_memory import MemoryStore, owners
from .ai_provider import AIConfig, GeminiProvider
from .ai_tools import AIError, AIClarification, Registry, AGENT_TOOLS
from .ai_windows import Windows
from .logui import ui_state, info, UI_MODE

# A page opened from another app shows in the browser within ~1 s here.
BROWSER_LANDING_SECONDS = 3.0

# Spoken length (owner 2026-11-25): ordinary replies stay short; errors, questions
# and requested detail are spoken whole. Local synthesis runs near real time and the
# microphone is off while Aidy talks, so even important speech has a ceiling.
ORDINARY_WORDS = 12
LONG_SENTENCE_WORDS = 24  # a longer single "sentence" is data (tab lists, file text)
IMPORTANT_WORDS = 150
# A sentence ends at . ! ? before a capital, digit, quote or bracket: "3 p.m. per the U.S. feed" is one.
SENTENCE_END = re.compile(r'(?:(?<=[.!?])|(?<=[.!?]["\')]))\s+(?=["\'(]?[A-Z0-9])')


def spoken_text(message, important=False):
    """What Aidy says aloud, in whole sentences; the complete message stays on screen."""
    from .voice_style import rejoin, speech_text
    message = ' '.join(str(message).split())
    progress = re.match(r'(\d+) of (\d+) (goals|actions) (?:completed|sent)\.\s*(.*)', message)
    if progress:
        done, total, kind, reason = progress.groups()
        if kind == 'goals':  # "<goal description>: <reason>"; action counts carry the reason alone
            reason = re.sub(r'^[^:]{0,240}:\s*', '', reason, count=1)
        message = ('Done. ' if done == total else f'Done {done} of {total}. ' if done != '0'
                   else "I couldn't do that. ") + reason
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', message)
    text = re.sub(r'(?:https?://|www\.)\S+', '', text)
    text = SYMBOLS.sub('', speech_text(text)).replace('*', '')
    text = re.sub(r'(?:^|(?<=\s))#+(?=\s)', '', text)  # markdown headings, not "C#"
    text = re.sub(r'\s+([.,!?;:])', r'\1', ' '.join(text.split()))
    sentences = [s.strip() for s in rejoin(SENTENCE_END.split(text)) if s.strip(' .!?')]
    if not sentences:
        return ''
    while len(sentences) > 1 and len(sentences[0].split()) <= 3:
        sentences[:2] = [sentences[0] + ' ' + sentences[1]]  # "Certainly." is never said alone
    words = sentences[0].split()
    if important and len(words) > IMPORTANT_WORDS:
        return ' '.join(words[:IMPORTANT_WORDS]).rstrip(',;:') + '.'
    if not important and len(words) > LONG_SENTENCE_WORDS:
        head = words[:LONG_SENTENCE_WORDS]
        clauses = [i for i, word in enumerate(head) if i >= 3 and word[-1] in ',;:']
        return ' '.join(head[:clauses[-1] + 1] if clauses else words[:ORDINARY_WORDS]).rstrip(',;:') + '.'
    limit = IMPORTANT_WORDS if important else ORDINARY_WORDS
    spoken = sentences[0]
    for sentence in sentences[1:]:
        if len((spoken + ' ' + sentence).split()) > limit:
            break
        spoken += ' ' + sentence
    return spoken


SYMBOLS = re.compile('[\U00010000-\U0010FFFF⌀-⏿☀-➿⬀-⯿️]')

# Dictation ends by itself after this much silence (the wake word is needed again).
DICTATION_IDLE_SECONDS = 60
# Numbered labels stay on screen (and Aidy listens for a number without the wake word).
LABELS_SECONDS = 30


class AIRuntime:
    def __init__(self, host):
        self.host = host
        self.config = AIConfig.load(host.base_dir)
        self.engine = None
        self.memory = MemoryStore()  # what the user asked Aidy to remember (local file)
        self._conversation_until = 0  # no-wake listening after Aidy's spoken reply
        # Voice dictation (ai_keys): every utterance is typed. Ends on a stop phrase,
        # cancel, the wake word, a failure, or DICTATION_IDLE_SECONDS of silence
        # checked between utterances (never while speech is being captured).
        self._dictating, self._dictation_identity, self._dictation_activity = False, None, 0.0
        self._labels, self._labels_window, self._labels_view, self._labels_until = [], None, '', 0
        self._active_target = None
        self._cancel_collector = None
        self._closed = False
        self._config_checked = 0
        self._confirmed_slots = None
        self._study_identity = None
        self.windows = Windows(host)
        self._command_until = 0
        self._exit_requested = False
        self._close_named_windows = False
        self._confirmed_actions = {}
        self._cancel_future = None
        from .ai_catalog import AppCatalog
        from .ai_automation import UIClient
        self.catalog = AppCatalog()
        if self.enabled and self.config.agent_enabled:
            # Discover registrations during model/audio startup, before a user
            # command needs them. This worker only returns local catalog data.
            self.catalog.refresh(timeout=0)
        self.uia = UIClient()
        self.desktop_task = None

    @property
    def enabled(self):
        return self.config.mode == 'api'

    @property
    def pending(self):
        return self.enabled and (time.monotonic() < max(self._command_until, self._conversation_until) or
                                 self.dictating or time.monotonic() < self._labels_until or
                                 (self.engine is not None and self.engine.pending))

    @property
    def dictating(self):
        return self._dictating

    @property
    def awaiting_number(self):
        return bool(self.enabled and self.engine and self.engine.pending and self.engine.pending_adjustments)

    def end_listening(self):
        self._command_until = self._conversation_until = 0
        if self.engine:
            self.engine.pending_until = 0
        if self._dictating and time.monotonic() - self._dictation_activity >= DICTATION_IDLE_SECONDS:
            self._stop_dictation()
            self.report(Result('verified', 'Dictation off.'))

    def accept_wake(self):
        self._stop_dictation()  # "Aidy" while dictating means a command comes next
        self._command_until = time.monotonic() + 8
        if self.engine:
            self.engine.clarification_count = 0
            self.engine.awaiting_clarification = False
        self.host._play_wake_ack()
        ui_state('COMMAND_LISTENING')

    def reload(self):
        cfg = AIConfig.load(self.host.base_dir)
        if cfg != self.config:
            self.cancel()  # also closes the conversation window
            if self.engine:
                self.engine.close()
            self.engine = None
            self.config = cfg
            if self.enabled:
                from .intent_api import stop_local_intent_api
                stop_local_intent_api()
                self.host.intent_api_ready=False
            if self.enabled and self.config.agent_enabled:
                self.catalog.refresh(timeout=0)
            self.host.follow_up.clear_pending()
            self.host.follow_mode.clear()
            self.host.history.clear()
            self.host.context_mgr.clear_context()
            self.host.short_memory = {'last_intent': None, 'args': None, 'status': None}

    def cancel(self):
        self._command_until = self._conversation_until = 0
        self._stop_dictation()
        self._hide_labels()
        if self.engine:
            self.engine.cancel()

    def close(self):
        self._closed = True
        if self.engine:
            self.engine.close()
        self.uia.close()

    def identity(self):
        a = self.host
        if not a.voice_id_enabled:
            return ('local', 'Admin')
        if not a.voice_auth.has_admin() or not a._last_audio_buffer:
            raise AIError('Voice ID requires an enrolled administrator and fresh speaker audio.')
        embedding = a.voice_auth.extract_embedding(a._last_audio_buffer)
        role, *_ = a.voice_auth.identify(a._last_audio_buffer, precomputed_emb=embedding)
        identity = getattr(a.voice_auth, 'last_identity', None)
        if not role or not identity or identity[0] is None:
            if self.engine:
                self.engine.clear()
            raise AIError('Access denied. Voice not recognized.')
        return identity

    def authorize(self, identity, intent):
        if self._closed or self.host._stop_event.is_set() or not self.enabled:
            return False
        if not self.host.voice_id_enabled:
            return identity == ('local', 'Admin')
        if identity[0] == 'local':
            return False
        return self.host.voice_auth.authorize_identity(identity, intent)

    def report(self, result):
        a = self.host
        data = result.data or {}
        redact = self.engine.provider.credentials.redact if self.engine else (lambda text: text)
        message = ' '.join(result.message.split())[:1800]
        # A conversational reply is spoken whole with its tone; an acting turn
        # may carry one too (data['reply']), spoken after the action outcome.
        answered = result.status == 'answered'
        reply = result.message if answered else data.get('reply', '') if result.ok else ''
        tone = data.get('tone', 'neutral')
        if reply and not answered:
            message = (message + ' ' + ' '.join(reply.split()))[:1800]
        sources = '; '.join(s['title'] for s in data.get('sources', []) if s.get('title'))
        if reply and sources:
            message += ' Sources: ' + sources[:300]  # shown, never spoken
        if data.get('raw'):  # the data behind a spoken answer: shown on one line, never spoken
            message = (message + ' Data: ' + ' '.join(str(data['raw']).split()))[:1800]
        message = redact(message)
        # A failure never borrows a success acknowledgement; a confirmation may
        # carry a shorter spoken question than its on-screen text.
        voice_text = result.spoken_message if result.spoken_message and (result.ok or result.status == 'clarification') else result.message if not answered else ''
        if result.ok and data.get('silent'):
            voice_text = ''  # voice keyboard: a spoken "Done." after every key would slow the user down
        # Errors and questions are spoken whole; a reply when the model marked it as detail or it
        # ends with a question (Aidy then listens for the answer, so the user must hear it).
        important = not result.ok or result.status == 'clarification'
        reply_important = important or bool(data.get('detail')) or reply.rstrip().endswith('?')
        # Redact before shortening too: a cut credential would no longer match.
        spoken = redact(spoken_text(redact(' '.join(voice_text.split())[:1800]), important)) if voice_text else ''
        reply_spoken = redact(spoken_text(redact(reply), reply_important)) if reply else ''
        # A distinct UI channel keeps dynamic answers out of recognition routing.
        if UI_MODE:
            print('AI_RESPONSE:' + message, flush=True)
        ui_state('SPEAKING' if result.ok or result.status == 'clarification' else 'WARNING')
        if not a.voice.muted and not a._stop_event.is_set():
            if spoken and result.voice_key:
                a.voice.play_or_tts(result.voice_key, spoken)
            elif spoken:
                a.voice.tts_blocking(spoken)
            if reply_spoken:
                a.voice.tts_blocking(reply_spoken, tone=tone)
            a._deafen_after_speak()
        # Call-like flow: after a pure conversational reply keep listening
        # without the wake word; after an action the wake word is needed again.
        followup = self.config.conversation_followup_seconds if answered and reply_spoken else 0
        self._conversation_until = time.monotonic() + followup if followup else 0
        ui_state('IDLE')

    def _announce_live(self):
        """Prerecorded filler while a grounded web answer loads."""
        a = self.host
        if not getattr(self, '_slow_ack_done', False) and not a.voice.muted and not a._stop_event.is_set():
            self._slow_ack_done = True
            a.voice.play_or_tts('let_me_check', 'Let me check.')

    def _network_progress(self):
        """While Google thinks: poll for a spoken cancel. No filler (owner 2026-11-24)."""
        self.poll_cancel_audio()

    def _attach_reply(self, engine, result):
        """Fetch the converse part of a request that also acted, after success only."""
        conversation = engine.turn_conversation
        if not (result.ok and conversation):
            return result
        try:
            reply, sources, _ = engine.answer(conversation)
        except AIError as exc:
            if str(exc) == 'Cancelled.':
                return result
            reply, sources = str(exc), []
        return Result(result.status, result.message, voice_key=result.voice_key,
                      data=dict(result.data or {}, reply=reply, tone=conversation['tone'],
                                detail=conversation['detail'], sources=sources),
                      spoken_message=result.spoken_message)

    @staticmethod
    def _confirmation_key(step):
        return json.dumps([step['tool'], step['arguments']], sort_keys=True)

    def _prepare_confirmation(self, step):
        a = self.host
        self._active_target = None
        self._close_named_windows = False
        self.windows._frozen = None
        label = step['tool'].replace('_', ' ')
        args = step['arguments']
        if step['tool'] == 'close_window':
            label = self.windows.bind_one(args['window'])
        elif step['tool'] == 'close_everything':
            label = self.windows.bind_all(args['except_apps'], self.engine.registry.aliases)
        elif step['tool'] == 'exit_assistant':
            label = 'close Aidy'
        elif step['tool'] == 'close_app':
            if args['app'] == '$active':
                from .system import get_active_window_info
                self._active_target = get_active_window_info()
                if not self._active_target:
                    raise AIError('The active window is unavailable.')
                label += ' ' + self._active_target['process']
            else:
                label += ' ' + args['app']
                self._close_named_windows = self.windows.bind_app(args['app'])
        elif 'action' in args:
            label += ' ' + args['action']
        spoken = None
        def control_name(text):
            # Speech names only the control, in at most five words: window titles
            # and non-English names made the English voice read 10+ s of noise.
            text = ' '.join(str(text).split()[:5])[:40]
            return text if text and text.isascii() else 'this control'
        def verb(operation):
            return 'Press' if operation == 'invoke' else operation.replace('_', '-').capitalize()
        if step['tool'] in {'ui_action', 'mouse_input'} and self.desktop_task:
            view = self.desktop_task.views.get(args['window'], {})
            element = next((e for e in view.get('elements', []) if e['id'] == args['element']), None)
            if not element:
                raise AIError('Inspect the selected control before confirming.')
            operation = str(args.get('operation') or args.get('gesture'))
            label = operation + ' ' + element['name'] + ' in ' + self.desktop_task.target(args['window'])['title'][:100]
            spoken = verb(operation) + ' ' + control_name(element['name'])
        elif step['tool'] == 'key_input' and self.desktop_task:
            # The keyboard guard confirms Enter/space only when it would send.
            label = 'press ' + args['chord'] + ' to send in ' + self.desktop_task.target(args['window'])['title'][:100]
            spoken = 'Send the message'
        elif step['tool'] == 'click_point' and self.desktop_task:
            label = args['gesture'] + ' ' + args['target'][:80] + ' in ' + self.desktop_task.target(args['window'])['title'][:100]
            spoken = verb(args['gesture']) + ' ' + control_name(args['target'])
        elif step['tool'] == 'click_target':
            label = args['gesture'].replace('_', ' ').capitalize() + ' ' + args['target'][:80]
            spoken = verb(args['gesture']) + ' ' + control_name(args['target'])
        if step['tool'] == 'file_action':
            label = args['operation'] + ' ' + args['path']
        if step['tool'] == 'memory':
            label = spoken = 'Forget everything I remember'
        if step['tool'] == 'recycle_bin':
            label = ('Permanently empty the Windows Recycle Bin' if args['operation']=='empty' else
                     'Restore Recycle Bin item '+args['item'])
        if step['tool'] == 'browser_action':
            label = args['operation'].replace('_', ' ') + ' in ' + self.desktop_task.target(args['window'])['title'][:100]
            binding=self.desktop_task.browser_bindings.get(self._confirmation_key(step),{})
            if 'target_tabs' in binding:label+=f' ({len(binding["target_tabs"])} selected tabs)'
        if step['tool'] == 'voice_access' and args['action'] == 'grant':
            label += ' ' + args['role'] + (f' for {args["duration_seconds"]} seconds' if args['duration_seconds'] else ' permanently')
        if step['tool'] == 'custom_mode' and args['action'] == 'start':
            try:
                cfg = json.loads(Path(a.config_path).read_text(encoding='utf-8-sig'))
                self._confirmed_slots = [s for s in cfg.get('custom_mode', {}).get('slots', []) if s.get('target')]
                label += ': ' + ', '.join(str(s.get('action_type')) + ' ' + str(s['target']) for s in self._confirmed_slots)
            except (OSError, ValueError, TypeError, AttributeError):
                raise AIError('Saved mode is unavailable.')
        if step['delay_seconds']:
            label += f' in {step["delay_seconds"]} seconds'
            spoken = spoken and spoken + f' in {step["delay_seconds"]} seconds'
        return label, spoken or label, copy.deepcopy((self.windows._frozen, self._active_target,
                                                      self._close_named_windows, self._confirmed_slots))

    def confirm(self, step, identity):
        return self.confirm_batch([step], identity)

    def confirm_batch(self, steps, identity):
        a = self.host
        prepared = [(s, *self._prepare_confirmation(s)) for s in steps]
        label = '; '.join(row[1] for row in prepared)
        spoken = '; '.join(row[2] for row in prepared)
        self.report(Result('clarification', f'{label}. Are you sure?', 'are_you_sure_clean',
                           spoken_message=f'{spoken}. Are you sure?'))
        ui_state('CONFIRM')
        for _ in range(2):
            reply = a._listen_confirm_reply()
            if self.engine.cancel_event.is_set() or a._stop_event.is_set():
                return False
            decision = a._classify_confirm_reply(reply or '')
            if decision == 'no':
                return False
            if decision == 'yes':
                try:
                    if self.identity() != identity:
                        return False
                    for s, _, _, binding in prepared:
                        self._confirmed_actions[self._confirmation_key(s)] = binding
                    # Bound stale selections, including cancelled schedules.
                    while len(self._confirmed_actions) > 36:
                        del self._confirmed_actions[next(iter(self._confirmed_actions))]
                    return True
                except AIError:
                    return False
        return False

    def confirm_workspace_identity(self):
        try:
            return (self._study_identity is not None and self.identity() == self._study_identity
                    and self.authorize(self._study_identity, 'open_app'))
        except AIError:
            return False

    def progress(self):
        a = self.host
        a._drain_control_commands()
        if time.monotonic() - self._config_checked > .35:
            self._config_checked = time.monotonic()
            self.reload()
        if a._stop_event.is_set() or self._closed:
            self.cancel()

    def poll_cancel_audio(self):
        """Consume completed cancellation speech during HTTP, never task commands.

        Capture uses the existing buffered microphone/VAD. Complete cancellation
        utterances are decoded off-thread; their late results are discarded.
        """
        self.progress()
        a = self.host
        if self._cancel_future is not None and self._cancel_future.done():
            try:
                text = self._cancel_future.result()
                if ' '.join((text or '').lower().split()) in {'cancel', 'stop', 'abort', 'never mind'}:
                    self.cancel()
            except Exception:
                pass
            self._cancel_future = None
        if a._stop_event.is_set() or not a._is_listening_allowed() or a._is_deafened():
            return
        if self._cancel_collector is None:
            from .audio_capture import UtteranceBuffer
            self._cancel_collector = UtteranceBuffer(max_seconds=3)
        # Capture stays serial with all action/TTS work; silence reads are bounded.
        a.start_stream()
        if a.stream is None:
            return
        import audioop
        from .audio_capture import FRAME_BYTES, AudioDiscontinuityError
        try:
            chunk = a.stream.read(int(a._capture_rate * .02), exception_on_overflow=True)
            if a._capture_rate != 16000:
                chunk, a._resample_state = audioop.ratecv(chunk, 2, 1, a._capture_rate, 16000, a._resample_state)
            a._pcm_pending += chunk
            while len(a._pcm_pending) >= FRAME_BYTES:
                frame, a._pcm_pending = a._pcm_pending[:FRAME_BYTES], a._pcm_pending[FRAME_BYTES:]
                if self._cancel_collector.feed(frame):
                    audio = None if self._cancel_collector.truncated else self._cancel_collector.finish()
                    self._cancel_collector = None
                    if audio is not None and self._cancel_future is None and a.speech is not None:
                        # Transcript-only work cannot extend the HTTP deadline,
                        # change speaker identity, or execute a late command.
                        self._cancel_future = a.speech.start_preview(audio, purpose='command')
                    break
        except (AudioDiscontinuityError, OSError):
            self._cancel_collector = None
            a.stop_stream()

    def run(self, text):
        self._turn_text = text  # the user's own words: a note must not hold a secret they named
        if self.dictating:
            return self._dictate(text)
        if time.monotonic() < self._labels_until:
            from .ai_pointer import label_number, pointer_plan
            number = label_number(text)
            question = self.engine is not None and self.engine.pending
            if number and not question:
                text = f'click {number}'  # a bare number picks a shown label
            elif not pointer_plan(text):
                woken = time.monotonic() < max(self._command_until, self._conversation_until)
                if not woken and not question:
                    return False  # shown numbers keep the mic open only for numbers and clicks
                self._hide_labels()  # another command: the numbers no longer apply
        from .ai_keys import key_plan
        typed = key_plan(text)
        if typed and typed['steps'][0]['tool'] == 'type_into_focus':
            text = self._spoken_text(text)  # type the words as Whisper wrote them
        a = self.host
        request_start = time.perf_counter()
        self._turn_started, self._slow_ack_done = time.monotonic(), False
        self._command_until = 0  # the wake word's listening window is used by this command
        try:
            ui_state('PROCESSING')
            identity = self.identity()
            if self.config.agent_enabled:
                self.catalog.refresh(timeout=.1 if self.catalog.entries else 1.5)
            if self.engine is None:
                from .apps import find_app
                from .ai_desktop import discover_chatgpt
                if not find_app(a.apps, 'chatgpt_app'):
                    if self.catalog.entries:
                        matches = [app for app in self.catalog.entries if app['type']=='shell' and app['display_name'].strip().casefold()=='chatgpt']
                        desktop = dict(matches[0], id='chatgpt_app', aliases=['chatgpt desktop app', 'installed chatgpt']) if len(matches)==1 else None
                    else:
                        desktop = discover_chatgpt()
                    if desktop:
                        desktop['aliases'] = list(desktop.get('aliases', [])) + ['chatgpt', 'chatgpt app', 'gpt app', 'g p t app', 'chat gpt app', 'chat with the app']
                        a.apps.append(desktop)
                browser = find_app(a.apps, 'browser')
                aliases = {'browser': getattr(a, '_preferred_browser', '') or (browser['id'] if browser else 'browser'),
                           'music': getattr(a, '_preferred_music', '') or 'yandex_music'}
                self.engine = AIEngine(GeminiProvider(self.config), Registry([x['id'] for x in a.apps], aliases),
                                       self.authorize, self.confirm, self.execute, self.progress)
            # Local discovery can only supply registered launch targets. Model
            # output selects an opaque ID and never supplies an executable path.
            if self.config.agent_enabled:
                relevant = self.catalog.relevant(text)
                if not relevant and self.engine.pending:
                    retained = {row['id'] for row in self.engine.registry.installed_apps}
                    relevant = [entry for entry in self.catalog.entries if entry['id'] in retained]
                existing = {app['id'] for app in a.apps}
                a.apps.extend(app for app in relevant if app['id'] not in existing)
                registry = self.engine.registry
                registry.installed_ids = {app['id'] for app in relevant}
                registry.app_ids = frozenset(app['id'] for app in a.apps if not app['id'].startswith('installed_') or app['id'] in registry.installed_ids) | {'browser', 'music', 'aidy'}
                registry.installed_apps = [dict(id=app['id'], name=app['display_name'], source=app['type']) for app in relevant]
            self.engine.confirm_batch = self.confirm_batch
            from .apps import find_app
            default_browser = find_app(a.apps, 'browser')
            self.engine.registry.aliases.update(
                browser=getattr(a, '_preferred_browser', '') or (default_browser['id'] if default_browser else 'browser'),
                music=getattr(a, '_preferred_music', '') or 'yandex_music')
            self.engine.registry.app_names = {app['id']: list(app.get('aliases', []))
                for app in a.apps if not app['id'].startswith('installed_')}
            # Lets the model tell "WhatsApp app" from a configured website entry.
            self.engine.registry.website_ids = frozenset(app['id'] for app in a.apps if app.get('type') == 'url')
            from urllib.parse import urlsplit
            self.engine.registry.website_hosts = {app['id']: urlsplit(str(app.get('target', ''))).hostname or ''
                                                  for app in a.apps if app.get('type') == 'url'}
            self.engine.state = ui_state
            self.engine.task_update = self.task_update
            self.engine.step_log = self.step_log
            self.engine.start_agent = self.start_agent
            self.engine.refresh_agent = lambda ref: self.desktop_task.observe(ref)
            self.engine.action_guard = self.action_guard
            self.engine.prepare_agent_step = lambda step: self.desktop_task.prepare(step)
            self.engine.announce = self._announce_live
            if self.engine.identity != identity:
                self.uia.close()
                a.follow_up.clear_pending()
                a.follow_mode.clear()
                a.last_step_actions.clear()
                a.history.clear()
                a.context_mgr.clear_context()
                a.short_memory = {'last_intent': None, 'args': None, 'status': None}
            self.engine.registry.windows = self.windows.snapshot()
            self.engine.registry.active_window = self.windows.active_ref
            self.engine.registry.active_app = self.windows.active_app
            # This speaker's notes for the planner and the data answer (a Guest has none).
            self.engine.registry.user_memory = self.memory.items(owners(identity)) if hasattr(self, 'memory') else []
            if self.engine.last_window not in self.windows.targets and self.engine.last_app:
                matches = [ref for ref, window in self.windows.targets.items() if self.windows.matches(window, self.engine.last_app)]
                if len(matches) == 1:
                    self.engine.last_window = matches[0]
                elif self.windows.active_ref in matches:
                    self.engine.last_window = self.windows.active_ref
            # Controls observed in the preceding task are never valid in a new task.
            self.engine.registry.desktop_state = {}
            # Small name correction; the complete task still goes to the model.
            text = re.sub(r'\b(close|quit|exit|stop|open|launch)\s+(?:the\s+)?(?:ad|80|eighty|aidi|ady|eddie)\b',
                          r'\1 Aidy', text, flags=re.IGNORECASE)
            ui_state('PROCESSING')
            # Only HTTP interpretation polls cancellation audio. Desktop executors
            # and confirmations share the same assistant thread, never this worker.
            engine = self.engine
            engine.network_progress = self._network_progress
            prepared = time.perf_counter()
            local = engine.local_commands
            engine.local_commands = local and self._pointer_prefetch(text)
            try:
                result = self._attach_reply(engine, engine.run(text, identity))
            finally:
                engine.local_commands = local
            engine.timings['preparation_ms'] = (prepared-request_start)*1000
            a.stop_stream()
            self._cancel_collector = None
            self._cancel_future = None  # Ignore any late cancellation transcript.
            feedback_start = time.perf_counter()
            self.report(result)
            engine.timings['feedback_ms'] = (time.perf_counter() - feedback_start) * 1000
            # Owner 2026-11-23: only Aidy's own question keeps listening; after a
            # completed or failed command the next one needs the wake word again.
            engine.pending_until = (time.monotonic() + 20 if result.status == 'clarification'
                                    and engine.awaiting_clarification else 0)
            timing = dict(engine.timings)
            timing['feedback_start_ms'] = (feedback_start - engine._turn_start) * 1000
            timing['request_to_feedback_ms'] = (feedback_start-request_start)*1000
            info('AI timing: ' + json.dumps({k: round(v, 2) for k, v in timing.items()}))
            if self._exit_requested:
                # Let WPF close normally so bridge/audio/services are disposed.
                self.end_listening()
                a._stop_event.set()
                if UI_MODE:
                    print('CONTROL:EXIT_APPLICATION', flush=True)
            return result.ok
        except AIError as exc:
            self.report(Result('failed', str(exc)))
            return False
        except Exception:
            self.cancel()
            self.report(Result('failed', 'Could not process that request.'))
            return False
        finally:
            a.stop_stream()
            self._cancel_collector = None
            self._cancel_future = None
            self.desktop_task = None

    def start_agent(self, text):
        from .ai_automation import DesktopTask
        self.desktop_task = DesktopTask(self, text)
        self.desktop_task.observe('')

    def task_update(self, message):
        message=' '.join(message.split())[:1200]
        info('Task progress: '+message)
        if UI_MODE:
            print('AI_RESPONSE:'+message,flush=True)

    def _await_browser_page(self, before):
        """True once a browser window appears or changes its title after a launch."""
        end = time.monotonic() + BROWSER_LANDING_SECONDS
        while time.monotonic() < end:
            self.progress()
            if self.engine.cancel_event.is_set():
                return False
            if any(before.get(hwnd) != title for hwnd, title in self.windows.browser_titles().items()):
                return True
            self.engine.cancel_event.wait(.05)
        return False

    def step_log(self, step, result, elapsed_ms):
        """One line per executed action, so a failed chain can be traced; typed text stays out."""
        arguments = {k: v for k, v in step['arguments'].items() if k != 'text'}
        # Successful messages can carry read file/page content: only failure reasons are logged.
        detail = '' if result.ok else ' '.join(result.message.split())[:120]
        credentials = getattr(getattr(self.engine, 'provider', None), 'credentials', None)
        if detail and credentials is not None:
            detail = credentials.redact(detail)
        info(f"AI step: {step['tool']} {json.dumps(arguments, ensure_ascii=False)[:200]} -> {result.status} "
             f"{elapsed_ms:.0f}ms {detail}".rstrip())

    def action_guard(self, step):
        if step['tool'] == 'open_app' and self.desktop_task:
            chosen = next((app for app in self.host.apps if app['id'] == step['arguments']['app']), None)
            if chosen and chosen['id'].startswith('installed_'):
                choices = [app for app in self.host.apps if app['id'] in self.engine.registry.installed_ids
                           and app.get('display_name', '').casefold() == chosen.get('display_name', '').casefold()
                           and app.get('type') == chosen.get('type')]
                if len(choices) > 1 and not re.search(r'\b(first|second|1|2)\b', self.desktop_task.request, re.I):
                    raise AIClarification('Which ' + chosen['display_name'][:60] + ': first or second?')
        if step['tool'] in AGENT_TOOLS and self.desktop_task:
            return self.desktop_task.guard(step)
        return False

    def tick(self):
        if self._labels and time.monotonic() >= self._labels_until:
            self._hide_labels()
        if self.enabled and self.engine:
            for result in self.engine.tick():
                self.report(result)

    def execute(self, step):
        # Imports here avoid circular dependency with Aidy and preserve patchable
        # existing executor functions for deterministic tests.
        a = self.host
        name, args = step['tool'], step['arguments']
        binding = self._confirmed_actions.pop(self._confirmation_key(step), None)
        if binding is not None:
            (self.windows._frozen, self._active_target,
             self._close_named_windows, self._confirmed_slots) = binding
        a.stop_stream()
        ui_state('EXECUTING')
        def outcome(ok, message, verified=False, failure='Could not complete that action.'):
            return Result(('verified' if verified else 'dispatched') if ok else 'failed',
                          message if ok else failure)
        if name in AGENT_TOOLS - {'open_url', 'task_complete'}:
            if not self.desktop_task:
                return Result('failed', 'No active desktop task is available.')
            return self.desktop_task.execute(step)
        if name == 'memory':
            return self._memory(args)
        from . import assistant as core
        from . import system
        if name in {'press_keys', 'type_into_focus', 'dictation'}:
            return self._keyboard(name, args)
        if name in {'click_target', 'show_labels'}:
            return self._pointer(name, args)
        if name in {'web_search', 'open_url'}:
            url = 'https://www.google.com/search?' + urlencode({'q': args['query'].strip()}) if name == 'web_search' else args['url']
            parsed = urlparse(url)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                return Result('failed', 'Use an HTTP or HTTPS address without embedded credentials.')
            target = self.engine.registry.aliases.get('browser', 'browser') if self.engine else 'browser'
            browser = next((entry for entry in a.apps if entry['id'] == target), None) or core.find_app(a.apps, target)
            if target != 'browser' and (not browser or browser.get('type') != 'exe'):
                return Result('failed', 'The saved browser is unavailable. Choose an installed browser in settings.')
            # Inside a desktop task the next goal must not start before this
            # page exists: two launches 20 ms apart left a page in a hidden window.
            before = self.windows.browser_titles() if self.desktop_task else None
            # Only registered browser executables receive this fixed HTTPS URL.
            if browser and browser.get('type') == 'exe':
                browser = dict(browser, args=[*(browser.get('args') or []), url])
                ok = core.launch_app(browser)
            else:
                try:
                    os.startfile(url)
                    ok = True
                except OSError:
                    ok = False
            if not ok:
                return Result('failed', 'Could not open the browser.')
            kind = 'Search' if name == 'web_search' else 'Website'
            if self.desktop_task:
                self.desktop_task.input_window=None
                if self._await_browser_page(before):
                    return Result('verified', kind + ' opened.', spoken_message='Done.')
            return Result('dispatched', kind + ' request sent.', spoken_message='Done.')
        if name in ('open_app', 'close_app'):
            target = args['app']
            if name == 'close_app' and self._close_named_windows:
                self._close_named_windows = False
                return self.windows.close_frozen(self.engine.cancel_event,
                    lambda: self.authorize(self.engine.identity, 'close_app'))
            if target == '$active':
                current = system.get_active_window_info()
                if not current or current != self._active_target:
                    return Result('failed', 'The foreground window changed after confirmation.')
                proc = current['process']
                if not proc.lower().endswith('.exe'):
                    proc += '.exe'
                if a._is_protected_process_for_close(proc):
                    return Result('failed', 'This application is protected.')
                ok = core.close_app_by_process(proc, force=False)
                return outcome(ok, 'Close request sent; final window state is unverified.')
            app = next((entry for entry in a.apps if entry['id'] == target), None) or core.find_app(a.apps, target)
            if not app:
                return Result('failed', 'The configured application is unavailable.')
            if name == 'close_app' and app.get('process') and a._is_protected_process_for_close(app['process']):
                return Result('failed', 'This application is protected.')
            ok = core.launch_app(app) if name == 'open_app' else core.close_app(app)
            if ok and name=='open_app' and self.desktop_task:
                self.desktop_task.input_window=None  # Explicitly requested focus change.
            if ok and name == 'open_app' and self.desktop_task and app.get('type') != 'url':
                deadline = min(self.engine.task_deadline, time.monotonic() + 3)
                while time.monotonic() < deadline:
                    self.progress()
                    self.engine._check(self.engine.task_deadline)
                    self.windows.snapshot()
                    matched = [(ref, window) for ref, window in self.windows.targets.items() if self.windows.matches(window, target)]
                    if matched:
                        selected = next(((ref, row) for ref, row in matched if ref == self.windows.active_ref), matched[0])
                        return Result('verified', 'Opened.', data=dict(window=selected[0], app=target))
                    self.engine.cancel_event.wait(.04)
            # Process enumeration through tasklist added seconds and did not
            # prove that a newly launched window was ready. Report dispatch.
            return outcome(ok, f'{"Opening" if name == "open_app" else "Closing"} {target.replace("_", " ")}.',
                           failure=f'Could not {"open" if name == "open_app" else "close"} {target}.')
        if name in ('close_window', 'close_everything'):
            result = self.windows.close_frozen(self.engine.cancel_event,
                lambda: self.authorize(self.engine.identity, 'close_active' if name == 'close_window' else 'close_everything'))
            if result.status == 'verified' and self.desktop_task and self.desktop_task.input_window:
                from .ai_windows import owner
                selected = self.desktop_task.input_window
                if owner(selected['hwnd']) != selected['pid']:
                    self.desktop_task.input_window = None
            return result
        if name == 'exit_assistant':
            self._exit_requested = True
            return Result('dispatched', 'Closing Aidy.')
        if name == 'brightness_change':
            brightness=system.adjust_brightness(args['direction']=='up',args['steps'])
            return Result('verified' if brightness.ok else 'failed',brightness.message,
                data=dict(levels=brightness.levels or {},independent_control_unavailable=brightness.not_executed))
        if name == 'volume_change':
            return outcome(a._apply_volume_steps(args['direction']=='up',args['steps']),
                           'Volume adjustment sent.',failure='Volume control is unavailable.')
        if name == 'set_volume':
            return outcome(core.set_volume_percent(args['value']), f'Volume: {args["value"]} percent.', True)
        if name == 'system_action':
            key = args['action'].replace('_', ' ')
            result = core.COMMANDS[key]()
            if key in ('shutdown', 'restart'):
                ok = type(result) is int and result == 0
            elif key == 'lock':
                ok = bool(result)
            else:
                ok = result is not False  # Some legacy Win32 send functions return None.
            return outcome(ok, f'{key.capitalize()} requested.')
        if name == 'mouse_action':
            return outcome(bool(core.execute_mouse_command(args['action'])), 'Mouse input sent.')
        if name == 'window_switch':
            action = args['action']
            if action == 'start':
                a.start_window_switch()
            elif not a.window_switch_active:
                return Result('failed', 'Window switching is not active.')
            elif action in ('left', 'right'):
                a.window_switch_step(action)
            else:
                a.end_window_switch(cancel=action == 'cancel')
            return Result('dispatched', 'Window switch input sent.')
        if name == 'timer_start':
            return outcome(a._start_timer(args['seconds']), 'Timer started.', True)
        if name == 'timer_cancel':
            return outcome(a._cancel_timer(announce=False), 'Timer cancelled.', True)
        if name == 'timer_status':
            left = max(0, int(a.timer_end_at - time.time())) if a.timer_active else 0
            return Result('verified', f'Timer has {left} seconds remaining.' if left else 'No active timer.')
        if name == 'study_mode':
            action = args['action']
            if action == 'status':
                return Result('verified', f'Study mode has {a._study_time_left_seconds()} seconds remaining.' if a.study_mode_active else 'Study mode is off.')
            if action == 'start':
                self._study_identity = self.engine.identity
            ok = a._start_study_mode() if action == 'start' else a._stop_study_mode(announce=False, prompt_restore_now=True)
            return outcome(ok, 'Study mode request accepted; workspace setup/restoration may still be running.')
        if name == 'assistant_audio':
            a.voice.muted = args['muted']
            return Result('verified', 'Aidy voice muted.' if args['muted'] else 'Aidy voice enabled.')
        if name == 'voice_access':
            if not a.voice_id_enabled:
                return Result('failed', 'Enable Voice ID and authenticate before managing access.')
            action = args['action']
            if action == 'list':
                a._send_voice_users()
                return Result('verified', 'Voice profiles are shown in settings.')
            if action == 'revoke':
                count = a.voice_auth.revoke_expired()
                return Result('verified', f'Revoked {count} expired entries.')
            expiry = time.time() + args['duration_seconds'] if args['duration_seconds'] else None
            ok = a.voice_auth.grant_access(role=args['role'], expires_at=expiry)
            return outcome(ok, 'Access granted to the pending speaker.', True)
        if name == 'custom_mode':
            if args['action'] == 'stop':
                return Result('verified', 'Custom mode disabled. Previously launched apps remain open.')
            return self._custom_mode()
        if name == 'current_time':
            return Result('verified', datetime.now().astimezone().strftime('It is %H:%M on %A, %d %B %Y.'))
        if name == 'battery_status':
            from . import system
            info = system.battery_status()
            if not info['has_battery']:
                return Result('verified', 'This computer has no battery.')
            if info['percent'] is None:
                return Result('failed', "I couldn't read the battery level.")
            # Exact prerecorded wording (voice pack families) plays instantly.
            return Result('verified', f"Battery at {info['percent']} percent" + (', charging.' if info['charging'] else '.'))
        if name == 'volume_status':
            from . import system
            percent, muted = system.get_volume_percent()
            if percent is None:
                return Result('failed', "I couldn't read the volume level.")
            return Result('verified', f'Volume is at {percent} percent' + (', muted.' if muted else '.'))
        return Result('failed', 'No executor is registered for this tool.')

    def _memory(self, args):
        """remember / list / forget / forget_all for the current speaker (Guests never get here)."""
        keys = owners(self.engine.identity)
        if not keys:
            return Result('failed', 'Access denied for this action.')
        operation = args['operation']
        try:
            if operation == 'remember':
                self.memory.add(keys[0], args['text'], said=getattr(self, '_turn_text', ''))
                return Result('verified', "I'll remember that.")
            if operation == 'list':  # a data read: the answer request turns it into speech
                return Result('verified', json.dumps(dict(notes=self.memory.items(keys)), ensure_ascii=False))
            if operation == 'forget':  # say which note went: there is no undo for a misheard id
                note = self.memory.forget(keys, args['id'])
                return Result('verified', 'Forgotten: ' + note['text']) if note else Result('failed', "I couldn't find that note.")
            return Result('verified', "I've forgotten everything." if self.memory.forget_all(keys) else 'There was nothing to forget.')
        except AIError as exc:
            return Result('failed', str(exc))

    def _spoken_text(self, text):
        """Whisper's own text (case, punctuation) for this utterance, else the normalized text.

        Recognition lowercases and strips punctuation for command matching; typed words
        need the original. The raw text is used only when it is this same utterance
        (a wake prefix such as "Aidy," may precede it)."""
        from .speech import normalize_transcript
        raw = str(getattr(getattr(self.host, 'speech', None), 'last_raw_text', '') or '').split()
        want = normalize_transcript(text)
        for start in range(len(raw)):
            candidate = ' '.join(raw[start:])
            if normalize_transcript(candidate) == want:
                return candidate
        return text

    def _stop_dictation(self):
        self._dictating, self._dictation_identity = False, None

    def _dictate(self, text):
        """While dictating, words are typed and editing phrases press keys; no Google."""
        from . import ai_keys
        try:
            step = ai_keys.dictation_step(self._spoken_text(text))
            identity = self.identity()
            if identity != self._dictation_identity:
                raise AIError('Another voice is speaking.')
            if not self.authorize(identity, 'desktop_control'):
                raise AIError('Access denied for this action.')
            self._dictation_activity = time.monotonic()
            ui_state('EXECUTING')
            result = self._keyboard(step['tool'], step['arguments'])
        except AIError as exc:
            result = Result('failed', str(exc))
        except Exception:
            result = Result('failed', 'Could not type that.')
        if not result.ok and self._dictating:
            self._stop_dictation()
            result = Result('failed', result.message + ' Dictation stopped.')
        self.report(result)
        return result.ok

    def _set_dictation(self, on):
        from . import ai_keys, system
        if not on:
            self._stop_dictation()
            return Result('verified', 'Dictation off.')
        try:
            ai_keys.foreground_guard(system.get_active_window_info())
            identity = self.identity()
        except AIError as exc:
            return Result('failed', str(exc))
        self._dictating, self._dictation_identity = True, identity
        self._dictation_activity = time.monotonic()
        return Result('verified', 'Dictation on. Say stop dictation to finish.')

    def _overlay(self, labels):
        if UI_MODE:
            from .ai_pointer import overlay_line
            print(overlay_line(labels), flush=True)

    def _hide_labels(self):
        if self._labels:
            self._overlay([])
        self._labels, self._labels_window, self._labels_view, self._labels_until = [], None, '', 0

    def _show_labels(self, window, view, targets):
        self._labels, self._labels_window, self._labels_view = list(targets), dict(window), view
        self._labels_until = time.monotonic() + LABELS_SECONDS
        self._overlay([dict(n=i + 1, x=t['x'], y=t['y'], w=t['w'], h=t['h']) for i, t in enumerate(targets)])

    def _pointer_prefetch(self, text):
        """False when "click <name>" names nothing on screen: the normal planner (Google,
        agent) handles it then, e.g. "click the first video". Keeps the scan for _pointer."""
        from . import ai_keys, ai_pointer
        self._prefetched = None
        plan = ai_pointer.pointer_plan(text)
        if not plan or plan['steps'][0]['tool'] != 'click_target' or plan['steps'][0]['arguments']['target'].isdigit():
            return True
        try:
            window = ai_pointer.foreground_window()
            ai_keys.foreground_guard(window)
            found = self.uia.call(window, 'targets', self.engine.cancel_event, self.progress, timeout=4)
        except Exception:
            return True  # _pointer reports the actual problem
        self._prefetched = (window['hwnd'], found)
        return bool(ai_pointer.match_targets(found['targets'], plan['steps'][0]['arguments']['target']))

    def _targets(self, window):
        prefetched, self._prefetched = getattr(self, '_prefetched', None), None
        if prefetched and prefetched[0] == window['hwnd']:
            return prefetched[1]
        return self.uia.call(window, 'targets', self.engine.cancel_event, self.progress, timeout=4)

    def _pointer(self, name, args):
        """Voice clicks on the foreground window through the UIA worker's checked mouse operation."""
        from . import ai_keys, ai_pointer
        window = ai_pointer.foreground_window()
        try:
            ai_keys.foreground_guard(window)
            if name == 'show_labels':
                if args['action'] == 'hide':
                    self._hide_labels()
                    return Result('verified', 'Numbers hidden.', data={'silent': True})
                found = self._targets(window)
                if not found['targets']:
                    return Result('failed', "I can't find anything to click in this window.")
                self._show_labels(window, found['view'], found['targets'][:ai_pointer.MAX_LABELS])
                return Result('verified', f"{min(len(found['targets']), ai_pointer.MAX_LABELS)} numbers shown.",
                              data={'silent': True})
            target, gesture = args['target'], args['gesture']
            if target.isdigit() and time.monotonic() < self._labels_until and self._labels:
                if window['hwnd'] != self._labels_window['hwnd']:
                    self._hide_labels()
                    return Result('failed', 'The window changed. Say show numbers again.')
                if not 1 <= int(target) <= len(self._labels):
                    return Result('failed', f'There is no number {int(target)}.')
                chosen, view = self._labels[int(target) - 1], self._labels_view
            else:
                found = self._targets(window)
                matches = ai_pointer.match_targets(found['targets'], target)
                if not matches and target.isdigit():
                    # "click seven" with no numbers shown: a button named Seven (Calculator).
                    matches = ai_pointer.match_targets(found['targets'], ai_pointer.number_word(int(target)))
                if not matches:
                    return Result('failed', 'Say show numbers first.' if target.isdigit() else f"I can't find {target} here.")
                if len(matches) > 1 or found.get('truncated'):
                    # Never guess: a second control with this name may exist off the scan.
                    self._show_labels(window, found['view'], matches[:ai_pointer.MAX_LABELS])
                    return Result('verified', 'Say one to click it.' if len(matches) == 1
                                  else f'I see {len(matches)}. Say the number.')
                chosen, view = matches[0], found['view']
            self._hide_labels()
            if ai_pointer.risky(chosen):
                step = dict(tool='click_target', delay_seconds=0,
                            arguments=dict(target=(chosen['name'] or chosen.get('automation_id') or 'this control')[:80],
                                           gesture=gesture))
                if not self.confirm(step, self.identity()):
                    return Result('cancelled', 'Cancelled.')
            self.uia.call(window, 'mouse', self.engine.cancel_event, self.progress, timeout=4,
                          element=chosen['id'], view=view, gesture=gesture,
                          rect=[chosen['x'], chosen['y'], chosen['w'], chosen['h']])
        except AIError as exc:
            return Result('failed', str(exc))
        except Exception:
            self._hide_labels()
            return Result('failed', 'That control could not be used.')
        label = gesture.replace('_', ' ').capitalize()
        return Result('dispatched', f"{label}: {chosen['name'] or 'item'}.", data={'silent': True})

    def _keyboard(self, name, args):
        """Voice keyboard on the foreground window (ai_keys.py). Success is silent."""
        from . import ai_keys, system
        if name == 'dictation':
            return self._set_dictation(args['action'] == 'start')
        try:
            ai_keys.foreground_guard(system.get_active_window_info())
            if name == 'press_keys':
                keys = ai_keys.parse_keys(args['keys'])
                if keys and ai_keys.is_blocked(keys):
                    raise AIError('That shortcut is blocked for safety.')
                if keys != args['keys']:
                    raise AIError('That key is not allowed.')
                if args['times'] > ai_keys.MAX_TIMES:
                    raise AIError(f'At most {ai_keys.MAX_TIMES} presses.')  # short: spoken replies are cut at ten words
                ai_keys.press(keys, args['times'])
                message = f'Pressed {keys}' + (f' {args["times"]} times.' if args['times'] > 1 else '.')
            else:
                ai_keys.type_text(args['text'])
                message = 'Typed: ' + args['text'][:80]
        except AIError as exc:
            return Result('failed', str(exc))
        except Exception:
            return Result('failed', 'Windows did not accept the keys.')
        return Result('dispatched', message, data={'silent': True})

    def _custom_mode(self):
        """Run only saved slots. Propagate real results; stop on first failure."""
        from . import assistant as core
        a = self.host
        slots, self._confirmed_slots = self._confirmed_slots, None
        if not slots or len(slots) > 8:
            return Result('failed', 'Custom mode needs 1 to 8 saved slots.')
        results = []
        a._last_custom_mode_exec_ts = time.time()
        for slot in slots:
            self.progress()
            if self.engine.cancel_event.is_set() or not self.authorize(self.engine.identity, 'grant_access'):
                return Result('failed', f'Custom mode stopped after {len(results)} slots.')
            kind, target = slot.get('action_type'), slot['target']
            name, args = None, {}
            if kind in ('open_app', 'close_app'):
                name, args = kind, {'app': target}
            elif kind == 'function':
                if target in ('shutdown_computer', 'restart_computer'):
                    name, args = 'system_action', {'action': target.split('_')[0]}
                elif target == 'close_everything':
                    name = 'close_everything'
                elif re.fullmatch(r'(volume|brightness)_(up|down)_\d{1,3}', target):
                    family, direction, amount = target.split('_')
                    # Preserve the saved mode's existing step interpretation.
                    ok = a._apply_volume_steps(direction == 'up', max(1, min(100, int(amount)))) if family == 'volume' else core.brightness_steps(direction == 'up', max(1, min(10, int(amount))))
                    result = Result('dispatched' if ok else 'failed', 'Saved adjustment dispatched.' if ok else 'Saved adjustment failed.')
                elif re.fullmatch(r'timer_\d{1,3}', target):
                    name, args = 'timer_start', {'seconds': max(1, min(100, int(target[6:]))) * 60}
                elif target.startswith('website_'):
                    sites = {'whatsapp': 'https://web.whatsapp.com', 'chatgpt': 'https://chatgpt.com', 'youtube': 'https://youtube.com', 'gemini': 'https://gemini.google.com', 'stepik': 'https://stepik.org'}
                    kind, target = 'open_url', sites.get(target[8:], 'https://' + target[8:])
                else:
                    return Result('failed', f'Unsupported saved function; stopped after {len(results)} slots.')
            elif kind != 'open_url':
                return Result('failed', f'Unsupported saved slot; stopped after {len(results)} slots.')
            if kind == 'open_url':
                parsed = urlparse(target)
                if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                    return Result('failed', 'Saved URL is not a valid web address.')
                # ShellExecute via os.startfile; URL is never passed to cmd /c.
                try:
                    os.startfile(target)
                    ok = True
                except OSError:
                    ok = False
                result = Result('dispatched' if ok else 'failed', 'Saved website dispatched.' if ok else 'Saved website failed.')
            elif name:
                if name == 'close_everything':
                    self.windows.bind_all([], self.engine.registry.aliases)
                result = self.execute({'tool': name, 'arguments': args, 'delay_seconds': 0})
            results.append(result)
            if not result.ok:
                return Result('failed', f'Custom mode stopped at slot {len(results)}: {result.message}')
        return Result('dispatched', f'{len(results)} saved slots dispatched. Final application state may be unverified.')
