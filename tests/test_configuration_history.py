"""Real temporary-file history and native APIs; no live configuration or probes."""
import copy
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import pytest
import server
from ccc_server import configuration_history as history, queue_policy


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(server,'COMMAND_CENTER_STATE_DIR',tmp_path)
    monkeypatch.setattr(server,'MODEL_POLICY_FILE',tmp_path/'model-policy.json')
    server.MODEL_POLICY_FILE.write_text('{"blocked_models":[]}')
    monkeypatch.setattr(server,'SPAWN_DEFAULTS_FILE',tmp_path/'spawn-defaults.json')
    monkeypatch.setattr(server,'_wt_config_path',lambda:tmp_path/'queue-config.json')
    monkeypatch.setattr(server,'_spawn_fallback_model_for_engine',lambda engine:'auto')
    monkeypatch.setattr(server,'_model_policy_blocked_models',lambda:set())
    monkeypatch.setattr(server,'_model_policy_blocks',lambda model:False)
    monkeypatch.setattr(server,'_model_policy_error',lambda model:None)
    monkeypatch.setattr(server,'_model_catalog_allows_model',lambda engine,model:True)
    monkeypatch.setattr(server,'_worker_fallback_catalog_models',lambda engine:[{'id':'auto'},{'id':'default'}])
    monkeypatch.setattr(server,'_codex_context_1m_enabled',lambda:False)
    monkeypatch.setattr(server,'_codex_context_config',lambda **kw:{'model_context_window':1000000})
    def forbidden(*a,**kw):pytest.fail('No processes, secrets or services')
    monkeypatch.setattr(server.subprocess,'run',forbidden);monkeypatch.setattr(server.subprocess,'Popen',forbidden)
    monkeypatch.setattr(server,'_reconcile_once_async',forbidden)
    monkeypatch.setattr(server,'byok_get_key',forbidden)
    defaults=server._factory_spawn_defaults();defaults.update(worker_engine='codex',worker_model='auto',worker_reasoning_effort='high',future_policy={'path':'/private/example','credential':'not-a-secret'})
    server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(defaults))
    queues={'MANUAL':{'auto_drain':False,'repo_path':'/private/example','future':{'credential':'not-a-secret'}}}
    server._wt_config_path().write_text(json.dumps(queues))
    return history.History(server)


@pytest.fixture
def api(state):
    class Handler(server.CommandCenterHandler):
        def log_message(self,*a):pass
        def _phone_pin_blocked(self,path):return False
        def _check_proxy_secret(self):return True
    httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    def request(route='/api/settings/history',data=None):
        req=urllib.request.Request(f'http://127.0.0.1:{httpd.server_address[1]}'+route,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())
    yield request,state
    httpd.shutdown();httpd.server_close();thread.join(timeout=5)


def save(changes):
    result=server._save_spawn_defaults(changes);assert result['ok'],result
    return result


def undo(store,entry=None):
    entry=entry or store.snapshot()['entries'][0]
    preview=store.preview(entry['id'])[0]
    assert store.undo(preview)['applied']


def queue_save(changes):
    config=queue_policy.read(server._wt_config_path());raw=queue_policy.defaults_text(server.SPAWN_DEFAULTS_FILE)
    payload={'queue':'MANUAL','policy_action':'preview','expected_revision':queue_policy.state_revision(config,raw),'changes':changes}
    view=server._queue_policy_action(payload)
    return server._queue_policy_action({**payload,'policy_action':'apply','preview_token':view['preview_token']})


def test_empty_reads_never_create_or_seed_history(api):
    request,store=api;before=server.SPAWN_DEFAULTS_FILE.read_bytes()
    assert request()[1]['entries']==[]
    assert not store.path.exists() and server.SPAWN_DEFAULTS_FILE.read_bytes()==before


