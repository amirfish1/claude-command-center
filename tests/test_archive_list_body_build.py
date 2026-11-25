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

import pytest


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
    initial_diagnostics_ready = threading.Event()

    def record(kind, ms, detail):
        if kind == 'list_server':
            diagnostics.append(detail)
            if len(diagnostics) == 10:
                initial_diagnostics_ready.set()

    monkeypatch.setattr(server, 'record_diag', record)
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

        assert initial_diagnostics_ready.wait(5)
        assert [entry['body_cache'].get('encoding_hit') for entry in diagnostics] == [
            False, True, True, False, True, True, True, False, False, None,
        ]
        assert all(entry['projection']['rows'] for entry in diagnostics)

        monkeypatch.setattr(replay, '_RUN_MAX_ROWS', 1)
        monkeypatch.setattr(replay, '_HISTORY_ORDERS', replay.ByteBudgetCache(1 << 20, 4))
        rows[:] = [{'session_id': 'archive-row-' + str(i)} for i in range(100)]
        response = read(encoding='identity', order='')
        base = json.loads(response[3])
        rows.insert(27, {'session_id': 'joined', 'first_message': 'New note ' * 200})
        response = read(encoding='identity', since=base['delta_token'], order=base['order_token'])
        assert response[0] == 200
        patch = json.loads(response[3])
        assert patch['ids'] == [] and patch['base_order'] == base['order_token']
        assert patch['order_spans'] == [[0, 27], 'joined', [27, 73]]
        assert [item['session_id'] for item in patch['conversations']] == ['joined']
        monkeypatch.setattr(list_encoding, '_zstd', lambda raw: raw)
        response = read(encoding='gzip, zstd', since=base['delta_token'],
                        order=base['order_token'], row_layouts=True)
        assert response[:2] == (200, 'zstd')
        assert json.loads(response[3]) == patch
        monkeypatch.setattr(server.phone_access, 'is_remote_request', lambda *args: True)
        remote = read(encoding='gzip, zstd', since=base['delta_token'],
                      order=base['order_token'], row_layouts=True)
        assert remote[:2] == (200, 'gzip')
        assert json.loads(gzip.decompress(remote[3])) == patch
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

def test_history_delta_preserves_projected_values_and_order(monkeypatch):
    from ccc_server import archive_list_replay as replay

    monkeypatch.setattr(replay, '_RUN_MAX_ROWS', 2)
    monkeypatch.setattr(replay, '_HISTORY_RUN_BOUNDARY_MASK', 0xffffffff)
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

    wait = server.wait

    def observe_wait(*args, **kwargs):
        waiting.set()
        return wait(*args, **kwargs)

    monkeypatch.setattr(server, 'wait', observe_wait)
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


def test_all_projection_stops_when_all_opted_in_readers_leave():
    import server

    entered = threading.Event()
    continue_owner = threading.Event()
    rows = [{'session_id': 'row'}]

    def build(check_client):
        entered.set()
        assert continue_owner.wait(5)
        check_client()
        pytest.fail('projection continued without a reader')

    def disconnected():
        raise server._ArchiveListClientDisconnected()

    with ThreadPoolExecutor(2) as pool:
        project = server._archive_list_history_projection
        owner = pool.submit(project, 'scope', rows, build, {}, disconnected)
        try:
            assert entered.wait(5)
            follower = pool.submit(project, 'scope', rows, build, {}, disconnected)
            with pytest.raises(server._ArchiveListClientDisconnected):
                follower.result(timeout=3)
        finally:
            continue_owner.set()
        with pytest.raises(server._ArchiveListClientDisconnected):
            owner.result(timeout=3)


