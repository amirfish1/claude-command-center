"""Repeated app boots reuse page work while edits remain immediately visible."""
import gzip
from pathlib import Path

from tests.test_static_asset_caching import _get, _server


def test_repeat_index_load_does_not_read_or_transform_unchanged_source(tmp_path, monkeypatch):
    server = _server()
    source = tmp_path / 'index.html'
    source.write_text('<body><script src="/static/demo.js"></script></body>')
    asset = tmp_path / 'demo.js'
    asset.write_text('first')
    monkeypatch.setattr(server, '_INDEX_HTML_PATH', source)
    monkeypatch.setattr(server, 'STATIC_DIR', tmp_path)
    first = server._load_index_html()
    read = Path.read_text
    def checked(candidate, *args, **kwargs):
        assert candidate != source, 'unchanged page source was reread'
        return read(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', checked)
    assert server._load_index_html() == first
    monkeypatch.setattr(Path, 'read_text', read)
    source.write_text('<body>edited</body>')
    assert 'edited' in server._load_index_html()


def test_asset_version_and_selected_app_changes_are_visible(tmp_path, monkeypatch):
    server = _server()
    source = tmp_path / 'index.html'
    source.write_text('<html lang="en"><head><meta charset="utf-8">'
                      '<script src="/static/app-shell-boot.js"></script>'
                      '<script src="/static/demo.js"></script></head></html>')
    asset = tmp_path / 'demo.js'
    asset.write_text('first')
    monkeypatch.setattr(server, '_INDEX_HTML_PATH', source)
    monkeypatch.setattr(server, 'STATIC_DIR', tmp_path)
    app = {'id':'demo','nav':'/view/demo','label':'First'}
    first = server._load_index_html(app_shell=True, selected_app=app)
    import os
    stamp = int(asset.stat().st_mtime) + 2
    os.utime(asset, (stamp, stamp))
    after = server._load_index_html(app_shell=True, selected_app=app)
    assert first != after
    assert f'/static/demo.js?v={stamp}' in after
    changed = server._load_index_html(app_shell=True, selected_app={**app, 'label':'Second'})
    assert 'Second' in changed and 'First' not in changed
    asset.unlink()
    assert f'/static/demo.js?v={server.__version__}' in server._load_index_html(app_shell=True, selected_app=app)


def test_repeated_html_encoding_is_reused_without_browser_caching(tmp_path, monkeypatch):
    server = _server()
    source = tmp_path / 'index.html'
    source.write_text('<body>' + 'page data ' * 500 + '</body>')
    monkeypatch.setattr(server, '_INDEX_HTML_PATH', source)
    calls = []
    compress = gzip.compress
    monkeypatch.setattr(server.gzip, 'compress', lambda data, **kw: (calls.append(data), compress(data, **kw))[1])
    for _ in range(3):
        status, headers, body = _get(server, '/?ccc_app=sessions', {'Accept-Encoding':'gzip'})
        assert status == 200 and gzip.decompress(body).decode() == source.read_text().replace('<body>', '<body data-repo="">', 1)
        # CCC-718: the dashboard page revalidates on every load (no-cache).
        assert 'no-cache' in headers['cache-control'] and headers['etag']
    assert len(calls) == 1
    status, headers, body = _get(server, '/?ccc_app=sessions')
    assert status == 200 and 'content-encoding' not in headers
    assert body.startswith(b'<body data-repo="">')
    source.write_text('<body>' + 'changed data ' * 500 + '</body>')
    _, _, body = _get(server, '/?ccc_app=sessions', {'Accept-Encoding':'gzip'})
    assert b'changed data' in gzip.decompress(body)
    assert len(calls) == 2


def test_transform_dependencies_invalidate_without_source_changes(tmp_path, monkeypatch):
    server = _server()
    source = tmp_path / 'index.html'
    source.write_text('<head><meta charset="utf-8"></head><body class="voice-call-btn-off"></body>')
    added = tmp_path / 'app-embed-boot.js'
    added.write_text('// boot')
    monkeypatch.setattr(server, '_INDEX_HTML_PATH', source)
    monkeypatch.setattr(server, 'STATIC_DIR', tmp_path)
    first = server._load_index_html(app_embed=True)
    import os
    stamp = int(added.stat().st_mtime) + 2
    os.utime(added, (stamp, stamp))
    assert first != server._load_index_html(app_embed=True)
    assert f'/static/app-embed-boot.js?v={stamp}' in server._load_index_html(app_embed=True)
    assert 'app-embed-boot.js' not in server._load_index_html()


def test_unchanged_dashboard_page_revalidates_without_a_body(tmp_path, monkeypatch):
    """CCC-718: a phone re-downloaded the ~80 KB page on every boot."""
    server = _server()
    source = tmp_path / 'index.html'
    source.write_text('<head></head><body>' + 'page data ' * 500 + '</body>')
    monkeypatch.setattr(server, '_INDEX_HTML_PATH', source)
    status, headers, body = _get(server, '/', {'Accept-Encoding': 'gzip'})
    assert status == 200 and body and headers['cache-control'] == 'private, no-cache'
    etag = headers['etag']
    # Same page, same encoding: 304, no body, still revalidating next time.
    status, again, body = _get(server, '/', {'Accept-Encoding': 'gzip', 'If-None-Match': 'W/' + etag})
    assert status == 304 and body == b''
    assert again['etag'] == etag and again['cache-control'] == 'private, no-cache'
    assert 'html-304' in again['server-timing'] and 'content-length' not in again
    # A different encoding is a different representation.
    status, plain, body = _get(server, '/', {'If-None-Match': etag})
    assert status == 200 and plain['etag'] != etag and body.startswith(b'<head>')
    # Any change to the sent bytes (source, stamps, injected globals) is a new tag.
    source.write_text('<head></head><body>' + 'changed data ' * 500 + '</body>')
    status, changed, body = _get(server, '/', {'Accept-Encoding': 'gzip', 'If-None-Match': etag})
    assert status == 200 and b'changed data' in gzip.decompress(body) and changed['etag'] != etag
    status, _, _ = _get(server, '/', {'Accept-Encoding': 'gzip', 'If-None-Match': etag},
                        http_port=9999)
    assert status == 200


def test_app_pages_stay_no_store(tmp_path, monkeypatch):
    """App pages embed startup data; only the dashboard page revalidates."""
    server = _server()
    calls = []
    real = server.CommandCenterHandler._send_prepared_html
    def spy(self, prepared, **kw):
        calls.append(kw)
        return real(self, prepared, **kw)
    monkeypatch.setattr(server.CommandCenterHandler, '_send_prepared_html', spy)
    monkeypatch.setattr(server._conv_reader, 'forward_page', lambda *a, **k: None)
    monkeypatch.setattr(server, '_load_application_html', lambda *a, **k: '<body>' + 'x' * 2000 + '</body>')
    status, headers, _ = _get(server, '/view/demo')
    assert status == 200 and headers['cache-control'] == 'no-store, must-revalidate'
    assert 'etag' not in headers and calls and not calls[-1].get('revalidate')


def test_service_worker_has_no_fetch_listener():
    """CCC-718: any fetch listener, even a no-op, routes every WebKit request
    through the worker. Notification clicks still need the worker itself."""
    import re
    source = (Path(__file__).resolve().parent.parent / 'static' / 'sw.js').read_text()
    code = re.sub(r'//[^\n]*', '', source)
    assert "addEventListener('notificationclick'" in code
    assert not re.search(r"""addEventListener\(\s*['"]fetch['"]|\bonfetch\b""", code)