def test_native_legacy_defaults_save_then_actual_http_preview_apply(api):
    request,store=api;original=history.read(server.SPAWN_DEFAULTS_FILE)
    code,saved=request('/api/spawn-defaults',{'auto_compact_k':310});assert code==200 and saved['configuration_history']['state']=='recorded'
    entry=request()[1]['entries'][0];assert entry['scope']=='machine' and entry['reversible']
    before=store.path.read_bytes(),server.SPAWN_DEFAULTS_FILE.read_bytes()
    code,preview=request(data={'action':'preview_undo','entry_id':entry['id']});assert code==200
    assert (store.path.read_bytes(),server.SPAWN_DEFAULTS_FILE.read_bytes())==before
    assert preview['changes']==[{'key':'auto_compact_k','before':{'present':True,'value':310},'after':{'present':True,'value':250}}]
    code,result=request(data={'action':'apply_undo',**preview});assert code==200 and result['applied']
    assert history.read(server.SPAWN_DEFAULTS_FILE)==original
    assert request()[1]['entries'][0]['operation']=='undo'
    text=store.path.read_text()
    for secret in ('not-a-secret','/private/example','future_policy'):assert secret not in text


def test_staged_policy_apply_only_records_success_not_previews_or_noops(state):
    raw,stored,view=server._routing_policy_snapshot()
    payload={'policy_action':'preview','expected_revision':view['policy_revision'],'changes':{'auto_compact_k':320}}
    preview=server._routing_policy_request(payload);assert preview['ok']
    assert not state.path.exists()
    result=server._routing_policy_request({**payload,'policy_action':'apply','preview_token':preview['preview_token']});assert result['applied']
    save({'auto_compact_k':320})
    assert len(state.snapshot()['entries'])==1


def test_queue_native_api_preserves_absence_metadata_and_manual_scope(api):
    request,store=api;before=history.read(server._wt_config_path())
    assert queue_save({'fallback_to_default_worker':False,'headroom_dispatch':True})['applied']
    entry=request()[1]['entries'][0];assert entry['scope']=='queue' and entry['target']=='MANUAL'
    assert entry['changes'][0]['before']=={'present':False}
    undo(store,entry)
    assert history.read(server._wt_config_path())==before
    assert 'not-a-secret' not in store.path.read_text()


@pytest.mark.parametrize('changes',[{'worker_inherit_session':True},{'worker_inherit_session':False}])
def test_inheritance_exact_retained_snapshot_round_trip(state,changes):
    if not changes['worker_inherit_session']:save({'worker_inherit_session':True})
    before=history.read(server.SPAWN_DEFAULTS_FILE)
    save(changes);undo(state)
    assert history.read(server.SPAWN_DEFAULTS_FILE)==before


def test_linked_session_edit_undo_restores_both_materialized_tuples(state):
    save({'worker_inherit_session':True});before=history.read(server.SPAWN_DEFAULTS_FILE)
    save({'engine':'codex','reasoning_effort':'high','models':{'codex':'default'}})
    undo(state);assert history.read(server.SPAWN_DEFAULTS_FILE)==before


@pytest.mark.parametrize('field,value',[('worker_model','not-a-secret'),('worker_overrides',{'credential':'not-a-secret'}),('worker_fallback',{'enabled':False,'models':[],'path':'/private/example'})])
def test_unapproved_values_become_nonreversible_markers_without_secret_copies(state,field,value):
    before=history.read(server.SPAWN_DEFAULTS_FILE);before[field]=value;server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(before))
    if field=='worker_model':save({'worker_model':'auto'})
    elif field=='worker_overrides':save({'worker_inherit_session':True})
    else:save({'worker_fallback':{'enabled':False,'models':[]}})
    entry=state.snapshot()['entries'][0];assert not entry['reversible']
    assert 'not-a-secret' not in state.path.read_text() and '/private/example' not in state.path.read_text()
    with pytest.raises(ValueError):state.preview(entry['id'])


def test_routes_and_profiles_undo_use_native_current_policy_validation(state,monkeypatch):
    before=history.read(server.SPAWN_DEFAULTS_FILE)
    save({'worker_fallback':{'enabled':True,'models':[{'engine':'codex','model':'auto','effort':'high'}]},'model_profiles':{'fast':{'models':[{'engine':'claude','model':'default','effort':'high'}]}}})
    undo(state);assert history.read(server.SPAWN_DEFAULTS_FILE)==before
    save({'worker_model':'default'})
    monkeypatch.setattr(server,'_model_policy_blocks',lambda model:model=='auto')
    with pytest.raises(ValueError):state.preview(state.snapshot()['entries'][0]['id'])


