"""Complete list replay must preserve live data, ticket work and JSON bytes."""
import copy
import json
import gzip
import zlib

import pytest
import server
from ccc_server import archive_list_replay as replay
from ccc_server import continuation_topics


@pytest.fixture
def inputs(monkeypatch):
    state = {'ideas': {}, 'topics': 1, 'graph': (1, 1), 'titles': {}, 'parents': {},
             'resume': {}, 'calls': 0, 'ticks': []}
    monkeypatch.setattr(server, '_ARCHIVE_LIST_PROJECTION_CACHE', server.ByteBudgetCache(1 << 22, 8))
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: state['ideas'])
    monkeypatch.setattr(server._idea_tickets, 'sweep_rows', lambda rows: None)
    monkeypatch.setattr(server._idea_tickets, 'autopilot_rows',
                        lambda rows: state['ticks'].append(copy.deepcopy(rows)))
    monkeypatch.setattr(server._idea_tickets, 'backfill_recent_once', lambda rows: None)
    monkeypatch.setattr(server._idea_tickets, 'relabel_continued_once', lambda: None)
    monkeypatch.setattr(server, '_archive_list_title_overlays', lambda: state['titles'])
    monkeypatch.setattr(server, '_load_usage_limit_resumes', lambda: state['resume'])
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: state['parents'])
    monkeypatch.setattr(server._session_graph, 'driver_snapshot', lambda: {})
    monkeypatch.setattr(server._session_graph, 'projection_revision', lambda: state['graph'])
    monkeypatch.setattr(continuation_topics, 'revision', lambda: state['topics'])
    monkeypatch.setattr(server, '_queue_health_stats', lambda rows, now=None: {'at': now})
    monkeypatch.setattr(server, '_trace_row_flicker', lambda *args, **kwargs: None)

    def topics(rows):
        state['calls'] += 1
        by_sid = {row['session_id']: row for row in rows}
        for row in rows:
            parent = by_sid.get(row.get('parent_session_id'))
            row['continuation_topic_title'] = (parent or row).get('display_name', '')

    monkeypatch.setattr(continuation_topics, 'attach_topics', topics)
    return state


def fixture_rows():
    return [
        {'session_id': 'archive', 'source': 'codex', 'display_name': 'Archive', 'mtime': 9000,
         'session_state': {'todos': [{'text': 'nested'}]}},
        {'session_id': 'live', 'source': 'codex', 'display_name': 'Live ☃', 'mtime': 9500,
         'modified': 9500, 'parent_session_id': 'archive'},
    ]


def read(rows, *, version=1, context='context', now=10000, window='7d', extra=None):
    delta = {'ver': version, 'ctx': context, 'extra': extra if extra is not None else rows[1:]}
    payload = server._archive_list_replay_payload(
        rows, delta, window=window, now=now, cached=True)
    payload['delta_token'] = f'{version}.{context}.{now}'
    assert replay.encode_payload(payload) == json.dumps(dict(payload)).encode()
    compressed = replay.encode_gzip_payload(payload)
    if compressed is not None:
        assert gzip.decompress(compressed) == replay.encode_payload(payload)
    return payload


def test_reuses_archive_but_keeps_timestamps_ticket_ticks_and_containers_fresh(inputs):
    rows = fixture_rows()
    first = read(rows)
    first['conversations'][0]['session_state']['todos'][0]['text'] = 'caller change'
    rows[1].update(mtime=9900, modified=9900)
    again = read(rows, now=10001)
    assert inputs['calls'] == 1
    assert again['conversations'][1]['mtime'] == 9900
    assert again['conversations'][1]['modified'] == 9900
    assert again['conversations'][0]['session_state']['todos'][0]['text'] == 'nested'
    assert inputs['ticks'][-1][1]['mtime'] == 9900
    assert len(inputs['ticks']) == 2
    assert again['queue_health'] == {'at': 10001}
    assert again['count'] == 2 and again['total_count'] == 2

    # Later replays restore a private clone rather than decoding JSON again.
    again['conversations'][0]['session_state']['todos'][0]['text'] = 'second caller'
    rows[1].update(mtime=9950, modified=9950)
    third = read(rows, now=10002)
    assert inputs['calls'] == 1
    assert third['conversations'][1]['mtime'] == 9950
    assert third['conversations'][0]['session_state']['todos'][0]['text'] == 'nested'
    assert inputs['ticks'][-1][1]['mtime'] == 9950
    assert len(inputs['ticks']) == 3


def test_owned_clone_preserves_json_normalization_and_byte_budget():
    original = [{'session_id': 'live', 'mtime': 1,
                 'session_state': {'values': (1, 2), 'keys': {1: 'one'}}}]
    raw, spans = replay.encode_rows(original, {'live'})
    entry = replay.compressed_entry(raw, spans)
    normalized = replay.replay_rows(entry, {'live': original[0]})
    cloned = replay.with_owned_rows(entry, normalized)
    assert replay.entry_weight(cloned) == replay.entry_weight(entry) + len(cloned[3])
    restored = replay.replay_rows(cloned, {'live': dict(original[0], mtime=2)})
    assert restored[0]['session_state'] == {'values': [1, 2], 'keys': {'1': 'one'}}
    assert restored[0]['mtime'] == 2
    restored[0]['session_state']['values'].append(3)
    assert replay.replay_rows(cloned, {'live': original[0]}) == normalized
    cache = server.ByteBudgetCache(replay.entry_weight(entry), 2)
    assert cache.put('entry', entry, replay.entry_weight(entry))
    assert not cache.put('entry', cloned, replay.entry_weight(cloned))
    assert cache.bytes == 0 and len(cache) == 0


