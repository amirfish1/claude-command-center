"""Voice setup uses existing config/profile APIs without media or secret access."""
import json
import threading
import urllib.error
import urllib.request

import pytest
import server
from ccc_server import realtime_voice as voice


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, 'VOICE_STATE_DIR', tmp_path)
    monkeypatch.setattr(voice, 'VOICE_CONFIG_FILE', tmp_path/'voice.json')
    monkeypatch.setattr(server, 'byok_list_profiles', lambda: [
        {'name': 'Demo profile', 'providers': ['openai'], 'ignored': 'not-a-profile-name'},
        {'name': 'Other provider', 'providers': ['anthropic']}])
    def forbidden(*args, **kwargs): pytest.fail('Setup must not retrieve credentials, launch processes or start media')
    monkeypatch.setattr(server, 'byok_get_key', forbidden)
    monkeypatch.setattr(server, '_resolve_codex_bin', forbidden)
    monkeypatch.setattr(server, 'voice_start', forbidden)
    class Handler(server.CommandCenterHandler):
        def log_message(self, *args): pass
        def _phone_pin_blocked(self, path): return False
        def _check_proxy_secret(self): return True
    httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    def request(path='/api/voice/config', body=None, origin=None):
        req=urllib.request.Request(f'http://127.0.0.1:{httpd.server_address[1]}'+path,
                                   data=None if body is None else json.dumps(body).encode(),
                                   headers={'Content-Type':'application/json', **({'Origin':origin} if origin else {})})
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())
    yield request,voice.VOICE_CONFIG_FILE
    httpd.shutdown();httpd.server_close();thread.join(timeout=5)


def test_setup_reads_never_create_configuration_or_access_provider_keys(api):
    request, file=api
    code,data=request();assert code==200
    assert data['openai_profiles']==['Demo profile']
    assert 'ignored' not in json.dumps(data)
    assert not file.exists()
    code,catalog=request('/api/voice/voices');assert code==200
    assert 'cove' in catalog['voices']['v1']
    assert not file.exists()


def test_partial_call_default_save_preserves_other_effective_config_and_missing_profile(api):
    request,file=api
    original={**voice._default_config(),'voice':'coral','profile':'Missing profile','max_minutes':25,'show_mobile_btn':False}
    file.write_text(json.dumps(original));before=file.read_bytes()
    assert request()[1]['config']==original
    assert file.read_bytes()==before
    code,data=request(body={'max_minutes':30});assert code==200
    assert data['config']=={**original,'max_minutes':30}
    assert json.loads(file.read_text())=={**original,'max_minutes':30}


def test_failed_atomic_config_publication_leaves_existing_file(api, monkeypatch):
    request,file=api
    file.write_text(json.dumps(voice._default_config()));before=file.read_bytes()
    original=type(file).replace
    def fail(path,target):
        if target==file:raise OSError('simulated publication failure')
        return original(path,target)
    monkeypatch.setattr(type(file),'replace',fail)
    code,data=request(body={'max_minutes':30});assert code==400 and not data['ok']
    assert file.read_bytes()==before


def test_cross_origin_and_malformed_call_config_rejected_before_writes(api):
    request,file=api
    assert request(body={'max_minutes':30},origin='https://example.test')[0]==403
    assert not file.exists()
    assert request(body=['invalid'])[0]==400
    assert not file.exists()
