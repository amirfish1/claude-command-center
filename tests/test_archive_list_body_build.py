"""CCC-142: the /list body is built once per snapshot, and projected rows
never share mutable containers with the cached snapshot."""
import importlib
import threading
import time
import urllib.request


def test_projection_isolates_flat_and_nested_containers():
    server = importlib.import_module('server')
    row = {
        'session_id': 'one',
        'cost_breakdown_usd': {'input': 1.5},
        'hermes_lineage_session_ids': ['a', 'b'],
        'session_state': {'todos': [{'text': 'x'}]},
    }
    out = server._archive_list_project_row(row)
    assert out['cost_breakdown_usd'] == {'input': 1.5}
    out['cost_breakdown_usd']['input'] = 9
    out['hermes_lineage_session_ids'].append('c')
    out['session_state']['todos'][0]['text'] = 'changed'
    assert row['cost_breakdown_usd'] == {'input': 1.5}
    assert row['hermes_lineage_session_ids'] == ['a', 'b']
    assert row['session_state'] == {'todos': [{'text': 'x'}]}


def test_concurrent_polls_of_one_snapshot_build_the_body_once(monkeypatch):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', {})
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_BUILD_LOCKS', {})
    monkeypatch.setattr(
        server, '_archive_list_source_rows_cached',
        lambda *a, **kw: ([{'session_id': 'one'}], True, 11),
    )
    builds = []
    real_payload = server._archive_list_payload

    def slow_payload(*a, **kw):
        builds.append(1)
        time.sleep(0.3)
        return real_payload(*a, **kw)

    monkeypatch.setattr(server, '_archive_list_payload', slow_payload)
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=all&stale_ok=1'
    etags = []

    def poll():
        with urllib.request.urlopen(url, timeout=10) as response:
            etags.append(response.headers['ETag'])

    try:
        polls = [threading.Thread(target=poll) for _ in range(4)]
        for p in polls:
            p.start()
        for p in polls:
            p.join(timeout=10)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert len(etags) == 4 and len(set(etags)) == 1
    assert len(builds) == 1


def test_list_and_live_paths_do_not_wait_on_a_held_acp_lock():
    # Boot loads ACP state under _ACP_LOCK for ~18-30s; /list's overlay and
    # live discovery used to queue behind it.
    server = importlib.import_module('server')
    if not server._ACP_HARNESSES:
        return
    held, release = threading.Event(), threading.Event()

    def holder():
        with server._ACP_LOCK:
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    held.wait(2)
    try:
        start = time.monotonic()
        server._archive_overlay_acp_sessions([])
        server._discover_live_session_ids()
        assert time.monotonic() - start < 4
    finally:
        release.set()
        t.join(timeout=5)
