"""Data-only semantic task plans. Goals come from the model, never keywords."""
import copy
import json
import ntpath


def task_schema(registry, names):
    # Repeating the merged argument enums inside goals/steps exceeds Gemini's
    # constrained-decoder complexity limit. Serialize argument objects as JSON
    # data, then strictly decode/validate each exact tool schema before dispatch.
    action_list=dict(type='array',items=dict(type='object',additionalProperties=False,
        required=['tool','arguments_json','delay_seconds'],properties=dict(tool=dict(type='string',enum=sorted(names)),
        arguments_json=dict(type='string',description='Serialized JSON object of exact tool arguments. Never code.'),
        delay_seconds=dict(type='integer'))))
    return dict(type='object', additionalProperties=False,
        required=['continuation', 'clarification', 'goals'], properties=dict(
            continuation=dict(type='string',enum=['new_task','continue_task']),
            clarification=dict(type='string'),
            goals=dict(type='array', items=dict(type='object', additionalProperties=False,
                required=['id','description','requires_followup','required_tools','criteria','steps'], properties=dict(
                    id=dict(type='string',enum=['g'+str(i) for i in range(1,9)]),
                    description=dict(type='string'),
                    requires_followup=dict(type='boolean'),required_tools=dict(type='array',items=dict(type='string',enum=sorted(names))),
                    criteria=dict(type='array',items=dict(type='object',additionalProperties=False,
                        required=['kind','target','source_goal'],properties=dict(kind=dict(type='string',enum=['app_open','document_written','file_saved','file_read','tool_verified']),target=dict(type='string'),source_goal=dict(type='string')))),
                    steps=action_list)))))


def unpack_task(args, allowed):
    if type(args) is not dict or set(args) != {'continuation','clarification','goals'}:
        raise ValueError('Return the complete task plan as an object.')
    if args['continuation'] not in {'new_task','continue_task'} or type(args['clarification']) is not str:
        raise ValueError('Invalid task continuation or clarification.')
    goals = args['goals']
    if type(goals) is not list or len(goals)>8 or bool(args['clarification'].strip()) == bool(goals):
        raise ValueError('Return all task goals, or one clarification without goals.')
    steps, metadata = [], []
    for goal in goals:
        if type(goal) is not dict or set(goal) != {'id','description','requires_followup','required_tools','criteria','steps'}:
            raise ValueError('Each goal needs an id, description, requires_followup and steps.')
        actions = goal['steps']
        if type(actions) is not list or not 1<=len(actions)<=6 or any(type(s) is not dict or s.get('tool') not in allowed for s in actions):
            raise ValueError('Every goal needs a nonempty list of available typed actions.')
        decoded=[]
        for action in actions:
            action=copy.deepcopy(action)
            if 'arguments_json' in action:
                if 'arguments' in action:
                    raise ValueError('Conflicting action argument representations.')
                def pairs(items):
                    result={}
                    for key,value in items:
                        if key in result:
                            raise ValueError('Duplicate action argument.')
                        result[key]=value
                    return result
                encoded=action.pop('arguments_json')
                if type(encoded) is not str or len(encoded)>24000:
                    raise ValueError('Invalid serialized arguments.')
                action['arguments']=json.loads(encoded,object_pairs_hook=pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if type(action['arguments']) is not dict:
                    raise ValueError('Action arguments must decode to an object.')
            decoded.append(action)
        metadata.append(dict(id=goal['id'],description=goal['description'],requires_followup=goal['requires_followup'],
                             required_tools=copy.deepcopy(goal['required_tools']),
                             criteria=copy.deepcopy(goal['criteria']),
                             step_start=len(steps),step_count=len(actions)))
        steps.extend(decoded)
    plan = dict(clarification=args['clarification'],steps=steps,goals=metadata,continuation=args['continuation'])
    validate_goals(plan)
    return plan


def validate_goals(plan):
    goals = plan.get('goals', [])
    if type(goals) is not list or len(goals)>8 or plan.get('continuation') not in {'new_task','continue_task'}:
        raise ValueError('Invalid task goals.')
    if bool(goals) != bool(plan['steps']):
        raise ValueError('All actions must belong to a task goal.')
    seen, position = set(), 0
    for goal in goals:
        if type(goal) is not dict or set(goal) != {'id','description','requires_followup','required_tools','criteria','step_start','step_count'}:
            raise ValueError('Invalid goal structure.')
        if (type(goal['id']) is not str or goal['id'] not in {'g'+str(i) for i in range(1,9)} or goal['id'] in seen
            or type(goal['description']) is not str or not goal['description'].strip() or len(goal['description'])>1000
            or any(ord(c)<32 and c not in '\r\n\t' for c in goal['description'])
            or type(goal['requires_followup']) is not bool
            or type(goal['required_tools']) is not list or len(goal['required_tools'])>8
            or any(type(t) is not str for t in goal['required_tools'])
            or len(set(goal['required_tools']))!=len(goal['required_tools'])
            or type(goal['step_start']) is not int or goal['step_start'] != position
            or type(goal['step_count']) is not int or not 1<=goal['step_count']<=6):
            raise ValueError('Goals must uniquely cover every action in order.')
        if type(goal['criteria']) is not list or not 1<=len(goal['criteria'])<=8:
            raise ValueError('Define observable completion criteria for every goal.')
        for criterion in goal['criteria']:
            if (type(criterion) is not dict or not {'kind','target'}<=set(criterion) or set(criterion)-{'kind','target','source_goal'}
                or criterion['kind'] not in {'app_open','document_written','file_saved','file_read','tool_verified'}
                or type(criterion['target']) is not str or not 1<=len(criterion['target'])<=500):
                raise ValueError('Use supported typed completion criteria.')
            source=criterion.get('source_goal','')
            if type(source) is not str or source and (criterion['kind']!='file_saved' or source not in seen|{goal['id']}):
                raise ValueError('A document save must reference its current or earlier document goal.')
        seen.add(goal['id'])
        position += goal['step_count']
    if position != len(plan['steps']):
        raise ValueError('A task action was omitted from its goal.')


def goal_plans(plan):
    return [(goal,dict(clarification='',steps=copy.deepcopy(plan['steps'][goal['step_start']:goal['step_start']+goal['step_count']])))
            for goal in plan.get('goals',[])]


def criterion_satisfied(criterion, actions):
    kind,target=criterion['kind'],criterion['target']
    def path(value):
        # Model escaping is presentation data, never an additional path grant.
        # Executors independently enforce the original user's canonical scope.
        return ntpath.normcase(ntpath.normpath(value.strip(' \\"').replace('\\\\','\\')))
    for row in actions:
        if row['status'] not in {'verified','dispatched','scheduled'}:
            continue
        step=row['step'];name,args=step['tool'],step['arguments']
        if kind=='tool_verified' and name==target:
            return True
        if kind=='app_open' and name=='open_app' and args.get('app')==target:
            return True
        if row['status']!='verified':
            continue
        if kind=='document_written' and name=='document_write' and (target=='current' or args.get('application')==target or row.get('verified_state',{}).get('app')==target):
            return True
        if kind=='file_saved' and (name=='save_document' or name=='file_action' and args.get('operation')=='write') and path(args.get('path',''))==path(target):
            if not criterion.get('source_goal') or row.get('verified_state',{}).get('source_goal')==criterion['source_goal']:
                return True
        if kind=='file_read' and name=='file_action' and args.get('operation')=='read' and path(args.get('path',''))==path(target):
            return True
    return False
