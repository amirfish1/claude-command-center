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
    monkeypatch.setattr(server._idea_tickets, 'relabel_continued_once', lambda: None)
    monkeypatch.setattr(server, '_archive_list_title_overlays', lambda: state['titles'])
    monkeypatch.setattr(server, '_load_usage_limit_resumes', lambda: state['resume'])
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: state['parents'])
    monkeypatch.setattr(server._session_graph, 'driver_snapshot', lambda: {})
    monkeypatch.setattr(server._session_graph, 'projection_revision', lambda: state['graph'])
    monkeypatch.setattr(continuation_topics, 'revision', lambda: state['topics'])
    monkeypatch.setattr(server, '_queue_health_stats', lambda rows, now=None: {'at': now})
    monkeypatch.setattr(server, '_trace_row_flicker', lambda *args, **kwargs: None)

    def topics(rows, *, diagnostics=None, check_client=None):
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


def read(rows, *, version=1, context='context', now=10000, window='7d', extra=None,
         projection_context=None, row_layouts=False):
    delta = {'ver': version, 'ctx': context, 'extra': extra if extra is not None else rows[1:]}
    if projection_context is not None:
        delta['projection_ctx'] = projection_context
    payload = server._archive_list_replay_payload(
        rows, delta, window=window, now=now, cached=True, row_layouts=row_layouts)
    payload['delta_token'] = f'{version}.{context}.{now}'
    raw = replay.encode_payload(payload)
    if row_layouts:
        decoded = json.loads(raw)
        layouts = decoded.pop('row_layouts')
        decoded['conversations'] = [dict(zip(layouts[index], values, strict=True))
                                    for index, values in decoded['conversations']]
        assert decoded == {key: value for key, value in payload.items() if key != 'row_layouts'}
    else:
        assert raw == json.dumps(dict(payload)).encode()
    compressed = replay.encode_gzip_payload(payload)
    if compressed is not None:
        assert gzip.decompress(compressed) == replay.encode_payload(payload)
    return payload


def test_packed_projection_replays_overlay_order_and_ticket_layout_changes(inputs):
    rows = fixture_rows()
    second_live = dict(rows[-1], session_id='other', mtime=9700, first_message='Different layout')
    rows.append(second_live)
    first = read(rows, projection_context='stable', row_layouts=True)
    first['conversations'][0]['session_state']['todos'][0]['text'] = 'caller edit'
    rows[1].update(mtime=9800, modified=9800)
    rows[1], rows[2] = rows[2], rows[1]
    inputs['ideas'] = {'live': {'ref': 'TEST-1', 'explicitly_archived': False}}
    updated = read(rows, context='ticket', projection_context='stable', row_layouts=True)
    assert inputs['calls'] == 1
    assert [row['session_id'] for row in updated['conversations']] == ['archive', 'other', 'live']
    assert updated['conversations'][0]['session_state']['todos'] == [{'text': 'nested'}]
    assert updated['conversations'][-1]['idea_ticket']['ref'] == 'TEST-1'
    inputs['ideas'] = {}
    rows[-1].update(mtime=9950, modified=9950)
    replayed = read(rows, projection_context='stable', row_layouts=True)
    assert inputs['calls'] == 1
    assert replayed['conversations'][-1]['mtime'] == 9950
    assert 'idea_ticket' not in replayed['conversations'][-1]


def test_packed_seven_day_replay_keeps_existing_templates_without_admitting_new_rows(inputs, monkeypatch):
    cache = server.ByteBudgetCache(1 << 20, 8)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    rows = fixture_rows()
    server._archive_list_project_row(rows[0], resumes={}, parents={})
    template = cache.get(('codex', 'archive'))

    packed = read(rows, row_layouts=True)
    assert cache.get(('codex', 'archive')) is template
    assert ('codex', 'live') not in cache

    legacy = read(rows)
    assert legacy['conversations'] == packed['conversations']
    assert ('codex', 'live') in cache


