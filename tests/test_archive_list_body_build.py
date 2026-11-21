"""CCC-142: the /list body is built once per snapshot, and projected rows
never share mutable containers with the cached snapshot."""
from concurrent.futures import ThreadPoolExecutor
import gzip
import importlib
import json
import socket
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


def test_concurrent_polls_of_one_snapshot_build_the_body_once(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 26, 96))
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_BUILD_LOCKS', {})
    monkeypatch.setattr(
        server, '_archive_list_source_rows_cached',
        lambda *a, **kw: ([{'session_id': 'one'}], True, 11),
    )
    builds = []
    diagnostics = []
    monkeypatch.setattr(server, 'record_diag',
                        lambda kind, ms, detail: diagnostics.append(detail)
                        if kind == 'list_server' else None)
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
    assert len(diagnostics) == 4
    cache_diagnostics = [d['body_cache'] for d in diagnostics]
    assert sum(d['hit'] for d in cache_diagnostics) == 3
    assert sorted(d['reason'] for d in cache_diagnostics) == ['hit', 'hit', 'hit', 'version-miss']
    assert all(d['version'] == 11 and d['budget_bytes'] == 1 << 26
               for d in cache_diagnostics)
    projected = [d['projection'] for d in diagnostics if d['projection']]
    assert len(projected) == 1
    assert set(projected[0]) == {'inputs', 'window', 'rows', 'titles_drivers', 'topics',
                                 'topic_cache', 'tickets'}
    assert 'reason' in projected[0]['topic_cache']
    for detail in diagnostics:
        timings = [value for name, value in detail['projection'].items() if name != 'topic_cache']
        for phase in [detail['delta_state'], *timings]:
            assert set(phase) == {'wall_ms', 'cpu_ms'}
            assert phase['wall_ms'] >= 0 and phase['cpu_ms'] >= 0


def test_body_cache_diagnostics_track_actual_key_and_unversioned_bypass(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    from ccc_server import continuation_topics
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 20, 8))
    graph = [1, 2]
    monkeypatch.setattr(server._session_graph, 'projection_revision', lambda: tuple(graph))
    monkeypatch.setattr(continuation_topics, 'revision', lambda: 3)
    builds = []

    def payload():
        builds.append(True)
        return {'conversations': [{'session_id': 'private-session', 'first_message': 'private-prompt'}]}

    def read(version):
        detail = {}
        result = server._archive_list_body_get_or_build('key', '7d', True, payload,
                                                       version, diagnostics=detail)
        assert 'private' not in json.dumps(detail)
        return result, detail

    first, miss = read((7, 'overlay-hash'))
    again, hit = read((7, 'overlay-hash'))
    assert first == again and len(builds) == 1
    assert miss['version'] == (7, 'overlay-hash') and miss['graph'] == (1, 2)
    assert miss['topics'] == 3 and miss['reason'] == 'version-miss'
    assert hit['hit'] and hit['bytes'] > 0 and hit['entries'] == 1
    graph[1] += 1
    _, changed = read((7, 'overlay-hash'))
    assert not changed['hit'] and changed['graph'] == (1, 3) and len(builds) == 2
    for _ in range(2):
        _, unversioned = read(0)
        assert not unversioned['hit'] and unversioned['reason'] == 'unversioned'
    assert len(builds) == 4


