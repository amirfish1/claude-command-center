"""Native reset transactions over temporary documents; audit writes are forbidden."""
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
import server
from ccc_server import configuration_history as history, settings_resets as resets, queue_policy


@pytest.fixture
def state(tmp_path, monkeypatch):
    # Pin both CCC and optional WT helpers BEFORE any native configuration call.
    monkeypatch.setenv('WATCHTOWER_CONFIG_FILE', str(tmp_path/'queue-config.json'))
    monkeypatch.setenv('CCC_SPAWN_DEFAULTS_FILE', str(tmp_path/'spawn-defaults.json'))
    monkeypatch.setattr(server,'COMMAND_CENTER_STATE_DIR',tmp_path)
    monkeypatch.setattr(server,'SPAWN_DEFAULTS_FILE',tmp_path/'spawn-defaults.json')
    monkeypatch.setattr(server,'MODEL_POLICY_FILE',tmp_path/'model-policy.json')
    monkeypatch.setattr(server,'_wt_config_path',lambda:tmp_path/'queue-config.json')
    monkeypatch.setattr(server,'_WT_CONFIG_AVAILABLE',False)
    native = getattr(server,'_wt_config',None)
    def forbidden(*a,**kw):pytest.fail('No native audit, processes, credentials or services')
    if native:
        monkeypatch.setattr(native,'CONFIG_FILE',tmp_path/'queue-config.json',raising=False)
        monkeypatch.setattr(native,'_record_audit',forbidden,raising=False)
    original_write=Path.write_text
    def guard(path,*args,**kwargs):
        if 'audit' in path.name and not path.resolve().is_relative_to(tmp_path):
            pytest.fail('Non-temporary audit write forbidden')
        return original_write(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',guard)
    import builtins
    import io
    def guarded_open(original):
        def opened(file, mode='r', *args, **kwargs):
            if isinstance(file, (str, bytes, Path)) and any(flag in mode for flag in 'wax+'):
                candidate = Path(file.decode() if isinstance(file, bytes) else file)
                if 'audit' in candidate.name and not candidate.resolve().is_relative_to(tmp_path):
                    pytest.fail('Non-temporary audit write forbidden')
            return original(file, mode, *args, **kwargs)
        return opened
    monkeypatch.setattr(builtins, 'open', guarded_open(builtins.open))
    monkeypatch.setattr(io, 'open', guarded_open(io.open))
    monkeypatch.setattr(server,'_spawn_fallback_model_for_engine',lambda engine:'auto')
    monkeypatch.setattr(server,'_model_policy_blocked_models',lambda:set())
    monkeypatch.setattr(server,'_model_policy_blocks',lambda model:False)
    monkeypatch.setattr(server,'_model_policy_error',lambda model:None)
    monkeypatch.setattr(server,'_model_catalog_allows_model',lambda engine,model:True)
    monkeypatch.setattr(server,'_worker_fallback_catalog_models',lambda engine:[{'id':'auto'},{'id':'default'}])
    monkeypatch.setattr(server,'_codex_context_1m_enabled',lambda:False)
    monkeypatch.setattr(server,'_codex_context_config',lambda **kw:{'model_context_window':1000000})
    monkeypatch.setattr(server.subprocess,'run',forbidden);monkeypatch.setattr(server.subprocess,'Popen',forbidden)
    monkeypatch.setattr(server,'_reconcile_once_async',forbidden);monkeypatch.setattr(server,'byok_get_key',forbidden)
    server.MODEL_POLICY_FILE.write_text('{"blocked_models":[]}')
    defaults=server._factory_spawn_defaults()
    defaults.update(engine='codex',auto_compact_k=420,worker_engine='codex',worker_model='auto',worker_reasoning_effort='high',
                    future={'credential':'not-a-secret','path':'/private/example'})
    server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(defaults))
    server._wt_config_path().write_text(json.dumps({'MANUAL':{'engine':'codex','model':'auto','effort':'high',
        'fallback_to_default_worker':False,'headroom_dispatch':True,'auto_drain':False,
        'repo_path':'/private/example','future':{'credential':'not-a-secret'}}}))
    return history.History(server)


@pytest.fixture
def api(state):
    class Handler(server.CommandCenterHandler):
        def log_message(self,*a):pass
        def _phone_pin_blocked(self,path):return False
        def _check_proxy_secret(self):return True
    httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    def request(data=None,route='/api/settings/resets',raw=None):
        req=urllib.request.Request(f'http://127.0.0.1:{httpd.server_address[1]}'+route,
            data=raw if raw is not None else None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())
    yield request,state
    httpd.shutdown();httpd.server_close();thread.join(timeout=5)