def test_packed_seven_day_wire_build_reads_nested_values_without_mutating_source(inputs):
    rows = fixture_rows()
    source_before = copy.deepcopy(rows)
    baseline = read(rows, row_layouts=True)
    server._ARCHIVE_LIST_PROJECTION_CACHE.clear()
    delta = {'ver': 1, 'ctx': 'context', 'extra': rows[1:]}
    wire = server._archive_list_replay_payload(
        rows, delta, window='7d', now=10000, cached=True, row_layouts=True, _wire_only=True)
    wire['delta_token'] = baseline['delta_token']

    assert replay.encode_payload(wire) == replay.encode_payload(baseline)
    assert wire['conversations'][0]['session_state'] is rows[0]['session_state']
    assert rows == source_before

    replayed = read(rows, row_layouts=True)
    replayed['conversations'][0]['session_state']['todos'][0]['text'] = 'caller change'
    assert rows == source_before


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


@pytest.mark.parametrize('snapshot_ticket', [None, {'ref': 'TEST-1', 'closed': False}])
def test_ticket_overlay_changes_reuse_projection_and_restore_snapshot_inputs(inputs, snapshot_ticket):
    rows = fixture_rows()
    if snapshot_ticket is not None:
        rows[0].update(idea_ticket=snapshot_ticket, explicitly_archived=False)
    first = read(rows, projection_context='stable')
    for index, ideas in enumerate([
        {'archive': {'ref': 'TEST-2', 'closed': False, 'explicitly_archived': False}},
        {'archive': {'ref': 'TEST-2', 'closed': True, 'explicitly_archived': True}},
        {},
    ]):
        inputs['ideas'] = ideas
        actual = read(rows, context=f'changed-ticket-{index}', projection_context='stable')
        # Ticket routines see the original snapshot, even after a prior overlay
        # added, removed or archived a ticket. They still run on every replay.
        assert inputs['ticks'][-1][0].get('idea_ticket') == snapshot_ticket
        assert inputs['ticks'][-1][0].get('explicitly_archived') == (False if snapshot_ticket else None)
        expected = server._archive_list_payload(rows, window='7d', now=10000, cached=True)
        expected['delta_token'] = actual['delta_token']
        assert dict(actual) == expected
        assert inputs['calls'] == index + 2, 'only the independent reference reprojects'
    assert first['conversations'][0].get('idea_ticket') == snapshot_ticket
    assert rows[0].get('idea_ticket') == snapshot_ticket


def test_projection_context_excludes_only_live_ticket_map(inputs, monkeypatch):
    rows = fixture_rows()
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    server._archive_serve_cache_store('scope', rows, server._archive_serve_generation)
    base = server._archive_serve_cache['scope']['rows']
    state = lambda: server._archive_list_delta_state('scope', base, '7d', True)
    original = state()
    inputs['ideas'] = {'archive': {'ref': 'TEST-1', 'closed': True}}
    ticket_change = state()
    assert original['ctx'] != ticket_change['ctx'], 'HTTP/delta freshness remains strict'
    assert original['projection_ctx'] == ticket_change['projection_ctx']
    for field, value in [('titles', {'archive': {'display_name': 'Renamed'}}),
                         ('resume', {'archive': 20000}), ('topics', 2), ('graph', (2, 2))]:
        previous = state()
        inputs[field] = value
        assert state()['projection_ctx'] != previous['projection_ctx'], field


def test_ticket_window_membership_changes_still_rebuild_with_stable_projection_context(inputs):
    rows = fixture_rows()
    rows[0]['mtime'] = 1
    rows[1].update(mtime=99999, modified=99999)
    options = dict(window='8h', now=100000, projection_context='stable')
    first = read(rows, **options)
    assert {row['session_id'] for row in first['conversations']} == {'archive', 'live'}
    inputs['ideas'] = {'archive': {'ref': 'TEST-1', 'explicitly_archived': True}}
    actual = read(rows, context='removed-parent', **options)
    assert [row['session_id'] for row in actual['conversations']] == ['live']
    assert inputs['calls'] == 2, 'the complete selected-id guard remains authoritative'


def test_owned_clone_preserves_json_normalization_and_byte_budget():
    original = [{'session_id': 'live', 'mtime': 1,
                 'session_state': {'values': (1, 2), 'keys': {1: 'one'}}}]
    entry = replay.encode_rows(original, {'live'})
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