@pytest.mark.parametrize('source',['machine','queue','policy'])
def test_preview_rejects_source_changes_before_apply(state,source,monkeypatch):
    save({'auto_compact_k':330});preview=state.preview(state.snapshot()['entries'][0]['id'])[0]
    if source=='policy':monkeypatch.setattr(server,'_model_policy_blocked_models',lambda:{'default'})
    else:
        path=state.target_path(source);doc=history.read(path);doc['external']={'credential':'not-a-secret'};path.write_text(json.dumps(doc))
    before=state.path.read_bytes(),server.SPAWN_DEFAULTS_FILE.read_bytes()
    with pytest.raises(ValueError):state.undo(preview)
    assert (state.path.read_bytes(),server.SPAWN_DEFAULTS_FILE.read_bytes())==before


def test_older_entry_cannot_overwrite_newer_or_external_edits(state):
    save({'auto_compact_k':310});entry=state.snapshot()['entries'][0]
    save({'auto_compact_k':320})
    with pytest.raises(history.Stale):state.preview(entry['id'])


def test_concurrent_undo_allows_only_one_commit(state):
    save({'auto_compact_k':310});preview=state.preview(state.snapshot()['entries'][0]['id'])[0]
    def attempt(_):
        try:return state.undo(preview)['applied']
        except history.Stale:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(attempt,range(2)))==[False,True]


def test_history_prewrite_failure_leaves_configuration_unchanged(state,monkeypatch):
    before=server.SPAWN_DEFAULTS_FILE.read_bytes()
    monkeypatch.setattr(history,'atomic',lambda *a:(_ for _ in ()).throw(OSError('fixture')))
    with pytest.raises(OSError):server._save_spawn_defaults({'auto_compact_k':310})
    assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before and not state.path.exists()


def test_config_write_failure_does_not_record_saved_change(state,monkeypatch):
    monkeypatch.setattr(server,'_write_spawn_defaults_file',lambda *a:(_ for _ in ()).throw(OSError('fixture')))
    with pytest.raises(OSError):server._save_spawn_defaults({'auto_compact_k':310})
    assert state.snapshot()['entries']==[]


def test_finalization_failure_reports_saved_and_recovers_readonly(state,monkeypatch):
    original=history.atomic;calls=[]
    def fail_second(*args):
        calls.append(1)
        if len(calls)==2:raise OSError('fixture')
        return original(*args)
    monkeypatch.setattr(history,'atomic',fail_second)
    result=save({'auto_compact_k':310});assert result['configuration_history']['state']=='pending'
    assert history.read(server.SPAWN_DEFAULTS_FILE)['auto_compact_k']==310
    before=state.path.read_bytes();view=state.snapshot()
    assert view['pending_status']=='saved' and len(view['entries'])==1 and state.path.read_bytes()==before
    monkeypatch.setattr(history,'atomic',original);undo(state)
    assert history.read(server.SPAWN_DEFAULTS_FILE)['auto_compact_k']==250
    assert len(state.snapshot()['entries'])==2


def test_pending_outcome_unknown_never_claims_safe_undo(state,monkeypatch):
    original=history.atomic
    def fail_final(path,value):
        if value['pending'] is None:raise OSError('fixture')
        original(path,value)
    monkeypatch.setattr(history,'atomic',fail_final);save({'auto_compact_k':310})
    doc=history.read(server.SPAWN_DEFAULTS_FILE);doc['auto_compact_k']=450;server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(doc))
    view=state.snapshot();assert view['pending_status']=='unknown' and view['entries'][0]['outcome']=='unknown' and not view['entries'][0]['reversible']


