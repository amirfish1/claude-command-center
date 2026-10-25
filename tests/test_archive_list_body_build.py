"""CCC-142: the /list body is built once per snapshot, and projected rows
never share mutable containers with the cached snapshot."""
import importlib
import json
import urllib.error
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


def _row(sid, title, ts=None):
    return {'session_id': sid, 'title': title, 'modified': ts or time.time()}


def test_identical_refresh_keeps_snapshot_version(monkeypatch):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    gen = server._archive_serve_generation
    assert server._archive_serve_cache_store('k', [_row('a', 'A', 1), _row('b', 'B', 2)], gen)
    first = server._archive_serve_cache['k']
    ver = first['ver']
    # A rebuild that produced equal rows (new dicts) is not a new snapshot.
    assert server._archive_serve_cache_store('k', [_row('a', 'A', 1), _row('b', 'B', 2)], gen)
    assert server._archive_serve_cache['k'] is first and first['ver'] == ver
    # One changed row: new version, unchanged rows keep their old dicts.
    old_a = first['rows'][0]
    assert server._archive_serve_cache_store('k', [_row('a', 'A', 1), _row('b', 'B2', 2)], gen)
    entry = server._archive_serve_cache['k']
    assert entry['ver'] > ver
    assert entry['rows'][0] is old_a
    assert entry['track_since'] == ver
    assert entry['row_rev'] == {'b': entry['ver']}
    # Removal drops the row's revision; the id list carries the removal.
    assert server._archive_serve_cache_store('k', [_row('b', 'B2', 2)], gen)
    assert 'a' not in server._archive_serve_cache['k']['row_rev']


def test_delta_list_sends_only_changed_rows(monkeypatch):
    server = importlib.import_module('server')
    key = server._archive_response_cache_key()
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', {})
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_BUILD_LOCKS', {})
    gen = server._archive_serve_generation
    now = time.time()
    server._archive_serve_cache_store(key, [_row('a', 'A', now), _row('b', 'B', now)], gen)

    def source(*a, **kw):
        sc = server._archive_serve_cache[key]
        return sc['rows'], True, sc['ver']

    monkeypatch.setattr(server, '_archive_list_source_rows_cached', source)
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=7d&stale_ok=1'

    def get(url):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    try:
        status, full = get(base)
        assert status == 200 and not full.get('delta')
        token = full['delta_token']
        assert [r['session_id'] for r in full['conversations']] == ['a', 'b']
        # Nothing changed: 304 without a body.
        assert get(base + '&since=' + token)[0] == 304
        # One row changed and one appeared: only those two rows ship.
        server._archive_serve_cache_store(
            key, [_row('c', 'C', now), _row('a', 'A', now), _row('b', 'B2', now)], gen)
        status, delta = get(base + '&since=' + token)
        assert status == 200 and delta['delta'] is True
        assert delta['ids'] == ['c', 'a', 'b']
        assert sorted(r['session_id'] for r in delta['conversations']) == ['b', 'c']
        assert delta['delta_token'] != token
        # A token from another process (or garbage) gets a full body.
        status, again = get(base + '&since=deadbeef.1.x.0')
        assert status == 200 and not again.get('delta') and len(again['conversations']) == 3
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_delta_list_survives_overlay_rows_coming_and_going(monkeypatch):
    server = importlib.import_module('server')
    key = server._archive_response_cache_key()
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    gen = server._archive_serve_generation
    now = time.time()
    server._archive_serve_cache_store(key, [_row('a', 'A', now)], gen)
    base = server._archive_serve_cache[key]['rows']
    token = server._archive_list_delta_state(key, base, '7d', True)['token']
    # An in-memory ACP session appears: still a delta, carrying that row.
    rows = base + [_row('acp', 'Live', now)]
    state = server._archive_list_delta_state(key, rows, '7d', True)
    assert state['token'] != token
    delta = server._archive_list_delta_payload(state, token, rows, window='7d')
    assert delta['ids'] == ['a', 'acp']
    assert [r['session_id'] for r in delta['conversations']] == ['acp']
    # An overlay duplicating a snapshot id can't merge by id: full body.
    dup = base + [_row('a', 'Dup', now)]
    state = server._archive_list_delta_state(key, dup, '7d', True)
    assert server._archive_list_delta_payload(state, token, dup, window='7d') is None


def test_lifecycle_restamp_keeps_delta_tracking(monkeypatch):
    # Archive/trash/lane mutations rebuild the snapshot from sidecars; that
    # used to drop delta tracking, so every client got a full body.
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: ({'b'}, set()))
    monkeypatch.setattr(server, '_load_session_lane_overrides', lambda: {})
    gen = server._archive_serve_generation
    rows = [dict(_row('a', 'A', 1), archived=False, trashed=False, all_lane_override=''),
            dict(_row('b', 'B', 2), archived=False, trashed=False, all_lane_override='')]
    server._archive_serve_cache_store('k', rows, gen)
    since = server._archive_serve_cache['k']['ver']
    server._restamp_archive_serve_cache_after_mutation()
    entry = server._archive_serve_cache['k']
    assert entry['track_since'] == since
    assert entry['row_rev'] == {'b': entry['ver']}