@pytest.mark.parametrize('projection_context', [None, 'stable'])
def test_clone_that_exceeds_budget_keeps_existing_json_replay(inputs, projection_context):
    rows = fixture_rows()
    read(rows, projection_context=projection_context)
    cache = server._ARCHIVE_LIST_PROJECTION_CACHE
    cache.max_bytes = cache.bytes
    budget = cache.bytes
    rows[1]['mtime'] += 1
    read(rows, now=10001, projection_context=projection_context)
    rows[1]['mtime'] += 1
    third = read(rows, now=10002, projection_context=projection_context)
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
        entry = replay.encode_rows(rows, {'live', 'other'})
        raw = entry[0]
        assert raw == json.dumps(rows).encode()
        assert replay.encode_replay(entry, rows) == raw


def test_encoding_chunks_reuse_only_exact_projected_values(monkeypatch):
    cache = server.ByteBudgetCache(1 << 22, 512)
    monkeypatch.setattr(replay, '_RUN_CACHE', cache)
    rows = [{'id': str(i), 'title': 'שלום', 'nested': {'values': [i, None, False]}}
            for i in range(150)]
    overlays = {'65', '149'}

    def check(current):
        entry = replay.encode_rows(current, overlays)
        raw = entry[0]
        assert raw == json.dumps(current).encode()
        encoded = replay.compress_replay(entry, current)
        assert zlib.decompress(encoded + b'\x03\x00', -15) == raw
        return entry

    first = check(rows)
    count = len(cache)
    check(copy.deepcopy(rows))
    assert len(cache) == count, 'new row objects with the same values reuse chunks'
    check(json.loads(json.dumps(rows)))
    assert len(cache) == count, 'JSON replay loses aliases without changing encoded values'
    rows[70]['nested']['values'][1] = 'changed in place'
    second = check(rows)
    assert len(cache) == count + 1, 'only the changed archive chunk is encoded'
    assert first[2][0] == second[2][0]
    rows[65]['title'] = 'changed overlay'
    check(rows)
    assert len(cache) == count + 1, 'live slots never enter the archive run cache'
    rows[70]['nested']['added'] = None
    check(rows)
    rows[70]['nested'].pop('added')
    check(rows)
    check(list(reversed(rows)))


@pytest.mark.parametrize('change', ['insert', 'remove', 'reorder', 'overlay'])
def test_encoding_reuses_unchanged_runs_after_membership_changes(monkeypatch, change):
    cache = server.ByteBudgetCache(1 << 22, 512)
    monkeypatch.setattr(replay, '_RUN_CACHE', cache)
    rows = [{'session_id': f'session-{i}', 'nested': {'text': f'row {i}'}}
            for i in range(1000)]
    overlays = {'session-500'}

    def check(current):
        entry = replay.encode_rows(current, overlays)
        raw, spans, _ = entry
        assert raw == json.dumps(current).encode()
        assert zlib.decompress(replay.compress_replay(entry, current) + b'\x03\x00', -15) == raw
        assert [current[index]['session_id'] for index, _, _ in spans] == [
            row['session_id'] for row in current if row['session_id'] in overlays]

    check(rows)
    original_runs = len(cache)
    assert original_runs > 5, 'fixture exercises multiple reusable runs'
    changed = copy.deepcopy(rows)
    if change == 'insert':
        changed.insert(0, {'session_id': 'new-session', 'nested': {'text': 'new'}})
    elif change == 'remove':
        changed.pop(0)
    elif change == 'reorder':
        changed.insert(0, changed.pop(1))
    else:
        changed.insert(0, changed.pop(500))
        changed[0]['mtime'] = 123
    check(changed)
    assert len(cache) - original_runs <= 3, 'distant archive runs survive a local list change'
    check(rows)
    assert len(cache) - original_runs <= 3, 'the original immutable runs remain reusable'


