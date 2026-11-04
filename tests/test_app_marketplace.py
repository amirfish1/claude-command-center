"""Marketplace trust-boundary checks. Never invokes the smoke suite."""
import io
import json
from pathlib import Path
import subprocess

import pytest

from ccc_server import app_marketplace as apps

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def market(tmp_path):
    return apps.Marketplace(tmp_path / 'state', REPO)


@pytest.fixture
def package(tmp_path):
    folder = tmp_path / 'source'
    folder.mkdir()
    (folder / 'app.json').write_text(json.dumps({
        'id': 'sample', 'label': 'Sample', 'description': 'Test app',
        'permissions': ['sessions.read'],
    }))
    (folder / 'index.html').write_text('<h1>Sample</h1>')
    return folder


def install(market, package):
    preview = market.prepare(str(package))
    market.install(preview['token'], True)
    return market.grant('sample')


@pytest.mark.parametrize('ident', ['', '../escape', '/absolute', 'UPPER', 'a/b', 'a%2fb', 'a'*41, 'sessions', None, 4])
def test_invalid_ids(ident):
    with pytest.raises(ValueError):
        apps.app_id(ident)


@pytest.mark.parametrize('permissions', [['spawn'], ['api.*'], ['sessions.read', 'sessions.read'], [None], 'sessions.read'])
def test_unknown_permissions_refused(market, package, permissions):
    data = json.loads((package / 'app.json').read_text())
    data['permissions'] = permissions
    (package / 'app.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        market.prepare(str(package))
    assert not (market.apps / 'sample').exists()


def test_local_install_consent_and_remove(market, package):
    preview = market.prepare(str(package))
    assert preview['permissions'] == ['sessions.read']
    with pytest.raises(PermissionError):
        market.install(preview['token'], False)
    assert not (market.apps / 'sample').exists()
    market.install(preview['token'], True)
    assert (market.apps / 'sample/index.html').read_bytes() == (package / 'index.html').read_bytes()
    assert market.installed()[0]['id'] == 'sample'
    assert market._grant_path('sample').stat().st_mode & 0o777 == 0o600
    market.remove('sample')
    assert market.installed() == []
    assert market.isolated('sample')  # tombstone must never promote an app
    assert list((market.apps / '.removed').glob('sample-*'))


def test_reviewed_bytes_are_frozen(market, package):
    preview = market.prepare(str(package))
    (package / 'index.html').write_text('source changed')
    market.install(preview['token'], True)
    assert (market.apps / 'sample/index.html').read_text() == '<h1>Sample</h1>'


def test_staging_tamper_refused(market, package):
    preview = market.prepare(str(package))
    (market.staged(preview['token']) / 'app/index.html').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        market.install(preview['token'], True)


def test_symlink_refused(market, package, tmp_path):
    outside = tmp_path / 'outside'
    outside.write_text('not app content')
    (package / 'link.js').symlink_to(outside)
    with pytest.raises(ValueError, match='regular'):
        market.prepare(str(package))
    (package / 'link.js').unlink()
    alias = tmp_path / 'alias'
    alias.symlink_to(package, target_is_directory=True)
    with pytest.raises(ValueError, match='Symlink'):
        market.prepare(str(alias))


def test_invalid_manifest_id_and_url(market, package):
    data = json.loads((package / 'app.json').read_text())
    for patch in ({'id': '../escape'}, {'url': '/'}):
        (package / 'app.json').write_text(json.dumps({**data, **patch}))
        with pytest.raises(ValueError):
            market.prepare(str(package))


def test_server_denies_ungranted_bridge_and_revoked_generation(market, package):
    grant = install(market, package)
    calls = []
    callbacks = {'sessions.read': lambda: calls.append('sessions') or [{'state': 'idle'}],
                 'queues.read': lambda: calls.append('queues')}
    assert market.bridge('sample', grant['generation'], 'sessions.read', callbacks) == [{'state': 'idle'}]
    for method in ['queues.read', 'spawn', '/api/sessions/spawn']:
        with pytest.raises(PermissionError):
            market.bridge('sample', grant['generation'], method, callbacks)
    assert calls == ['sessions']
    market.remove('sample')
    new = install(market, package)
    assert new['generation'] != grant['generation']
    with pytest.raises(PermissionError):
        market.bridge('sample', grant['generation'], 'sessions.read', callbacks)


class Handler:
    def __init__(self, body=None):
        self.wfile = io.BytesIO()
        data = json.dumps(body or {}).encode()
        self.rfile = io.BytesIO(data)
        self.headers = {'Host': '127.0.0.1:8090', 'Content-Length': str(len(data))}
        self.response_headers = {}

    def send_json(self, data, status=200):
        self.status = status
        self.data = data

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass


def test_sandbox_on_direct_content_and_legacy_svg(market, package):
    (package / 'image.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    install(market, package)
    for path in ['/marketplace-content/sample/index.html', '/app-static/sample/image.svg']:
        handler = Handler()
        assert apps.handle_get(handler, path, market)
        assert handler.status == 200
        csp = handler.response_headers['Content-Security-Policy']
        assert 'sandbox allow-scripts;' in csp
        assert 'allow-same-origin' not in csp
        assert "connect-src 'none'" in csp
        assert '127.0.0.1:8090/marketplace-content/sample/' in csp


@pytest.mark.parametrize('path', ['/marketplace-content/sample/../outside.js',
    '/marketplace-content/sample/%2e%2e/outside.js', '/app-static/local/../sample/image.svg',
    '/app-static/.removed/sample/image.svg', '/app-static/sample/../../secret.js'])
def test_path_escape_refused(market, path):
    handler = Handler()
    assert apps.handle_get(handler, path, market)
    assert handler.status == 400


def test_local_apps_keep_legacy_route(market):
    assert not apps.handle_get(Handler(), '/view/local', market)
    assert not apps.handle_get(Handler(), '/app-static/local/app.js', market)


def test_missing_grant_fails_closed(market, package):
    install(market, package)
    market._grant_path('sample').unlink()
    assert market.isolated('sample')
    handler = Handler()
    assert apps.handle_get(handler, '/view/sample', market)
    assert handler.status == 403


def test_http_bridge_denial(market, package):
    grant = install(market, package)
    handler = Handler({'id': 'sample', 'generation': grant['generation'], 'method': 'queues.read'})
    assert apps.handle_post(handler, '/api/app-marketplace/bridge', market, {})
    assert handler.status == 403


def test_host_bridge_checks_source_origin_and_permissions():
    # Execute the actual host JS; forbidden calls must never reach fetch.
    script = r'''
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
let receive; let fetches = 0; const replies = [];
const source = {postMessage: message => replies.push(message)};
const frame = {contentWindow: source};
const config = {id:'sample', generation:'test-generation', permissions:['sessions.read']};
vm.runInNewContext(fs.readFileSync('static/app-marketplace-host.js', 'utf8'), {
  document:{getElementById: id => id === 'app-frame' ? frame : {textContent:JSON.stringify(config)}},
  window:{addEventListener: (name, callback) => {receive = callback;}},
  fetch: async () => {fetches++; return {json:async () => ({ok:true, result:[]})};},
  Set, Date, JSON, encodeURIComponent
});
(async () => {
  const call = {type:'ccc.app.call', requestId:'test', method:'queues.read'};
  await receive({source, origin:'null', data:call});
  assert.equal(replies[0].error, 'Permission denied');
  assert.equal(fetches, 0);
  await receive({source:{}, origin:'null', data:{...call,method:'sessions.read'}});
  await receive({source, origin:'http://localhost', data:{...call,method:'sessions.read'}});
  assert.equal(fetches, 0);
  await receive({source, origin:'null', data:{...call,method:'sessions.read', id:'another-app'}});
  assert.equal(fetches, 1);
  assert.equal(replies.at(-1).ok, true);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(['node', '-e', script], cwd=REPO, check=True, timeout=10)


def test_cli_local_path(market, package, capsys):
    assert apps.cli(['install', str(package), '--yes'], market.state, REPO) == 0
    assert 'sessions.read' in capsys.readouterr().out
    assert apps.cli(['list'], market.state, REPO) == 0
    assert 'sample' in capsys.readouterr().out
    assert apps.cli(['remove', 'sample'], market.state, REPO) == 0


def test_cli_decline(market, package, monkeypatch):
    monkeypatch.setattr('builtins.input', lambda _: 'n')
    assert apps.cli(['install', str(package)], market.state, REPO) == 1
    assert market.installed() == []
    assert list((market.store / 'staged').iterdir()) == []


def test_packaged_examples_validate(market):
    assert len(market.index()['apps']) == 3
    for row in market.index()['apps']:
        preview = market.prepare(market.index_source(row['id']))
        assert preview['permissions'] == row['permissions']
        market.install(preview['token'], True)


@pytest.fixture
def http_market(market, monkeypatch):
    import http.server
    import threading
    import server

    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', market.state)
    monkeypatch.setattr(server, 'USER_APPS_DIR', market.apps)
    monkeypatch.setattr(server, '_marketplace_sessions', lambda: [
        {'session_id': 'fixture-session', 'title': 'Fixture session', 'engine': 'test', 'state': 'idle'}])
    monkeypatch.setattr(server, '_marketplace_queues', lambda: [
        {'id': 'fixture-item', 'title': 'Fixture queue item', 'status': 'open'}])
    monkeypatch.delenv('CCC_PROXY_SECRET', raising=False)
    monkeypatch.delenv('CCC_PROXY_SECRET_FILE', raising=False)
    # Real handler and real origin checks, without main() or background workers.
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(httpd.server_port)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_opaque_origin_rejected_by_existing_api_guard(http_market):
    import urllib.error
    import urllib.request
    for route in ['/api/app-marketplace/bridge', '/api/sessions/spawn']:
        request = urllib.request.Request(http_market + route, data=b'{}',
                                         headers={'Origin': 'null', 'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(request, timeout=5)
        assert result.value.code == 403


def test_browser_isolation_and_consent(http_market, market):
    # Uses the repository's Puppeteer harness, never Playwright. CI environments
    # without the optional browser harness still run the pure JS bridge test.
    probe = subprocess.run(['node', '-e', "require('./require-puppeteer')"], cwd=REPO, capture_output=True)
    if probe.returncode:
        pytest.skip('Optional Puppeteer harness is not installed')
    for ident in ('session-summary', 'queue-summary'):
        preview = market.prepare(str(REPO / 'examples/apps' / ident))
        market.install(preview['token'], True)
    subprocess.run(['node', 'tests/app_marketplace_browser.cjs', http_market],
                   cwd=REPO, check=True, timeout=60)


def test_snapshot_projection_uses_one_reader_call(monkeypatch):
    import server
    calls = []
    def census():
        calls.append('census')
        return {'sessions': [{'session_id': 'fixture', 'name': 'Title', 'engine': 'test',
                              'state': 'idle', 'repo_path': '/not-exported', 'prompt': 'not exported'}] * 100}
    monkeypatch.setattr(server, 'build_session_census', census)
    result = server._marketplace_sessions()
    assert calls == ['census']
    assert len(result) == 100
    assert result[0] == {'session_id': 'fixture', 'title': 'Title', 'engine': 'test', 'state': 'idle'}
    def queues(status, lane):
        calls.append('queues')
        return [{'id': 'item', 'title': 'Title', 'status': 'open', 'body': 'not exported'}] * 100
    monkeypatch.setattr(server, '_ux_fixes_list_items_cached', queues)
    assert server._marketplace_queues()[0] == {'id': 'item', 'title': 'Title', 'status': 'open'}
    assert calls == ['census', 'queues']


def test_special_file_refused(market, package):
    import os
    os.mkfifo(package / 'pipe')
    with pytest.raises(ValueError, match='regular'):
        market.prepare(str(package))


def test_existing_app_is_never_overwritten(market, package):
    market.apps.mkdir(parents=True)
    local = market.apps / 'sample'
    local.mkdir()
    (local / 'index.html').write_text('local app')
    with pytest.raises(ValueError, match='already exists'):
        market.prepare(str(package))
    assert (local / 'index.html').read_text() == 'local app'
    assert not market.isolated('sample')


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a/../../escape',
    'a\\escape', 'C:/escape', 'a//escape', './index.html', 'a/../index.html'])
def test_zip_traversal_refused_before_extraction(market, package, tmp_path, name):
    import zipfile
    archive = tmp_path / 'hostile.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('index.html', 'safe first entry')
        output.writestr(name, 'hostile')
    with pytest.raises(ValueError, match='archive path'):
        market.prepare(str(archive))
    assert list((market.store / 'staged').iterdir()) == []
    assert not (tmp_path / 'escape').exists()


def test_zip_symlink_and_duplicate_refused(market, tmp_path):
    import stat
    import zipfile
    archive = tmp_path / 'hostile.zip'
    link = zipfile.ZipInfo('link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(link, '../../outside')
    with pytest.raises(ValueError, match='regular'):
        market.prepare(str(archive))
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('app.json', '{}')
        with pytest.warns(UserWarning):
            output.writestr('app.json', '{}')
    with pytest.raises(ValueError, match='Duplicate'):
        market.prepare(str(archive))


def test_zip_install_and_expanded_size_limit(market, package, tmp_path, monkeypatch):
    import zipfile
    archive = tmp_path / 'sample.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for filename in package.iterdir():
            output.write(filename, filename.name)
    preview = market.prepare(str(archive))
    market.install(preview['token'], True)
    assert market.grant('sample')['permissions'] == ['sessions.read']
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr('large.txt', 'x' * 10001)
    monkeypatch.setattr(apps, 'MAX_BYTES', 10000)
    with pytest.raises(ValueError, match='exceeds'):
        market.prepare(str(archive))


@pytest.mark.parametrize('raw', [
    '{"id":"safe","id":"../escape"}',
    '{"id":"sample","label":"Sample","description":"Test","permissions":[],"permissions":["spawn"]}',
    '{"id":"sample","label":"Sample","description":"Test","icon":{"url":"/api"}}',
    '{"id":"sample","label":"Sample","description":"Test","sandbox":"allow-same-origin"}',
    '{"id":"sample","label":"Sample","description":"Test","scripts":{"install":"echo unsafe"}}',
    '{"id":"sample","label":"Sample","description":"Test","icon":NaN}',
    '[]', 'null', '{broken',
])
def test_malicious_manifest_is_rejected(market, package, raw):
    (package / 'app.json').write_text(raw)
    with pytest.raises(ValueError):
        market.prepare(str(package))
    assert market.installed() == []
    assert list((market.store / 'staged').iterdir()) == []


@pytest.mark.parametrize('ident', ['..', '../sample', '%2e%2e', 'a\\b', '/sample', 'a\x00b'])
def test_traversal_ids_cannot_read_or_remove_grants(market, package, ident):
    install(market, package)
    for operation in (market.grant, market.remove):
        with pytest.raises(ValueError):
            operation(ident)
    assert market.grant('sample')['installed'] is True


def test_removed_archive_and_installed_symlink_unservable(market, package, tmp_path):
    install(market, package)
    outside = tmp_path / 'private.html'
    outside.write_text('private')
    (market.apps / 'sample/link.html').symlink_to(outside)
    for prefix in ('/marketplace-content/', '/app-static/'):
        handler = Handler()
        apps.handle_get(handler, prefix + 'sample/link.html', market)
        assert handler.status == 404
    market.remove('sample')
    archived = next((market.apps / '.removed').iterdir())
    for prefix in ('/marketplace-content/', '/app-static/'):
        handler = Handler()
        apps.handle_get(handler, prefix + '.removed/' + archived.name + '/index.html', market)
        assert handler.status == 400