def test_corrupt_or_injected_ledger_is_unavailable_not_exported(api):
    request,store=api;store.path.write_text('{"credential":"not-a-secret"}')
    code,value=request();assert code==503 and 'not-a-secret' not in json.dumps(value)
    before=server.SPAWN_DEFAULTS_FILE.read_bytes();assert not server._save_spawn_defaults({'auto_compact_k':310})['ok'];assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before


def test_legacy_queue_observation_is_honestly_not_reversible(state):
    before=history.read(server._wt_config_path());after=copy.deepcopy(before);after['MANUAL']['effort']='low';server._wt_config_path().write_text(json.dumps(after))
    assert state.observed_queue(before,after,'MANUAL')['state']=='recorded'
    e=state.snapshot()['entries'][0];assert e['operation']=='legacy_save' and not e['reversible']


def test_bounded_history_keeps_latest_without_rewriting_config_from_ledger(state,monkeypatch):
    monkeypatch.setattr(history,'LIMIT',3)
    for value in range(310,316):save({'auto_compact_k':value})
    assert len(state.snapshot()['entries'])==3
    assert history.read(server.SPAWN_DEFAULTS_FILE)['auto_compact_k']==315


def test_malformed_current_model_policy_prevents_undo_without_writes(state):
    save({'auto_compact_k':310})
    server.MODEL_POLICY_FILE.write_text('{')
    before=state.path.read_bytes(),server.SPAWN_DEFAULTS_FILE.read_bytes()
    with pytest.raises(ValueError):state.preview(state.snapshot()['entries'][0]['id'])
    assert (state.path.read_bytes(),server.SPAWN_DEFAULTS_FILE.read_bytes())==before


def test_exact_legacy_blank_queue_value_is_restored_not_erased(state):
    queues=history.read(server._wt_config_path());queues['MANUAL']['effort']='';server._wt_config_path().write_text(json.dumps(queues))
    queue_save({'effort':'low'})
    undo(state)
    restored=history.read(server._wt_config_path())['MANUAL']
    assert 'effort' in restored and restored['effort']==''


def test_native_machine_final_publication_guard_preserves_external_edit(state,monkeypatch):
    original=history.check_publication
    def concurrent(path):
        value=history.read(path);value['external']='not-a-secret';path.write_text(json.dumps(value));original(path)
    monkeypatch.setattr(history,'check_publication',concurrent)
    assert not server._save_spawn_defaults({'auto_compact_k':310})['ok']
    assert history.read(server.SPAWN_DEFAULTS_FILE)['auto_compact_k']==250
    assert history.read(server.SPAWN_DEFAULTS_FILE)['external']=='not-a-secret'
    assert state.snapshot()['entries']==[]


def test_native_legacy_queue_full_save_is_observed_without_claiming_reversible_service_effects(api,monkeypatch):
    request,store=api
    monkeypatch.setattr(server,'_WT_CONFIG_AVAILABLE',False)
    monkeypatch.setattr(server,'_reconcile_once_async',lambda:None)
    monkeypatch.setattr(server,'_wt_log_queue_config_change',lambda *a,**kw:None)
    code,result=request('/api/queue/config',{'queue':'MANUAL','engine':'codex','model':'auto','effort':'low','auto_drain':False,'backend':'file'})
    assert code==200,result
    assert result['configuration_history']['state']=='recorded'
    entry=store.snapshot()['entries'][0]
    assert entry['operation']=='legacy_save' and entry['reversible'] is False


def test_invalid_current_queue_effort_blocks_restore_before_history_writes(state,monkeypatch):
    queues=history.read(server._wt_config_path());queues['MANUAL'].update(engine='codex',effort='high');server._wt_config_path().write_text(json.dumps(queues))
    queue_save({'effort':'low'})
    monkeypatch.setattr(server,'_engine_reasoning_efforts',lambda engine:['low'])
    before=state.path.read_bytes(),server._wt_config_path().read_bytes()
    with pytest.raises(ValueError):state.preview(state.snapshot()['entries'][0]['id'])
    assert (state.path.read_bytes(),server._wt_config_path().read_bytes())==before