def test_encoding_caps_runs_without_identity_boundaries(monkeypatch):
    lengths = []
    encode = replay._encoded_run
    def track_run(rows, **options):
        lengths.append(len(rows))
        return encode(rows, **options)

    monkeypatch.setattr(replay, '_encoded_run', track_run)
    rows = [{'title': str(i)} for i in range(1000)]
    raw, _, _ = replay.encode_rows(rows, set())
    assert raw == json.dumps(rows).encode()
    assert len(lengths) > 1 and max(lengths) <= 256


def test_encoding_chunk_budget_and_custom_json_values(monkeypatch):
    cache = server.ByteBudgetCache(1, 1)
    monkeypatch.setattr(replay, '_RUN_CACHE', cache)
    rows = [{'id': 'archive', 'state': {'values': (1, 2), 'keys': {1: 'one'}}}]
    for _ in range(2):
        entry = replay.encode_rows(rows, set())
        raw = entry[0]
        assert raw == json.dumps(rows).encode()
        assert replay.replay_rows(entry, {}) == json.loads(raw)
    assert len(cache) == 0 and cache.bytes == 0

    class CustomDict(dict):
        def __reduce__(self):
            return dict, ()  # does not describe its JSON-visible contents

    monkeypatch.setattr(replay, '_RUN_CACHE', server.ByteBudgetCache(1 << 20, 10))
    rows[0]['state'] = CustomDict(value=1)
    first, _, _ = replay.encode_rows(rows, set())
    rows[0]['state']['value'] = 2
    second, _, _ = replay.encode_rows(rows, set())
    assert first != second and second == json.dumps(rows).encode()
    assert len(replay._RUN_CACHE) == 0


def test_compressed_runs_preserve_json_and_fresh_overlay_and_envelope():
    for rows in ([], [{'id': 'a'}], [{'id': 'live'}],
                 [{'id': 'a'}, {'id': 'live', 'title': 'שלום'},
                  {'id': 'b'}, {'id': 'other'}, {'id': 'c'}]):
        entry = replay.encode_rows(rows, {'live', 'other'})
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


def test_changed_ticket_rows_keep_current_content_in_chunked_gzip(inputs, monkeypatch):
    assert replay.encode_gzip_payload({'conversations': []}) is None
    cache = server.ByteBudgetCache(1 << 22, 512)
    monkeypatch.setattr(replay, '_RUN_CACHE', cache)
    rows = [dict(fixture_rows()[0], session_id=f'archive-{i}') for i in range(1000)]
    rows.append(fixture_rows()[1])
    options = {'extra': rows[-1:]}
    original = read(rows, **options)
    original_bytes = replay.encode_payload(original)
    original_runs = len(cache)
    assert original_runs > 5

    def changed(projected, ideas):
        projected[501]['goal'] = {'text': 'new ticket data'}
        return True

    monkeypatch.setattr(server, '_archive_list_ticket_updates', changed)
    actual = read(rows, now=10001, **options)
    assert actual['conversations'][501]['goal'] == {'text': 'new ticket data'}
    assert len(cache) == original_runs + 1, 'only the changed archive run is encoded'
    encoded = replay.encode_gzip_payload(actual)
    assert encoded is not None
    decoder = zlib.decompressobj(wbits=31)
    assert decoder.decompress(encoded) == json.dumps(dict(actual)).encode()
    assert decoder.eof and decoder.unused_data == b'', 'one complete gzip member'

    monkeypatch.setattr(server, '_archive_list_ticket_updates', lambda *args: False)
    restored = read(rows, **options)
    assert replay.encode_payload(restored) == original_bytes
    assert len(cache) == original_runs + 1, 'ticket edits did not mutate cached runs'


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


