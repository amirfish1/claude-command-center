"""Bounded nonsecret audit journal around authoritative configuration files.

The journal stores allowlisted diffs, never configuration copies. Pending intent
is durable before publication. Reads interpret pending intent without repairing
files; the next write consolidates it. There is no cross-file atomic commit.
"""
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import time
import threading
import uuid
from pathlib import Path
from .settings_preferences import _lock
from .settings_changes import FIELDS, WORKER_FIELDS, model_ids, safe, HIDDEN, kind

LIMIT = 50
MISSING = {'present': False}
PUBLICATION = threading.local()
MACHINE = set(FIELDS) | {'models', 'worker_overrides', 'worker_fallback', 'model_profiles', 'disabled_engines'}
QUEUE = {'engine', 'model', 'effort', 'fallback_to_default_worker', 'headroom_dispatch'}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def revision(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def read(path):
    try:
        result = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (ValueError, RecursionError) as exc:
        raise ValueError('Configuration unavailable; invalid JSON') from exc
    if not isinstance(result, dict):
        raise ValueError('Configuration unavailable; expected an object')
    return result


def check_publication(path):
    check = getattr(PUBLICATION, 'source_check', None)
    if check: check()
    expected = getattr(PUBLICATION, 'expected', None)
    if expected and Path(path) == expected[0] and revision(read(path)) != expected[1]:
        raise Stale('Configuration changed before publication; preview again')


def sync_directory(directory):
    if os.name != 'nt':
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='configuration-history-', suffix='.tmp', dir=path.parent)
    try:
        if hasattr(os, 'fchmod'): os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(encoded(value)); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def approved(core, key, value):
    """Exact shapes only: unknown metadata/custom identifiers are not retained."""
    models = model_ids(core)
    if key in FIELDS or key in QUEUE:
        typ = 'boolean' if key in ('fallback_to_default_worker', 'headroom_dispatch') else kind(key)
        return safe(value, typ, core, models) != HIDDEN and safe(value, typ, core, models) == value
    if key == 'disabled_engines':
        return isinstance(value, list) and all(v in core._ORCHESTRATION_SPAWN_ENGINES for v in value)
    if key == 'models':
        return isinstance(value, dict) and all(k in core._ORCHESTRATION_SPAWN_ENGINES and safe(v, 'model', core, models) != HIDDEN for k,v in value.items())
    if key == 'worker_overrides':
        return isinstance(value, dict) and set(value) == set(WORKER_FIELDS) and all(approved(core,k,v) for k,v in value.items())
    if key in ('worker_fallback', 'model_profiles'):
        if not isinstance(value, dict): return False
        if key == 'model_profiles':
            return set(value) <= {'fast','standard','deep'} and all(route(core,v,False) for v in value.values())
        return route(core,value,True)
    return False


def route(core, value, fallback):
    keys = {'models','enabled'} if fallback else {'models'}
    return (isinstance(value,dict) and set(value)==keys and (not fallback or type(value['enabled']) is bool)
            and isinstance(value['models'],list) and len(value['models']) <= 4
            and all(isinstance(r,dict) and set(r)=={'engine','model','effort'}
                    and all(approved(core,k,v) for k,v in r.items()) for r in value['models']))


def differences(core, scope, before, after):
    fields = MACHINE if scope == 'machine' else QUEUE
    result = []; reversible = True
    for key in sorted(fields):
        a = {'present':True,'value':before[key]} if key in before else MISSING
        b = {'present':True,'value':after[key]} if key in after else MISSING
        if encoded(a) == encoded(b): continue
        valid = all(not v['present'] or approved(core,key,v['value']) for v in (a,b))
        reversible &= valid
        result.append({'key':key, 'before':copy.deepcopy(a) if valid else {'supported':False},
                       'after':copy.deepcopy(b) if valid else {'supported':False}})
    return result, reversible


class Stale(ValueError): pass


class OutcomeUnknown(OSError): pass


