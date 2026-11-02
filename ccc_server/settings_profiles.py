"""Portable selected host preferences over the native staged-policy protocol.

No configuration copies, identities, names, paths or observed model identifiers
are accepted. Browser v1 profiles remain a separate compatible format/transaction.
"""
import json
from .configuration_history import History, Stale, approved, encoded, revision
from .routing_policy import revision as policy_revision

FORMAT = 'ccc-operational-preferences'
MAX_BYTES = 128 * 1024
MAX_REQUEST = MAX_BYTES * 4
SCALARS = {
    'engine': ('Session engine', 'engine'),
    'reasoning_effort': ('Session effort', 'effort'),
    'auto_compact_k': ('Session compaction (K tokens)', 'integer'),
    'worker_inherit_session': ('Workers follow session defaults', 'boolean'),
    'worker_engine': ('Independent worker engine', 'engine'),
    'worker_model': ('Independent worker model', 'model'),
    'worker_reasoning_effort': ('Independent worker effort', 'effort'),
    'worker_auto_compact_k': ('Independent worker compaction (K tokens)', 'integer'),
    'worker_fallback': ('Worker fallback policy', 'routes'),
}
WORKER = {'worker_engine','worker_model','worker_reasoning_effort','worker_auto_compact_k'}


class Invalid(ValueError): pass


def _pairs(items):
    result={}
    for key,value in items:
        if key in result: raise Invalid('Duplicate JSON fields are not allowed')
        result[key]=value
    return result


def decode(raw,limit=MAX_BYTES):
    if not isinstance(raw,(str,bytes)) or len(raw.encode() if isinstance(raw,str) else raw)>limit:
        raise Invalid('Profile JSON exceeds the size limit')
    try:
        return json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(Invalid('Nonfinite JSON values are not allowed')))
    except (ValueError,UnicodeError,RecursionError) as exc:
        if isinstance(exc,Invalid):raise
        raise Invalid('Enter valid profile JSON') from exc


def specs(core):
    values={key:dict(key=key,label=label,type=typ,scope='machine') for key,(label,typ) in SCALARS.items()}
    for engine in core._ORCHESTRATION_SPAWN_ENGINES:
        values['models.'+engine]=dict(key='models.'+engine,label=engine+' shared model',type='model',scope='machine')
    for name in ('fast','standard','deep'):
        values['model_profiles.'+name]=dict(key='model_profiles.'+name,label=name+' managed profile',type='profile',scope='machine')
    return values


def get_value(document,key):
    value=document
    for part in key.split('.'):
        if not isinstance(value,dict) or part not in value:return False,None
        value=value[part]
    return True,value


def valid(core,key,value):
    if key.startswith('models.'):return approved(core,'worker_model',value)
    if key.startswith('model_profiles.'):return approved(core,'model_profiles',{key.split('.')[1]:value})
    return approved(core,key,value)


def fields(core,selected,available):
    if not isinstance(selected,list) or not selected or any(not isinstance(k,str) or k not in specs(core) or k not in available for k in selected) or len(set(selected))!=len(selected):
        raise Invalid('Select supported fields explicitly')
    return sorted(selected)


def profile(core,raw):
    value=decode(raw)
    if not isinstance(value,dict) or set(value)!={'format','version','scope','values'}:
        raise Invalid('Use the host profile schema without extra metadata')
    if value['format']!=FORMAT or type(value['version']) is not int or value['version']!=1 or value['scope']!='machine':
        raise Invalid('Use a version 1 host profile; browser profiles use the separate browser controls')
    if not isinstance(value['values'],dict) or not value['values'] or any(k not in specs(core) for k in value['values']):
        raise Invalid('Unknown fields are rejected; remove unsupported fields from the profile explicitly')
    return value


def stored(core):
    try: raw=core.SPAWN_DEFAULTS_FILE.read_text()
    except FileNotFoundError:return None,{}
    value=decode(raw,MAX_REQUEST)
    if not isinstance(value,dict):raise Invalid('Saved host preferences are unavailable')
    return raw,value