def test_clone_that_exceeds_budget_keeps_existing_json_replay(inputs):
    rows = fixture_rows()
    read(rows)
    cache = server._ARCHIVE_LIST_PROJECTION_CACHE
    cache.max_bytes = cache.bytes
    budget = cache.bytes
    rows[1]['mtime'] += 1
    read(rows, now=10001)
    rows[1]['mtime'] += 1
    third = read(rows, now=10002)
    assert inputs['calls'] == 1
    assert third['conversations'][1]['mtime'] == rows[1]['mtime']
    assert cache.bytes == budget and len(cache) == 1


@pytest.mark.parametrize('mutation', ['title', 'parent', 'other-field', 'presence', 'membership',
                                      'snapshot', 'context', 'topics', 'graph', 'ideas'])
def test_invalidates_every_projection_dependency(inputs, mutation):
    rows = fixture_rows()
    read(rows, window='8h')
    options = {'window': '8h'}
    if mutation == 'title':
        rows[1]['display_name'] = 'Renamed'
    elif mutation == 'parent':
        rows[1]['parent_session_id'] = 'different'
    elif mutation == 'other-field':
        rows[1]['goal'] = {'text': 'new goal'}
    elif mutation == 'presence':
        del rows[1]['modified']
    elif mutation == 'membership':
        options['now'] = 100000
    elif mutation == 'snapshot':
        options['version'] = 2
        rows[0]['display_name'] = 'New archive snapshot'
    elif mutation == 'context':
        options['context'] = 'changed-title-context'
        inputs['titles'] = {'archive': {'display_name': 'New override'}}
    elif mutation == 'topics':
        inputs['topics'] += 1
    elif mutation == 'graph':
        inputs['graph'] = (2, 1)
        inputs['parents'] = {'live': 'different'}
    elif mutation == 'ideas':
        inputs['ideas'] = {'archive': {'ref': 'TEST-1', 'explicitly_archived': True}}
    actual = read(rows, **options)
    expected = server._archive_list_payload(rows, window=options['window'],
                                           now=options.get('now', 10000), cached=True)
    expected['delta_token'] = actual['delta_token']
    assert dict(actual) == expected
    assert inputs['calls'] == 3  # two real builds plus independent reference


def test_timestamp_updates_reselect_window_and_required_ancestors(inputs):
    rows = fixture_rows()
    rows[0]['mtime'] = 1
    read(rows, window='8h', now=30000)
    rows[1].update(mtime=50000, modified=50000)
    actual = read(rows, window='8h', now=50001)
    assert inputs['calls'] == 1  # same selected live row and retained old ancestor
    assert [r['session_id'] for r in actual['conversations']] == ['live', 'archive']
    assert actual['conversations'][0]['mtime'] == 50000
    rows[1]['mtime'] = rows[1]['modified'] = 1
    actual = read(rows, window='8h', now=50001)
    assert actual['count'] == 0 and inputs['calls'] == 2


def test_changed_overlay_can_change_existing_archive_topic(inputs):
    rows = fixture_rows()
    rows[0]['parent_session_id'] = 'live'
    assert read(rows)['conversations'][0]['continuation_topic_title'] == 'Live ☃'
    rows[1]['display_name'] = 'Changed topic'
    assert read(rows)['conversations'][0]['continuation_topic_title'] == 'Changed topic'
    assert inputs['calls'] == 2


def test_no_delta_no_overlay_ambiguous_ids_and_budget_use_normal_projection(inputs):
    rows = fixture_rows()
    for delta in (None, {'extra': []}):
        server._archive_list_replay_payload(rows, delta, now=10000)
    for _ in range(2):
        read(rows + [dict(rows[1])])
    assert inputs['calls'] == 4
    assert len(server._ARCHIVE_LIST_PROJECTION_CACHE) == 0
    server._ARCHIVE_LIST_PROJECTION_CACHE = server.ByteBudgetCache(1, 1)
    read(rows)
    read(rows)
    assert inputs['calls'] == 6
    assert len(server._ARCHIVE_LIST_PROJECTION_CACHE) == 0


def test_offsets_cover_empty_interleaved_and_multibyte_runs():
    for rows in ([], [{'id': 'a'}], [{'id': 'live'}],
                 [{'id': 'a'}, {'id': 'live', 'title': 'שלום'},
                  {'id': 'b'}, {'id': 'other'}, {'id': 'c'}]):
        raw, spans = replay.encode_rows(rows, {'live', 'other'})
        assert raw == json.dumps(rows).encode()
        assert replay.encode_replay((raw, spans), rows) == raw