def payload(fields,scope='machine',target=''):
    view=resets.registry(server,scope,target)
    return dict(action='preview',scope=scope,target=target,fields=fields,expected_revision=view['revision'])


def apply(p):
    preview=resets.request(server,p)
    return resets.request(server,{**p,'action':'apply','preview_token':preview['preview_token']})


def documents(store):
    return tuple(p.read_bytes() if p.exists() else None for p in (server.SPAWN_DEFAULTS_FILE,server._wt_config_path(),store.path))


def edit(path,**values):
    doc=json.loads(path.read_text());doc.update(values);path.write_text(json.dumps(doc));return doc


def test_registry_readonly_allowlist_redaction_and_explicit_same_default(api,monkeypatch):
    req,store=api;before=documents(store)
    monkeypatch.setattr(server,'_factory_spawn_defaults',lambda:pytest.fail('No factory/model lookup on registry'))
    code,view=req();assert code==200
    fields={f['key']:f for f in view['fields']}
    assert 'models.codex' not in fields and 'disabled_engines' not in fields
    assert fields['auto_compact_k']['current']['value']==420
    assert fields['worker_auto_compact_k']['available'] # explicitly stored same default, truthful no-op
    assert documents(store)==before
    text=json.dumps(view)
    assert 'not-a-secret' not in text and '/private/example' not in text and 'future' not in text


def test_host_preview_apply_and_journal_undo_exact(api):
    req,store=api;original=history.read(server.SPAWN_DEFAULTS_FILE);before=documents(store)
    p=payload(['auto_compact_k','reasoning_effort'])
    code,view=req(p);assert code==200,view
    assert [{**c,'before':{k:v for k,v in c['before'].items() if k!='local_metadata_preserved'},'after':{k:v for k,v in c['after'].items() if k!='local_metadata_preserved'}} for c in view['changes']]==[{'key':'auto_compact_k','label':'Session compaction (K tokens)','before':{'present':True,'value':420},'after':{'present':True,'value':250},'selected':True}]
    assert view['undo_supported'] and documents(store)==before
    code,result=req({**p,'action':'apply','preview_token':view['preview_token']});assert code==200,result
    assert result['applied'] and result['configuration_history']['state']=='recorded'
    saved=history.read(server.SPAWN_DEFAULTS_FILE);assert saved=={**original,'auto_compact_k':250}
    undo=store.preview(store.snapshot()['entries'][0]['id'])[0];assert store.undo(undo)['applied']
    assert history.read(server.SPAWN_DEFAULTS_FILE)==original
    assert 'not-a-secret' not in store.path.read_text() and '/private/example' not in store.path.read_text()


def test_queue_absence_explicit_off_manual_and_restore(api):
    req,store=api;original=history.read(server._wt_config_path());before=documents(store)
    p=payload(list(queue_policy.FIELDS),'queue','MANUAL')
    code,view=req(p);assert code==200,view
    assert len(view['changes'])==5 and all(not c['after']['present'] for c in view['changes'])
    assert next(c for c in view['changes'] if c['key']=='fallback_to_default_worker')['before']['value'] is False
    assert view['configured_after']['engine']['value']=='codex'
    assert view['undo_supported'] and documents(store)==before
    code,result=req({**p,'action':'apply','preview_token':view['preview_token']});assert code==200,result
    saved=history.read(server._wt_config_path())['MANUAL']
    assert saved=={k:v for k,v in original['MANUAL'].items() if k not in queue_policy.FIELDS}
    assert saved['auto_drain'] is False
    assert store.undo(store.preview(store.snapshot()['entries'][0]['id'])[0])['applied']
    assert history.read(server._wt_config_path())==original


def test_linked_dependency_preview_and_restore_retained_snapshot(state):
    original=history.read(server.SPAWN_DEFAULTS_FILE)
    retained={k:original[k] for k in resets.WORKER_FIELDS}
    edit(server.SPAWN_DEFAULTS_FILE,worker_inherit_session=True,worker_overrides=retained,
         worker_auto_compact_k=420,worker_reasoning_effort='')
    p=payload(['auto_compact_k']);view=resets.request(server,p)
    assert {c['key'] for c in view['changes']}=={'auto_compact_k','worker_auto_compact_k'}
    assert apply(p)['applied']
    p=payload(['worker_inherit_session']);view=resets.request(server,p)
    assert {'worker_inherit_session','worker_reasoning_effort'}<={c['key'] for c in view['changes']}
    assert apply(p)['applied']
    saved=history.read(server.SPAWN_DEFAULTS_FILE)
    assert not saved['worker_inherit_session'] and all(saved[k]==v for k,v in retained.items())
    assert saved['worker_overrides']==retained