def registry(core):
    raw,current=stored(core);result=[]
    for key,spec in specs(core).items():
        present,value=get_value(current,key)
        linked=key in WORKER and current.get('worker_inherit_session') is True
        available=present and valid(core,key,value) and not linked
        reason=('Materialized linked worker fields stay local; export session fields and the link instead.' if linked else
                'Not explicitly stored; automatic defaults are not resolved for export.' if not present else
                'Contains unsupported values or local metadata; this field cannot be exported.' if not available else '')
        result.append({**spec,'available':available,'reason':reason,**({'value':value} if available else {})})
    if stored(core)[0]!=raw:raise Stale('Host preferences changed while reading; refresh')
    return {'version':1,'format':FORMAT,'scope':'machine','revision':policy_revision(raw),'fields':result,
            'max_bytes':MAX_BYTES,'coverage':'Selected saved session/worker defaults, shared registered models, worker fallback and managed profiles. No credentials, paths, account/machine/queue identities, custom models, retained local worker snapshots, engine visibility, headroom flags, queue templates or browser values. Literal auto/default values resolve on the receiving host; installed/authenticated readiness is not asserted. Browser profiles apply separately.'}


def inspect(core,request):
    value=profile(core,request.get('profile'));definitions=specs(core)
    return {'ok':True,'scope':'machine','fields':[{**definitions[k],'portable':valid(core,k,v),
            **({'value':v} if valid(core,k,v) else {}),'reason':'' if valid(core,k,v) else 'Unsupported value; uncheck this field explicitly to omit it, or correct the JSON.'}
            for k,v in value['values'].items()]}


def export(core,request):
    view=registry(core)
    if request.get('expected_revision')!=view['revision']:raise Stale('Host preferences changed; refresh before exporting')
    available={v['key']:v for v in view['fields'] if v['available']}
    chosen=fields(core,request.get('fields'),available)
    return {'ok':True,'profile':{'format':FORMAT,'version':1,'scope':'machine','values':{key:available[key]['value'] for key in chosen}}}


def origins_for(current,incoming,fallback):
    """Keep local metadata by provider/managed-model identity; never guess drops."""
    if not isinstance(current,dict):
        if current is not None:raise Invalid('Local routing document is incompatible; use the native policy editor')
        current={}
    old=current.get('models',[])
    if not isinstance(old,list) or any(not isinstance(r,dict) for r in old):raise Invalid('Local routes are incompatible; use the native policy editor')
    identity=lambda r:(r.get('engine'),) if fallback else (r.get('engine'),r.get('model'))
    origins=[];used=set()
    for row in incoming['models']:
        matches=[i for i,r in enumerate(old) if identity(r)==identity(row)]
        if len(matches)>1:raise Invalid('Local routing identity is ambiguous; use the native policy editor')
        origin=matches[0] if matches else None
        origins.append(origin)
        if origin is not None:used.add(origin)
    if any(set(r)-{'engine','model','effort'} for i,r in enumerate(old) if i not in used):
        raise Invalid('Import would remove local route metadata; uncheck this field or use the native policy editor')
    return origins


def changes_for(current,values):
    changes={};origins={}
    for key,value in values.items():
        if key.startswith('models.'):
            changes.setdefault('models',{})[key.split('.')[1]]=value
        elif key.startswith('model_profiles.'):
            name=key.split('.')[1];local=current.get('model_profiles',{})
            if not isinstance(local,dict):raise Invalid('Local managed profiles are unavailable')
            changes.setdefault('model_profiles',{})[name]=value
            origins.setdefault('model_profiles',{})[name]=origins_for(local.get(name),value,False)
        else:
            changes[key]=value
            if key=='worker_fallback':origins[key]=origins_for(current.get(key),value,True)
    return changes,origins


def display_value(core,key,present,value):
    if not present:return {'present':False}
    clean=value;omitted=False
    if key=='worker_fallback' or key.startswith('model_profiles.'):
        if isinstance(value,dict) and isinstance(value.get('models'),list) and all(isinstance(r,dict) for r in value['models']):
            allowed={'models','enabled'} if key=='worker_fallback' else {'models'}
            clean={k:v for k,v in value.items() if k in allowed}
            clean['models']=[{k:r[k] for k in ('engine','model','effort') if k in r} for r in value['models']]
            omitted=encoded(clean)!=encoded(value)
    if key=='disabled_engines':
        if not isinstance(value,list):return {'present':True,'redacted':True}
        clean=[v for v in value if isinstance(v,str) and v in core._ORCHESTRATION_SPAWN_ENGINES]
        return {'present':True,'value':clean,'local_metadata_preserved':encoded(clean)!=encoded(value)}
    if key=='worker_overrides':
        if isinstance(value,dict):
            clean={k:v for k,v in value.items() if k in WORKER};omitted=encoded(clean)!=encoded(value)
        acceptable=approved(core,key,clean)
    else:acceptable=valid(core,key,clean)
    return {'present':True,'value':clean,'local_metadata_preserved':omitted} if acceptable else {'present':True,'redacted':True}


