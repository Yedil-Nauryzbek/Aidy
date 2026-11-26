"""Data-only semantic task plans. Goals come from the model, never keywords."""
import copy
import json
import ntpath
import re


def task_schema(registry, names, future=()):
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
                    requires_followup=dict(type='boolean'),required_tools=dict(type='array',items=dict(type='string',enum=sorted(set(names)|set(future)))),
                    criteria=dict(type='array',items=dict(type='object',additionalProperties=False,
                        required=['kind','target','source_goal'],properties=dict(kind=dict(type='string',enum=['app_open','document_written','file_saved','file_read','tool_verified']),target=dict(type='string'),source_goal=dict(type='string',enum=['']+['g'+str(i) for i in range(1,9)])))),
                    steps=action_list)))))


def salvage_document_arguments(encoded):
    """Typed scalar fields of a document_write arguments_json the model truncated.

    The model often cuts the string where the long or empty "text" value starts.
    Only fixed scalar fields are recovered; text is regenerated from the goal and
    the whole step is still validated against the exact tool schema.
    """
    application = re.search(r'"?application"?\s*:\s*"(notepad|word|current)"', encoded)
    if not application:
        raise ValueError('Invalid document_write arguments.')
    flag = lambda name: (lambda m: m is not None and m[1] == 'true')(re.search(r'"?' + name + r'"?\s*:\s*(true|false)', encoded))
    count = re.search(r'"?word_count"?\s*:\s*(\d{1,4})', encoded)
    return dict(application=application[1], new_document=flag('new_document'), text='',
                word_count=int(count[1]) if count else 0, finish_task=flag('finish_task'))


def repair_arguments(encoded):
    """An arguments_json object the model truncated at its end, e.g. '{"window":'.

    Gemini routinely drops an escaped empty string and the closing brace. Only the
    missing tail ("" after a final colon, closing braces) is added; the result is
    still validated against the exact tool schema before any effect.
    """
    text = encoded.strip()
    if text.endswith(':'):
        text += '""'
    text += '}' * max(0, text.count('{') - text.count('}'))
    def unique(items):
        if len({key for key, _ in items}) != len(items):
            raise ValueError('Duplicate action argument.')
        return dict(items)
    value = json.loads(text, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(value) is not dict:
        raise ValueError('Action arguments must decode to an object.')
    return value


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
                try:
                    action['arguments']=json.loads(encoded,object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except ValueError:
                    if action.get('tool') == 'document_write':
                        action['arguments']=salvage_document_arguments(encoded)
                    elif action.get('tool') == 'observe_desktop':
                        # Read-only: an empty window just lists windows. Effect tools
                        # are never repaired (an empty text could clear a field).
                        action['arguments']=repair_arguments(encoded)
                    else:
                        from .ai_tools import DATA_READS
                        repaired=repair_arguments(encoded)  # reads only: no effect to get wrong
                        if (action.get('tool'),repaired.get('operation')) not in DATA_READS:
                            raise
                        action['arguments']=repaired
                if type(action['arguments']) is not dict:
                    raise ValueError('Action arguments must decode to an object.')
                if action.get('tool') == 'document_write':
                    # Generated drafts omit text: an escaped empty string inside
                    # arguments_json was routinely truncated by the model.
                    action['arguments'].setdefault('text', '')
            decoded.append(action)
        criteria=copy.deepcopy(goal['criteria'])
        for criterion in criteria if type(criteria) is list else []:
            # Only a saved document binds its source goal; the model often links
            # ordinary dependent goals too, which used to reject the whole plan.
            if type(criterion) is dict and criterion.get('kind')!='file_saved' and criterion.get('source_goal'):
                criterion['source_goal']=''
        from .ai_tools import UI_EFFECT_TOOLS
        # A goal that still needs observed UI input can never finish with its
        # placeholder/prerequisite step alone.
        followup=goal['requires_followup'] is True or (type(goal['required_tools']) is list and (
            any(t in UI_EFFECT_TOOLS for t in goal['required_tools']) or
            any(a.get('tool')=='observe_desktop' for a in decoded) and bool(set(goal['required_tools'])-{'observe_desktop'})))
        metadata.append(dict(id=goal['id'],description=goal['description'],requires_followup=followup if type(goal['requires_followup']) is bool else goal['requires_followup'],
                             required_tools=copy.deepcopy(goal['required_tools']),
                             criteria=criteria,
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


def _host(url):
    from urllib.parse import urlsplit
    host = (urlsplit(str(url)).hostname or '').casefold()
    return host[4:] if host.startswith('www.') else host


def criterion_satisfied(criterion, actions, website_hosts=None, folder_listing=None):
    """website_hosts maps a website app ID to its host: opening that site is opening the app.
    folder_listing (completion of the current goal only): the model writes file_read:<folder> for
    "what's in Downloads" and "what does the readme in Downloads say". A read of a file inside the
    folder then counts; a list/search of the folder counts only when folder_listing is True
    (the goal needs no follow-up). None keeps the exact-path rule."""
    kind,target=criterion['kind'],criterion['target']
    site=(website_hosts or {}).get(target)
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
        if kind=='app_open' and site and name=='open_url' and _host(args.get('url',''))==_host('https://'+site):
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
        if kind=='file_read' and folder_listing is not None:
            from .ai_files import known_folders
            folders=known_folders()  # explorer_action also takes "downloads", "desktop"...
            folder=path(folders.get(target.strip().casefold(),target))
            used=path(folders.get(str(args.get('path','')).strip().casefold(),str(args.get('path',''))))
            if name=='file_action' and args.get('operation')=='read' and used.startswith(folder+'\\'):
                return True
            if folder_listing and name in {'file_action','explorer_action'} and args.get('operation') in {'list','search'} and used==folder:
                return True
    return False
