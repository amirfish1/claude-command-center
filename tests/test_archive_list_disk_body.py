"""CCC-142: right after a restart a bootstrap /list read gets the last saved
body at once while one background warm builds the fresh one."""
import importlib
import threading
import time
import urllib.error
import urllib.request

import pytest


@pytest.fixture
def srv(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BUILT', set())
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_STATE', {'saved': {}, 'warming': set()})
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 26, 96))
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_BUILD_LOCKS', {})
    monkeypatch.setattr(server, '_SERVER_START_TS', time.time())
    return server


def _wait(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_save_and_load_round_trip(srv):
    key = srv._archive_response_cache_key()
    raw = b'{"conversations": []}' * 100
    assert srv._archive_list_disk_save(key, '7d', raw, None, '"e1"')
    assert not srv._archive_list_disk_save(key, '7d', raw, None, '"e2"')  # throttled
    assert _wait(lambda: srv._archive_list_disk_path(key, '7d').exists())
    got = srv._archive_list_disk_load(key, '7d')
    assert got[0] == raw and got[2] == '"e1"' and got[3] == ''
    assert srv._archive_list_disk_load(key, '7d', now=time.time() + srv._ARCHIVE_LIST_DISK_MAX_AGE_S + 5) is None


def test_candidate_only_for_boot_bootstrap_reads_before_a_build(srv):
    key = srv._archive_response_cache_key()
    srv._archive_list_disk_save(key, '7d', b'{"x": 1}', None, '"e"')
    assert _wait(lambda: srv._archive_list_disk_path(key, '7d').exists())
    assert srv._archive_list_disk_candidate(key, '7d', '') is not None
    assert srv._archive_list_disk_candidate(key, '7d', srv._ARCHIVE_DELTA_NONCE + '.2.a.b') is None  # this process
    assert srv._archive_list_disk_candidate(key, '7d', 'oldnonce.2.a.b') is not None  # page open across restart
    assert srv._archive_list_disk_candidate(key, '1d', '') is None             # nothing saved
    late = srv._SERVER_START_TS + srv._ARCHIVE_LIST_DISK_BOOT_S + 1
    assert srv._archive_list_disk_candidate(key, '7d', '', now=late) is None   # past boot
    srv._ARCHIVE_LIST_BUILT.add((key, '7d'))
    assert srv._archive_list_disk_candidate(key, '7d', '') is None             # fresh body exists


def test_bootstrap_read_serves_disk_body_and_warms_once(srv, monkeypatch):
    key = srv._archive_response_cache_key()
    srv._archive_list_disk_save(key, '7d', b'{"conversations": ["old"]}', None, '"old"')
    assert _wait(lambda: srv._archive_list_disk_path(key, '7d').exists())
    srv._ARCHIVE_LIST_DISK_STATE['saved'].clear()
    gate = threading.Event()
    warms = []

    def source(*a, **kw):
        warms.append(1)
        gate.wait(5)
        return [{'session_id': 'new', 'modified': time.time()}], True, 7

    monkeypatch.setattr(srv, '_archive_list_source_rows_cached', source)
    httpd = srv.http.server.ThreadingHTTPServer(('127.0.0.1', 0), srv.CommandCenterHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=7d&stale_ok=1'
    try:
        for _ in range(3):
            start = time.time()
            with urllib.request.urlopen(url, timeout=5) as r:
                assert r.headers['X-CCC-List-Body'] == 'disk'
                assert b'old' in r.read()
            assert time.time() - start < 2
        assert _wait(lambda: len(warms) == 1)
        gate.set()
        assert _wait(lambda: (key, '7d') in srv._ARCHIVE_LIST_BUILT)
        with urllib.request.urlopen(url, timeout=5) as r:
            assert r.headers.get('X-CCC-List-Body') is None
            assert b'new' in r.read()
        assert len(warms) == 2  # the warm, then this normal read's source fetch
    finally:
        gate.set()
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def test_open_page_holding_the_saved_body_gets_304(srv, monkeypatch):
    key = srv._archive_response_cache_key()
    raw = b'{"conversations": [], "delta_token": "oldnonce.5.c.d"}'
    srv._archive_list_disk_save(key, '7d', raw, None, '"e"')
    assert _wait(lambda: srv._archive_list_disk_path(key, '7d').exists())
    assert srv._archive_list_disk_load(key, '7d')[3] == 'oldnonce.5.c.d'
    gate = threading.Event()
    monkeypatch.setattr(srv, '_archive_list_source_rows_cached', lambda *a, **kw: gate.wait(5) and ([], True, 0))
    httpd = srv.http.server.ThreadingHTTPServer(('127.0.0.1', 0), srv.CommandCenterHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=7d&stale_ok=1&since='
    try:
        try:
            urllib.request.urlopen(base + 'oldnonce.5.c.d', timeout=5)
            raise AssertionError('expected 304')
        except urllib.error.HTTPError as e:
            assert e.code == 304
        with urllib.request.urlopen(base + 'oldnonce.4.x.y', timeout=5) as r:
            assert r.headers['X-CCC-List-Body'] == 'disk'
            assert b'delta_token' in r.read()
    finally:
        gate.set()
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def test_scoped_boot_replays_the_verified_wire_body(srv, monkeypatch):
    from ccc_server import list_encoding

    key = srv._archive_list_body_key(srv._archive_response_cache_key(), True)
    raw = b'{"conversations": [], "padding": "' + b"x" * 1024 + b'"}'
    encoded = b"saved zstd representation"
    monkeypatch.setattr(list_encoding, "zstd_list", lambda body: encoded if body == raw else None)
    assert srv._archive_list_disk_save(key, "7d", raw, None, '"saved"')
    retained = srv._archive_list_disk_path(key, "7d").with_suffix(".zst")
    assert _wait(retained.exists)

    monkeypatch.setattr(srv, "_archive_list_start_warm", lambda *args, **kwargs: False)
    httpd = srv.http.server.ThreadingHTTPServer(("127.0.0.1", 0), srv.CommandCenterHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=7d&row_layouts=1"
    try:
        request = urllib.request.Request(url, headers={"Accept-Encoding": "zstd, gzip"})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.headers["X-CCC-List-Body"] == "disk"
            assert response.headers["Content-Encoding"] == "zstd"
            assert response.headers["ETag"] == '"saved"'
            assert response.read() == encoded

        request = urllib.request.Request(url, headers={"Accept-Encoding": "zstd;q=0, gzip;q=0"})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.headers.get("Content-Encoding") is None
            assert response.read() == raw

        request = urllib.request.Request(url, headers={"Accept-Encoding": "zstd", "If-None-Match": '"saved"'})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=5)
        assert exc.value.code == 304
        assert exc.value.read() == b""
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)

    for damaged in (retained.read_bytes() + b"x", retained.read_bytes().replace(b'"saved"', b'"older"')):
        retained.write_bytes(damaged)
        body, _, etag, _, encoding = srv._archive_list_disk_load(key, "7d", accept_encoding="zstd")
        assert body == raw
        assert etag == '"saved"'
        assert encoding is None