def test_unknown_link_model_blocks_preview_no_guess(state):
    edit(server.SPAWN_DEFAULTS_FILE,worker_inherit_session=True,models={})
    before=documents(state)
    with pytest.raises(ValueError,match='model source'):resets.request(server,payload(['auto_compact_k']))
    assert documents(state)==before


def test_fallback_off_preserves_chain_and_metadata_not_fake_reversible(state):
    chain={'enabled':True,'models':[{'engine':'codex','model':'auto','effort':'high','future':'not-a-secret'}],'local':'not-a-secret'}
    edit(server.SPAWN_DEFAULTS_FILE,worker_fallback=chain)
    p=payload(['worker_fallback.enabled']);view=resets.request(server,p)
    assert not view['undo_supported']
    assert 'not-a-secret' not in json.dumps(view)
    assert apply(p)['applied']
    assert history.read(server.SPAWN_DEFAULTS_FILE)['worker_fallback']=={**chain,'enabled':False}
    assert not state.snapshot()['entries'][0]['reversible']


def test_managed_profile_clear_preserves_document_other_profiles_rejects_row_metadata(state):
    profiles={'fast':{'models':[{'engine':'claude','model':'auto','effort':'high'}],'local':'not-a-secret'},
              'custom':{'opaque':'not-a-secret'}}
    edit(server.SPAWN_DEFAULTS_FILE,model_profiles=profiles)
    p=payload(['model_profiles.fast']);view=resets.request(server,p)
    assert 'not-a-secret' not in json.dumps(view) and not view['undo_supported']
    assert apply(p)['applied']
    assert history.read(server.SPAWN_DEFAULTS_FILE)['model_profiles']=={**profiles,'fast':{**profiles['fast'],'models':[]}}
    profiles['fast']['models'][0]['future']='not-a-secret'
    edit(server.SPAWN_DEFAULTS_FILE,model_profiles=profiles)
    field=next(f for f in resets.registry(server)['fields'] if f['key']=='model_profiles.fast')
    assert not field['available']


@pytest.mark.parametrize('scope,target,field',[('machine','','auto_compact_k'),('queue','MANUAL','engine')])
@pytest.mark.parametrize('source',['machine','queue','policy'])
def test_stale_sources_apply_never_mutates(api,scope,target,field,source):
    req,store=api;p=payload([field],scope,target);code,view=req(p);assert code==200
    if source=='machine':edit(server.SPAWN_DEFAULTS_FILE,auto_compact_k=500)
    elif source=='queue':edit(server._wt_config_path(),OTHER={'auto_drain':False})
    else:server.MODEL_POLICY_FILE.write_text('{"blocked_models":["default"]}')
    before=documents(store)
    code,result=req({**p,'action':'apply','preview_token':view['preview_token']})
    assert code==409,result
    assert documents(store)==before


@pytest.mark.parametrize('scope,target,field',[('machine','','auto_compact_k'),('queue','MANUAL','engine')])
def test_replace_failure_configuration_intact_no_false_history(state,monkeypatch,scope,target,field):
    p=payload([field],scope,target);before=documents(state);original=history.os.replace
    destination=server.SPAWN_DEFAULTS_FILE if scope=='machine' else server._wt_config_path()
    def fail(src,dst):
        if Path(dst)==destination:raise OSError('fixture replace failed')
        return original(src,dst)
    monkeypatch.setattr(history.os,'replace',fail)
    with pytest.raises(OSError):apply(p)
    assert documents(state)[:2]==before[:2]
    assert state.snapshot()['entries']==[]
    assert not list(destination.parent.glob('*-*.tmp'))


def test_journal_initial_failure_and_final_failure_truth(state,monkeypatch):
    p=payload(['auto_compact_k']);original=history.atomic;before=documents(state)
    def fail(path,value):raise OSError('fixture history failed')
    monkeypatch.setattr(history,'atomic',fail)
    with pytest.raises(OSError):apply(p)
    assert documents(state)==before
    def final(path,value):
        if value['pending'] is None:raise OSError('fixture final failed')
        return original(path,value)
    monkeypatch.setattr(history,'atomic',final)
    result=apply(p);assert result['applied']
    assert result['configuration_history']['state']=='pending'
    assert history.read(server.SPAWN_DEFAULTS_FILE)['auto_compact_k']==250
    monkeypatch.setattr(history,'atomic',original)
    assert state.snapshot()['entries'][0]['reversible']


def test_noop_cancel_invalid_and_missing_preserve_files(api):
    req,store=api;p=payload(['worker_auto_compact_k']);before=documents(store)
    code,view=req(p);assert code==200 and not view['changes']
    code,result=req({**p,'action':'apply','preview_token':view['preview_token']});assert code==200 and not result['applied']
    for patch in ({'fields':[]},{'fields':['models.codex']},{'fields':['auto_compact_k','auto_compact_k']},{'action':'cancel'},{'arbitrary':True}):
        assert req({**p,**patch})[0]==400
    assert documents(store)==before