def test_history_encoding_reuses_bytes_after_fresh_projection(monkeypatch, tmp_path):
    from ccc_server import archive_list_replay as replay, list_encoding

    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(list_encoding, '_HISTORY_CACHE', server.ByteBudgetCache(1 << 20, 4))
    row = {'session_id': 'archive-row', 'first_message': 'history ' * 200}
    rows = [row]
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *a, **kw: ([dict(item) for item in rows], True, 0))
    diagnostics = []
    monkeypatch.setattr(server, 'record_diag',
                        lambda kind, ms, detail: diagnostics.append(detail)
                        if kind == 'list_server' else None)
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=all&stale_ok=1'

    def read(encoding='gzip', validator='', since='', row_layouts=False, order=None):
        target = url + ('&since=' + since if since else '')
        if row_layouts:
            target += '&row_layouts=1'
        if order is not None:
            target += '&order_spans=1&saved_order=' + order
        request = urllib.request.Request(target, headers={'Accept-Encoding': encoding,
                                                          'If-None-Match': validator})
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers['Content-Encoding'], response.headers['ETag'], response.read()

    try:
        first = read()
        assert first[:2] == (200, 'gzip')
        first_list = json.loads(gzip.decompress(first[3]))
        assert first_list['conversations'][0]['first_message'] == row['first_message']
        assert read() == first
        assert read(validator=first[2]) == (304, None, first[2], b'')
        packed = read(row_layouts=True, validator=first[2])
        assert packed[:2] == (200, 'gzip') and packed[2] != first[2]
        assert read(row_layouts=True) == packed
        assert read(row_layouts=True, validator=packed[2]) == (304, None, packed[2], b'')
        assert read(validator=packed[2]) == first
        decoded = json.loads(gzip.decompress(packed[3]))
        layouts = decoded.pop('row_layouts')
        decoded['conversations'] = [dict(zip(layouts[index], values))
                                    for index, values in decoded['conversations']]
        assert decoded == first_list
        assert list(decoded['conversations'][0]) == list(first_list['conversations'][0])
        row['first_message'] += 'updated'
        changed = read(validator=first[2])
        assert changed[:2] == (200, 'gzip') and changed[2] != first[2]
        assert read(encoding='identity') == (200, None, changed[2], gzip.decompress(changed[3]))
        active_token = json.loads(gzip.decompress(changed[3]))['delta_token']
        assert read(since=active_token) == (304, None, '"d-' + active_token + '"', b'')

        assert [entry['body_cache'].get('encoding_hit') for entry in diagnostics] == [
            False, True, True, False, True, True, True, False, False, None,
        ]
        assert all(entry['projection']['rows'] for entry in diagnostics)

        monkeypatch.setattr(replay, '_RUN_MAX_ROWS', 1)
        monkeypatch.setattr(replay, '_HISTORY_ORDERS', replay.ByteBudgetCache(1 << 20, 4))
        rows[:] = [{'session_id': 'archive-row-' + str(i)} for i in range(100)]
        response = read(encoding='identity', order='')
        base = json.loads(response[3])
        rows.insert(27, {'session_id': 'joined'})
        response = read(encoding='identity', since=base['delta_token'], order=base['order_token'])
        assert response[0] == 200
        patch = json.loads(response[3])
        assert patch['ids'] == [] and patch['base_order'] == base['order_token']
        assert patch['order_spans'] == [[0, 27], 'joined', [27, 73]]
        assert [item['session_id'] for item in patch['conversations']] == ['joined']
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

def test_history_delta_preserves_projected_values_and_order(monkeypatch):
    from ccc_server import archive_list_replay as replay

    monkeypatch.setattr(replay, '_RUN_MAX_ROWS', 2)
    monkeypatch.setattr(replay, '_RUN_BOUNDARY_MASK', 0xffffffff)
    monkeypatch.setattr(replay, '_HISTORY_MANIFESTS', replay.ByteBudgetCache(1 << 20, 4))
    first = [{'session_id': sid, 'title': sid} for sid in 'abcdef']
    base, _ = replay.history_delta(replay.prepare_history({'conversations': first}),
                                   'scope', 'boot', '', '')
    token, order = base['delta_token'], base['order_token']
    modified = [*first[:5], dict(first[5], title='new title')]
    shared = {'conversations': modified}
    prepared = replay.prepare_history(shared)
    delta, _ = replay.history_delta(prepared, 'scope', 'boot', token, order)
    assert delta['same_order'] and delta['ids'] == []
    assert delta['conversations'] == modified[-2:]
    assert delta['order_token'] == order and delta['delta_token'] != token
    full, _ = replay.history_delta(prepared, 'scope', 'boot', '', '')
    assert shared == {'conversations': modified}
    assert 'delta' not in full and full['conversations'] == modified
    assert replay.history_delta(prepared, 'scope', 'boot', full['delta_token'], '') == (None, None)

    reordered = modified[2:4] + [{'session_id': 'new'}] + modified[4:]
    prepared = replay.prepare_history({'conversations': reordered})
    delta, _ = replay.history_delta(prepared, 'scope', 'boot', token, order)
    assert delta['ids'] == [row['session_id'] for row in reordered]
    by_id = {row['session_id']: row for row in [*first, *delta['conversations']]}
    assert [by_id[sid] for sid in delta['ids']] == reordered
    assert delta['order_token'] != order

    full, _ = replay.history_delta(prepared, 'other-scope', 'boot', token, order)
    assert 'delta' not in full and full['conversations'] == reordered
    duplicate = {'conversations': [*first, first[0]]}
    assert replay.history_delta(replay.prepare_history(duplicate), 'scope', 'boot', token, order) == (duplicate, None)


