"""Selected nonsecret operational resets over canonical policy transactions.

Host resets install explicit known literals; queue resets restore absence.
No guessed model baseline, arbitrary document deletion or new value store.
"""
import copy
import re
from . import queue_policy
from .configuration_history import History, Stale, differences, encoded, revision, approved, validate_restore
from .settings_changes import FIELDS, WORKER_FIELDS, safe, model_ids, HIDDEN
from .settings_profiles import stored, get_value, display_value, specs, decode
from .routing_policy import revision as policy_revision
from .launch_preview import worker_plan

MAX_REQUEST = 32768
HOST = {key: value for key, value in FIELDS.items() if key != 'worker_headroom_dispatch'}
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}')
COVERAGE = ('Selected saved session/independent-worker scalar defaults, worker inheritance, '
            'fallback enable and configured managed-profile routes; or one existing queue’s '
            'engine/model/effort/fallback/headroom exceptions. Browser reset is separate. '
            'Shared model baselines, engine visibility, retained snapshots, credentials, '
            'network, services, repository appearance and unknown metadata are not reset. '
            'Future matching launches only; running work and queue pins are unaffected by host resets. '
            'No cross-scope transaction or claim of runtime readiness.')


def state(core):
    raw, host = stored(core)
    path = core._wt_config_path()
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError('Queue configuration exceeds reset review limit')
        queues = decode(path.read_text(), 2_000_000)
        if not isinstance(queues, dict):
            raise ValueError('Queue configuration unavailable; expected an object')
    except FileNotFoundError:
        queues = {}
    if any(not isinstance(value, dict) for value in queues.values()):
        raise ValueError('Queue configuration unavailable; expected native objects')
    return raw, host, queues


def context(core, scope, target):
    if scope not in ('machine', 'queue') or not isinstance(target, str):
        raise ValueError('Choose this host or one existing queue')
    raw, host, queues = state(core)
    if scope == 'machine':
        if target:
            raise ValueError('Host resets do not accept a queue target')
        current = host
    else:
        target = queue_policy.entry(queues, target)
        if not NAME.fullmatch(target):
            raise ValueError('Queue identifier is unsupported by reversible history')
        current = queues[target]
    return raw, host, queues, target, current


def definitions(core):
    result = {key: {'key': key, 'label': label, 'replacement': value,
                   'effect': 'Replace with the known saved default literal; no model is guessed.'}
              for key, (label, value) in HOST.items()}
    result['worker_inherit_session']['effect'] = 'Leave linked defaults and restore the retained independent worker tuple; every dependent change is previewed.'
    result['worker_engine']['effect'] = 'Blank removes the saved engine choice; native dispatch chooses at launch, not a guessed engine.'
    result['worker_model']['effect'] = 'Blank removes the independent worker model choice; shared/engine defaults remain runtime dependent.'
    result['worker_fallback.enabled'] = {'key':'worker_fallback.enabled','label':'Worker fallback enable', 'replacement':False,
        'effect':'Turn worker fallback Off while retaining its configured chain and local metadata. Explicit queue On/Off pins still win.'}
    for name in ('fast','standard','deep'):
        key = 'model_profiles.' + name
        result[key] = {'key':key,'label':name+' managed-profile routes','replacement':{'models':[]},
            'effect':'Clear this profile’s known routes. Managed launches selecting it will be unavailable until configured; its record/local document metadata remain.'}
    return result


def available(core, key, present, value, host):
    if not present:
        return False, 'Not explicitly stored; nothing to reset.'
    linked = host.get('worker_inherit_session') is True
    if key in WORKER_FIELDS and linked:
        return False, 'Materialized linked field. Reset the link first to restore independent defaults, or reset session fields.'
    if key == 'worker_inherit_session' and linked:
        retained = host.get('worker_overrides')
        if not isinstance(retained, dict) or any(k not in retained or not approved(core,k,retained[k]) for k in WORKER_FIELDS):
            return False, 'Retained worker tuple is unavailable or unsupported; use Launch policy.'
    shown = display_value(core, key, True, value)
    if shown.get('redacted'):
        return False, 'Unsupported/local value is hidden and will not be reset; use the canonical editor.'
    if key.startswith('model_profiles.'):
        rows = value.get('models') if isinstance(value,dict) else None
        if not isinstance(rows,list) or any(not isinstance(row,dict) or set(row)-{'engine','model','effort'} for row in rows):
            return False, 'Routes contain local metadata that clearing would delete; use Launch policy.'
    return True, ''