@pytest.mark.parametrize('since', ['', '&since=old'])
@pytest.mark.parametrize('stage', ['projection', 'manifest', 'representation'])
def test_all_list_disconnect_abandons_only_opted_in_reads(monkeypatch, tmp_path, stage, since):
    import server
    from ccc_server import archive_list_replay, continuation_topics

    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *a, **kw: ([{'session_id': 'row'}], True, 0))
    entered = threading.Event()
    ready = threading.Event()
    topic_work = []
    fingerprints = []
    dispositions = []

    def wait_for_disconnect():
        entered.set()
        assert ready.wait(5)

    if stage == 'projection':
        original = server._archive_list_project_row

        def project(*args, **kwargs):
            wait_for_disconnect()
            return original(*args, **kwargs)

        monkeypatch.setattr(server, '_archive_list_project_row', project)
    elif stage == 'manifest':
        original = archive_list_replay.payload_fingerprint

        def fingerprint(value):
            fingerprints.append(value)
            if isinstance(value, tuple):
                wait_for_disconnect()
            return original(value)

        monkeypatch.setattr(archive_list_replay, 'payload_fingerprint', fingerprint)
    else:
        original = archive_list_replay._packed_history_fragments

        def fragments(*args):
            wait_for_disconnect()
            return original(*args)

        monkeypatch.setattr(archive_list_replay, '_packed_history_fragments', fragments)
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
                request = (
                    'GET /api/conversations/list?window=all&stale_ok=1&row_layouts=1'
                    f'{since} HTTP/1.1\r\n'
                )
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
    assert topic_work == ([True] if stage == 'projection' else [True, True])
    if stage == 'manifest':
        # Both computed their layout digest. Only the ordinary client
        # reached the run and metadata fingerprints after receiving EOF.
        assert [type(value) for value in fingerprints] == [tuple, list, dict, tuple]
    assert dispositions == [200, 'abandoned']


def test_all_list_disconnect_stops_unpacked_archive_merge(monkeypatch, tmp_path):
    import http.client
    import server
    from ccc_server import archive_store, continuation_topics

    rows = [{'session_id': f'row-{index}'} for index in range(420)]
    key = server._archive_response_cache_key()
    archive = tmp_path / 'archive.json'
    entry = {'cached_at': time.time(), 'conversations': rows}
    archive_store.save(archive, {
        'schema_version': server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
        'build_id': server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
        'entries': {key: entry},
    }, complete=True)
    assert not archive_store.sidebar_pages_available(
        archive, key, server._ARCHIVE_SIDEBAR_PACKED_VERSION)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', archive)
    monkeypatch.setattr(server, '_archive_response_cache_get', lambda _key: entry)
    monkeypatch.setattr(server, '_archive_list_disk_candidate', lambda *a: None)
    monkeypatch.setattr(server, '_archive_serve_hot_rows_versioned',
                        lambda *a, **kw: (rows[:1], True, 0))
    monkeypatch.setattr(server, '_archive_list_source_overlays', lambda rows, *a: (rows, []))
    monkeypatch.setattr(server, '_archive_list_ticket_updates', lambda *a: None)
    monkeypatch.setattr(continuation_topics, 'attach_topics', lambda *a, **kw: None)

    entered = threading.Event()
    disconnected = threading.Event()
    batches = []
    decode = archive_store._decode_full_batch

    def pause_first_batch(bodies):
        batches.append(len(bodies))
        if len(batches) == 1:
            entered.set()
            assert disconnected.wait(5)
        return decode(bodies)

    monkeypatch.setattr(archive_store, '_decode_full_batch', pause_first_batch)
    url = '/api/conversations/list?window=all&stale_ok=1'
    headers = {'X-CCC-Abort-On-Disconnect': '1'}
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(httpd.server_address, timeout=5) as client:
            client.sendall((f'GET {url} HTTP/1.1\r\nHost: localhost\r\n'
                            'Connection: close\r\nX-CCC-Abort-On-Disconnect: 1\r\n\r\n').encode())
            assert entered.wait(5)
            client.shutdown(socket.SHUT_WR)
            disconnected.set()
            assert client.recv(1) == b''
        assert len(batches) == 1

        conn = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        try:
            conn.request('GET', url, headers=headers)
            response = conn.getresponse()
            assert response.status == 200
            assert [row['session_id'] for row in json.load(response)['conversations']] == [
                row['session_id'] for row in rows]
        finally:
            conn.close()
        assert len(batches) > 2
    finally:
        disconnected.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('stage', ['source', 'projection'])
