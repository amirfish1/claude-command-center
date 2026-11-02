"""Existing service API boundaries: temporary indexes and an inert secret backend."""
import json
import threading
import urllib.error
import urllib.request

import pytest
import server
from ccc_server import byok, secret_store, vault


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(byok, '_state_dir', lambda: tmp_path / 'providers')
    monkeypatch.setattr(vault, '_vault_state_dir', lambda: tmp_path / 'credentials')
    monkeypatch.setattr(secret_store, 'keychain_available', lambda: True)
    writes = []
    monkeypatch.setattr(secret_store, 'keychain_set', lambda service, account, value: writes.append((service, account)) is None)
    monkeypatch.setattr(secret_store, 'keychain_delete', lambda *args: None)

    def forbidden(*args, **kwargs):
        pytest.fail('Directory must never read credentials or launch a process')
    monkeypatch.setattr(secret_store, 'keychain_get', forbidden)
    monkeypatch.setattr(server, 'byok_get_key', forbidden)
    monkeypatch.setattr(server.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(server.subprocess, 'run', forbidden)
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, 'byok_usage_summary', lambda **kwargs: {'spawns': 0})
    byok.byok_set_key('example-profile', 'openai', 'not-a-secret')
    byok.byok_set_key('example-profile', 'openrouter', 'not-a-secret')
    vault.vault_create_entry('example-login', 'not-a-secret', kind='login', service='Example service')
    defaults = tmp_path / 'spawn-defaults.json'
    defaults.write_text(json.dumps({'key_profile': 'example-profile', 'models': {'kilo': 'byok/openai/example-model'}, 'future_policy': {'keep': True}}))
    class Handler(server.CommandCenterHandler):
        def log_message(self, *args): pass
        def _phone_pin_blocked(self, path): return False
        def _check_proxy_secret(self): return True
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    def request(route, data=None, origin=None):
        req = urllib.request.Request('http://127.0.0.1:' + str(httpd.server_address[1]) + route,
            data=None if data is None else json.dumps(data).encode(),
            headers={'Content-Type': 'application/json', **({'Origin': origin} if origin else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    yield request, defaults, writes, tmp_path
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_directory_metadata_never_reads_or_returns_values(api):
    request, defaults, writes, _ = api
    before = defaults.read_bytes(), list(writes)
    code, profiles = request('/api/byok/profiles')
    assert code == 200
    assert profiles['profiles'] == [{'name': 'example-profile', 'providers': ['openai', 'openrouter']}]
    code, credentials = request('/api/vault')
    assert code == 200
    assert credentials['entries'][0]['name'] == 'example-login'
    assert credentials['entries'][0]['saved'] is True
    assert len(credentials['byok']) == 2
    for data in (profiles, credentials):
        assert 'not-a-secret' not in json.dumps(data)
        assert 'value' not in data
    assert (defaults.read_bytes(), writes) == before


def test_replace_preserves_profile_model_references_and_other_provider(api):
    request, defaults, writes, _ = api
    before = defaults.read_bytes()
    code, result = request('/api/byok/keys', {'profile': 'example-profile', 'provider': 'openai', 'key': 'not-a-secret-replacement'})
    assert code == 200 and result['ok']
    assert 'not-a-secret' not in json.dumps(result)
    assert request('/api/byok/profiles')[1]['profiles'][0]['providers'] == ['openai', 'openrouter']
    assert defaults.read_bytes() == before
    assert writes[-1] == ('ccc-byok', 'example-profile:openai')


def test_general_credential_metadata_edit_keeps_value_without_reading_it(api):
    request, defaults, writes, _ = api
    before = defaults.read_bytes(), list(writes)
    code, result = request('/api/vault/entries/update', {'name': 'example-login', 'service': 'Example updated'})
    assert code == 200 and result['ok']
    assert result['entry']['service'] == 'Example updated'
    assert 'value' not in result['entry']
    assert (defaults.read_bytes(), writes) == before


@pytest.mark.parametrize('route,payload', [
    ('/api/byok/keys', {'profile': 'example-profile', 'provider': 'openai', 'key': 'not-a-secret'}),
    ('/api/byok/keys/delete', {'profile': 'example-profile', 'provider': 'openai'}),
    ('/api/vault/entries', {'name': 'example-new', 'value': 'not-a-secret'}),
    ('/api/vault/entries/update', {'name': 'example-login', 'service': 'Example updated'}),
    ('/api/vault/entries/delete', {'name': 'example-login'}),
])
def test_native_mutations_still_reject_untrusted_origins_before_writes(api, route, payload):
    request, defaults, writes, base = api
    before = defaults.read_bytes(), list(writes), {p: p.read_bytes() for p in base.rglob('index.json')}
    assert request(route, payload, 'https://untrusted.example')[0] == 403
    assert (defaults.read_bytes(), writes, {p: p.read_bytes() for p in base.rglob('index.json')}) == before


def test_unknown_provider_rejected_without_mutating_either_store(api):
    request, defaults, writes, base = api
    before = defaults.read_bytes(), list(writes), {p: p.read_bytes() for p in base.rglob('index.json')}
    assert request('/api/byok/keys', {'profile': 'example-profile', 'provider': 'unknown-example', 'key': 'not-a-secret'})[0] == 400
    assert (defaults.read_bytes(), writes, {p: p.read_bytes() for p in base.rglob('index.json')}) == before
