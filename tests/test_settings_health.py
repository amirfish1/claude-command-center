"""Read-only actual API and evidence contract on disposable state."""
import json
import threading
import time
import urllib.request

import pytest
import server
from ccc_server import engines
from ccc_server.settings_health import project, snapshot


def checks(rows):
    return {r['id']: r for r in rows}


def test_cache_evidence_is_not_a_probe_or_a_readiness_claim():
    engine={'ts':990, 'data':{'engines':[{'engine':'codex','installed':False,'detail':'not-a-secret'}, {'engine':'claude','installed':True}]}}
    service={'ts':990,'payload':{'ok':True,'services':[{'id':'worker','state':'offline'}, {'id':'watchtower','state':'offline','api_ok':False,'api_probe_age_s':2}]}}
    rows=checks(project(engine,service,now=1000))
    assert rows['engine-codex']['state']=='issue'
    assert rows['engine-claude']['state']=='observed'
    assert 'not been tested' in rows['engine-claude']['message']
    assert rows['worker']['state']==rows['watchtower']['state']=='issue'
    assert rows['connections']['state']=='unchecked'
    assert 'not-a-secret' not in json.dumps(rows)
    rows=checks(project(engine,service,now=1100))
    assert rows['engine-codex']['state']==rows['worker']['state']=='stale'


@pytest.mark.parametrize('payload', [None, {'ok':False,'services':[{'id':'worker','state':'online'}]}, {}])
def test_absent_or_failed_service_payload_is_never_healthy(payload):
    rows=checks(project({}, {'ts':990,'payload':payload},now=1000))
    assert rows['worker']['state']==rows['watchtower']['state']=='unchecked'


def test_watchtower_unreachable_requires_actual_recent_probe():
    def row(probe):
        return checks(project({}, {'ts':990,'payload':{'ok':True,'services':[{'id':'watchtower','state':'offline','api_ok':False,**probe}]}},now=1000))['watchtower']
    assert row({})['state']=='unavailable'
    assert row({'api_probe_age_s':40})['state']=='unavailable'
    assert row({'api_probe_age_s':1})['state']=='issue'


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'SPAWN_DEFAULTS_FILE',tmp_path/'spawn-defaults.json')
    monkeypatch.setattr(engines,'_ENGINES_INSTALLED_CACHE',{'ts':0,'data':None})
    monkeypatch.setattr(server,'_system_services_cache',{'ts':0,'payload':None})
    monkeypatch.setattr(server,'_worker_fallback_catalog_models',lambda engine:[{'id':'example-model'}])
    monkeypatch.setattr(server,'_model_policy_blocks',lambda model:model=='blocked-model')
    monkeypatch.setattr(server,'_model_catalog_allows_model',lambda engine,model:model!='wrong-engine-model')
    def forbidden(*a,**kw):pytest.fail('Health must not read credentials, probe, migrate, or write')
    for name in ('byok_get_key','byok_list_profiles','build_ccc_doctor','_get_onboarding_status','_engines_installed','build_system_services','_load_spawn_defaults','_write_spawn_defaults_file'):
        monkeypatch.setattr(server,name,forbidden)
    monkeypatch.setattr(server.subprocess,'run',forbidden)
    monkeypatch.setattr(server.subprocess,'Popen',forbidden)
    return server.SPAWN_DEFAULTS_FILE


def test_no_seed_migration_or_unknown_metadata_loss(isolated):
    assert checks(snapshot(server)['checks'])['policy']['state']=='unchecked'
    assert not isolated.exists()
    raw={'engine':'codex','reasoning_effort':'high','models':{'codex':''},'future':{'keep':1},'model_profiles':{'future-profile':{'keep':2}}}
    isolated.write_text(json.dumps(raw));before=isolated.read_bytes()
    assert checks(snapshot(server)['checks'])['policy']['state']=='observed'
    assert isolated.read_bytes()==before