def test_bounded_list_disconnect_leaves_versioned_body_available_to_next_reader(
        monkeypatch, tmp_path, stage):
    import http.client
    import server
    from ccc_server import continuation_topics

    entered = threading.Event()
    disconnected = threading.Event()
    topic_work = []
    dispositions = []
    rows = [{'session_id': 'row', 'pinned': True}]

    def pause_first_read():
        if not entered.is_set():
            entered.set()
            assert disconnected.wait(5)

    def source(*args, **kwargs):
        if stage == 'source':
            pause_first_read()
        return rows, True, 42

    original = server._archive_list_project_row

    def project(*args, **kwargs):
        if stage == 'projection':
            pause_first_read()
        return original(*args, **kwargs)

    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_archive_list_disk_candidate', lambda *a: None)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 20, 8))
    monkeypatch.setattr(server, '_archive_list_source_rows_cached', source)
    monkeypatch.setattr(server, '_archive_list_delta_state', lambda *a: None)
    monkeypatch.setattr(server, '_archive_list_project_row', project)
    monkeypatch.setattr(continuation_topics, 'attach_topics',
                        lambda *a, **kw: topic_work.append(True))
    monkeypatch.setattr(server, 'record_diag',
                        lambda kind, ms, detail: dispositions.append(detail['status'])
                        if kind == 'list_server' else None)
    url = '/api/conversations/list?window=7d&stale_ok=1'
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(httpd.server_address, timeout=5) as client:
            client.sendall((f'GET {url} HTTP/1.1\r\nHost: localhost\r\n'
                            'X-CCC-Abort-On-Disconnect: 1\r\n\r\n').encode())
            assert entered.wait(5)
            client.shutdown(socket.SHUT_WR)
            disconnected.set()
            assert client.recv(1) == b''
        connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        try:
            connection.request('GET', url, headers={'X-CCC-Abort-On-Disconnect': '1'})
            response = connection.getresponse()
            assert response.status == 200
            body = json.loads(response.read())
            assert [row['session_id'] for row in body['conversations']] == ['row']
        finally:
            connection.close()
    finally:
        disconnected.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert topic_work == [True]
    assert dispositions == ['abandoned', 200]


def test_history_encoding_builds_gzip_only_when_requested_or_needed(monkeypatch):
    from ccc_server import list_encoding

    monkeypatch.setattr(list_encoding, '_HISTORY_CACHE', list_encoding.ByteBudgetCache(1 << 20, 4))
    monkeypatch.setattr(list_encoding, '_CACHE', list_encoding.ByteBudgetCache(1 << 20, 4))
    payload = {'conversations': [{'first_message': 'history ' * 200}]}
    monkeypatch.setattr(list_encoding.static_encoding, 'brotli', lambda *a, **kw: b'encoded br')
    gzip_calls = []
    compress = gzip.compress
    monkeypatch.setattr(gzip, 'compress',
                        lambda *a, **kw: gzip_calls.append(True) or compress(*a, **kw))
    assert list_encoding.history_representation(payload, 'gzip, br', None)[:2] == (b'encoded br', 'br')
    assert not gzip_calls
    monkeypatch.setattr(list_encoding.static_encoding, 'brotli', lambda *a, **kw: None)
    payload['updated'] = True
    body, encoding, _ = list_encoding.history_representation(payload, 'gzip;q=0.8, br', None)
    assert encoding == 'gzip' and json.loads(gzip.decompress(body)) == payload
    assert list_encoding.history_representation(payload, 'gzip;q=0.8, br', None)[:2] == (body, 'gzip')
    assert len(gzip_calls) == 1


def test_packed_history_reuses_runs_but_checks_wire_layout_slots(monkeypatch):
    from ccc_server import archive_list_replay as replay, list_encoding

    monkeypatch.setattr(replay, '_RUN_MAX_ROWS', 1)
    cache = replay.ByteBudgetCache(4096, 8)
    monkeypatch.setattr(replay, '_PACKED_RUN_CACHE', cache)
    left = {'session_id': 'left', 'first_message': 'first', 'mtime': 12}
    right = {'session_id': 'right', 'mtime': 14}
    exact_repeat = []
    digests = []
    for rows in ([left, right], [left, right], [right, left], [left, dict(right, mtime=15)]):
        payload = {'ok': True, 'conversations': rows}
        _, manifest, packed = replay.prepare_history(payload)
        assert packed is not None
        runs = manifest[1]
        digests.append([digest for digest, _, _ in runs])
        representation = list_encoding.history_representation(
            payload, '', None, row_layouts=True, packed_rows=packed, packed_runs=runs)
        expected = list_encoding.history_representation(
            payload, '', None, row_layouts=True, packed_rows=packed)
        assert representation == expected
        exact_repeat.append(len(cache))
    assert exact_repeat == [2, 2, 4, 5]
    assert digests[0] == list(reversed(digests[2]))
    assert digests[0][1] != digests[3][1]

