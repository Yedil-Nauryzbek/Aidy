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
from .ai_provider import AIConfig, GeminiProvider
from .ai_tools import AIError, AIClarification, Registry, AGENT_TOOLS
from .ai_windows import Windows
from .logui import ui_state, info, UI_MODE


class AIRuntime:
    def __init__(self, host):
        self.host = host
        self.config = AIConfig.load(host.base_dir)
        self.engine = None
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
        return self.enabled and (time.monotonic() < self._command_until or
                                 (self.engine is not None and self.engine.pending))

    @property
    def awaiting_number(self):
        return bool(self.enabled and self.engine and self.engine.pending and self.engine.pending_adjustments)

    def end_listening(self):
        self._command_until = 0
        if self.engine:
            self.engine.pending_until = 0

    def accept_wake(self):
        self._command_until = time.monotonic() + 8
        if self.engine:
            self.engine.clarification_count = 0
            self.engine.awaiting_clarification = False
        self.host._play_wake_ack()
        ui_state('COMMAND_LISTENING')

    def reload(self):
        cfg = AIConfig.load(self.host.base_dir)
        if cfg != self.config:
            self.cancel()
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
        self._command_until = 0
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
        message = ' '.join(result.message.split())[:1800]
        if self.engine:
            message = self.engine.provider.credentials.redact(message)
        spoken = result.spoken_message if result.ok and result.spoken_message else message
        if self.engine:
            spoken = self.engine.provider.credentials.redact(spoken)
        # A distinct UI channel keeps dynamic answers out of recognition routing.
        if UI_MODE:
            print('AI_RESPONSE:' + message, flush=True)
        ui_state('SPEAKING' if result.ok or result.status == 'clarification' else 'WARNING')
        if not a.voice.muted and not a._stop_event.is_set():
            if result.voice_key:
                a.voice.play_or_tts(result.voice_key, spoken)
            else:
                a.voice.tts_blocking(spoken)
            a._deafen_after_speak()
        ui_state('IDLE')

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
        if step['tool'] in {'ui_action', 'mouse_input'} and self.desktop_task:
            view = self.desktop_task.views.get(args['window'], {})
            element = next((e for e in view.get('elements', []) if e['id'] == args['element']), None)
            if not element:
                raise AIError('Inspect the selected control before confirming.')
            label = str(args.get('operation') or args.get('gesture')) + ' ' + element['name'] + ' in ' + self.desktop_task.target(args['window'])['title'][:100]
        if step['tool'] == 'file_action':
            label = args['operation'] + ' ' + args['path']
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
        return label, copy.deepcopy((self.windows._frozen, self._active_target,
                                     self._close_named_windows, self._confirmed_slots))

    def confirm(self, step, identity):
        return self.confirm_batch([step], identity)

    def confirm_batch(self, steps, identity):
        a = self.host
        prepared = [(s, *self._prepare_confirmation(s)) for s in steps]
        label = '; '.join(row[1] for row in prepared)
        self.report(Result('clarification', f'{label}. Are you sure?', 'are_you_sure_clean'))
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
                    for s, _, binding in prepared:
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
        a = self.host
        request_start = time.perf_counter()
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
            self.engine.state = ui_state
            self.engine.task_update = self.task_update
            self.engine.start_agent = self.start_agent
            self.engine.refresh_agent = lambda ref: self.desktop_task.observe(ref)
            self.engine.action_guard = self.action_guard
            self.engine.prepare_agent_step = lambda step: self.desktop_task.prepare(step)
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
            engine.network_progress = self.poll_cancel_audio
            prepared = time.perf_counter()
            result = engine.run(text, identity)
            engine.timings['preparation_ms'] = (prepared-request_start)*1000
            a.stop_stream()
            self._cancel_collector = None
            self._cancel_future = None  # Ignore any late cancellation transcript.
            feedback_start = time.perf_counter()
            self.report(result)
            engine.timings['feedback_ms'] = (time.perf_counter() - feedback_start) * 1000
            if result.ok or (result.status == 'clarification' and engine.awaiting_clarification):
                engine.pending_until = time.monotonic() + (20 if engine.awaiting_clarification else 8)
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
        from . import assistant as core
        from . import system
        if name in {'web_search', 'open_url'}:
            url = 'https://www.google.com/search?' + urlencode({'q': args['query'].strip()}) if name == 'web_search' else args['url']
            parsed = urlparse(url)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                return Result('failed', 'Use an HTTP or HTTPS address without embedded credentials.')
            target = self.engine.registry.aliases.get('browser', 'browser') if self.engine else 'browser'
            browser = next((entry for entry in a.apps if entry['id'] == target), None) or core.find_app(a.apps, target)
            if target != 'browser' and (not browser or browser.get('type') != 'exe'):
                return Result('failed', 'The saved browser is unavailable. Choose an installed browser in settings.')
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
            if ok and self.desktop_task:
                self.desktop_task.input_window=None
            return outcome(ok, 'Search request sent.' if name == 'web_search' else 'Website request sent.', failure='Could not open the browser.')
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
        return Result('failed', 'No executor is registered for this tool.')

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