class History:
    def __init__(self, core):
        self.core = core
        # Follows the caller's authoritative state path, including test instances.
        self.directory = core.SPAWN_DEFAULTS_FILE.parent
        self.path = self.directory / 'configuration-history.json'

    def target_path(self, scope):
        if scope not in ('machine','queue'): raise ValueError('Unsupported scope')
        return self.core.SPAWN_DEFAULTS_FILE if scope == 'machine' else self.core._wt_config_path()

    def document(self, scope): return read(self.target_path(scope))

    def sources(self):
        return {'machine':revision(self.document('machine')), 'queue':revision(self.document('queue')),
                'policy':revision([self.policy_document(), sorted(self.core._model_policy_blocked_models())])}

    def policy_document(self):
        value=read(self.core.MODEL_POLICY_FILE)
        if value is not None and (not isinstance(value.get('blocked_models'),list) or any(not isinstance(v,str) for v in value['blocked_models'])):
            raise ValueError('Current model policy unavailable')
        return value

    def _entry_valid(self, e):
        if not isinstance(e,dict) or set(e) != {'id','timestamp','scope','target','operation','changes','reversible','before_revision','after_revision','outcome'}: return False
        if not re.fullmatch(r'[a-f0-9]{32}',str(e['id'])) or type(e['timestamp']) not in (int,float) or not math.isfinite(e['timestamp']) or e['scope'] not in ('machine','queue'): return False
        if e['target'] != '' and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',str(e['target'])): return False
        if e['operation'] not in ('save','undo','legacy_save') or e['outcome'] not in ('saved','unknown') or type(e['reversible']) is not bool: return False
        if any(not isinstance(e[k],str) or not re.fullmatch(r'[a-f0-9]{64}',e[k]) for k in ('before_revision','after_revision')): return False
        if not isinstance(e['changes'],list) or len(e['changes']) > len(MACHINE): return False
        for d in e['changes']:
            if not isinstance(d,dict) or set(d) != {'key','before','after'} or d['key'] not in (MACHINE if e['scope']=='machine' else QUEUE): return False
            for part in ('before','after'):
                v=d[part]
                if v == {'supported':False}:
                    if e['reversible']: return False
                elif not isinstance(v,dict) or type(v.get('present')) is not bool or set(v) != ({'present','value'} if v['present'] else {'present'}): return False
                elif v['present'] and not approved(self.core,d['key'],v['value']): return False
        return True

    def state(self):
        try:
            if self.path.exists() and self.path.stat().st_size > 2_000_000:
                raise ValueError('Oversized history')
            value=read(self.path)
            if value is None: value={'version':1,'entries':[],'pending':None}
            if (set(value) != {'version','entries','pending'} or type(value['version']) is not int or value['version'] != 1
                    or not isinstance(value['entries'],list) or len(value['entries']) > LIMIT
                    or any(not self._entry_valid(e) for e in value['entries'])
                    or value['pending'] is not None and not self._entry_valid(value['pending'])):
                raise ValueError('Invalid history')
            return value
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            raise ValueError('Configuration history unavailable; no restore is safe') from exc

    def recover(self,state):
        state=copy.deepcopy(state);pending=state['pending'];status='none'
        if pending:
            live=revision(self.document(pending['scope']))
            if live == pending['after_revision']:
                state['entries'].append(pending);status='saved'
            elif live == pending['before_revision']: status='not_saved'
            else:
                pending.update(reversible=False,outcome='unknown');state['entries'].append(pending);status='unknown'
            state['entries']=state['entries'][-LIMIT:];state['pending']=None
        return state,status

    def snapshot(self):
        original=self.state();state,pending=self.recover(original)
        return {'version':1,'entries':list(reversed(state['entries'])), 'revision':revision(original),
                'pending_status':pending,'limit':LIMIT,
                'coverage':'Recorded from successful CCC launch-default and scoped queue-policy saves after this feature was installed. Legacy full queue saves are non-reversible observations; external writers and older history are not reconstructed. Browser history remains separate. Unsupported values/metadata are omitted and cannot be restored. Undo changes future launches, never running work. No cross-file atomicity.'}

    def _commit(self,state,scope,target,before,after,writer,operation,force_marker=False,source_check=None):
        a=(before or {}) if scope=='machine' else (before or {}).get(target,{})
        b=after if scope=='machine' else after.get(target,{})
        diff,reversible=differences(self.core,scope,a,b)
        if not diff:
            writer();return {'state':'not_recorded','reason':'No covered changes'}
        if scope=='queue' and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',target):
            target='';diff=[];reversible=False
        entry={'id':uuid.uuid4().hex,'timestamp':time.time(),'scope':scope,'target':target,'operation':operation,
               'changes':diff,'reversible':reversible and not force_marker and before is not None,'before_revision':revision(before),
               'after_revision':revision(after),'outcome':'saved'}
        pending={**state,'pending':entry}
        atomic(self.path,pending)  # Failure here prevents the configuration write.
        try:
            if source_check: source_check()
            PUBLICATION.source_check = source_check
            PUBLICATION.expected = (self.target_path(scope), revision(before))
            try: writer()
            finally:
                PUBLICATION.expected = None
                PUBLICATION.source_check = None
        except Exception as exc:
            from .queue_policy import StalePreview
            if isinstance(exc, (Stale, StalePreview)):
                try: atomic(self.path,state)
                except OSError: pass
                raise
            try: live=revision(self.document(scope))
            except (OSError, ValueError) as read_error:
                raise OutcomeUnknown('Save outcome cannot be read; refresh configuration and history') from read_error
            if live == revision(before):
                try: atomic(self.path,state)
                except OSError: pass  # Pending resolves to not_saved on the next read.
                raise
            if live == revision(after):
                return {'state':'pending','warning':'Configuration saved; history finalization pending. Refresh history before undo.'}
            raise OutcomeUnknown('Save outcome is unknown; refresh configuration and history') from exc
        try:
            try: live=revision(self.document(scope))
            except (OSError, ValueError) as read_error:
                raise OutcomeUnknown('Published configuration cannot be read; refresh history') from read_error
            if live != entry['after_revision']:
                raise OutcomeUnknown('Configuration changed during publication; refresh history')
            sync_directory(self.target_path(scope).parent)
            atomic(self.path,{'version':1,'entries':(state['entries']+[entry])[-LIMIT:],'pending':None})
            return {'state':'recorded','reversible':entry['reversible']}
        except OutcomeUnknown:
            raise
        except OSError:
            return {'state':'pending','warning':'Configuration saved; history finalization pending. Refresh history before undo.'}

    def publish(self,scope,target,before,after,writer,operation='save',source_check=None):
        with _lock(self.directory/'configuration-history-lock'):
            state,_=self.recover(self.state())
            if revision(self.document(scope)) != revision(before): raise Stale('Configuration changed before save; preview again')
            return self._commit(state,scope,target,before,after,writer,operation,source_check=source_check)

    def observed_queue(self,before,after,target):
        # The legacy native form has multi-step setters and service effects;
        # do not claim its complete operation can be undone or crash-replayed.
        with _lock(self.directory/'configuration-history-lock'):
            state,_=self.recover(self.state())
            diff,_=differences(self.core,'queue',before.get(target,{}),after.get(target,{}))
            if not diff: return {'state':'not_recorded'}
            e={'id':uuid.uuid4().hex,'timestamp':time.time(),'scope':'queue','target':target if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',target) else '',
               'operation':'legacy_save','changes':diff,'reversible':False,'before_revision':revision(before),'after_revision':revision(after),'outcome':'saved'}
            atomic(self.path,{'version':1,'entries':(state['entries']+[e])[-LIMIT:],'pending':None})
            return {'state':'recorded','reversible':False}

    def preview(self,entry_id):
        if not isinstance(entry_id,str) or not re.fullmatch(r'[a-f0-9]{32}',entry_id): raise ValueError('Select a retained history entry')
        state=self.snapshot();entry=next((e for e in state['entries'] if e['id']==entry_id),None)
        if not entry or not entry['reversible'] or entry['outcome'] != 'saved': raise ValueError('This change has no supported reversible snapshot')
        sources=self.sources();scope=entry['scope'];before=self.document(scope)
        if revision(before) != entry['after_revision']: raise Stale('Newer or external edits exist; undo would overwrite them')
        if before is None: raise Stale('Configuration is no longer available')
        after=copy.deepcopy(before)
        if scope=='queue':
            from .queue_policy import entry as queue_entry
            target=queue_entry(after,entry['target']);candidate=after[target]
        else: target='';candidate=after
        for change in entry['changes']:
            v=change['before']
            if v['present']: candidate[change['key']]=copy.deepcopy(v['value'])
            else: candidate.pop(change['key'],None)
        validate_restore(self.core,scope,candidate,entry['changes'])
        if sources != self.sources(): raise Stale('Sources changed during validation; preview again')
        result={'entry_id':entry_id,'scope':scope,'target':target,'history_revision':state['revision'],'sources':sources,
                'changes':[{'key':d['key'],'before':d['after'],'after':d['before']} for d in entry['changes']]}
        result['preview_token']=revision(result)
        return result,entry,before,after

    def undo(self,payload):
        initial=self.preview(payload.get('entry_id'))[0]
        if payload.get('preview_token') != initial['preview_token']: raise Stale('History, defaults or model policy changed; preview undo again')
        with _lock(self.directory/'configuration-history-lock'):
            preview,entry,before,after=self.preview(payload.get('entry_id'))
            if payload.get('preview_token') != preview['preview_token']: raise Stale('History, defaults or model policy changed; preview undo again')
            state,_=self.recover(self.state());core=self.core;scope=entry['scope']
            def writer():
                if preview['sources'] != self.sources(): raise Stale('Sources changed before undo')
                if scope=='machine': core._write_spawn_defaults_file(after)
                else:
                    from .queue_policy import publish,state_revision,defaults_text
                    publish(self.target_path('queue'),core.SPAWN_DEFAULTS_FILE,after,state_revision(before,defaults_text(core.SPAWN_DEFAULTS_FILE)))
            result=self._commit(state,scope,entry['target'],before,after,writer,'undo')
            return {'ok':True,'applied':True,'history':result,'scope':scope,'target':entry['target']}