@pytest.mark.parametrize('change,key',[
    ({'engine':'unsupported'},'engine'),
    ({'engine':'codex','reasoning_effort':'max'},'reasoning_effort'),
    ({'models':{'codex':'wrong-engine-model'}},'models'),
    ({'worker_engine':'codex','worker_model':'blocked-model'},'worker_model'),
    ({'worker_fallback':{'enabled':True,'models':[]}},'worker_fallback'),
    ({'model_profiles':{'fast':{'models':[{'engine':'codex','model':'example-model','effort':'high'}]}}},'model_profiles'),
])
def test_invalid_native_consumer_routes_have_staged_repair(isolated,change,key):
    isolated.write_text(json.dumps(change));before=isolated.read_bytes()
    rows=checks(snapshot(server)['checks']);assert rows['policy-'+key]['state']=='issue'
    assert rows['policy-'+key]['repair']=='policy'
    assert isolated.read_bytes()==before


def test_unreadable_and_malformed_policy_remain_distinct(isolated):
    isolated.write_text('{');assert checks(snapshot(server)['checks'])['policy']['state']=='unavailable'
    isolated.write_text('[]');assert checks(snapshot(server)['checks'])['policy']['state']=='issue'


def test_actual_http_metadata_boundary(isolated):
    class Handler(server.CommandCenterHandler):
        def log_message(self,*a):pass
        def _phone_pin_blocked(self,p):return False
        def _check_proxy_secret(self):return True
    httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_address[1]}/api/settings/health',timeout=5) as response:
            data=json.loads(response.read())
        assert data['generated_at']<=time.time()
        assert checks(data['checks'])['connections']['state']=='unchecked'
        assert not isolated.exists()
        assert str(isolated.parent) not in json.dumps(data)
    finally:httpd.shutdown();httpd.server_close();thread.join(5)


def test_native_install_cache_clock_is_monotonic(isolated,monkeypatch):
    isolated.write_text(json.dumps({'engine':'codex'}))
    monkeypatch.setattr(engines,'_ENGINES_INSTALLED_CACHE',{'ts':time.monotonic()-2,'data':{'engines':[{'engine':'codex','installed':False}]}})
    row=checks(snapshot(server)['checks'])['engine-codex']
    assert row['state']=='issue'
    assert 1<=row['age_seconds']<=3


def test_unused_missing_engine_is_optional_not_a_failure(isolated,monkeypatch):
    isolated.write_text(json.dumps({'engine':'claude'}))
    monkeypatch.setattr(engines,'_ENGINES_INSTALLED_CACHE',{'ts':time.monotonic()-2,'data':{'engines':[{'engine':'codex','installed':False}]}})
    row=checks(snapshot(server)['checks'])['engine-codex']
    assert row['state']=='observed' and 'Not selected' in row['message']


def test_engine_aliases_follow_native_consumer_without_rewriting(isolated,monkeypatch):
    isolated.write_text(json.dumps({'engine':'openai-codex','reasoning_effort':'high','models':{'openai-codex':''}}))
    before=isolated.read_bytes()
    monkeypatch.setattr(engines,'_ENGINES_INSTALLED_CACHE',{'ts':time.monotonic()-2,'data':{'engines':[{'engine':'codex','installed':False}]}})
    rows=checks(snapshot(server)['checks'])
    assert rows['engine-codex']['state']=='issue'
    assert rows['policy']['state']=='observed'
    assert isolated.read_bytes()==before


@pytest.mark.parametrize("stored", [None, {"engine": "codex", "auto_compact_k": 310}])
def test_policy_get_context_uses_readonly_snapshot(tmp_path, monkeypatch, stored):
    defaults_file = tmp_path / "spawn-defaults.json"
    monkeypatch.setattr(server, "SPAWN_DEFAULTS_FILE", defaults_file)
    monkeypatch.delenv("CCC_CODEX_CONTEXT_TOKENS", raising=False)
    monkeypatch.delenv("CCC_WORKER_PROCESS", raising=False)
    if stored is not None:
        defaults_file.write_text(json.dumps(stored))
    before = defaults_file.read_bytes() if defaults_file.exists() else None
    original = server._load_spawn_defaults
    calls = []
    def read_only(*args, **kwargs):
        assert kwargs.get("persist") is False
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(server, "_load_spawn_defaults", read_only)
    class Handler(server.CommandCenterHandler):
        def log_message(self, *args): pass
        def _phone_pin_blocked(self, path): return False
        def _check_proxy_secret(self): return True
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/spawn-defaults?policy=1", timeout=5) as response:
            data = json.loads(response.read())
        assert data["ok"] and calls
        assert data["codex_context_tokens"] == (310000 if stored else 250000)
        assert (defaults_file.read_bytes() if defaults_file.exists() else None) == before
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join(5)