def test_history_delta_reuses_rows_when_layout_indexes_move(monkeypatch):
    from ccc_server import archive_list_replay as replay

    monkeypatch.setattr(replay, '_RUN_MAX_ROWS', 1)
    monkeypatch.setattr(replay, '_HISTORY_MANIFESTS', replay.ByteBudgetCache(1 << 20, 4))
    named = {'session_id': 'named', 'title': 'value'}
    state = {'session_id': 'state', 'status': 'value'}
    first, _ = replay.history_delta(
        replay.prepare_history({'conversations': [named, state]}), 'scope', 'boot', '', '')
    token, order = first['delta_token'], first['order_token']

    delta, _ = replay.history_delta(
        replay.prepare_history({'conversations': [state, named]}), 'scope', 'boot', token, order)
    assert delta['ids'] == ['state', 'named']
    assert delta['conversations'] == []

    renamed_field = {'session_id': 'named', 'label': 'value'}
    delta, _ = replay.history_delta(
        replay.prepare_history({'conversations': [renamed_field, state]}), 'scope', 'boot', token, order)
    assert delta['same_order']
    assert delta['conversations'] == [renamed_field]


def test_history_delta_sends_order_spans_only_for_the_retained_base(monkeypatch):
    from ccc_server import archive_list_replay as replay

    monkeypatch.setattr(replay, '_RUN_MAX_ROWS', 1)
    monkeypatch.setattr(replay, '_HISTORY_MANIFESTS', replay.ByteBudgetCache(1 << 20, 4))
    monkeypatch.setattr(replay, '_HISTORY_ORDERS', replay.ByteBudgetCache(1 << 20, 4))
    rows = [{'session_id': str(n)} for n in range(100)]
    base, _ = replay.history_delta(
        replay.prepare_history({'conversations': rows}), 'scope', 'boot', '', '',
        order_spans=True)
    token, order = base['delta_token'], base['order_token']
    reordered = rows[51:] + [{'session_id': 'added'}] + rows[:50]
    prepared = replay.prepare_history({'conversations': reordered})
    delta, _ = replay.history_delta(prepared, 'scope', 'boot', token, order, order_spans=True)
    assert delta['ids'] == []
    assert delta['base_order'] == order and delta['order_token'] != order
    assert delta['order_spans'] == [[51, 49], 'added', [0, 50]]
    assert delta['conversations'] == [{'session_id': 'added'}]

    expected_ids = [row['session_id'] for row in reordered]
    for saved, enabled in [(order, False), ('unknown', True)]:
        fallback, _ = replay.history_delta(
            prepared, 'scope', 'boot', token, saved, order_spans=enabled)
        assert fallback['ids'] == expected_ids and 'order_spans' not in fallback
        assert fallback['delta_token'] == delta['delta_token']
    reverse, _ = replay.history_delta(
        replay.prepare_history({'conversations': rows[::-1]}),
        'scope', 'boot', token, order, order_spans=True)
    assert reverse['ids'] == [row['session_id'] for row in rows[::-1]]
    assert 'order_spans' not in reverse
    replay._HISTORY_ORDERS.clear()
    fallback, _ = replay.history_delta(prepared, 'scope', 'boot', token, order, order_spans=True)
    assert fallback['ids'] == expected_ids and 'order_spans' not in fallback