def test_history_zstd_requires_preference_and_client_acceptance(monkeypatch):
    from ccc_server import list_encoding

    monkeypatch.setattr(list_encoding, '_HISTORY_CACHE', list_encoding.ByteBudgetCache(1 << 20, 4))
    monkeypatch.setattr(list_encoding, '_CACHE', list_encoding.ByteBudgetCache(1 << 20, 4))
    monkeypatch.setattr(list_encoding.static_encoding, 'brotli', lambda *a, **kw: b'encoded br')
    zstd = list_encoding._zstd
    monkeypatch.setattr(list_encoding, '_zstd', lambda raw: b'encoded zstd')
    payload = {'conversations': [{'first_message': 'history ' * 200}]}
    accept = 'gzip, br, zstd'

    def read(header=accept, *, local=True, packed=True):
        return list_encoding.history_representation(
            payload, header, None, row_layouts=packed, prefer_zstd=local)[:2]

    assert read() == (b'encoded zstd', 'zstd')
    assert read(local=False) == (b'encoded br', 'br')
    assert read(packed=False) == (b'encoded zstd', 'zstd')
    assert read('gzip, br, zstd;q=0') == (b'encoded br', 'br')
    assert read('gzip, br, zstd;q=0.5') == (b'encoded br', 'br')
    monkeypatch.setattr(list_encoding, '_zstd_encoder', lambda: None)
    monkeypatch.setattr(list_encoding, '_zstd', zstd)
    monkeypatch.setattr(list_encoding.static_encoding, 'brotli', lambda *a, **kw: None)
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


def test_seven_day_delta_splices_a_known_order_after_one_row_leaves(monkeypatch):
    server = importlib.import_module('server')
    key = server._archive_response_cache_key()
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_ARCHIVE_SEVEN_DAY_ORDERS', server.ByteBudgetCache(1 << 20, 8))
    now = time.time()
    ids = [f'row-{i}' for i in range(64)]
    old_rows = [_row(sid, sid, now) for sid in ids]
    gen = server._archive_serve_generation
    assert server._archive_serve_cache_store(key, old_rows, gen)
    old_rows = server._archive_serve_cache[key]['rows']
    previous = server._archive_list_delta_state(key, old_rows, '7d', True)
    order = server._archive_list_payload_order({'conversations': old_rows}, key, '7d')

    wanted = ids[:17] + ids[18:]
    new_rows = [_row(sid, sid, now) for sid in wanted]
    new_rows[0] = _row(wanted[0], 'Changed', now)
    assert server._archive_serve_cache_store(key, new_rows, gen)
    new_rows = server._archive_serve_cache[key]['rows']
    current = server._archive_list_delta_state(key, new_rows, '7d', True)

    def delta(saved_order=order, *, negotiate=True):
        return server._archive_list_delta_payload(
            current, previous['token'], new_rows, window='7d', now=now,
            saved_order=saved_order, order_spans=negotiate)

    compact = delta()
    assert compact['ids'] == [] and compact['base_order'] == order
    assert compact['order_spans'] == [[0, 17], [18, 46]]
    assert [r['session_id'] for r in compact['conversations']] == [wanted[0]]
    for fallback in (delta('f' * 64), delta(negotiate=False)):
        assert fallback['ids'] == wanted and 'order_spans' not in fallback
    server._ARCHIVE_SEVEN_DAY_ORDERS.clear()
    assert delta()['ids'] == wanted


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


def test_delta_context_compares_serialized_numeric_types(monkeypatch):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_CTX_HIST', server.collections.OrderedDict())
    server._archive_delta_ctx_remember(
        'before', 'scope', '7d', '{"a":{"value":1},"b":[true],"c":[0.0],"d":[1]}', '{}', '{}', 1)
    server._archive_delta_ctx_remember(
        'after', 'scope', '7d', '{"a":{"value":1.0},"b":[1],"c":[-0.0],"d":["1"]}', '{}', '{}', 1)
    assert server._archive_delta_ctx_changed_sids('before', 'after') == {
        'a', 'b', 'c', 'd', 'session_a', 'session_b', 'session_c', 'session_d',
    }


