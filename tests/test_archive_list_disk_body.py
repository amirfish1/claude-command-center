"""CCC-142: right after a restart a bootstrap /list read gets the last saved
body at once while one background warm builds the fresh one."""
import importlib
import threading
import time
import urllib.request

import pytest


@pytest.fixture
def srv(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BUILT', set())
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_STATE', {'saved': {}, 'warming': set()})
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', {})
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
    assert got[0] == raw and got[2] == '"e1"'
    assert srv._archive_list_disk_load(key, '7d', now=time.time() + srv._ARCHIVE_LIST_DISK_MAX_AGE_S + 5) is None


def test_candidate_only_for_boot_bootstrap_reads_before_a_build(srv):
    key = srv._archive_response_cache_key()
    srv._archive_list_disk_save(key, '7d', b'{"x": 1}', None, '"e"')
    assert _wait(lambda: srv._archive_list_disk_path(key, '7d').exists())
    assert srv._archive_list_disk_candidate(key, '7d', '') is not None
    assert srv._archive_list_disk_candidate(key, '7d', 'tok') is None          # delta poll
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