def preview(core,request):
    value=profile(core,request.get('profile'));chosen=fields(core,request.get('fields'),value['values'])
    selected={k:value['values'][k] for k in chosen}
    if any(not valid(core,k,v) for k,v in selected.items()):raise Invalid('Selected fields contain unsupported values; omit them explicitly or correct the JSON')
    raw,current=stored(core);audit=History(core);sources=audit.sources()
    if current.get('worker_inherit_session') is True and set(selected)&WORKER and selected.get('worker_inherit_session') is not False:
        raise Invalid('Local workers follow session defaults; explicitly include worker_inherit_session false or omit independent worker fields')
    changes,origins=changes_for(current,selected)
    native={'policy_action':'preview','expected_revision':policy_revision(raw),'changes':changes,'origins':origins}
    result=core._routing_policy_request(native)
    if not result.get('ok'):
        if result.get('status')==409:raise Stale('Host policy changed; preview again')
        raise Invalid('Selected fields are incompatible with local engine/model/effort policy; change the selection or JSON')
    candidate=result['effective']
    # A portable value has one exact meaning; native normalization is never hidden.
    for key,v in selected.items():
        present,after=get_value(candidate,key)
        shown=display_value(core,key,present,after)
        if not present or 'value' not in shown or encoded(shown['value'])!=encoded(v):
            raise Invalid('Native validation would translate a selected value; use an explicit compatible value')
    differences=[];definitions={**specs(core),'worker_overrides':{'label':'Retained independent worker snapshot (local only)'},'disabled_engines':{'label':'Engine visibility (local dependency)'}}
    for key,spec in definitions.items():
        a,av=get_value(current,key);b,bv=get_value(candidate,key)
        if encoded([a,av])!=encoded([b,bv]):differences.append({'key':key,'label':spec['label'],
            'before':display_value(core,key,a,av),'after':display_value(core,key,b,bv),'selected':key in chosen})
    from .configuration_history import differences as history_diff
    _,reversible=history_diff(core,'machine',current,candidate)
    if stored(core)[0]!=raw or audit.sources()!=sources:raise Stale('Default sources or model policy changed while validating; preview again')
    result_view={'ok':True,'scope':'machine','selected':chosen,'omitted':sorted(set(value['values'])-set(chosen)),
                 'changes':differences,'undo_supported':reversible and raw is not None,
                 'sources':sources,'policy_revision':policy_revision(raw)}
    result_view['preview_token']=revision([value,selected,result_view,result['preview_token']])
    return result_view,native,result['preview_token'],sources


def request(core,payload):
    if not isinstance(payload,dict):raise Invalid('Expected a profile request object')
    action=payload.get('action')
    if action=='inspect':return inspect(core,payload)
    if action=='export':return export(core,payload)
    if action not in ('preview_import','apply_import'):raise Invalid('Choose export, inspect, preview_import or apply_import')
    view,native,token,sources=preview(core,payload)
    if action=='preview_import':return view
    if payload.get('preview_token')!=view['preview_token']:raise Stale('Profile, selection, defaults or model policy changed; preview again')
    def check_sources():
        if History(core).sources()!=sources:raise Stale('Default sources or model policy changed before publication; preview again')
    result=core._routing_policy_request({**native,'policy_action':'apply','preview_token':token},source_check=check_sources)
    if not result.get('ok'):
        if result.get('status')==409:raise Stale('Sources changed; profile was not applied. Preview again')
        if result.get('status')==503:raise OSError('Host profile save could not be confirmed; refresh configuration and history')
        raise Invalid('Host policy validation failed; no profile apply was confirmed')
    return {'ok':True,'scope':'machine','applied':result['applied'],'changes':view['changes'],
            'configuration_history':result.get('configuration_history',{}),'omitted':view['omitted']}