def validate_restore(core,scope,candidate,changes):
    """Use native consumer validators; never replay arbitrary saved documents."""
    keys={d['key'] for d in changes}
    if scope=='queue':
        engine=candidate.get('engine') or (read(core.SPAWN_DEFAULTS_FILE) or {}).get('worker_engine')
        effort=candidate.get('effort');model=candidate.get('model')
        if candidate.get('engine') and candidate['engine'] not in core._QUEUE_CONFIG_ENGINES: raise ValueError('Unsupported queue engine')
        if effort and (effort not in core._QUEUE_CONFIG_EFFORTS or engine and effort not in core._engine_reasoning_efforts(engine)): raise ValueError('Queue effort is no longer compatible')
        if model and core._model_policy_error(model): raise ValueError('Restored queue model is no longer allowed')
        return
    for prefix in ('','worker_'):
        engine=candidate.get(prefix+'engine') or 'claude'
        if engine not in core._ORCHESTRATION_SPAWN_ENGINES or core._validate_reasoning_effort(candidate.get(prefix+'reasoning_effort',''),engine,strict=True) is None: raise ValueError('Restored engine/effort is no longer compatible')
    disabled=candidate.get('disabled_engines')
    if isinstance(disabled,list) and any(e and e in disabled for e in (candidate.get('engine','claude'),candidate.get('worker_engine'))):
        raise ValueError('A restored default engine cannot also be disabled')
    for key in ('worker_fallback','model_profiles'):
        if key in keys and key in candidate:
            validator=core._validate_worker_fallback if key=='worker_fallback' else core._validate_model_profiles
            validator(candidate[key],strict=True)
    retained=candidate.get('worker_overrides')
    if 'worker_overrides' in keys and isinstance(retained,dict):
        engine=retained.get('worker_engine') or 'claude'
        effort=retained.get('worker_reasoning_effort','')
        model=retained.get('worker_model','')
        if engine not in core._ORCHESTRATION_SPAWN_ENGINES or core._validate_reasoning_effort(effort,engine,strict=True) is None:
            raise ValueError('Retained worker overrides are no longer compatible')
        if model and (core._model_policy_blocks(model) or not core._model_catalog_allows_model(engine,model)):
            raise ValueError('Retained worker model is no longer allowed')
    models=candidate.get('models',{})
    if isinstance(models,dict):
        for engine,model in models.items():
            if engine in core._ORCHESTRATION_SPAWN_ENGINES and model and (core._model_policy_blocks(model) or not core._model_catalog_allows_model(engine,model)):
                raise ValueError('Restored model policy is no longer compatible')
    model=candidate.get('worker_model');engine=candidate.get('worker_engine')
    if model and (core._model_policy_blocks(model) or engine and not core._model_catalog_allows_model(engine,model)): raise ValueError('Restored worker model is no longer allowed')
    if candidate.get('worker_inherit_session') is True:
        effective=core._materialize_worker_defaults(copy.deepcopy(candidate))
        if any(effective.get(k)!=candidate.get(k) for k in WORKER_FIELDS): raise ValueError('Stored inheritance snapshot no longer matches its session sources')