def test_unversioned_history_projection_shares_only_overlapping_exact_source(monkeypatch):
    server = importlib.import_module('server')
    waiting = threading.Event()
    building = threading.Event()
    finish = threading.Event()

    class ObservableFuture(server.Future):
        def result(self):
            waiting.set()
            return super().result()

    monkeypatch.setattr(server, 'Future', ObservableFuture)
    rows = [{'session_id': 'first'}]
    payload = {'conversations': rows}

    def build(check_client):
        building.set()
        assert finish.wait(5)
        check_client()
        return payload

    def own_projection(_check_client):
        return {'conversations': []}

    def disconnected_owner():
        raise server._ArchiveListClientDisconnected()

    project = server._archive_list_history_projection
    phases = {}
    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(project, 'scope', rows, build, {}, disconnected_owner)
        try:
            assert building.wait(5)
            follower = pool.submit(project, 'scope', rows, own_projection, phases)
            assert waiting.wait(5)
            assert project('other', rows, own_projection, {}) != payload
            assert project('scope', list(rows), own_projection, {}) != payload
        finally:
            finish.set()
        assert owner.result(timeout=5) is follower.result(timeout=5) is payload
    assert 'projection_flight' in phases
    assert project('scope', rows, own_projection, {}) != payload


def test_all_list_disconnect_abandons_only_opted_in_conditional_reads(monkeypatch, tmp_path):
    import server
    from ccc_server import continuation_topics

    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *a, **kw: ([{'session_id': 'row'}], True, 0))
    entered = threading.Event()
    ready = threading.Event()
    original = server._archive_list_project_row
    topic_work = []
    dispositions = []

    def project(*args, **kwargs):
        entered.set()
        assert ready.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(server, '_archive_list_project_row', project)
    monkeypatch.setattr(continuation_topics, 'attach_topics',
                        lambda *a, **kw: topic_work.append(True))
    monkeypatch.setattr(server, 'record_diag',
                        lambda kind, ms, detail: dispositions.append(detail['status'])
                        if kind == 'list_server' else None)
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        for opted_in in (False, True):
            entered.clear()
            ready.clear()
            with socket.create_connection(httpd.server_address, timeout=5) as client:
                request = 'GET /api/conversations/list?window=all&stale_ok=1&since=old HTTP/1.1\r\n'
                request += 'Host: localhost\r\nConnection: close\r\n'
                if opted_in:
                    request += 'X-CCC-Abort-On-Disconnect: 1\r\n'
                client.sendall((request + '\r\n').encode())
                assert entered.wait(5)
                client.shutdown(socket.SHUT_WR)
                ready.set()
                with client.makefile('rb') as response:
                    body = response.read()
                if opted_in:
                    assert body == b''
                else:
                    assert body.split(b' ', 2)[1] == b'200'
                    assert json.loads(body.split(b'\r\n\r\n', 1)[1])['count'] == 1
    finally:
        ready.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert topic_work == [True]
    assert dispositions == [200, 'abandoned']


def test_history_encoding_builds_gzip_only_when_requested_or_needed(monkeypatch):
    from ccc_server import list_encoding

    monkeypatch.setattr(list_encoding, '_HISTORY_CACHE', list_encoding.ByteBudgetCache(1 << 20, 4))
    payload = {'conversations': [{'first_message': 'history ' * 200}]}
    monkeypatch.setattr(list_encoding, '_brotli', lambda raw: b'encoded br')
    gzip_calls = []
    compress = gzip.compress
    monkeypatch.setattr(gzip, 'compress',
                        lambda *a, **kw: gzip_calls.append(True) or compress(*a, **kw))
    assert list_encoding.history_representation(payload, 'gzip, br', None)[:2] == (b'encoded br', 'br')
    assert not gzip_calls
    monkeypatch.setattr(list_encoding, '_brotli', lambda raw: None)
    payload['updated'] = True
    body, encoding, _ = list_encoding.history_representation(payload, 'gzip;q=0.8, br', None)
    assert encoding == 'gzip' and json.loads(gzip.decompress(body)) == payload
    assert list_encoding.history_representation(payload, 'gzip;q=0.8, br', None)[:2] == (body, 'gzip')
    assert len(gzip_calls) == 1