def test_read_during_reader_warm_gets_previous_body_until_a_mutation(monkeypatch, tmp_path):
    # CCC-691: a page load racing the list reader's background warm waited for
    # the same build; it now gets the previous body, unless a mutation voided it.
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 20, 8))
    monkeypatch.setattr(server, '_ARCHIVE_LIST_LATEST', {})
    monkeypatch.setattr(server._conv_reader, 'list_mutation', lambda *a, **k: None)
    warming = [False]
    monkeypatch.setattr(server, '_ARCHIVE_LIST_STALE_OK',
                        lambda reason, slot: reason == 'rebuilding' and warming[0])
    builds = []

    def payload(tag):
        def build():
            builds.append(tag)
            return {'conversations': [{'session_id': tag}]}
        return build

    def read(version, tag, allow_stale=True):
        detail = {}
        raw, _, _ = server._archive_list_body_get_or_build(
            'key', '1d', True, payload(tag), version, diagnostics=detail, allow_stale=allow_stale)
        return json.loads(raw)['conversations'][0]['session_id'], detail['reason']

    assert read(1, 'v1') == ('v1', 'version-miss')
    warming[0] = True
    assert read(2, 'v2') == ('v1', 'stale-while-warming')
    assert read(2, 'v2', allow_stale=False) == ('v2', 'version-miss')  # the warm itself
    assert read(2, 'v2') == ('v2', 'hit')
    assert read(3, 'v3') == ('v2', 'stale-while-warming')
    server._restamp_archive_serve_cache_after_mutation(mutated_sids={'v2'})
    assert read(3, 'v3') == ('v3', 'version-miss')
    warming[0] = False
    assert read(4, 'v4') == ('v4', 'version-miss')
    assert builds == ['v1', 'v2', 'v3', 'v4']


def test_previous_body_still_served_minutes_after_it_was_built(monkeypatch, tmp_path):
    # Live CCC-691 check: reloads come every few minutes, so a 120 s cap on
    # the build time rejected every fallback.
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 20, 8))
    monkeypatch.setattr(server, '_ARCHIVE_LIST_LATEST', {})
    monkeypatch.setattr(server, '_ARCHIVE_LIST_STALE_OK', lambda reason, slot: True)
    server._archive_list_body_get_or_build('key', '7d', True, lambda: {'conversations': []}, 1)
    slot = ('key', '7d', True)
    built = server._ARCHIVE_LIST_LATEST[slot][1]
    assert server._archive_list_stale_body(slot, now=built + 600) is not None
    assert server._archive_list_stale_body(slot, now=built + 2 * 3600) is None


def test_dashboard_never_serves_a_previous_body(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 20, 8))
    monkeypatch.setattr(server, '_ARCHIVE_LIST_LATEST', {})
    assert server._ARCHIVE_LIST_STALE_OK is None
    for version in (1, 2):
        detail = {}
        server._archive_list_body_get_or_build(
            'key', '1d', True, lambda: {'conversations': []}, version,
            diagnostics=detail, allow_stale=True)
        assert detail['reason'] == 'version-miss'
    assert server._ARCHIVE_LIST_LATEST == {}


def test_overlay_only_change_serves_previous_body(monkeypatch, tmp_path):
    # Live CCC-691 check: 20 of 437 page loads rebuilt the body because only
    # the live-session overlay hash moved. Deltas resend overlay rows whole,
    # so the previous body's token catches up on the next poll.
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 20, 8))
    monkeypatch.setattr(server, '_ARCHIVE_LIST_LATEST', {})
    asked = []

    def hook(reason, slot):
        asked.append((reason, slot[1]))
        return reason == 'overlay'
    monkeypatch.setattr(server, '_ARCHIVE_LIST_STALE_OK', hook)

    def read(snap_ver, tag, base):
        detail = {}
        raw, _, _ = server._archive_list_body_get_or_build(
            'key', '1d', True, lambda: {'conversations': [{'session_id': tag}]}, snap_ver,
            diagnostics=detail, allow_stale=True, stale_base=base)
        return json.loads(raw)['conversations'][0]['session_id'], detail['reason']

    ctx = 'ctx1'
    assert server._archive_list_stale_base((5, 'ovl-a'), {'ctx': ctx}) == (5, ctx)
    assert server._archive_list_stale_base(5, {'ctx': ctx}) == (5, ctx)
    assert server._archive_list_stale_base((5, 'ovl-a'), None) is None
    assert read(((5, 'ovl-a'), ctx), 'a', (5, ctx)) == ('a', 'version-miss')
    assert read(((5, 'ovl-b'), ctx), 'b', (5, ctx)) == ('a', 'stale-overlay')
    assert asked[-1] == ('overlay', '1d')
    # A new snapshot or projection context is not an overlay-only change.
    assert read(((6, 'ovl-b'), ctx), 'c', (6, ctx)) == ('c', 'version-miss')
    assert read(((6, 'ovl-b'), 'ctx2'), 'd', (6, 'ctx2')) == ('d', 'version-miss')
    # No delta token: never served stale for an overlay change.
    assert read((6, 'ovl-c'), 'e', None) == ('e', 'version-miss')
