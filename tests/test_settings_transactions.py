"""Native disposable HTTP and files; no live configuration or process actions."""
import json
import multiprocessing
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
import server
from ccc_server import log_parse, settings_transactions as tx, configuration_history

NATIVE_WT = server._wt_config


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, 'NETWORK_CONFIG_FILE', tmp_path / 'network.json')
    monkeypatch.setattr(log_parse, 'FEATURE_FLAGS_FILE', tmp_path / 'feature-flags.json')
    monkeypatch.setattr(server, 'MODEL_POLICY_FILE', tmp_path / 'model-policy.json')
    server.MODEL_POLICY_FILE.write_text('{"blocked_models":[]}')
    monkeypatch.setattr(server, 'SPAWN_DEFAULTS_FILE', tmp_path / 'spawn-defaults.json')
    server.SPAWN_DEFAULTS_FILE.write_text('{"worker_engine":"claude"}')
    monkeypatch.setenv('WATCHTOWER_CONFIG_FILE', str(tmp_path / 'queue-config.json'))
    monkeypatch.setattr(server, '_wt_config_path', lambda: tmp_path / 'queue-config.json')
    monkeypatch.setattr(server, '_model_policy_error', lambda model: 'Blocked model' if model == 'blocked' else None)
    monkeypatch.setattr(server, '_model_policy_blocks', lambda model: model == 'blocked')
    monkeypatch.setattr(server, '_model_policy_blocked_models', lambda: set())
    monkeypatch.setattr(server, '_worker_fallback_catalog_models', lambda engine: [])
    monkeypatch.setattr(server, '_detect_tailnet_origins', lambda port: {'available':False})
    monkeypatch.setattr(server, 'RUNTIME_NETWORK_INFO', {})
    monkeypatch.setattr(server, 'BIND_HOST', '127.0.0.1')
    def forbidden(*a, **kw):
        raise AssertionError('No processes, credentials or probes')
    monkeypatch.setattr(server.subprocess, 'run', forbidden)
    monkeypatch.setattr(server.subprocess, 'Popen', forbidden)
    effects = []
    monkeypatch.setattr(server, '_log_activity', lambda *a, **kw: effects.append('confirmation'))
    monkeypatch.setattr(server, '_schedule_restart', lambda: effects.append('restart'))
    monkeypatch.setattr(server, '_reconcile_once_async', lambda: effects.append('reconcile'))
    monkeypatch.setattr(server, '_wt_log_queue_config_change', lambda *a, **kw: effects.append('log'))
    # Writing setters must never be called, even when WT is importable.
    native = SimpleNamespace(canonical_model=lambda eng, model: 'claude-sonnet-5' if eng == 'claude' and model == 'sonnet-5' else model,
                             is_blocked_model=lambda model: model == 'blocked',
                             ensure_entry=forbidden, set_backend=forbidden, set_model=forbidden)
    monkeypatch.setattr(server, '_wt_config', native)
    monkeypatch.setattr(server, '_WT_CONFIG_AVAILABLE', True)
    class Handler(server.CommandCenterHandler):
        def log_message(self, *a): pass
        def _phone_pin_blocked(self, path): return False
        def _check_proxy_secret(self): return True
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    def request(route, value=None, headers=None):
        req = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}' + route,
            data=None if value is None else json.dumps(value).encode(),
            headers={'Content-Type':'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)
    yield request, tmp_path, effects
    httpd.shutdown(); httpd.server_close(); thread.join(5)


@pytest.mark.parametrize('installed', [True, False])
def test_complete_queue_single_publication_unknown_metadata_and_links(api, monkeypatch, installed):
    request, base, effects = api
    monkeypatch.setattr(server, '_WT_CONFIG_AVAILABLE', installed)
    path = base / 'queue-config.json'
    path.write_text(json.dumps({'sample': {'headroom_dispatch':False,'future':{'opaque':[1,2]},'grace_s':17,'fallback_to_default_worker':True}, 'OTHER':{'engine':'codex'}}))
    calls = []
    original = tx.os.replace
    def replace(src, dest):
        calls.append(dest); return original(src, dest)
    monkeypatch.setattr(tx.os, 'replace', replace)
    status, saved = request('/api/queue/config', {'queue':'SAMPLE','engine':'claude','model':'sonnet-5', 'effort':'high', 'auto_drain':True,'desired_workers':0,'session_id':'session-test','offer_workers':True})
    assert status == 200 and saved['ok']
    conf = json.loads(path.read_text())
    assert 'sample' not in conf and conf['OTHER'] == {'engine':'codex'}
    assert conf['SAMPLE']['future'] == {'opaque':[1,2]}
    assert conf['SAMPLE']['headroom_dispatch'] is False
    assert conf['SAMPLE']['fallback_to_default_worker'] is True
    assert conf['SAMPLE']['grace_s'] == 17
    assert conf['SAMPLE']['desired_workers'] == 1
    assert conf['SAMPLE']['session_id'] == 'session-test' and conf['SAMPLE']['offer_workers'] is True
    assert conf['SAMPLE']['model'] == ('claude-sonnet-5' if installed else 'sonnet-5')
    assert calls.count(path) == 1
    assert effects == ['log','reconcile']
    assert saved['configuration_history'].get('reversible') is not True
    assert request('/api/queue/config', {'queue':'SAMPLE','fallback_to_default_worker':None,'session_id':'','offer_workers':False})[0] == 200
    conf = json.loads(path.read_text())['SAMPLE']
    assert not {'fallback_to_default_worker','session_id','offer_workers'} & conf.keys()


@pytest.mark.parametrize('field,value', [('github_repo','bad repo'),('github_repo','owner/repo'),('effort','invalid'),('grace_s',-1),('session_id','invalid/session'),('model','blocked')])
def test_queue_late_validation_never_partially_saves(api, field, value):
    request, base, effects = api
    path = base / 'queue-config.json'; path.write_text('{"SAMPLE":{"backend":"file","engine":"codex"}}')
    before = path.read_bytes()
    code, _ = request('/api/queue/config', {'queue':'SAMPLE','backend':'github','github_repo':'example/project','engine':'claude',field:value})
    assert code == 400
    assert path.read_bytes() == before and effects == []
    assert not (base/'configuration-history.json').exists()


def test_queue_catchall_new_queue_and_malformed_guards(api):
    request, base, effects = api; path = base/'queue-config.json'
    path.write_text('{"OTHER":{"backend":"github","github_repo":"example/project","queue_label":"*"}}')
    before = path.read_bytes()
    assert request('/api/queue/config', {'queue':'NEW','backend':'github','github_repo':'example/project','queue_label':'*'})[0] == 400
    assert path.read_bytes() == before
    assert request('/api/queue/config', {'queue':'NEW','auto_drain':True})[0] == 200
    assert json.loads(path.read_text())['NEW']['auto_drain'] is False
    path.write_text('{malformed')
    assert request('/api/queue/config', {'queue':'NEW'})[0] == 400
    assert path.read_text() == '{malformed'


@pytest.mark.parametrize('route,filename,payload', [('/api/queue/config','queue-config.json',{'queue':'SAMPLE','engine':'codex'}),('/api/network-config','network.json',{'bind_host':'0.0.0.0','trust_tailnet':True}),('/api/features/flag','feature-flags.json',{'name':'phone_access','on':True})])
def test_replace_failure_preserves_original_and_skips_effects(api, monkeypatch, route, filename, payload):
    request, base, effects = api; path = base/filename; path.write_text('{}'); before=path.read_bytes()
    original=tx.os.replace
    def fail(src,dest):
        if dest == path: raise OSError('Fixture replace failure')
        return original(src,dest)
    monkeypatch.setattr(tx.os, 'replace', fail)
    status, result = request(route, payload)
    assert status >= 400 and not result.get('ok')
    assert path.read_bytes() == before and effects == []
    assert not list(base.glob('*.tmp'))


def test_network_revision_conflict_old_clients_security_and_restart_boundary(api):
    request, base, effects=api; path=base/'network.json'
    code, first=request('/api/network-config'); assert code == 200 and first['revision']
    assert not path.exists() and not (base/'preferences.lock').exists()
    code, saved=request('/api/network-config', {'bind_host':'127.0.0.1','trust_tailnet':True,'expected_revision':first['revision'],'unapproved':'ignored'})
    assert code == 200 and saved['restart'] is False
    assert set(json.loads(path.read_text())) == {'bind_host','trust_tailnet','allowed_origins'}
    before=path.read_bytes()
    assert request('/api/network-config', {'bind_host':'0.0.0.0','expected_revision':first['revision']})[0] == 409
    assert path.read_bytes() == before and effects == []
    assert request('/api/network-config', {'bind_host':'0.0.0.0'}, {'X-Forwarded-For':'192.0.2.1'})[0] == 403
    assert path.read_bytes() == before
    # Legacy callers omit a token and retain serialized last-writer behavior.
    code, saved=request('/api/network-config', {'bind_host':'0.0.0.0'})
    assert code == 200 and saved['restart'] is True
    assert effects == ['restart']


def test_network_concurrent_same_revision_only_one_wins(api):
    request, base, effects=api
    token=request('/api/network-config')[1]['revision']
    def save(on): return request('/api/network-config', {'bind_host':'127.0.0.1','trust_tailnet':on,'expected_revision':token})[0]
    # Both requests differ from an absent document, even when on=False.
    with ThreadPoolExecutor(2) as pool: codes=list(pool.map(save,[True,False]))
    assert sorted(codes) == [200,409]
    assert effects == []


def test_feature_switches_concurrent_preserve_unrelated_and_future_metadata(api):
    request, base, effects=api; path=base/'feature-flags.json'
    path.write_text('{"future":{"version":2},"unknown_flag":false}')
    names=list(log_parse._PREVIEW_FLAGS)
    with ThreadPoolExecutor(len(names)) as pool:
        results=list(pool.map(lambda name: request('/api/features/flag', {'name':name,'on':True}),names))
    assert all(code == 200 and data['ok'] for code,data in results)
    stored=json.loads(path.read_text())
    assert all(stored[name] is True for name in names)
    assert stored['future'] == {'version':2} and stored['unknown_flag'] is False
    assert effects == []
    before=path.read_bytes()
    assert request('/api/features/flag', {'name':'unknown_flag','on':True})[0] == 400
    assert path.read_bytes() == before


def test_failed_history_and_later_reconcile_do_not_undo_saved_config(api, monkeypatch):
    request, base, effects=api
    def fail(*a, **kw): raise OSError('Fixture secondary failure')
    monkeypatch.setattr(server, '_reconcile_once_async', fail)
    monkeypatch.setattr(configuration_history.History, 'observed_queue', fail)
    code, saved=request('/api/queue/config', {'queue':'SAMPLE','engine':'codex'})
    assert code == 200 and saved['ok']
    assert json.loads((base/'queue-config.json').read_text())['SAMPLE']['engine'] == 'codex'
    assert saved['configuration_history']['state'] == 'unavailable'
    assert any('reconciliation' in warning for warning in saved['warnings'])


def test_external_change_after_temp_fsync_conflicts_without_overwrite(api, monkeypatch):
    request, base, effects=api; path=base/'queue-config.json'; path.write_text('{}')
    original=tx.os.fsync
    def edit(fd):
        original(fd)
        path.write_text('{"EXTERNAL":{"engine":"codex"}}')
    monkeypatch.setattr(tx.os,'fsync',edit)
    code,_=request('/api/queue/config', {'queue':'SAMPLE'})
    assert code == 409 and json.loads(path.read_text()) == {'EXTERNAL':{'engine':'codex'}}
    assert effects == []


def test_directory_sync_failure_reports_saved_state_not_false_failure(api, monkeypatch):
    request, base, effects=api
    def fail(*a): raise OSError('Fixture directory sync failure')
    monkeypatch.setattr(tx,'sync_directory',fail)
    code,result=request('/api/network-config', {'bind_host':'127.0.0.1','trust_tailnet':True})
    assert code == 200 and result['ok'] and result['saved']['warnings']
    assert json.loads((base/'network.json').read_text())['trust_tailnet'] is True


@pytest.mark.parametrize('filename,route,payload', [('queue-config.json','/api/queue/config',{'queue':'SAMPLE'}),('network.json','/api/network-config',{'bind_host':'127.0.0.1'}),('feature-flags.json','/api/features/flag',{'name':'phone_access','on':True})])
def test_temp_fsync_failure_does_not_publish(api, monkeypatch, filename, route, payload):
    request,base,effects=api; path=base/filename; path.write_text('{}')
    def fail(fd): raise OSError('Fixture temp fsync failure')
    monkeypatch.setattr(tx.os,'fsync',fail)
    assert request(route,payload)[0] >= 400
    assert path.read_text() == '{}' and effects == []
    assert not list(base.glob('*.tmp'))


def test_queue_parallel_saves_preserve_other_entries(api):
    request,base,effects=api
    with ThreadPoolExecutor(5) as pool:
        results=list(pool.map(lambda i:request('/api/queue/config',{'queue':'TEST'+str(i),'engine':'codex'}),range(5)))
    assert all(code == 200 for code,data in results)
    assert set(json.loads((base/'queue-config.json').read_text())) == {'TEST'+str(i) for i in range(5)}


def test_native_wt_readers_consume_single_published_document(api, monkeypatch):
    if NATIVE_WT is None: pytest.skip('WatchTower package unavailable')
    request,base,effects=api
    monkeypatch.setattr(server,'_wt_config',NATIVE_WT)
    monkeypatch.setattr(NATIVE_WT,'CONFIG_FILE',base/'queue-config.json')
    monkeypatch.setattr(NATIVE_WT,'CCC_SPAWN_DEFAULTS_FILE',server.SPAWN_DEFAULTS_FILE)
    monkeypatch.setattr(NATIVE_WT,'is_blocked_model',lambda value:False)
    # The canonicalizer is real; its alias catalog is a disposable static input.
    monkeypatch.setattr(NATIVE_WT._models,'aliases',lambda engine:{'short-model':'canonical-model'})
    code,data=request('/api/queue/config',{'queue':'SAMPLE','backend':'github','github_repo':'example/project','queue_label':'team','engine':'codex','model':'short-model','effort':'high','workers':2,'fallback_to_default_worker':True})
    assert code == 200
    assert NATIVE_WT.backend('SAMPLE') == 'github'
    assert NATIVE_WT.github_repo('SAMPLE') == 'example/project'
    assert NATIVE_WT.engine('SAMPLE') == 'codex'
    assert NATIVE_WT.get_queue_config('SAMPLE')['model'] == 'canonical-model'
    assert NATIVE_WT.desired_workers('SAMPLE') == 2
    assert NATIVE_WT.effort('SAMPLE') == 'high'
    assert NATIVE_WT.fallback_to_default_worker('SAMPLE') is True


def test_feature_corrupt_source_is_not_replaced(api):
    request,base,effects=api; path=base/'feature-flags.json'; path.write_text('{bad')
    assert request('/api/features/flag',{'name':'phone_access','on':True})[0] == 400
    assert path.read_text() == '{bad'


def _flag_process(filename,name,barrier):
    from pathlib import Path
    log_parse.FEATURE_FLAGS_FILE=Path(filename)
    barrier.wait(5)
    if not log_parse._save_feature_flag(name,True)['ok']:
        raise AssertionError('Child save failed')


def test_feature_processes_share_advisory_lock(tmp_path):
    if 'fork' not in multiprocessing.get_all_start_methods():
        pytest.skip('fork process test requires POSIX; HTTP concurrency is portable')
    path=tmp_path/'feature-flags.json'; path.write_text('{"future":{"keep":true}}')
    ctx=multiprocessing.get_context('fork'); barrier=ctx.Barrier(3)
    names=list(log_parse._PREVIEW_FLAGS)[:3]
    processes=[ctx.Process(target=_flag_process,args=(str(path),name,barrier)) for name in names]
    try:
        for process in processes: process.start()
        for process in processes: process.join(10)
        assert [process.exitcode for process in processes] == [0,0,0]
        data=json.loads(path.read_text())
        assert all(data[name] is True for name in names) and data['future'] == {'keep':True}
    finally:
        for process in processes:
            if process.is_alive(): process.terminate(); process.join(5)


def test_native_secondary_audit_cannot_target_another_config_directory(api, monkeypatch):
    request,base,effects=api
    other=base/'other'; other.mkdir(); audited=[]
    native=SimpleNamespace(canonical_model=lambda engine,model:model,is_blocked_model=lambda model:False,
        config_path=lambda:other/'queue-config.json', _record_audit=lambda *a:audited.append(a))
    monkeypatch.setattr(server,'_wt_config',native)
    code,saved=request('/api/queue/config',{'queue':'SAMPLE'})
    assert code == 200 and saved['ok'] and audited == []
    assert list(other.iterdir()) == []
    assert any('audit path differs' in warning for warning in saved['warnings'])


def test_no_saved_worker_engine_does_not_guess_alias_context(api):
    request,base,effects=api
    server.SPAWN_DEFAULTS_FILE.write_text('{}')
    code,saved=request('/api/queue/config',{'queue':'SAMPLE','model':'sonnet-5'})
    assert code == 200 and saved['config']['model'] == 'sonnet-5'
    assert 'engine' not in saved['config']
    assert any('native dispatch resolves' in warning for warning in saved['warnings'])


def test_worker_defaults_change_during_alias_preparation_rejects_publication(api, monkeypatch):
    request,base,effects=api; path=base/'queue-config.json'; path.write_text('{}')
    original=tx.os.fsync
    def edit(fd):
        original(fd)
        server.SPAWN_DEFAULTS_FILE.write_text('{"worker_engine":"codex"}')
    monkeypatch.setattr(tx.os,'fsync',edit)
    code,_=request('/api/queue/config',{'queue':'SAMPLE','model':'sonnet-5'})
    assert code == 409 and path.read_text() == '{}' and effects == []


def test_explicit_blocked_confirmation_and_environment_pinned_bind_stay_compatible(api, monkeypatch):
    request,base,effects=api
    code,saved=request('/api/queue/config',{'queue':'SAMPLE','model':'blocked','confirm_blocked_model':True})
    assert code == 200 and saved['config']['model'] == 'blocked'
    assert effects == ['confirmation','log','reconcile']
    monkeypatch.setattr(server,'RUNTIME_NETWORK_INFO',{'env_overrides':{'bind_host':True}})
    code,saved=request('/api/network-config',{'bind_host':'0.0.0.0'})
    assert code == 200 and saved['restart'] is False
    assert 'restart' not in effects