def registry(core, scope='machine', target=''):
    audit = History(core); sources = audit.sources()
    raw, host, queues, target, current = context(core, scope, target)
    fields = []
    if scope == 'machine':
        for key, spec in definitions(core).items():
            present, value = get_value(current, key)
            # enabled is a leaf, not a portable routing-document field.
            if key == 'worker_fallback.enabled':
                can = present and type(value) is bool
                reason = '' if can else 'No supported saved enable switch; use Launch policy.'
                shown = {'present':present, **({'value':value} if can else {'redacted':True} if present else {})}
            else:
                can, reason = available(core,key,present,value,host)
                shown = display_value(core,key,present,value)
            fields.append({**spec,'available':can,'reason':reason,'current':shown,
                           'after':{'present':True,'value':spec['replacement']}})
    else:
        labels = {'engine':'Engine pin','model':'Model pin','effort':'Effort pin',
                  'fallback_to_default_worker':'Failure fallback exception','headroom_dispatch':'Headroom dispatch exception'}
        for key in queue_policy.FIELDS:
            present = key in current; value = current.get(key)
            can = present and approved(core,key,value)
            fields.append({'key':key,'label':labels[key],'available':can,
                'reason':'' if can else 'Already follows the default.' if not present else 'Unsupported/local value is hidden; use queue configuration.',
                'current':{'present':present, **({'value':value} if can else {'redacted':True} if present else {})},
                'after':{'present':False},'effect':'Remove this queue exception; follow the saved host/native policy. Explicit Off is an exception too.'})
    scopes = [{'scope':'machine','target':'','label':'This CCC host'}]
    scopes += [{'scope':'queue','target':name,'label':'Queue '+name} for name,conf in sorted(queues.items())
               if NAME.fullmatch(name) and not conf.get('archived')]
    if audit.sources() != sources:
        raise Stale('Reset sources changed while reading. Refresh again.')
    return {'ok':True,'version':1,'scope':scope,'target':target,'scopes':scopes,'fields':fields,
            'revision':revision([sources,scope,target]),'sources':sources,'coverage':COVERAGE}


def choices(payload, view):
    fields = payload.get('fields')
    available = {field['key'] for field in view['fields'] if field['available']}
    if not isinstance(fields,list) or not fields or any(not isinstance(k,str) or k not in available for k in fields) or len(set(fields)) != len(fields):
        raise ValueError('Explicitly select supported stored fields to reset')
    if payload.get('expected_revision') != view['revision']:
        raise Stale('Reset scope or sources changed. Refresh and preview again.')
    return sorted(fields)


def host_changes(core, host, selected):
    changes = {}; origins = {}
    for key in selected:
        if key == 'worker_fallback.enabled':
            local = host.get('worker_fallback')
            if not isinstance(local,dict) or not isinstance(local.get('models'),list):
                raise ValueError('Fallback policy unavailable; use Launch policy')
            changes['worker_fallback'] = {**copy.deepcopy(local),'enabled':False}
            origins['worker_fallback'] = list(range(len(local['models'])))
        elif key.startswith('model_profiles.'):
            name = key.split('.')[1]
            changes.setdefault('model_profiles',{})[name] = {'models':[]}
            origins.setdefault('model_profiles',{})[name] = []
        else:
            changes[key] = HOST[key][1]
    if host.get('worker_inherit_session') is True and changes.get('worker_inherit_session',True):
        engine = changes.get('engine',host.get('engine'))
        models = host.get('models')
        if (not isinstance(models,dict) or engine not in models or not approved(core,'worker_model',models[engine])
                or core._model_policy_blocks(models[engine])):
            raise ValueError('Linked session model source is unknown or blocked; repair Launch policy before resetting linked defaults')
    return changes, origins


def checked(result):
    if result.get('ok'):
        return result
    if result.get('status') == 409:
        raise Stale('Native policy sources changed. Refresh and preview again.')
    if result.get('status') == 503:
        raise OSError('Reset save could not be confirmed; refresh configuration/history')
    raise ValueError('Native policy rejects this reset selection; use Launch policy to repair compatibility')


def effective_queue(core, host, conf):
    plan = worker_plan(host,conf); result = {}
    models = model_ids(core)
    for key,item in plan['fields'].items():
        value = item['value']
        clean = safe(value,key,core,models) if value is not None else HIDDEN
        result[key] = {'known':clean != HIDDEN,'source':item['source'],
                       **({'value':clean} if clean != HIDDEN else {})}
    result['note'] = 'Saved configuration projection only. Unknown/ambient engine or model values are not guessed; native runtime checks and queue fallback decisions remain separate.'
    result['fallback'] = {'enabled':plan['fallback']['enabled'],'source':plan['fallback']['source'],
                          'headroom':plan['fallback']['headroom'],'headroom_source':plan['fallback']['headroom_source']}
    return result