def test_compressed_runs_preserve_json_and_fresh_overlay_and_envelope():
    for rows in ([], [{'id': 'a'}], [{'id': 'live'}],
                 [{'id': 'a'}, {'id': 'live', 'title': 'שלום'},
                  {'id': 'b'}, {'id': 'other'}, {'id': 'c'}]):
        raw, spans = replay.encode_rows(rows, {'live', 'other'})
        entry = replay.compressed_entry(raw, spans)
        changed = copy.deepcopy(rows)
        for row in changed:
            if row['id'] in {'live', 'other'}:
                row['mtime'] = 12345
                row['title'] = 'fresh ☃ "quoted"'
        current = replay.encode_replay(entry, changed)
        compressed = replay.compress_replay(entry, changed)
        assert zlib.decompress(compressed + b'\x03\x00', wbits=-zlib.MAX_WBITS) == current
        payload = replay.EncodedListPayload(
            {'ok': True, 'conversations': changed, 'queue_health': {'queued': 3},
             'delta_token': 'fresh-token'}, current, compressed)
        encoded = replay.encode_gzip_payload(payload)
        expected = json.dumps(dict(payload)).encode()
        assert gzip.decompress(encoded) == expected
        decoder = zlib.decompressobj(wbits=31)
        assert decoder.decompress(encoded) == expected
        assert decoder.eof and decoder.unused_data == b''  # exactly one member


def test_plain_payload_and_changed_ticket_rows_keep_normal_gzip_fallback(inputs, monkeypatch):
    assert replay.encode_gzip_payload({'conversations': []}) is None
    rows = fixture_rows()
    read(rows)
    def changed(projected, ideas):
        projected[0]['goal'] = {'text': 'new ticket data'}
        return True
    monkeypatch.setattr(server, '_archive_list_ticket_updates', changed)
    actual = read(rows, now=10001)
    assert actual['conversations'][0]['goal'] == {'text': 'new ticket data'}
    assert replay.encode_gzip_payload(actual) is None


def test_resume_defensive_expiry_invalidates_without_store_change(inputs, monkeypatch):
    from ccc_server import usage_limit
    rows = fixture_rows()
    inputs['resume'] = {'archive': {'resume_at': 20000}}
    clock = [20001]
    monkeypatch.setattr(usage_limit.time, 'time', lambda: clock[0])
    assert read(rows)['conversations'][0]['usage_limit_resume_at'] == 20000
    clock[0] = 24000
    assert 'usage_limit_resume_at' not in read(rows)['conversations'][0]
    assert inputs['calls'] == 2


def test_http_full_versions_reuse_projection_and_keep_body_etags_current(inputs, monkeypatch, tmp_path):
    import threading
    import urllib.request
    rows = fixture_rows()
    version = [1]
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_archive_list_disk_candidate', lambda *a: None)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 22, 8))
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *a, **kw: (rows, True, version[0]))
    monkeypatch.setattr(server, '_archive_list_delta_state',
                        lambda *a, **kw: {'ver': 1, 'ctx': 'fixed', 'extra': rows[1:],
                                          'token': str(version[0])})
    server_instance = server.http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=server_instance.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server_instance.server_port}/api/conversations/list?window=all&stale_ok=1'
    try:
        with urllib.request.urlopen(url) as response:
            first, first_etag = json.load(response), response.headers['ETag']
        version[0] += 1
        rows[1].update(mtime=9900, modified=9900)
        with urllib.request.urlopen(url) as response:
            second, second_etag = json.load(response), response.headers['ETag']
        with urllib.request.urlopen(url) as response:
            third, third_etag = json.load(response), response.headers['ETag']
        assert inputs['calls'] == 1
        assert first['conversations'][1]['mtime'] == 9500
        assert second['conversations'][1]['mtime'] == 9900
        assert first_etag != second_etag and second_etag == third_etag
        assert second == third and second['count'] == second['total_count'] == 2
        assert len(inputs['ticks']) == 2
    finally:
        server_instance.shutdown()
        server_instance.server_close()
        thread.join(5)


def test_projection_replay_retains_only_budgeted_immutable_bytes(inputs, monkeypatch):
    cache = server.ByteBudgetCache(4096, 8)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_PROJECTION_CACHE', cache)
    rows = fixture_rows()
    for version in range(20):
        read(rows, version=version)
        assert cache.bytes <= cache.max_bytes
    assert 0 < len(cache) < 8
    calls = inputs['calls']
    read(rows, version=19)
    assert inputs['calls'] == calls
    assert server._ARCHIVE_LIST_PROJECTION_CACHE.max_bytes <= 64 * 1024 * 1024


def test_hidden_duplicate_overlay_cannot_replace_archive_timestamp(inputs):
    rows = fixture_rows()
    rows[1] = dict(rows[0], mtime=1, modified=1)
    for _ in range(2):
        actual = read(rows, window='8h', now=30000)
        assert actual['conversations'][0]['mtime'] == 9000
    assert inputs['calls'] == 2
    assert len(server._ARCHIVE_LIST_PROJECTION_CACHE) == 0
