"""Native connection API contracts against disposable metadata, never real CLIs."""
import json
import threading
import urllib.error
import urllib.request

import pytest
import server


@pytest.fixture
def api(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'COMMAND_CENTER_STATE_DIR',tmp_path)
    monkeypatch.setattr(server,'SPAWN_DEFAULTS_FILE',tmp_path/'spawn-defaults.json')
    state_file=tmp_path/'detected.json'
    state_file.write_text(json.dumps({'claude':{'installed':True,'auth':True},'codex':{'installed':False,'auth':False},'kilo':{'installed':True,'auth':None}}))
    calls=[]
    def inventory(fresh=False):
        calls.append(('inventory',fresh))
        return {'engines':[{'engine':engine,'kind':'spawn','installed':row['installed'],'detail':'/example/bin/'+engine} for engine,row in json.loads(state_file.read_text()).items()]}
    def onboarding():
        return {'completed':True,'clis':{engine:{'available':row['installed'],'logged_in':row['auth']} for engine,row in json.loads(state_file.read_text()).items() if row['auth'] is not None}}
    monkeypatch.setattr(server,'_engines_installed',inventory)
    monkeypatch.setattr(server,'_get_onboarding_status',onboarding)
    monkeypatch.setattr(server,'byok_list_profiles',lambda:[])
    monkeypatch.setattr(server,'read_graphql_quota',lambda:{'ok':False})
    monkeypatch.setattr(server,'build_doctor_instances',lambda:{'status':'ok'})
    monkeypatch.setattr(server,'build_memory_doctor',lambda:{})
    monkeypatch.setattr(server,'_model_policy_health',lambda:{})
    monkeypatch.setattr(server,'_opencode_configured_providers',lambda:[])
    def forbidden(*args,**kwargs):pytest.fail('Setup status must not access secrets or launch a process')
    monkeypatch.setattr(server,'byok_get_key',forbidden)
    monkeypatch.setattr(server.subprocess,'Popen',forbidden)
    monkeypatch.setattr(server.subprocess,'run',forbidden)
    class Handler(server.CommandCenterHandler):
        def log_message(self,*args):pass
        def _phone_pin_blocked(self,path):return False
        def _check_proxy_secret(self):return True
    httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    def request(path,body=None,origin=None):
        req=urllib.request.Request(f'http://127.0.0.1:{httpd.server_address[1]}'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json',**({'Origin':origin} if origin else {})})
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())
    yield request,state_file,calls,tmp_path
    httpd.shutdown();httpd.server_close();thread.join(timeout=5)


def test_doctor_retains_unknown_auth_and_describes_binary_only_check(api):
    request,_,_,base=api
    code,data=request('/api/engines/doctor');assert code==200
    assert data['engines']['kilo']['auth_present'] is None
    assert data['engines']['kilo']['auth_source']=='no-probe-implemented'
    assert data['engines']['claude']['auth_present'] is True
    assert data['engines']['codex']['cli_present'] is False
    assert data['engines']['claude']['smoke_dry_run']['ok'] is True
    assert 'no subprocess' in data['engines']['claude']['smoke_dry_run']['note']
    assert not (base/'spawn-defaults.json').exists()


def test_recheck_refreshes_inventory_without_changing_configuration(api):
    request,file,calls,base=api
    original=file.read_bytes()
    request('/api/engines/doctor?fresh=1')
    assert ('inventory',True) in calls
    assert file.read_bytes()==original
    assert not (base/'spawn-defaults.json').exists()


def test_native_terminal_route_returns_real_helper_failure_without_writes(api,monkeypatch):
    request,_,calls,base=api
    def unavailable(engine):
        calls.append(('terminal',engine));return {'ok':False,'error':'Visible terminal unsupported in fixture'}
    monkeypatch.setattr(server,'_launch_install_terminal',unavailable)
    code,data=request('/api/onboarding/install-terminal',{'engine':'codex'})
    assert code==200 and data['ok'] is False
    assert ('terminal','codex') in calls
    assert not (base/'spawn-defaults.json').exists()


def test_cross_origin_action_rejected_before_native_helper(api,monkeypatch):
    request,_,_,_=api
    def forbidden(engine):pytest.fail('Origin rejection must precede native terminal helper')
    monkeypatch.setattr(server,'_launch_install_terminal',forbidden)
    assert request('/api/onboarding/install-terminal',{'engine':'codex'},origin='https://example.test')[0]==403


def test_unregistered_inline_auth_engine_returns_unsupported_without_a_process(api):
    request,_,_,_=api
    code,data=request('/api/onboarding/login/start',{'engine':'kilo'})
    assert code==400 and data['ok'] is False
    assert 'Unknown engine' in data['error']
