"""Bounded dialogue/execution coordinator, independent of audio and Windows."""
from collections import deque
from dataclasses import dataclass
import copy
import queue
import re
import threading
import time
import uuid

from .ai_tools import AIError, AIPlanError, AIClarification, permission, needs_confirmation, AGENT_TOOLS, TOOLS
from .ai_writing import writing_hints
from .ai_tasks import goal_plans,criterion_satisfied


@dataclass(frozen=True)
class Result:
    status: str  # verified, dispatched, failed, cancelled, scheduled
    message: str
    voice_key: str = ''
    data: dict | None = None
    spoken_message: str = ''

    @property
    def ok(self):
        return self.status in {'verified', 'dispatched', 'scheduled'}


class AIEngine:
    INDEPENDENT_TOOLS=frozenset({'open_app','open_url','web_search','current_time','timer_status',
                                'timer_start','timer_cancel','set_volume','volume_change','brightness_change'})

    @classmethod
    def independent_steps(cls,steps):
        return bool(steps) and all(s['tool'] in cls.INDEPENDENT_TOOLS and not s['delay_seconds']
            and not any(value in {'$last','$active','current'} for value in s['arguments'].values() if isinstance(value,str)) for s in steps)
    def __init__(self, provider, registry, authorize, confirm, execute, progress=lambda: None):
        self.provider, self.registry = provider, registry
        self.authorize, self.confirm, self.execute = authorize, confirm, execute
        self.progress = progress
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self.closed = False
        self.identity = None
        self.history = deque(maxlen=8)
        self.completed_ids = deque(maxlen=256)
        self.last_app = None
        self.last_window = None
        self.last_step = None
        self.undo_chain = []
        self.pending_until = 0
        self.context_until = 0
        self.scheduled = {}
        self._next_task = 1
        self.timings = {}
        self.awaiting_clarification = False
        self.clarification_count = 0
        self.pending_adjustments = None
        self.pending_request_text = None
        self.pending_task = None

    @property
    def busy(self):
        return self._lock.locked()

    @property
    def pending(self):
        return self.pending_until > time.monotonic()

    def cancel(self):
        self.cancel_event.set()
        self.pending_until = 0
        self.awaiting_clarification = False
        self.pending_adjustments = None
        self.pending_request_text = None
        self.pending_task = None

    def clear(self):
        self.history.clear()
        self.last_app = self.last_step = None
        self.last_window = None
        self.undo_chain = []
        self.pending_until = self.context_until = 0
        self.awaiting_clarification = False
        self.clarification_count = 0
        self.scheduled.clear()
        self.pending_adjustments = None
        self.pending_request_text = None
        self.pending_task = None
        self.registry.task_progress = {}
        self.registry.pending_task = None

    def close(self):
        self.closed = True
        self.cancel()
        self.clear()
        self.provider.close()

    def _check(self, deadline):
        if self.closed or self.cancel_event.is_set():
            raise AIError('Cancelled.')
        if time.monotonic() >= deadline:
            self.cancel_event.set()
            raise AIError('Request timed out. Please try again.')

    def _interpret(self, text, deadline):
        self.registry.last_app = self.last_app if self.last_app in self.registry.app_ids else None
        ids = {w['id'] for w in self.registry.windows}
        self.registry.last_window = self.last_window if self.last_window in ids else None
        self.registry.plan_feedback = ''
        for attempt in range(getattr(self.provider.config, 'max_plan_retries', 1) + 1):
            try:
                return self._interpret_once(text, deadline)
            except AIPlanError as exc:
                if attempt >= getattr(self.provider.config, 'max_plan_retries', 1):
                    raise AIError('I could not form a valid action plan. Please restate the task.') from None
                self.registry.plan_feedback = str(exc)
                self._check(deadline)

    def _interpret_once(self, text, deadline):
        if self._model_calls >= getattr(self.provider.config, 'max_model_calls', 12):
            raise AIError('Task model-call limit reached. Remaining steps stopped.')
        self._model_calls += 1
        self.timings['model_calls'] = self._model_calls
        self.registry.planning_calls_remaining = getattr(self.provider.config, 'max_model_calls', 12) - self._model_calls + 1
        getattr(self, 'state', lambda value: None)('PROCESSING')
        results = queue.Queue(maxsize=1)
        history = copy.deepcopy(list(self.history))
        registry_snapshot = copy.deepcopy(self.registry)
        # Daemon worker can only return data. A late response can never execute.
        cancel = self.cancel_event
        provider = self.provider
        def worker():
            count = getattr(provider, 'generation_calls', 0)
            try:
                value = provider.interpret(text, history, registry_snapshot, cancel)
                results.put((value, None, max(1,getattr(provider,'generation_calls',0)-count)))
            except AIError as exc:
                results.put((None, exc, max(1,getattr(provider,'generation_calls',0)-count)))
            except Exception:
                results.put((None, AIError('Google request failed. No action was executed.'),max(1,getattr(provider,'generation_calls',0)-count)))
        thread=threading.Thread(target=worker, name='aidy-ai-http', daemon=True)
        thread.start()
        while True:
            try:
                self._check(deadline)
            except AIError:
                cancel.set()
                if thread.is_alive() and hasattr(provider,'fresh_transport') and not self.closed:
                    self.provider=provider.fresh_transport()
                raise
            try:
                value, error, calls = results.get_nowait()
                self._model_calls += calls - 1
                self.timings['model_calls'] = self._model_calls
                if error:
                    if hasattr(registry_snapshot, 'rejected_plan'):
                        self.registry.rejected_plan = registry_snapshot.rejected_plan
                    raise error
                return self.registry.plan(value)
            except queue.Empty:
                getattr(self, 'network_progress', self.progress)()
                cancel.wait(.005)

    def _resolve(self, step):
        step = copy.deepcopy(step)
        if step['tool'] == 'repeat':
            if not self.last_step:
                raise AIError('There is no completed action to repeat. What should I do?')
            step = copy.deepcopy(self.last_step)
            step['delay_seconds'] = 0
        if step['tool'] in ('open_app', 'close_app') and step['arguments']['app'] == '$last':
            if step['tool'] == 'close_app' and self.last_window in {w['id'] for w in self.registry.windows}:
                return dict(step, tool='close_window', arguments={'window': self.last_window})
            if not self.last_app:
                if step['tool'] == 'close_app' and self.registry.active_window:
                    return dict(step, tool='close_window', arguments={'window': self.registry.active_window})
                raise AIError('Which application do you mean?')
            step['arguments']['app'] = self.last_app
        if step['tool'] in ('open_app', 'close_app'):
            app = step['arguments']['app']
            step['arguments']['app'] = self.registry.aliases.get(app, app)
        if step['tool'] == 'close_app' and step['arguments']['app'] == 'aidy':
            step = dict(step, tool='exit_assistant', arguments={})
        if step['tool'] == 'close_app' and step['arguments']['app'] == '$active':
            step = dict(step, tool='close_window', arguments={'window': '$active'})
        if step['tool'] == 'close_window' and step['arguments']['window'] == '$active':
            if self.registry.active_app == 'aidy':
                return dict(step, tool='exit_assistant', arguments={})
            if not self.registry.active_window:
                raise AIError('Which window should I close? Make it active or name it.')
            step['arguments']['window'] = self.registry.active_window
        return step

    @staticmethod
    def inverse(step):
        inverse = copy.deepcopy(step)
        inverse['delay_seconds'] = 0
        name, args = step['tool'], inverse['arguments']
        if name in ('open_app', 'close_app') and args['app'] != '$active':
            inverse['tool'] = 'close_app' if name == 'open_app' else 'open_app'
        elif name in ('volume_change', 'brightness_change'):
            args['direction'] = 'down' if args['direction'] == 'up' else 'up'
        elif name == 'assistant_audio':
            args['muted'] = not args['muted']
        else:
            return None
        return inverse

    def _dispatch(self, step, identity, action_id, deadline, confirmed=False):
        self.progress()
        self._check(deadline)
        if action_id in self.completed_ids:
            raise AIError('Duplicate action rejected.')
        if not self.authorize(identity, permission(step)):
            raise AIError('Access denied for this action.')
        extra_confirm = getattr(self, 'action_guard', lambda step: False)(step)
        if (needs_confirmation(step) or extra_confirm) and not confirmed:
            if not self.confirm(step, identity):
                raise AIError('Cancelled. Confirmation was not accepted.')
        self._check(deadline)
        # Permissions/profile validity may change while listening to confirmation.
        if not self.authorize(identity, permission(step)):
            raise AIError('Access changed. This action was not executed.')
        # Revalidate observed targets/path scope after listening to confirmation.
        getattr(self, 'action_guard', lambda step: False)(step)
        self.completed_ids.append(action_id)  # consume BEFORE invocation, even on failure
        if step['delay_seconds']:
            if len(self.scheduled) >= 5:
                return Result('failed', 'The scheduled task queue is full.')
            task_id = self._next_task
            self._next_task += 1
            task = copy.deepcopy(step)
            task['delay_seconds'] = 0
            self.scheduled[task_id] = (time.monotonic() + step['delay_seconds'], task, identity, action_id + ':due')
            return Result('scheduled', f'Scheduled task {task_id} in {step["delay_seconds"]} seconds.')
        name = step['tool']
        if name == 'scheduled_status':
            text = ', '.join(f'{key}: {max(0, int(row[0] - time.monotonic()))} seconds' for key, row in self.scheduled.items())
            return Result('verified', text or 'No scheduled AI tasks.')
        if name == 'cancel_scheduled':
            task_id = step['arguments']['id']
            if task_id == 0:
                self.scheduled.clear()
                return Result('verified', 'All scheduled AI tasks cancelled.')
            removed = self.scheduled.pop(task_id, None)
            return Result('verified' if removed else 'failed', 'Task cancelled.' if removed else 'Task ID was not found.')
        start = time.perf_counter()
        getattr(self, 'state', lambda value: None)('EXECUTING')
        self.timings.setdefault('dispatch_ms', (start - self._turn_start) * 1000)
        self.timings.setdefault('interpretation_to_dispatch_ms',
                                (start - self._turn_start) * 1000 - self.timings.get('interpretation_ms', 0))
        try:
            result = self.execute(step)
            if not isinstance(result, Result):
                return Result('failed', 'Executor did not report an outcome.')
            return result
        except AIPlanError:
            raise
        except AIError as exc:
            return Result('failed', str(exc))
        except Exception:
            return Result('failed', 'Action failed; its final state is unknown. It will not be retried.')
        finally:
            self.timings['execution_ms'] = self.timings.get('execution_ms', 0) + (time.perf_counter() - start) * 1000

    def run(self, text, identity, request_id=None):
        if not self._lock.acquire(blocking=False):
            return Result('failed', 'Aidy is busy with another request.')
        request_id = request_id or uuid.uuid4().hex
        results = []
        self._task_started = time.monotonic()
        self._turn_start = time.perf_counter()
        self.timings = {}
        self._model_calls = 0
        try:
            if self.closed or request_id in self.completed_ids:
                raise AIError('Request is closed or has already been processed.')
            self.completed_ids.append(request_id)
            continuation = self.pending_request_text if self.pending and identity == self.identity else None
            self.awaiting_clarification = False
            if isinstance(text, str) and re.match(r'\s*(?:actually|instead|open|launch|close|create|write|type|read|save|move|rename|delete|send|search|find)\b', text, re.I):
                continuation = None
                self.pending_task = None
            if not self.pending:
                self.clarification_count = 0
                self.pending_adjustments = None
                self.pending_task = None
            self.cancel_event = threading.Event()
            if identity != self.identity:
                self.clear()
                self.identity = identity
            elif time.monotonic() >= self.context_until:
                self.history.clear()
                self.last_app = self.last_step = None
                self.last_window = None
                self.undo_chain = []
            self.context_until = time.monotonic() + 300
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
                raise AIError('The request is empty or too long.')
            self.registry.task_progress = {}
            self.registry.pending_task = copy.deepcopy(self.pending_task)
            self.pending_until = 0
            budget = getattr(self.provider, 'planning_timeout', lambda t, r: self.provider.config.timeout_seconds)(text, self.registry)
            deadline = min(time.monotonic() + budget, self._task_started +
                           getattr(self.provider.config, 'task_timeout_seconds', 90))
            plan = self._interpret(text, deadline)
            self.timings['interpretation_ms'] = (time.perf_counter() - self._turn_start) * 1000
            amount = self._explicit_steps(text)
            if self.pending_adjustments and re.fullmatch(r'\s*(?:[1-9]|10|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s+steps?)?[.!?\s]*', text, re.I):
                # Both turns were interpreted by the API. The locally retained,
                # validated plan prevents a numeric follow-up dropping an action.
                steps = copy.deepcopy(self.pending_adjustments['steps'])
                for s in steps:
                    if s['tool'] in self.pending_adjustments['missing']:
                        s['arguments']['steps'] = amount
                plan = self.registry.plan({'clarification': '', 'steps': steps})
            self.pending_adjustments = None
            self.pending_until = 0
            if plan['clarification']:
                self.pending_request_text = (continuation + '\nUser clarification: ' + text) if continuation else text
                self.history.append({'request': text, 'clarification': plan['clarification']})
                self.clarification_count += 1
                if self.clarification_count >= 3:
                    self.awaiting_clarification = False
                    return Result('failed', plan['clarification'] + ' Say Aidy when ready to try again.')
                self.awaiting_clarification = True
                self.pending_until = time.monotonic() + 20
                key = 'by_how_much' if re.search(r'how (?:many|much)', plan['clarification'], re.I) else ''
                return Result('clarification', plan['clarification'], key)
            self.awaiting_clarification = False
            self.clarification_count = 0
            steps = plan['steps']
            if plan.get('continuation')=='continue_task':
                if not self.registry.pending_task or {g['id'] for g in plan.get('goals',[])} != {g['id'] for g in self.registry.pending_task['goals']}:
                    raise AIError('The previous task is no longer pending. Please state the task again.')
                text=self.registry.pending_task['original_request']+'\nUser clarification: '+text
            if len(steps) > getattr(self.provider.config,'max_actions',20):
                raise AIError('The task exceeds the configured action limit. No action was executed.')
            if plan.get('goals'):
                self.pending_task = None
                if len(plan['goals']) > 1:
                    getattr(self,'task_update',lambda message:None)(f'Planning {len(plan["goals"])} goals: ' +
                        ' → '.join(g['description'][:100] for g in plan['goals']))
            # Missing amounts and exceptions apply to the whole task, including
            # mixed installed-app/UI plans, before any action is authorized.
            adjustments = [s for s in steps if s['tool'] in {'volume_change', 'brightness_change'}]
            relative = (bool(re.fullmatch(r'\s*(?:a (?:little|bit) )?(?:more|less|again|repeat)[.!?\s]*', text, re.I))
                        and self.last_step is not None and all(s['tool'] == self.last_step['tool'] for s in adjustments))
            if adjustments and not relative and not all(self._adjustment_has_amount(text, s['tool']) for s in adjustments):
                self.pending_adjustments = {'steps':copy.deepcopy(steps),'missing':{
                    s['tool'] for s in adjustments if not self._adjustment_has_amount(text,s['tool'])}}
                self.awaiting_clarification = True
                self.pending_until = time.monotonic()+20
                self.history.append({'request':text,'clarification':'By how many steps, from one to ten?','pending_steps':steps})
                return Result('clarification','By how many steps, from one to ten?','by_how_much')
            if (re.search(r'\b(close|quit|shut)\b', text, re.I) and
                re.search(r'\b(everything|all)\b', text, re.I) and
                re.search(r'\b(except|apart from|other than|but keep|but leave|but not)\b', text, re.I) and
                not any(s['tool']=='close_everything' and s['arguments']['except_apps'] or
                        s['tool']=='browser_action' and s['arguments']['operation']=='close_tabs_except' and s['arguments']['text'].strip() for s in steps)):
                raise AIError('The plan did not preserve your exception. Please name the application to keep open.')
            writing_goal = self.document_goal(text)
            observed_contract=bool(plan.get('goals') and (
                any(g['requires_followup'] for g in plan['goals']) or
                len(plan['goals'])>1 and all(s['tool'] in {'open_app','open_url','web_search','timer_start','timer_cancel','set_volume','current_time','timer_status'} for s in steps)))
            closing={'close_app','close_window','close_everything','exit_assistant'}
            if plan.get('goals') and all(s['tool'] in closing for s in steps) and all(
                    c['kind']=='tool_verified' and c['target'] in closing for g in plan['goals'] for c in g['criteria']):
                observed_contract=False  # Preserve the existing grouped confirmation.
            if any(s['tool'] in AGENT_TOOLS or (s['tool'] == 'open_app' and
                   s['arguments']['app'] in getattr(self.registry, 'installed_ids', set())) for s in steps) or (
                   observed_contract and hasattr(self,'start_agent') and getattr(self.provider.config,'agent_enabled',True)):
                scoped = continuation + '\nUser clarification: ' + text if continuation else text
                self.pending_request_text = None
                return self._run_agent(scoped, identity, request_id, plan)
            if writing_goal and getattr(self.provider.config, 'agent_enabled', True) and hasattr(self, 'start_agent'):
                return self._run_agent(continuation + '\nUser clarification: ' + text if continuation else text,
                                       identity, request_id, plan)
            self.pending_request_text = None
            undoing = any(s['tool'] == 'undo' for s in steps)
            if undoing:
                if len(steps) != 1 or not self.undo_chain:
                    raise AIError('Nothing reversible is available. Ask to undo one recent action or chain.')
                originals = self.undo_chain[-1:] if steps[0]['arguments']['scope'] == 'last' else self.undo_chain
                steps = [self.inverse(s) for s in reversed(originals)]
                if len(steps) > 6 or any(s is None for s in steps):
                    raise AIError('This action chain cannot be safely undone.')
            new_chain = []
            deadline = time.monotonic() + 90  # Includes local confirmations; no unbounded agent loop.
            batch = getattr(self, 'confirm_batch', None)
            grouped = bool(batch and len(steps) > 1 and all(
                s['tool'] in {'close_app', 'close_window', 'close_everything', 'exit_assistant'} and
                s['arguments'].get('app') != '$last' for s in steps))
            if grouped:
                steps = [self._resolve(s) for s in steps]
                if not all(self.authorize(identity, permission(s)) for s in steps):
                    raise AIError('Access denied for this action.')
                if not batch(steps, identity):
                    raise AIError('Cancelled.')
            for index, raw in enumerate(steps):
                step = self._resolve(raw)
                result = self._dispatch(step, identity, f'{request_id}:{index}', deadline, confirmed=grouped)
                results.append({'step': step, 'status': result.status, 'message': result.message})
                if not result.ok:
                    if (result.data or {}).get('independent_control_unavailable') and self.independent_steps(steps[index+1:]):
                        continue
                    break
                if result.status != 'scheduled':
                    self._remember_target(result)
                    if step['tool']=='open_app' and not (result.data or {}).get('window') and step['arguments']['app']!=self.last_app:
                        self.last_window=None
                    self.last_step = copy.deepcopy(step)
                    if step['tool'] in ('open_app', 'close_app') and step['arguments']['app'] != '$active':
                        self.last_app = step['arguments']['app']
                    new_chain.append(step)
                    if undoing:
                        self.undo_chain.pop()
                    else:
                        self.undo_chain = list(new_chain)
            if new_chain and not undoing:
                self.undo_chain = new_chain
            self.history.append({'request': text, 'results': results})
            succeeded = sum(r['status'] in {'verified', 'dispatched', 'scheduled'} for r in results)
            failed = succeeded != len(steps)
            message = ' '.join(r['message'] for r in results)
            if failed:
                errors=' '.join(r['message'] for r in results if r['status'] not in {'verified','dispatched','scheduled'})
                message = f'{succeeded} of {len(steps)} actions sent. ' + (errors or results[-1]['message'])
            else:
                self.pending_until = time.monotonic() + 8
                # Queries retain their actual answers; action chains get one
                # brief acknowledgement instead of concatenated executor logs.
                informational = {'current_time', 'timer_status', 'scheduled_status', 'voice_access', 'study_mode'}
                if len(results) > 1 and not any(r['step']['tool'] in informational for r in results):
                    message = f'{len(results)} actions completed.' if all(r['status'] == 'verified' for r in results) else f'{len(results)} action requests sent.'
            return Result('failed' if failed else ('dispatched' if any(r['status'] == 'dispatched' for r in results) else results[-1]['status']), message,
                          result.voice_key if len(results) == 1 else '')
        except AIClarification as exc:
            self.awaiting_clarification = True
            self.pending_until = time.monotonic() + 20
            self.history.append({'request': text, 'clarification': str(exc)})
            self.pending_request_text = text
            return Result('clarification', str(exc))
        except AIError as exc:
            if results:
                self.history.append({'request': text, 'results': results, 'stopped': str(exc)})
            prefix = f'{sum(r["status"] in {"verified", "dispatched", "scheduled"} for r in results)} actions sent. ' if results else ''
            status = 'cancelled' if self.cancel_event.is_set() and not str(exc).startswith('Request timed out') else 'failed'
            return Result(status, prefix + str(exc))
        except Exception:
            return Result('failed', f'Something went wrong. Stopped after {len(results)} actions.')
        finally:
            self.registry.agent_mode = False
            if not self.awaiting_clarification:
                self.pending_task = self.pending_request_text = None
                self.registry.pending_task = None
            self.registry.plan_feedback = ''
            self.cancel_event.set()  # A late HTTP response cannot outlive this turn's authority.
            self.timings['total_ms'] = (time.perf_counter() - self._turn_start) * 1000
            self._lock.release()

    def _run_agent(self, text, identity, request_id, plan):
        """Keep every semantic goal while refreshing action/UI plans within it."""
        config = self.provider.config
        if not getattr(config, 'agent_enabled', True) or not hasattr(self, 'start_agent'):
            raise AIError('Desktop agent is unavailable or disabled. No general action was executed.')
        self.task_deadline = self._task_started + config.task_timeout_seconds
        self.registry.agent_mode = True
        self._agent_dispatch_attempts=0
        self.start_agent(text)
        groups = goal_plans(plan)
        if not groups:
            return self._run_agent_goal(text,identity,request_id,plan)
        outcomes = []
        states = [dict(id=g['id'],description=g['description'],criteria=g['criteria'],status='pending') for g,_ in groups]
        for index,(goal,goal_plan) in enumerate(groups):
            self._check(self.task_deadline)
            states[index]['status'] = 'executing'
            self.registry.task_progress = dict(original_request=text,current_goal=goal['id'],current_goal_spec=goal,goals=copy.deepcopy(states))
            getattr(self,'task_update',lambda message:None)(f'Goal {index+1}/{len(groups)}: {goal["description"][:180]}')
            try:
                result = self._run_agent_goal(goal['description'],identity,request_id+':'+goal['id'],goal_plan,goal)
            except AIClarification as exc:
                self.awaiting_clarification=True
                self.pending_until=time.monotonic()+20
                result=Result('clarification',str(exc))
            except AIError as exc:
                result=Result('cancelled' if self.cancel_event.is_set() and not str(exc).startswith('Request timed out') else 'failed',str(exc))
            states[index]['status'] = result.status
            outcomes.append(dict(id=goal['id'],goal=goal['description'],criteria=goal['criteria'],
                                 status=result.status,message=result.message,verified_state=result.data or {}))
            self.registry.task_progress['goals'] = copy.deepcopy(states)
            if result.status == 'clarification':
                self.pending_task = dict(original_request=text,goals=copy.deepcopy(states[index:]),
                                         clarification=result.message,
                    completed_actions=[dict(tool=r['step']['tool'],arguments={k:v for k,v in r['step']['arguments'].items() if k!='text'},
                                            status=r['status'],verified_state=r.get('verified_state',{}))
                        for turn in list(self.history) if turn.get('request')==goal['description']
                        for r in turn.get('results',[]) if r['status'] in {'verified','dispatched'}])
                return result
            if not result.ok:
                later=groups[index+1:]
                if (result.data or {}).get('independent_control_unavailable') and later and all(
                        not g['requires_followup'] and self.independent_steps(p['steps'])
                        and not any(c.get('source_goal') for c in g['criteria']) for g,p in later):
                    continue
                count=sum(o['status'] in {'verified','dispatched','scheduled'} for o in outcomes)
                return Result(result.status,f'{count} of {len(groups)} goals completed. {goal["description"][:100]}: {result.message}')
            if result.status=='dispatched' and any(r['status']=='dispatched' and r['step']['tool'] in {
                    'ui_action','key_input','mouse_input','browser_action','document_write'}
                    for turn in self.history if turn.get('request')==goal['description'] for r in turn.get('results',[])):
                return Result('dispatched',f'{index} of {len(groups)} goals verified. The last input is unverified; remaining goals stopped.')
        self.history.append(dict(request=text,task_goals=copy.deepcopy(outcomes)))
        self.pending_until = time.monotonic()+8
        if len(outcomes)==1:
            return result
        failed=[o for o in outcomes if o['status'] not in {'verified','dispatched','scheduled'}]
        if failed:
            return Result('failed',f'{len(outcomes)-len(failed)} of {len(groups)} goals completed. '+
                          ' '.join(o['message'] for o in failed),data=dict(goals=outcomes))
        status='dispatched' if any(o['status']!='verified' for o in outcomes) else 'verified'
        app_actions = {'open_app','close_app','close_window','close_everything'}
        brief = all(g['required_tools'] and set(g['required_tools']) <= app_actions for g,_ in groups)
        def query(step):
            name,args=step['tool'],step['arguments']
            return name in {'notepad_action','file_action','ui_action'} and args.get('operation')=='read'
        answers=[o['message'] for o,(_,p) in zip(outcomes,groups) if o['status']=='verified'
                 and any(query(s) for s in p['steps']) and all(query(s) or s['tool']=='observe_desktop' for s in p['steps'])]
        message=f'{len(outcomes)} of {len(groups)} goals '+('completed.' if status=='verified' else 'requested; some final states are unverified.')
        if answers:
            message+='\n'+'\n'.join(answers)
        return Result(status,message,
                      data=dict(goals=outcomes), spoken_message=('Done.' if status=='verified' else 'Requests sent.') if brief else '')

    def _run_agent_goal(self, text, identity, request_id, plan, goal=None):
        config = self.provider.config
        actions, calls, retries = [], 1, 0
        remaining = copy.deepcopy(plan['steps'][1:]) if goal else []
        uncertain = False
        consumed_mutations = set()
        selected_window = ''
        dispatch_attempts = 0
        def missing_outcomes():
            if not goal:return set()
            return {c['kind']+':'+c['target'] for c in goal['criteria'] if not criterion_satisfied(c,actions)}
        while True:
            self._check(self.task_deadline)
            if plan['clarification']:
                self.pending_request_text = text
                self.awaiting_clarification = True
                self.pending_until = time.monotonic() + 20
                self.history.append({'request': text, 'clarification': plan['clarification']})
                return Result('clarification', plan['clarification'])
            # Refresh UI references, while retaining independent bound actions.
            step = self._resolve(plan['steps'][0])
            if step['tool'] == 'task_complete':
                if not actions:
                    raise AIError('No task outcome has been observed yet.')
                if remaining:
                    plan = dict(clarification='',steps=[remaining.pop(0)])
                    continue
                if missing_outcomes():
                    self.registry.plan_feedback='The current goal is unfinished. Still required: '+', '.join(sorted(missing_outcomes()))+'. Do not repeat completed actions.'
                    plan=self._interpret_once(text,min(self.task_deadline,time.monotonic()+
                        getattr(self.provider,'planning_timeout',lambda t,r:config.timeout_seconds)(text,self.registry)))
                    continue
                if self.document_goal(text) and not any(r['status'] == 'verified' and (
                    r['step']['tool'] == 'document_write' or r['step']['tool'] == 'ui_action' and
                    r['step']['arguments']['operation'] == 'set_text') for r in actions):
                    self.registry.plan_feedback = 'The writing part of the user task is still unfinished. Generate and insert the document text; opening/focusing an app is not completion.'
                    plan = self._interpret_once(text, min(self.task_deadline, time.monotonic() +
                        getattr(self.provider, 'planning_timeout', lambda t, r: config.timeout_seconds)(text, self.registry)))
                    continue
                unverified = any(r['status'] != 'verified' for r in actions)
                if unverified:
                    return Result('dispatched', 'Action sent; the final result is unverified.')
                self.pending_until = time.monotonic() + 8
                queries = [r for r in actions if r['step']['tool'] == 'file_action' and r['step']['arguments']['operation'] == 'read'
                           or r['step']['tool'] == 'notepad_action' and r['step']['arguments']['operation'] == 'read'
                           or r['step']['tool'] == 'ui_action' and r['step']['arguments']['operation'] == 'read'
                           or r['step']['tool'] == 'browser_action' and r['step']['arguments']['operation'] in {'tabs', 'read_page'}]
                mutations = [r for r in actions if r['step']['tool'] != 'observe_desktop']
                if not mutations:
                    return Result('verified', 'Desktop inspected.')
                return Result('verified', queries[-1]['message'] if queries else
                              'Saved.' if mutations[-1]['step']['tool'] == 'save_document' else
                              mutations[-1]['message'] if mutations[-1]['step']['tool'] == 'document_write' else 'Done.')
            if self.timings.get('actions',0) >= config.max_actions or self._agent_dispatch_attempts >= config.max_actions:
                raise AIError('Task action limit reached. Remaining steps stopped.')
            if step['delay_seconds']:
                raise AIError('General desktop tasks cannot be scheduled.')
            # Unbound legacy mouse/window-switch input remains unavailable here.
            # Fixed system operations retain their normal role/confirmation gates.
            allowed = set(TOOLS)-{'undo','repeat','mouse_action','window_switch'}
            if step['tool'] not in allowed:
                raise AIError('This action cannot be mixed into an observed desktop task.')
            read_only = (step['tool'] == 'observe_desktop' or
                         step['tool'] in {'ui_action', 'file_action', 'notepad_action'} and step['arguments']['operation'] == 'read' or
                         step['tool'] == 'browser_action' and step['arguments']['operation'] in {'tabs', 'read_page'})
            if uncertain and not read_only:
                raise AIError('The previous action has an unverified outcome. Remaining input stopped; check the application before retrying.')
            if step['arguments'].get('window') and step['arguments']['window'] != '$active':
                selected_window = step['arguments']['window']
            previous = copy.deepcopy(self.registry.desktop_state)
            if not read_only:
                import json
                signature = json.dumps([step['tool'], step['arguments'],
                    previous.get('interface', {}).get('view') if step['tool'] in {'ui_action', 'key_input', 'mouse_input', 'browser_action'} else None], sort_keys=True)
                if signature in consumed_mutations:
                    raise AIError('The same action was already attempted in this state. It will not be repeated.')
                consumed_mutations.add(signature)
            try:
                if not self.authorize(identity, permission(step)):
                    raise AIError('Access denied for this action.')
                if goal and step['tool'] not in goal['required_tools']:
                    prospective=dict(step=step,status='verified')
                    later=[g for g in self.registry.task_progress.get('goals',[]) if g['status']=='pending']
                    if any(criterion_satisfied(dict(kind=c['kind'],target=c['target']),[prospective]) for g in later for c in g['criteria']):
                        raise AIPlanError('This action belongs to a later goal. Finish only the current goal: '+goal['description'][:160])
                step = getattr(self, 'prepare_agent_step', lambda s: s)(step)
                if step['arguments'].get('window'):
                    selected_window = step['arguments']['window']
            except AIPlanError as exc:
                if not read_only:
                    consumed_mutations.discard(signature)  # preparation sent no input
                self.registry.plan_feedback = str(exc)
                plan = self._interpret_once(text, min(self.task_deadline, time.monotonic() +
                    getattr(self.provider, 'planning_timeout', lambda t, r: config.timeout_seconds)(text, self.registry)))
                continue
            action_id = f'{request_id}:agent:{dispatch_attempts}'
            dispatch_attempts += 1
            self._agent_dispatch_attempts += 1
            try:
                result = self._dispatch(step, identity, action_id, self.task_deadline)
            except AIPlanError as exc:
                if not read_only:
                    consumed_mutations.discard(signature)  # executor explicitly rejected before action
                self.refresh_agent(selected_window)
                self.registry.plan_feedback = str(exc)
                plan = self._interpret_once(text, min(self.task_deadline, time.monotonic() +
                    getattr(self.provider, 'planning_timeout', lambda t, r: config.timeout_seconds)(text, self.registry)))
                continue
            row = dict(step=step, status=result.status, message=result.message)
            actions.append(row)
            self.timings['actions'] = self.timings.get('actions',0)+1
            self.history.append({'request': text, 'agent': True, 'results': [row]})
            if not result.ok:
                return Result(result.status, result.message, data=result.data)
            self._remember_target(result)
            if result.data:
                row['verified_state'] = copy.deepcopy(result.data)
                self.registry.desktop_state['verified_outcome'] = copy.deepcopy(result.data)
            if step['tool']=='open_app':
                target=step['arguments']['app']
                if not (result.data or {}).get('window') and target!=self.last_app:
                    self.last_window=None
                self.last_app=target
                self.last_step=copy.deepcopy(step)
            atomic=set(TOOLS)-{'ui_action','key_input','mouse_input','browser_action','observe_desktop'}
            if goal and not remaining and not missing_outcomes() and all(c['kind']!='tool_verified' or c['target'] in atomic for c in goal['criteria']):
                self._check(self.task_deadline)
                self.pending_until=time.monotonic()+8
                self.last_step=copy.deepcopy(step)
                return Result('dispatched' if any(r['status']!='verified' for r in actions) else 'verified',result.message,data=result.data)
            if (step['tool'] == 'document_write' and result.status == 'verified'
                    and step['arguments'].get('finish_task') is True and len(plan['steps']) == 1
                    and not remaining and not missing_outcomes() and (not goal or not goal['requires_followup'])):
                # The planner marks the scope, but only the executor's completed
                # text readback permits success. Avoid a redundant cloud request
                # turning an already verified document into a rate-limit failure.
                self._check(self.task_deadline)
                self.pending_until = time.monotonic() + 8
                return Result('verified', result.message, data=result.data)
            if step['tool'] == 'open_app':
                self.last_app = step['arguments']['app']
                self.last_step = copy.deepcopy(step)
            # Independent bound actions are retained from the model's complete
            # plan. UI references are always replanned after their state changes.
            stale_tools={'ui_action','key_input','mouse_input','browser_action','save_document'}
            semantic_browser = (step['tool']=='browser_action' and result.status=='verified'
                                and remaining and remaining[0]['tool']=='browser_action')
            if goal and remaining and (remaining[0]['tool'] not in stale_tools or semantic_browser):
                plan=dict(clarification='',steps=[remaining.pop(0)])
                continue
            if goal and not remaining and not goal['requires_followup'] and not missing_outcomes():
                self.pending_until=time.monotonic()+8
                return Result('dispatched' if any(r['status']!='verified' for r in actions) else 'verified',
                              result.message,data=result.data)
            # New dialogs/page transitions invalidate every previous control view.
            try:
                self.refresh_agent(selected_window)
                retries = 0
            except AIError as exc:
                if 'dialog' in str(exc).lower():
                    raise AIError('An unexpected dialog appeared. Remaining actions stopped; resolve the dialog first.') from None
                if retries >= config.max_observation_retries:
                    raise AIError('The target changed or a dialog appeared. Remaining actions stopped; inspect the target before continuing.')
                retries += 1
                selected_window = ''
                self.refresh_agent('')
            if result.status == 'dispatched' and step['tool'] in {'key_input', 'mouse_input', 'browser_action'} and previous != self.registry.desktop_state:
                row['status'] = 'verified'
                row['message'] = 'Interface changed; inspect current state for the requested outcome.'
            uncertain = uncertain or row['status'] == 'dispatched' and (not goal or step['tool'] not in {'open_app','web_search'})
            self._check(self.task_deadline)
            if self._model_calls >= config.max_model_calls:
                raise AIError('Task model-call limit reached. Remaining steps stopped.')
            started = time.perf_counter()
            budget = getattr(self.provider, 'planning_timeout', lambda t, r: config.timeout_seconds)(text, self.registry)
            plan = self._interpret(text, min(self.task_deadline, time.monotonic() + budget))
            self.timings['interpretation_ms'] += (time.perf_counter() - started) * 1000
            calls += 1
            self.timings['model_calls'] = self._model_calls

    @staticmethod
    def document_goal(text):
        return writing_hints(text).writing or bool(re.match(r'\s*(?:please\s+)?(?:compose|draft|(?:an?\s+)?essay|story|letter)\b', text, re.I))

    def _remember_target(self, result):
        if result.data:
            self.last_window = result.data.get('window', self.last_window)
            self.last_app = result.data.get('app', self.last_app)

    @staticmethod
    def _explicit_steps(text):
        words = 'one two three four five six seven eight nine ten'.split()
        match = re.search(r'\b(10|[1-9]|' + '|'.join(words) + r')\b', text, re.I)
        if match:
            token = match[0].lower()
            return int(token) if token.isdigit() else words.index(token) + 1
        if re.search(r'\b(a little|a bit|slightly)\b', text, re.I):
            return 1
        return None

    @classmethod
    def _adjustment_has_amount(cls, text, tool):
        # A timer duration or a browser query number is not an adjustment amount.
        if re.fullmatch(r'\s*(?:[1-9]|10|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s+steps?)?[.!?\s]*', text, re.I):
            return True
        terms = r'volume|louder|quieter|sound' if tool == 'volume_change' else r'brightness|brighter|dimmer'
        clauses = re.split(r'\b(?:and|then)\b|[,;]', text, flags=re.I)
        return any(re.search(r'\b(?:' + terms + r')\b', clause, re.I) and cls._explicit_steps(clause) is not None
                   for clause in clauses)

    def tick(self):
        if self.closed or not self._lock.acquire(blocking=False):
            return []
        try:
            return self._tick_locked()
        finally:
            self._lock.release()

    def _tick_locked(self):
        results = []
        # Cancellation of a previous utterance must not invalidate unrelated
        # previously authorized schedules; cancel_scheduled is explicit.
        self.cancel_event = threading.Event()
        for task_id, (due, step, identity, action_id) in list(self.scheduled.items()):
            if due > time.monotonic():
                continue
            del self.scheduled[task_id]
            try:
                self._turn_start = time.perf_counter()
                result = self._dispatch(step, identity, action_id, time.monotonic() + 30, confirmed=True)
            except AIError as exc:
                result = Result('failed', str(exc))
            results.append(result)
        return results