def test_history_zstd_requires_local_packed_response_and_client_acceptance(monkeypatch):
    from ccc_server import list_encoding

    monkeypatch.setattr(list_encoding, '_HISTORY_CACHE', list_encoding.ByteBudgetCache(1 << 20, 4))
    monkeypatch.setattr(list_encoding, '_brotli', lambda raw: b'encoded br')
    zstd = list_encoding._zstd
    monkeypatch.setattr(list_encoding, '_zstd', lambda raw: b'encoded zstd')
    payload = {'conversations': [{'first_message': 'history ' * 200}]}
    accept = 'gzip, br, zstd'

    def read(header=accept, *, local=True, packed=True):
        return list_encoding.history_representation(
            payload, header, None, row_layouts=packed, prefer_zstd=local)[:2]

    assert read() == (b'encoded zstd', 'zstd')
    assert read(local=False) == (b'encoded br', 'br')
    assert read(packed=False) == (b'encoded br', 'br')
    assert read('gzip, br, zstd;q=0') == (b'encoded br', 'br')
    assert read('gzip, br, zstd;q=0.5') == (b'encoded br', 'br')
    monkeypatch.setattr(list_encoding, '_zstd_encoder', lambda: None)
    monkeypatch.setattr(list_encoding, '_zstd', zstd)
    monkeypatch.setattr(list_encoding, '_brotli', lambda raw: None)
    payload['updated'] = True
    body, encoding = read()
    assert encoding == 'gzip'
    decoded = json.loads(gzip.decompress(body))
    assert decoded['conversations'] == [[0, [payload['conversations'][0]['first_message']]]]
    assert decoded['row_layouts'] == [['first_message']]


def test_projection_diagnostics_do_not_change_the_payload():
    server = importlib.import_module('server')
    rows = [{'session_id': 'diagnostic-row', 'display_name': 'Title',
             'modified': 1000, 'session_state': {'todos': [{'text': 'work'}]}}]
    before = json.loads(json.dumps(rows))
    expected = server._archive_list_payload(rows, now=2000, cached=True)
    phases = {}
    actual = server._archive_list_payload(rows, now=2000, cached=True, phases=phases)
    assert actual == expected
    assert rows == before
    assert 'projection' not in actual and 'phases' not in actual
    assert 'rows' in phases and 'topics' in phases


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
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 26, 96))
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
        order = full['order_token']
        # Nothing changed: 304 without a body.
        assert get(base + '&since=' + token)[0] == 304
        server._archive_serve_cache_store(
            key, [_row('a', 'A', now), _row('b', 'B2', now)], gen)
        status, compact = get(base + '&since=' + token + '&saved_order=' + order)
        assert status == 200 and compact['same_order'] is True
        assert compact['order_token'] == order and compact['ids'] == []
        assert [r['session_id'] for r in compact['conversations']] == ['b']
        status, legacy = get(base + '&since=' + token)
        assert status == 200 and legacy['ids'] == ['a', 'b']
        assert 'same_order' not in legacy
        # One row changed and one appeared: only those two rows ship.
        server._archive_serve_cache_store(
            key, [_row('c', 'C', now), _row('a', 'A', now), _row('b', 'B2', now)], gen)
        status, delta = get(base + '&since=' + token + '&saved_order=' + order)
        assert status == 200 and delta['delta'] is True
        assert delta['ids'] == ['c', 'a', 'b']
        assert delta['order_token'] != order and 'same_order' not in delta
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