def test_duplicate_oversize_unknown_scope_and_unreadable_not_all_default(api):
    req,store=api
    assert req(raw=b'{"action":"preview","action":"apply"}')[0]==400
    assert req(raw=b' '*32769)[0]==400
    assert req(route='/api/settings/resets?scope=invalid')[0]==503
    server.SPAWN_DEFAULTS_FILE.write_text('{')
    code,result=req();assert code==503 and not result.get('fields')


def test_concurrent_same_preview_only_one_publishes(api):
    req,store=api;p=payload(['engine']);code,view=req(p);assert code==200
    data={**p,'action':'apply','preview_token':view['preview_token']}
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:req(data),range(2)))
    assert sorted(c for c,_ in results)==[200,409]
    assert len(store.snapshot()['entries'])==1


def test_final_queue_publication_rechecks_model_policy(state,monkeypatch):
    p=payload(['engine'],'queue','MANUAL');original=queue_policy.os.fsync;fired=False
    def conflict(fd):
        nonlocal fired
        if not fired:
            fired=True;server.MODEL_POLICY_FILE.write_text('{"blocked_models":["default"]}')
        return original(fd)
    # Inject after intent is written, inside native queue publication.
    original_publish=queue_policy.publish
    def publish(*args):
        monkeypatch.setattr(queue_policy.os,'fsync',conflict)
        return original_publish(*args)
    monkeypatch.setattr(queue_policy,'publish',publish)
    before=server._wt_config_path().read_bytes()
    with pytest.raises(history.Stale):apply(p)
    assert server._wt_config_path().read_bytes()==before
    assert state.snapshot()['entries']==[]


def test_strict_queue_source_duplicates_and_unknown_values_block_destructive_reset(state):
    server._wt_config_path().write_text('{"MANUAL":{},"MANUAL":{"engine":"codex"}}')
    with pytest.raises(ValueError):resets.registry(server)
    server._wt_config_path().write_text('{"MANUAL":{"engine":"private-engine","model":"private-model"}}')
    view=resets.registry(server,'queue','MANUAL')
    assert not any(f['available'] for f in view['fields'])
    assert 'private-engine' not in json.dumps(view) and 'private-model' not in json.dumps(view)


def test_changed_selection_preview_token_rejected(state):
    p=payload(['auto_compact_k']);view=resets.request(server,p);before=documents(state)
    with pytest.raises(history.Stale):
        resets.request(server,{**p,'fields':['engine'],'action':'apply','preview_token':view['preview_token']})
    assert documents(state)==before


def test_profile_supported_clear_is_reversible_and_missing_fields_stay_missing(state):
    profile={'models':[{'engine':'claude','model':'auto','effort':'high'}]}
    edit(server.SPAWN_DEFAULTS_FILE,model_profiles={'fast':profile})
    p=payload(['model_profiles.fast']);view=resets.request(server,p)
    assert view['undo_supported']
    assert apply(p)['applied']
    assert state.undo(state.preview(state.snapshot()['entries'][0]['id'])[0])['applied']
    assert history.read(server.SPAWN_DEFAULTS_FILE)['model_profiles']=={'fast':profile}
    host=history.read(server.SPAWN_DEFAULTS_FILE);host.pop('auto_compact_k');server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(host))
    assert not next(f for f in resets.registry(server)['fields'] if f['key']=='auto_compact_k')['available']


def test_fallback_chain_normalization_rejected_and_invalid_combination_no_partial_write(state):
    edit(server.SPAWN_DEFAULTS_FILE,worker_fallback={'enabled':True,'models':[{'engine':'codex','model':' auto ','effort':'high'}]})
    before=documents(state)
    with pytest.raises(ValueError,match='normalize'):resets.request(server,payload(['auto_compact_k','worker_fallback.enabled']))
    assert documents(state)==before
    edit(server.SPAWN_DEFAULTS_FILE,engine='claude',reasoning_effort='max')
    # Clearing only worker engine to blank leaves an independent saved model intact.
    p=payload(['worker_engine']);view=resets.request(server,p)
    assert all(c['key']!='worker_model' for c in view['changes'])


def test_invalid_native_validation_never_writes(state,monkeypatch):
    before=documents(state)
    monkeypatch.setattr(server,'_prepare_spawn_defaults_unlocked',lambda changes:{'ok':False,'error':'fixture incompatible tuple'})
    with pytest.raises(ValueError,match='rejects'):resets.request(server,payload(['auto_compact_k','engine']))
    assert documents(state)==before