def test_unknown_future_route_metadata_survives_save_without_entering_ledger(state):
    saved=history.read(server.SPAWN_DEFAULTS_FILE)
    saved['worker_fallback']={'enabled':False,'models':[],'future_path':'/private/example'}
    server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(saved))
    raw,_,view=server._routing_policy_snapshot()
    request={'expected_revision':view['policy_revision'],'policy_action':'preview','changes':{'worker_fallback':{'enabled':True,'models':[{'engine':'codex','model':'auto','effort':'high'}]}},'origins':{'worker_fallback':[None]}}
    preview=server._routing_policy_request(request);assert preview['ok'],preview
    assert server._routing_policy_request({**request,'policy_action':'apply','preview_token':preview['preview_token']})['applied']
    assert history.read(server.SPAWN_DEFAULTS_FILE)['worker_fallback']['future_path']=='/private/example'
    assert '/private/example' not in state.path.read_text()
    assert not state.snapshot()['entries'][0]['reversible']


def test_saved_config_is_reported_even_if_writer_raises_after_publication(state,monkeypatch):
    original=server._write_spawn_defaults_file
    def after_write(value):
        original(value);raise OSError('Fixture after replace')
    monkeypatch.setattr(server,'_write_spawn_defaults_file',after_write)
    result=save({'auto_compact_k':310})
    assert result['configuration_history']['state']=='pending'
    assert state.snapshot()['pending_status']=='saved'


def test_uncertain_writer_never_reports_a_confirmed_success(state,monkeypatch):
    def unknown(value):
        other=history.read(server.SPAWN_DEFAULTS_FILE);other['auto_compact_k']=450;server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(other));raise OSError('Fixture uncertain outcome')
    monkeypatch.setattr(server,'_write_spawn_defaults_file',unknown)
    with pytest.raises(history.OutcomeUnknown):server._save_spawn_defaults({'auto_compact_k':310})
    assert state.snapshot()['entries'][0]['outcome']=='unknown'


def test_crash_before_publication_is_not_saved_and_next_write_consolidates(state,monkeypatch):
    original=history.atomic
    def crash(path,value):
        original(path,value)
        if value['pending']:raise KeyboardInterrupt('Fixture abrupt exit before configuration')
    monkeypatch.setattr(history,'atomic',crash)
    with pytest.raises(KeyboardInterrupt):server._save_spawn_defaults({'auto_compact_k':310})
    before=state.path.read_bytes();view=state.snapshot()
    assert view['pending_status']=='not_saved' and view['entries']==[] and state.path.read_bytes()==before
    monkeypatch.setattr(history,'atomic',original);save({'auto_compact_k':320})
    assert len(state.snapshot()['entries'])==1


@pytest.mark.parametrize('bad', [None, [], {}, {'version':1,'entries':[{'key':['invalid']}],'pending':None}])
def test_malformed_ledger_shapes_are_unavailable_not_silently_empty(api,bad):
    request,store=api;store.path.write_text(json.dumps(bad))
    code,value=request()
    assert code==503 and value['ok'] is False


def test_reading_old_history_and_pending_journal_never_migrates_or_writes(state,monkeypatch):
    save({'auto_compact_k':310});before=state.path.read_bytes(),server.SPAWN_DEFAULTS_FILE.read_bytes()
    def forbidden(*a,**kw):pytest.fail('History GET cannot seed defaults or write')
    monkeypatch.setattr(server,'_load_spawn_defaults',forbidden);monkeypatch.setattr(history,'atomic',forbidden)
    assert state.snapshot()['entries'][0]['changes'][0]['key']=='auto_compact_k'
    assert (state.path.read_bytes(),server.SPAWN_DEFAULTS_FILE.read_bytes())==before


def test_postpublication_unreadable_source_does_not_claim_no_save(state,monkeypatch):
    original=server._write_spawn_defaults_file
    def corrupted_after(value):
        original(value);server.SPAWN_DEFAULTS_FILE.write_text('{')
    monkeypatch.setattr(server,'_write_spawn_defaults_file',corrupted_after)
    with pytest.raises(history.OutcomeUnknown):server._save_spawn_defaults({'auto_compact_k':310})
    assert json.loads(state.path.read_text())['pending'] is not None
