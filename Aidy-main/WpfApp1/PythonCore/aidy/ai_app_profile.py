"""A smaller AI contract for complete, explicit, independent app requests.

The grammar only selects/validates a contract. Google still chooses every
action and target. No step is ever executed from the grammar matches.
Contextual, ambiguous and dependent tasks keep the general planner.
"""
import re

from .ai_tools import AIPlanError


def decode_chain(payload):
    if type(payload) is not dict or set(payload) != {'clarification', 'steps'} or type(payload['steps']) is not list:
        raise AIPlanError('Return the complete application action array.')
    steps = []
    for row in payload['steps']:
        if type(row) is not dict or set(row) != {'tool', 'app'}:
            raise AIPlanError('Each application action needs only tool and app.')
        steps.append(dict(tool=row['tool'], arguments={'app': row['app']}, delay_seconds=0))
    return dict(clarification=payload['clarification'], steps=steps)


def app_chain(text, registry):
    if registry.agent_mode or registry.pending_task or registry.plan_feedback:
        return None
    clauses = re.split(r'\s+(?:and then|and|then)\s+', text.strip().rstrip('.!?'), flags=re.I)
    if not 1 <= len(clauses) <= 6:
        return None
    names = {}
    configured = getattr(registry, 'app_names', {})
    for app in registry.app_ids - {'aidy', 'gpt', 'chatgpt', 'chatgpt_app'}:
        if app.startswith('installed_'):
            continue
        for name in [app.replace('_', ' '), *configured.get(app, [])]:
            name = ' '.join(name.casefold().split())
            if name and not re.search(r'\b(open|launch|start|run|close|quit|exit|and|then)\b', name):
                names.setdefault(name, set()).add(app)
    # Generic names always follow the user's selected browser/music provider.
    names['browser'] = {'browser'}
    names['music'] = {'music'}
    if 'chatgpt_app' in registry.app_ids:
        for name in ('chatgpt', 'chat gpt', 'chatgpt app', 'chat gpt app', 'gpt app',
                     'chatgpt desktop app', 'g p t app', 'gpt application', 'chatgpt application', 'chat with the app'):
            names[name] = {'chatgpt_app'}
    expected = []
    previous_operation = None
    for clause in clauses:
        match = re.fullmatch(r'(?:please )?(?:(open(?: up)?|launch|start|run|close|quit|exit) )?'
                            r'(?:the |my |a |an )?([\w ]+?)(?: please)?', clause.strip(), re.I)
        if not match or not match[1] and previous_operation is None:
            return None
        targets = names.get(' '.join(match[2].casefold().split()), set())
        if len(targets) != 1:
            return None
        operation = ('close_app' if match[1].casefold() in {'close', 'quit', 'exit'} else 'open_app') if match[1] else previous_operation
        previous_operation = operation
        app = next(iter(targets))
        expected.append((operation, registry.aliases.get(app, app)))
    return expected if len(expected)>1 or expected[0][1]=='chatgpt_app' else None


def validate_chain(plan, expected, registry):
    if plan['clarification']:
        return plan
    actual = [(s['tool'], registry.aliases.get(s['arguments'].get('app'), s['arguments'].get('app')))
              for s in plan['steps']]
    if actual != expected or any(s['delay_seconds'] for s in plan['steps']):
        registry.rejected_plan = plan
        raise AIPlanError('Preserve EVERY explicit application action, target and order. Do not omit, reverse or add actions.')
    if plan.get('goals'):
        return registry.plan(plan)
    # Each AI-selected atomic action is its own complete semantic goal. These
    # targets/actions came from the model and passed the closed local schema.
    plan.update(continuation='new_task', goals=[dict(
        id='g'+str(index+1), description=step['tool'].replace('_', ' ')+' '+step['arguments']['app'],
        requires_followup=False, required_tools=[step['tool']],
        criteria=[dict(kind='app_open' if step['tool']=='open_app' else 'tool_verified',
                       target=step['arguments']['app'] if step['tool']=='open_app' else step['tool'], source_goal='')],
        step_start=index, step_count=1) for index, step in enumerate(plan['steps'])])
    return registry.plan(plan)