def preview(core,payload):
    scope = payload.get('scope'); target = payload.get('target','')
    view = registry(core,scope,target); selected = choices(payload,view)
    raw, host, queues, target, current = context(core,scope,view['target'])
    if scope == 'machine':
        changes, origins = host_changes(core,host,selected)
        native = {'policy_action':'preview','expected_revision':policy_revision(raw),'changes':changes,'origins':origins}
        result = checked(core._routing_policy_request(native)); candidate = result['effective']
        if 'worker_fallback.enabled' in selected:
            before_route = copy.deepcopy(host['worker_fallback']); before_route['enabled'] = False
            if candidate.get('worker_fallback') != before_route:
                raise ValueError('Reset would normalize existing fallback routes; use Launch policy instead')
        # Show every known dependent change; never display arbitrary containers.
        definitions_all = {**specs(core),'worker_overrides':{'label':'Retained independent worker snapshot'},
                           'disabled_engines':{'label':'Engine visibility dependency'}}
        definitions_all.pop('worker_fallback',None)
        definitions_all['worker_fallback.enabled'] = {'label':'Worker fallback enable'}
        differences_view = []
        for key,spec in definitions_all.items():
            a,av = get_value(host,key); b,bv = get_value(candidate,key)
            if encoded([a,av]) == encoded([b,bv]): continue
            if key == 'worker_fallback.enabled':
                before = {'present':a, **({'value':av} if a else {})}; after = {'present':b, **({'value':bv} if b else {})}
            else:
                before = display_value(core,key,a,av); after = display_value(core,key,b,bv)
            if before.get('redacted') or after.get('redacted'):
                raise ValueError('Reset would change an unsupported dependent value; use Launch policy instead')
            differences_view.append({'key':key,'label':spec['label'],'before':before,'after':after,'selected':key in selected})
        undo_diff, reversible = differences(core,'machine',host,candidate)
        undo = reversible and raw is not None
        if undo and undo_diff:
            try:
                validate_restore(core, 'machine', host, undo_diff)
            except ValueError:
                undo = False
        extra = {}
    else:
        native = {'queue':target,'policy_action':'preview',
                  'expected_revision':queue_policy.state_revision(queues,queue_policy.defaults_text(core.SPAWN_DEFAULTS_FILE)),
                  'changes':{key:None for key in selected}}
        result = core._queue_policy_action(native)
        _,candidate,_ = queue_policy.prepare(queues,target,native['changes'])
        labels = {f['key']:f['label'] for f in view['fields']}
        differences_view = [{'key':d['key'],'label':labels[d['key']], 'selected':True,
            'before':{'present':d['before_set'], **({'value':d['before']} if d['before_set'] else {})},
            'after':{'present':d['after_set'], **({'value':d['after']} if d['after_set'] else {})}} for d in result['changes']]
        undo_diff,undo = differences(core,'queue',current,candidate[target])
        if undo and undo_diff:
            try:
                validate_restore(core, 'queue', current, undo_diff)
            except ValueError:
                undo = False
        extra = {'configured_after':effective_queue(core,host,candidate[target])}
    if History(core).sources() != view['sources']:
        raise Stale('Reset sources changed while validating. Refresh and preview again.')
    answer = {'ok':True,'scope':scope,'target':target,'selected':selected,'changes':differences_view,
              'undo_supported':undo,'sources':view['sources'], **extra}
    answer['preview_token'] = revision([answer,result['preview_token']])
    return answer,native,result['preview_token']


def request(core,payload):
    if not isinstance(payload,dict) or set(payload)-{'action','scope','target','fields','expected_revision','preview_token'}:
        raise ValueError('Use the selected-field reset request schema')
    action = payload.get('action')
    if action not in ('preview','apply'):
        raise ValueError('Choose preview or apply; cancellation sends no request')
    answer,native,token = preview(core,payload)
    if action == 'preview': return answer
    if payload.get('preview_token') != answer['preview_token']:
        raise Stale('Reset selection, policy or sources changed. Preview again.')
    def check_sources():
        if History(core).sources() != answer['sources']:
            raise Stale('Reset sources changed before publication. Preview again.')
    if answer['scope'] == 'machine':
        result = checked(core._routing_policy_request({**native,'policy_action':'apply','preview_token':token},source_check=check_sources))
    else:
        check_sources()
        result = core._queue_policy_action({**native,'policy_action':'apply','preview_token':token},source_check=check_sources)
    return {'ok':True,'scope':answer['scope'],'target':answer['target'],'applied':result['applied'],
            'changes':answer['changes'],'configuration_history':result.get('configuration_history',{})}