@pytest.mark.parametrize('row_layouts', [False, True])
def test_scoped_http_defers_gzip_and_preserves_the_same_body(inputs, monkeypatch, tmp_path, row_layouts):
    import threading
    import urllib.error
    import urllib.request
    from ccc_server import list_encoding

    rows = fixture_rows()
    rows[0]['first_message'] = 'archive body ' * 120
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_STATE', {'saved': {}, 'warming': set()})
    monkeypatch.setattr(server, '_archive_list_disk_candidate', lambda *a: None)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', server.ByteBudgetCache(1 << 22, 8))
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *a, **kw: (rows, True, 1))
    monkeypatch.setattr(server, '_archive_list_delta_state',
                        lambda *a, **kw: {'ver': 1, 'ctx': 'fixed', 'extra': rows[1:], 'token': '1'})
    monkeypatch.setattr(server, '_queue_health_stats', lambda rows, now=None: {'working': 0})
    monkeypatch.setattr(list_encoding, '_zstd', lambda raw: None)
    monkeypatch.setattr(list_encoding, '_CACHE', server.ByteBudgetCache(1 << 20, 8))
    gzip_builds = []
    compress = gzip.compress

    def track_gzip(raw, **options):
        gzip_builds.append(True)
        return compress(raw, **options)

    monkeypatch.setattr(gzip, 'compress', track_gzip)
    monkeypatch.setattr(replay, '_deflate_fragment',
                        lambda raw: pytest.fail('optional encoding built eager gzip fragments'))
    saved = threading.Event()
    replace = server.os.replace

    def mark_saved(src, dest):
        replace(src, dest)
        if server.Path(dest).parent == tmp_path:
            saved.set()

    monkeypatch.setattr(server.os, 'replace', mark_saved)
    with server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{httpd.server_port}/api/conversations/list?window=7d'
        target = url + '&row_layouts=1' if row_layouts else url

        def read(accept, **headers):
            request = urllib.request.Request(target, headers={'Accept-Encoding': accept, **headers})
            return urllib.request.urlopen(request, timeout=5)

        try:
            with read('zstd, gzip') as fallback:
                assert fallback.headers['Content-Encoding'] == 'gzip'
                etag = fallback.headers['ETag']
                expected = gzip.decompress(fallback.read())
                wire = json.loads(expected)
                assert ('row_layouts' in wire) is row_layouts
            with read('identity') as response:
                assert response.headers['ETag'] == etag
                assert response.read() == expected
            with read('gzip') as response:
                assert response.headers['ETag'] == etag
                assert gzip.decompress(response.read()) == expected
            with pytest.raises(urllib.error.HTTPError) as response:
                read('gzip', **{'If-None-Match': etag})
            assert response.value.code == 304
            response.value.close()
            assert inputs['calls'] == 1
            assert saved.wait(5)
            key = server._archive_response_cache_key()
            saved_key = server._archive_list_body_key(key, row_layouts)
            assert server._archive_list_disk_load(saved_key, '7d')[0] == expected
            assert len(gzip_builds) == 1
            if row_layouts:
                with urllib.request.urlopen(url, timeout=5) as legacy:
                    assert legacy.headers['ETag'] != etag
                    decoded = json.load(legacy)
                layouts = wire.pop('row_layouts')
                wire['conversations'] = [dict(zip(layouts[index], values, strict=True))
                                         for index, values in wire['conversations']]
                assert wire == decoded
        finally:
            httpd.shutdown()
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


def test_overlay_recency_order_reuses_archive_and_preserves_exact_wire_order(inputs):
    rows = fixture_rows()
    rows.append({'session_id': 'second', 'source': 'codex', 'display_name': 'Second',
                 'mtime': 9400, 'modified': 9400, 'session_state': {'todos': ['owned']}})
    first = read(rows)
    rows[1].update(mtime=9990, modified=9990)
    rows[2].update(mtime=9995, modified=9995)
    reordered = [rows[0], rows[2], rows[1]]
    actual = read(reordered, now=10001)
    assert inputs['calls'] == 1
    expected = server._archive_list_payload(reordered, window='7d', now=10001, cached=True)
    expected['delta_token'] = actual['delta_token']
    assert dict(actual) == expected
    assert [r['session_id'] for r in actual['conversations']] == ['archive', 'second', 'live']
    # A caller cannot mutate cached nested containers, even after a reorder.
    actual['conversations'][1]['session_state']['todos'].append('caller')
    again = read(rows, now=10002)
    assert again['conversations'][2]['session_state']['todos'] == ['owned']
    assert first['conversations'][0] == again['conversations'][0]