def test_manual_move_adds_old_session_to_recent_delta(monkeypatch):
    server = importlib.import_module('server')
    key = server._archive_response_cache_key()
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_CTX_HIST', server.collections.OrderedDict())
    ideas = {}
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: dict(ideas))
    now = time.time()
    server._archive_serve_cache_store(
        key, [_row('old', 'Older topic', now - 10 * 86400)], server._archive_serve_generation)
    base = server._archive_serve_cache[key]['rows']
    token = server._archive_list_delta_state(key, base, '7d', True)['token']
    assert server._archive_list_project_rows(base, window='7d', now=now) == []
    ideas['old'] = {'ref': 'IDEAS-1', 'queue': 'IDEAS', 'moved_at': now}
    server._restamp_archive_serve_cache_after_mutation(mutated_sids=['old'])
    base = server._archive_serve_cache[key]['rows']
    state = server._archive_list_delta_state(key, base, '7d', True)
    delta = server._archive_list_delta_payload(state, token, base, window='7d', now=now)
    assert delta['ids'] == ['old']
    assert delta['conversations'][0]['idea_ticket']['moved_at'] == now


def test_delta_reject_reason_names_why_a_token_gets_a_full_body(monkeypatch):
    # CCC-201: list_server diag logs this so repeat full bodies (the slow
    # list_read tail) can be attributed to restart / ctx churn / tracking.
    server = importlib.import_module('server')
    key = server._archive_response_cache_key()
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    gen = server._archive_serve_generation
    server._archive_serve_cache_store(key, [_row('a', 'A', time.time())], gen)
    base = server._archive_serve_cache[key]['rows']
    state = server._archive_list_delta_state(key, base, '7d', True)
    nonce, ver, ctx, ovl = state['token'].split('.')
    reject = server._archive_list_delta_reject
    assert reject(state, state['token']) is None
    assert reject(state, 'garbage') == 'malformed'
    assert reject(state, '.'.join(['ffffffff', ver, ctx, ovl])) == 'nonce'
    assert reject(state, '.'.join([nonce, ver, 'x' * 12, ovl])) == 'ctx'
    assert reject(state, '.'.join([nonce, str(int(ver) + 5), ctx, ovl])) == 'range'


def test_ctx_change_resends_only_rows_whose_projection_inputs_moved(monkeypatch):
    # CCC-201: an idea-ticket / usage-limit-resume change used to reject every
    # older token ('ctx'), so each open page re-downloaded the full 3MB body.
    server = importlib.import_module('server')
    key = server._archive_response_cache_key()
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_CTX_HIST', server.collections.OrderedDict())
    ideas = {'a': {'ref': 'IDEAS-1'}}
    resumes = {}
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: dict(ideas))
    monkeypatch.setattr(server, '_load_usage_limit_resumes', lambda: dict(resumes))
    gen = server._archive_serve_generation
    now = time.time()
    server._archive_serve_cache_store(
        key, [_row('a', 'A', now), _row('b', 'B', now), _row('session_k', 'K', now)], gen)
    base = server._archive_serve_cache[key]['rows']
    token = server._archive_list_delta_state(key, base, '7d', True)['token']

    ideas['b'] = {'ref': 'IDEAS-2'}
    resumes['k'] = {'resume_at': now + 600}
    state = server._archive_list_delta_state(key, base, '7d', True)
    assert state['ctx'] != token.split('.')[2]
    assert server._archive_list_delta_reject(state, token) is None
    delta = server._archive_list_delta_payload(state, token, base, window='7d')
    assert delta['ids'] == ['a', 'b', 'session_k']
    assert sorted(r['session_id'] for r in delta['conversations']) == ['b', 'session_k']

    # A ctx this process never remembered (evicted) still gets a full body.
    nonce, ver, _ctx, ovl = token.split('.')
    stale = '.'.join([nonce, ver, 'f' * 12, ovl])
    assert server._archive_list_delta_reject(state, stale) == 'ctx'
    assert server._archive_list_delta_payload(state, stale, base, window='7d') is None
    # Another window's ctx never vouches for this one.
    other = server._archive_list_delta_state(key, base, '1d', True)
    assert server._archive_list_delta_reject(state, other['token'].replace(
        other['token'].split('.')[1], ver, 1)) == 'ctx'