def test_reordered_overlay_content_changes_still_invalidate_topic_dependencies(inputs):
    rows = fixture_rows() + [{'session_id': 'second', 'source': 'codex', 'display_name': 'Second', 'mtime': 9400}]
    read(rows)
    rows[1]['display_name'] = 'Changed title'
    rows[1]['parent_session_id'] = 'second'
    reordered = [rows[0], rows[2], rows[1]]
    actual = read(reordered)
    assert inputs['calls'] == 2
    expected = server._archive_list_payload(reordered, window='7d', now=10000, cached=True)
    expected['delta_token'] = actual['delta_token']
    assert dict(actual) == expected


def test_overlay_cannot_move_across_unchanged_archive_positions(inputs):
    rows = fixture_rows() + [{'session_id': 'second', 'source': 'codex', 'mtime': 9400}]
    read(rows)
    # The mask changes when an archive row moves between overlay slots.
    reordered = [rows[2], rows[0], rows[1]]
    read(reordered, extra=[rows[2], rows[1]])
    assert inputs['calls'] == 2


def test_overlay_window_membership_swap_invalidates_even_with_same_slot_count(inputs):
    rows = [{'session_id': 'archive', 'source': 'codex', 'mtime': 39000},
            {'session_id': 'left', 'source': 'codex', 'mtime': 39000},
            {'session_id': 'right', 'source': 'codex', 'mtime': 1000}]
    first = read(rows, window='8h', now=40000)
    assert [row['session_id'] for row in first['conversations']] == ['archive', 'left']
    rows[1]['mtime'] = 1000
    rows[2]['mtime'] = 39001
    actual = read(rows, window='8h', now=40000)
    assert inputs['calls'] == 2
    assert [row['session_id'] for row in actual['conversations']] == ['archive', 'right']


def test_diagnostics_distinguish_replay_and_metadata_invalidation(inputs, monkeypatch):
    import collections
    monkeypatch.setattr(server, '_ARCHIVE_LIST_PROJECTION_DIAG_KEYS', collections.OrderedDict())
    rows = fixture_rows()
    delta = {'ver': 1, 'ctx': 'context', 'extra': rows[1:]}
    phases = {}
    first = server._archive_list_replay_payload(rows, delta, now=10000, phases=phases)
    assert phases['projection_cache']['reason'] == 'first-observed'
    rows[1]['mtime'] += 1
    again = server._archive_list_replay_payload(rows, delta, now=10000, phases=phases)
    assert phases['projection_cache']['hit'] is True
    assert again['conversations'][1]['mtime'] != first['conversations'][1]['mtime']
    rows[1]['display_name'] = 'Renamed'
    changed = server._archive_list_replay_payload(rows, delta, now=10000, phases=phases)
    assert phases['projection_cache']['changed'] == ['overlay']
    assert not phases['projection_cache']['hit']
    assert changed['conversations'][1]['display_name'] == 'Renamed'


def test_scoped_projection_reuses_preview_selection_without_changing_wire_bytes(inputs, monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_LIST_INITIAL_SELECTION_CACHE', server.ByteBudgetCache(1 << 20, 8))
    rows = fixture_rows()
    rows.insert(0, {'session_id': 'expired', 'mtime': 1})
    now = 1000000
    rows[-1]['mtime'] = now
    delta = {'ver': 1, 'ctx': 'context', 'extra': rows[-1:]}
    options = {'window': '7d', 'now': now, 'cached': True, 'row_layouts': True, '_wire_only': True}
    baseline = server._archive_list_replay_payload(rows, delta, **options)

    server._ARCHIVE_LIST_PROJECTION_CACHE.clear()
    version = ('default', delta['ver'])
    server._archive_list_initial_payload(rows, now=now, window='7d', selection_version=version)
    phases = {}
    reused = server._archive_list_replay_payload(
        rows, delta, phases=phases, selection_version=version, **options)

    assert phases['window_selection']['hit'] is True
    assert [row['session_id'] for row in reused['conversations']] == ['live', 'archive']
    assert replay.encode_payload(reused) == replay.encode_payload(baseline)
