"""Hot memory/cold disk parity and transactional archive publication."""
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from ccc_server import archive_preview, archive_store as store
from tests.test_archive_snapshot import snapshot

NOW = 2_000_000_000


def data(stamp=10):
    result = snapshot(stamp=stamp)
    result['entries']['base']['conversations'] = [
        {'session_id': 'old', 'mtime': NOW - 20 * 86400, 'first_message': 'history'},
        {'session_id': 'recent', 'mtime': NOW, 'parent_session_id': 'parent'},
        {'session_id': 'parent', 'mtime': NOW - 30 * 86400},
        {'session_id': 'pinned', 'mtime': NOW - 30 * 86400, 'pinned': True},
    ]
    return result


def test_hot_read_decodes_only_hot_bodies_and_preserves_old_parent(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    original = data()
    assert store.save(target, original, complete=True) == 4
    decoded = []
    loads = json.loads
    monkeypatch.setattr(store.json, 'loads', lambda body: decoded.append(body) or loads(body))
    hot = store.load(target, hot=True, now=NOW)
    rows = hot['entries']['base']['conversations']
    assert [r['session_id'] for r in rows] == ['recent', 'parent', 'pinned']
    assert not any('history' in body for body in decoded)
    assert store.load(target) == original


def test_full_read_preserves_batch_boundaries_and_legacy_bodies(tmp_path):
    target = tmp_path / 'archive.json'
    original = data()
    original['entries']['base']['conversations'].extend(
        {'session_id': f'archive-{i}', 'first_message': f'Note {i}: \u2603 [{{}}]'}
        for i in range(400)
    )
    store.save(target, original, complete=True)
    with sqlite3.connect(store.database_path(target)) as conn:
        conn.execute("UPDATE rows SET body = CAST(body AS BLOB) WHERE sid = 'archive-201'")
    assert store.load(target) == original

    with sqlite3.connect(store.database_path(target)) as conn:
        conn.execute("UPDATE rows SET body = '{} , {}' WHERE sid = 'archive-392'")
    with pytest.raises(json.JSONDecodeError):
        store.load(target)


def test_history_read_merges_live_rows_at_stored_positions(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    original = data()
    rows = original['entries']['base']['conversations']
    rows[1].update(first_message='superseded text', engine='claude')
    rows.append({'session_id': 'recent', 'engine': 'codex'})
    rows.append({'first_message': 'row without a session ID'})
    store.save(target, original, complete=True)
    appended = {'session_id': 'new'}
    live = dict(rows[1], first_message='live text')
    decoded = []
    loads = json.loads
    monkeypatch.setattr(store.json, 'loads', lambda body: decoded.append(body) or loads(body))
    options = dict(schema_version=original['schema_version'], build_id=original['build_id'],
                   min_cached_at=10)

    result = store.load_with_overrides(target, 'base', [appended, live], **options)

    assert result['conversations'] == [rows[0], live, *rows[2:], appended]
    assert result['conversations'][1] is live
    assert not any('superseded text' in body for body in decoded)
    assert store.load_with_overrides(target, 'base', [{}], **options) is None
    assert store.load_with_overrides(target, 'base', [live], **dict(options, min_cached_at=11)) is None
    assert store.load_with_overrides(target, 'base', [live], **dict(options, build_id='older')) is None


def test_decision_inbox_scoped_source_preserves_live_archive_inputs(tmp_path, monkeypatch):
    import server
    from ccc_server import decision_inbox

    target = tmp_path / 'archive.json'
    original = data()
    original.update(schema_version=server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
                    build_id=server._ARCHIVE_RESPONSE_CACHE_BUILD_ID)
    key = server._archive_response_cache_key()
    entry = original['entries'].pop('base')
    original['entries'][key] = entry
    _, recent, _, pinned = entry['conversations']
    duplicate = {'session_id': 'old', 'engine': 'claude', 'source': 'import'}
    deleted = {'session_id': 'deleted', 'engine': 'codex'}
    entry['conversations'].extend([duplicate, deleted])
    store.save(target, original, complete=True)
    latest = dict(recent, first_message='live input')
    new = {'session_id': 'new', 'first_message': 'not yet published'}
    hot = [new, latest, pinned, deleted]
    live_ids = {'old', 'recent', 'new', 'overlay', 'deleted'}
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {key: {'cached_at': 10}})
    monkeypatch.setattr(server, '_archive_serve_hot_rows_versioned',
                        lambda *a, **kw: (hot, True, 0))
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets',
                        lambda: ({'old'}, set()))
    monkeypatch.setattr(server, '_load_session_lane_overrides', lambda: {})
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda: {'deleted'})
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *_: pytest.fail('unexpected full history read'))
    seen = []

    def overlays(rows, existing, phase):
        seen.append(existing)
        return rows + [{'session_id': 'other'}, {'session_id': 'overlay'}], []

    monkeypatch.setattr(server, '_archive_list_source_overlays', overlays)
    decoded = []
    loads = json.loads
    monkeypatch.setattr(store.json, 'loads', lambda body: decoded.append(body) or loads(body))
    result = decision_inbox._server_rows(live_ids)
    assert [r['session_id'] for r in result] == ['old', 'recent', 'old', 'new', 'overlay']
    assert result[0]['archived'] and result[2]['archived']
    assert result[1]['first_message'] == 'live input'
    assert 'archived' not in latest and 'archived' not in new
    assert seen == [{'old', 'recent', 'new'}]
    assert not any('"session_id":"pinned"' in body or '"session_id":"parent"' in body
                   for body in decoded)
    assert decision_inbox._server_rows(set()) == []
    assert decision_inbox._server_rows({'recent'})[0]['first_message'] == 'live input'
    assert len(seen) == 1

    # A newer in-memory generation cannot be merged with an older store.
    server._ARCHIVE_RESPONSE_CACHE[key]['cached_at'] = 11
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *_: ([latest, pinned], True, 0))
    assert decision_inbox._server_rows({'recent'}) == [latest]


def test_sidebar_lifecycle_overlay_leaves_borrowed_history_unchanged(tmp_path, monkeypatch):
    import server

    target = tmp_path / 'archive.json'
    original = data()
    original.update(schema_version=server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
                    build_id=server._ARCHIVE_RESPONSE_CACHE_BUILD_ID)
    old, live, parent, pinned = original['entries']['base']['conversations']
    old.update(archived=False, trashed=False, all_lane_override='')
    parent['engine'] = 'codex'
    store.save(target, original, complete=True)
    reader = store._SidebarHistoryReader()
    monkeypatch.setattr(store, '_SIDEBAR_HISTORY_READER', reader)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_archive_response_cache_get', lambda key: original['entries'][key])
    monkeypatch.setattr(server, '_archive_serve_hot_rows_versioned',
                        lambda *args, **kwargs: ([live], True, 1))
    archived, trashed, lanes, deleted = set(), set(), {}, set()
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (archived, trashed))
    monkeypatch.setattr(server, '_load_session_lane_overrides', lambda: lanes)
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda: deleted)

    def read():
        rows, _, _ = server._archive_serve_rows_versioned(
            'base', {}, copy_rows=False, reuse_sidebar_history=True)
        return rows

    try:
        first = read()
        archived.add('old')
        trashed.add('old')
        lanes['old'] = 'other'
        deleted.add('parent')
        changed = read()
        assert [row['session_id'] for row in changed] == ['old', 'recent', 'pinned']
        assert (changed[0]['archived'], changed[0]['trashed'], changed[0]['all_lane_override']) == (
            True, True, 'other')
        assert not (first[0]['archived'] or first[0]['trashed'] or first[0]['all_lane_override'])

        archived.clear()
        trashed.clear()
        lanes.clear()
        deleted.clear()
        restored = read()
        assert restored == first
        assert restored[0] is first[0]  # The same SQLite memo survives overlay changes.
        assert changed[0]['trashed']
        assert 'archived' not in live
    finally:
        with reader.lock:
            reader._close()


def test_sidebar_history_reuses_cold_rows_with_fresh_overrides_and_detects_peer_commits(
        tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    original = data()
    rows = original['entries']['base']['conversations']
    store.save(target, original, complete=True)
    reader = store._SidebarHistoryReader()
    monkeypatch.setattr(store, '_SIDEBAR_HISTORY_READER', reader)
    decode = store._decode_full_batch
    decoded = []

    def track(bodies):
        decoded.append(len(bodies))
        return decode(bodies)

    monkeypatch.setattr(store, '_decode_full_batch', track)
    options = dict(schema_version=original['schema_version'], build_id=original['build_id'],
                   min_cached_at=10, reuse_sidebar_history=True)

    def read(live):
        return store.load_with_overrides(target, 'base', live, **options)['conversations']

    try:
        latest = dict(rows[1], first_message='fresh')
        first = read([rows[1]])
        first[0]['archived'] = True
        assert read([latest]) == [rows[0], latest, *rows[2:]]
        assert decoded == [3]

        # Both an extra session and an override of a previously decoded row
        # reuse the same layout. Removing them restores the original order.
        appended = {'session_id': 'appended'}
        replaced = dict(rows[0], first_message='live replacement')
        assert read([appended, latest, replaced]) == [replaced, latest, *rows[2:], appended]
        assert read([latest]) == [rows[0], latest, *rows[2:]]
        assert decoded == [3]

        # A peer can change a body without changing the variant header.
        updated = dict(rows[0], first_message='peer update')
        with sqlite3.connect(store.database_path(target)) as peer:
            peer.execute("UPDATE rows SET body=? WHERE sid='old'", (json.dumps(updated),))
        assert read([latest]) == [updated, latest, *rows[2:]]
        assert decoded == [3, 3]

        # The originally skipped body still needs to be read if its live
        # override disappears.
        memo = reader.memo
        assert read([appended, updated]) == [updated, *rows[1:], appended]
        assert decoded == [3, 3, 1]
        assert reader.memo is memo
        reader._expire(reader.memo[0])
        assert reader.conn is None and reader.memo is None
        assert read([latest]) == [updated, latest, *rows[2:]]
        assert decoded == [3, 3, 1, 3]
    finally:
        with reader.lock:
            reader._close()


def test_sidebar_history_retries_sparse_recovery_if_peer_commits(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    original = data()
    rows = original['entries']['base']['conversations']
    store.save(target, original, complete=True)
    reader = store._SidebarHistoryReader()
    monkeypatch.setattr(store, '_SIDEBAR_HISTORY_READER', reader)
    options = dict(schema_version=original['schema_version'], build_id=original['build_id'],
                   min_cached_at=10, reuse_sidebar_history=True, copy_history=False)
    recover = store._recover_sidebar_history
    updated = dict(rows[0], first_message='peer update')

    def publish_then_recover(conn, key, missing, version):
        with sqlite3.connect(store.database_path(target)) as peer:
            peer.execute("UPDATE rows SET body=? WHERE sid='old'", (json.dumps(updated),))
        return recover(conn, key, missing, version)

    try:
        live = dict(rows[1], first_message='live')
        assert store.load_with_overrides(target, 'base', [live], **options)['conversations'] == [
            rows[0], live, *rows[2:]]
        monkeypatch.setattr(store, '_recover_sidebar_history', publish_then_recover)
        assert store.load_with_overrides(target, 'base', [], **options)['conversations'] == [
            updated, *rows[1:]]
    finally:
        with reader.lock:
            reader._close()


def test_sidebar_static_projection_keeps_request_overlays_live(tmp_path, monkeypatch):
    import server
    from ccc_server import continuation_topics

    target = tmp_path / 'archive.json'
    original = data()
    old, live, *_ = original['entries']['base']['conversations']
    old['session_state'] = {'todos': [{'text': 'stored'}]}
    store.save(target, original, complete=True)
    reader = store._SidebarHistoryReader()
    monkeypatch.setattr(store, '_SIDEBAR_HISTORY_READER', reader)
    monkeypatch.setattr(continuation_topics, 'attach_topics', lambda *args, **kwargs: None)
    monkeypatch.setattr(server, '_archive_list_ticket_updates', lambda *args: False)
    monkeypatch.setattr(server, '_queue_health_stats', lambda *args, **kwargs: {})
    monkeypatch.setattr(server, '_trace_row_flicker', lambda *args, **kwargs: None)
    graph_parents = {}
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: graph_parents)
    options = dict(schema_version=original['schema_version'], build_id=original['build_id'],
                   min_cached_at=10, reuse_sidebar_history=True)

    def read(current, **kwargs):
        return store.load_with_overrides(
            target, 'base', [current], **options, **kwargs)['conversations']

    def payload(source, projection=None, overlays=None, phases=None):
        return server._archive_list_payload(
            source, _retain_templates=False, _row_overlays=overlays or {},
            _history_projection=projection, phases=phases)

    try:
        writable = read(live)
        assert store.sidebar_history_projection() is None
        writable[0]['session_state']['todos'][0]['text'] = 'caller edit'
        source = read(live, copy_history=False)
        assert source[0]['session_state'] == old['session_state']
        projection = store.sidebar_history_projection()
        assert projection is not None
        original_expiry = reader.memo[0]
        phases = {}
        first = payload(source, projection, phases=phases)
        assert phases['history_static_rows'] == {'hits': 0, 'prepared': 3}
        assert json.dumps(first) == json.dumps(payload(source))
        renewed_expiry = reader.memo[0]
        assert renewed_expiry is not original_expiry
        reader._expire(original_expiry)
        assert store.sidebar_history_projection() is projection

        graph_parents['old'] = 'parent'
        overlays = {'old': {'display_name': 'Current title'}}
        latest = read(dict(live, first_message='live change'), copy_history=False)
        assert store.sidebar_history_projection() is projection
        phases = {}
        second = payload(latest, projection, overlays, phases)
        assert reader.memo[0] is renewed_expiry
        assert phases['history_static_rows'] == {'hits': 3, 'prepared': 0}
        assert json.dumps(second) == json.dumps(payload(latest, overlays=overlays))
        assert second['conversations'][0]['display_name'] == 'Current title'
        assert 'parent_session_id' not in first['conversations'][0]
        assert second['conversations'][0]['parent_session_id'] == 'parent'
        assert second['conversations'][1]['first_message'] == 'live change'

        # A lifecycle overlay creates a new source row and bypasses the old
        # preparation. The original layout still describes the stored row.
        lifecycle = [dict(latest[0], archived=True), *latest[1:]]
        assert payload(lifecycle, projection)['conversations'][0]['archived']
        assert not payload(latest, projection)['conversations'][0].get('archived')

        reader._expire(reader.memo[0])
        assert store.sidebar_history_projection() is None
        renewed = read(live, copy_history=False)
        assert store.sidebar_history_projection() is not projection
        replacement_expiry = reader.memo[0]
        reader.publish_projection(projection, projection.rows)
        assert reader.memo[0] is replacement_expiry
        assert renewed[0]['session_state'] == old['session_state']

        # A delayed timer must not let a late publisher renew an expired memo.
        replacement_projection = store.sidebar_history_projection()
        reader.expires_at = 0
        reader.publish_projection(replacement_projection, projection.rows)
        assert replacement_projection.rows is None
        assert reader.memo[0] is replacement_expiry
    finally:
        with reader.lock:
            reader._close()


def test_full_sidebar_response_renews_only_its_current_history_lease(monkeypatch):
    now = 100.0
    monkeypatch.setattr(store, 'time', SimpleNamespace(monotonic=lambda: now))
    reader = store._SidebarHistoryReader()
    previous = store.SidebarHistoryProjection([])
    projection = store.SidebarHistoryProjection([])
    reader.memo = (None, projection)
    try:
        reader._arm_expiry()
        initial_marker = reader.memo[0]
        now = 110.0
        reader.renew_after_full_response(previous)
        assert reader.memo[0] is initial_marker

        reader.renew_after_full_response(projection)
        renewed_marker = reader.memo[0]
        assert renewed_marker is not initial_marker
        assert reader.expires_at == 110.0 + store._SIDEBAR_HISTORY_REUSE_S
        reader._expire(initial_marker)
        assert reader.memo[0] is renewed_marker

        now = 115.0
        reader.renew_after_full_response(projection)
        assert reader.memo[0] is renewed_marker

        replacement = store.SidebarHistoryProjection([])
        reader.memo = (renewed_marker, replacement)
        now = reader.expires_at
        reader.renew_after_full_response(replacement)
        assert reader.memo[0] is renewed_marker
        assert not replacement.full_response_served
    finally:
        reader._close()


def test_unchanged_cold_rows_are_not_rewritten(tmp_path):
    target = tmp_path / 'archive.json'
    store.save(target, data(), complete=True)
    updated = data(20)
    updated['entries']['base']['conversations'][1]['first_message'] = 'new'
    assert store.save(target, updated, complete=True) == 1
    assert store.load(target) == updated
    assert store.save(target, data(5), complete=True) == 0
    assert store.load(target) == updated


def test_hot_publication_cannot_delete_cold_rows_but_complete_build_can(tmp_path):
    target = tmp_path / 'archive.json'
    store.save(target, data(), complete=True)
    hot = data(20)
    hot['entries']['base']['conversations'] = hot['entries']['base']['conversations'][1:]
    store.save(target, hot)
    assert len(store.load(target)['entries']['base']['conversations']) == 4
    hot['entries']['base']['cached_at'] = 30
    store.save(target, hot, complete=True)
    assert len(store.load(target)['entries']['base']['conversations']) == 3


def test_row_order_updates_without_rewriting_bodies(tmp_path):
    target = tmp_path / 'archive.json'
    store.save(target, data(), complete=True)
    newer = data(20)
    newer['entries']['base']['conversations'].reverse()
    assert store.save(target, newer, complete=True) == 0
    assert store.load(target) == newer


def test_missing_database_is_not_created_by_read(tmp_path):
    target = tmp_path / 'archive.json'
    assert store.load(target) is None
    assert not store.database_path(target).exists()


def test_failed_transaction_preserves_previous_rows(tmp_path):
    target = tmp_path / 'archive.json'
    store.save(target, data(), complete=True)
    newer = data(20)
    newer['entries']['base']['conversations'][0]['non_json'] = object()
    with pytest.raises(TypeError):
        store.save(target, newer, complete=True)
    assert store.load(target) == data()


def test_ancestor_cycle_terminates_and_both_implementations_agree(tmp_path):
    target = tmp_path / 'archive.json'
    original = data()
    original['entries']['base']['conversations'][2]['parent_session_id'] = 'recent'
    store.save(target, original, complete=True)
    assert store.load(target, hot=True, now=NOW)['entries']['base']['conversations'] == store.hot_rows(original['entries']['base']['conversations'], now=NOW)


def test_new_pin_lookup_returns_only_target_and_ancestors(tmp_path):
    target = tmp_path / 'archive.json'
    store.save(target, data(), complete=True)
    rows = store.lookup(target, 'base', ['recent'])
    assert [r['session_id'] for r in rows] == ['recent', 'parent']


def test_newer_row_epoch_cannot_be_overwritten_by_old_builder(tmp_path):
    target = tmp_path / 'archive.json'
    current = data()
    current.update(schema_version=42, build_id='42')
    store.save(target, current, complete=True)
    assert store.save(target, data(20), complete=True) == 0
    assert store.load(target) == current


def test_cold_lookup_masks_outdated_provenance(tmp_path):
    target = tmp_path / 'archive.json'
    current = data()
    current['entries']['base']['conversations'][0]['turn_driver'] = {'kind': 'owner'}
    store.save(target, current, complete=True)
    rows = store.lookup(target, 'base', ['old'], schema_version=42)
    assert rows[0]['turn_driver'] is None
    assert rows[0]['last_owner_input_at'] == 0


def test_empty_sidebar_never_waits_for_build_lock(tmp_path, monkeypatch):
    import server
    import threading
    from types import SimpleNamespace
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', tmp_path / 'archive.json')
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_LOADED', True)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {})
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_archive_serve_refreshing', {})
    monkeypatch.setattr(server, '_archive_refresh_load_blocked', lambda now: False)
    monkeypatch.setattr(server, '_archive_refresh_slot_available', lambda *args: True)
    monkeypatch.setattr(server, '_archive_refresh_mark_started', lambda *args: None)
    started = []
    monkeypatch.setattr(server.threading, 'Thread', lambda **kwargs: SimpleNamespace(start=lambda: started.append(kwargs)))
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(server, '_ARCHIVE_BUILD_GLOBAL_LOCK', lock)
    monkeypatch.setattr(server, '_archive_compute_rows', lambda *args, **kwargs: pytest.fail('request-thread rebuild'))
    rows, cached, version = server._archive_serve_rows_versioned('base', {}, defer_cold=True, window='1d')
    assert rows == [] and cached and version == 0
    assert started[0]['target'] is server._archive_serve_refresh
    lock.release()


@pytest.mark.parametrize("bump", [False, True])
def test_server_keeps_only_hot_rows_but_all_history_is_available(tmp_path, monkeypatch, bump):
    import time
    import server
    target = tmp_path / 'archive.json'
    original = data(time.time())
    original.update(schema_version=server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
                    build_id=server._ARCHIVE_RESPONSE_CACHE_BUILD_ID)
    for row in original['entries']['base']['conversations']:
        row['mtime'] -= NOW - time.time()
    store.save(target, original, complete=True)
    if bump:
        version = server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION + 1
        monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION', version)
        monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_BUILD_ID', str(version))
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_LOADED', False)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {})
    monkeypatch.setattr(server, '_ARCHIVE_STATMAP_BY_SIG', {})
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_archive_refresh_load_blocked', lambda now: True)
    monkeypatch.setattr(server, '_overlay_conversation_lifecycle_flags', lambda rows, **kwargs: rows)
    monkeypatch.setattr(server, '_pinned_rank_map', lambda: {})
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: {})
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: {})
    monkeypatch.setattr(server, '_archive_compute_rows', lambda *a, **kw: pytest.fail('synchronous build'))
    hot, _, _ = server._archive_serve_rows_versioned('base', {}, window='1d', defer_cold=True)
    assert {r['session_id'] for r in hot} == {'recent', 'parent', 'pinned'}
    assert len(server._ARCHIVE_RESPONSE_CACHE['base']['conversations']) == 3
    full, _, _ = server._archive_serve_rows_versioned('base', {}, defer_cold=True)
    assert {r['session_id'] for r in full} == {'old', 'recent', 'parent', 'pinned'}
    assert len(server._archive_serve_cache['base']['rows']) == 3
    # A pin set after publication retrieves only that cold session.
    monkeypatch.setattr(server, '_pinned_rank_map', lambda: {'old': 0})
    monkeypatch.setattr(server, '_apply_pinned_conversation_fields',
                        lambda rows: [r.update(pinned=r['session_id'] == 'old') for r in rows])
    hot, _, _ = server._archive_serve_rows_versioned('base', {}, window='1d', defer_cold=True)
    old = next(r for r in hot if r['session_id'] == 'old')
    assert old['pinned']


def test_rehydration_only_visits_hot_rows(tmp_path, monkeypatch):
    import time
    import server
    target = tmp_path / 'archive.json'
    original = data()
    rows = original['entries']['base']['conversations']
    for row in rows:
        row['mtime'] -= NOW - time.time()
    store.save(target, original, complete=True)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    visited = []
    def rehydrate(hot):
        visited.extend(r['session_id'] for r in hot)
        return [dict(r, state='idle') for r in hot]
    monkeypatch.setattr(server, '_rehydrate_archive_hot_rows', rehydrate)
    result = server._rehydrate_archive_cached_rows(rows)
    assert visited == ['recent', 'parent', 'pinned']
    assert result[0] is rows[0]  # cold row is unchanged, not re-probed/copied
    assert [r['session_id'] for r in result] == [r['session_id'] for r in rows]


def _publish_variant(target, key, start):
    start.wait(10)
    incoming = data()
    incoming['entries'] = {key: incoming['entries']['base']}
    store.save(target, incoming, complete=True)


def test_independent_publishers_preserve_each_variant(tmp_path):
    import multiprocessing
    target = tmp_path / 'archive.json'
    store.save(target, data(), complete=True)
    ctx = multiprocessing.get_context('spawn')
    start = ctx.Event()
    workers = [ctx.Process(target=_publish_variant, args=(target, key, start)) for key in ('prs', 'worktrees')]
    try:
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
    assert set(store.load(target)['entries']) == {'base', 'prs', 'worktrees'}


def test_contended_transaction_cannot_destroy_previous_snapshot(tmp_path):
    target = tmp_path / 'archive.json'
    store.save(target, data(), complete=True)
    con = store._connect(target, write=True)
    try:
        con.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            store.save(target, data(20), complete=True)
    finally:
        con.rollback()
        con.close()
    assert store.load(target) == data()


def test_legacy_import_finishing_before_publish_still_bounds_memory(tmp_path, monkeypatch):
    import time
    import server
    from types import SimpleNamespace
    target = tmp_path / 'archive.json'
    original = data(time.time())
    original.update(schema_version=server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
                    build_id=server._ARCHIVE_RESPONSE_CACHE_BUILD_ID)
    original['entries']['base']['conversations'] = [
        {'session_id': str(i), 'mtime': time.time() - 30 * 86400} for i in range(300)
    ]
    target.write_text(json.dumps(original))
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {})
    monkeypatch.setattr(server, '_ARCHIVE_STATMAP_BY_SIG', {})
    monkeypatch.setattr(server.threading, 'Thread',
                        lambda target, args, **kw: SimpleNamespace(start=lambda: target(*args)))
    server._read_archive_response_cache_file()
    assert server._ARCHIVE_RESPONSE_CACHE['base']['conversations'] == []
    assert len(store.load(target)['entries']['base']['conversations']) == 300


@pytest.mark.parametrize('endpoint', ['/api/conversations/all', '/api/conversations/list?window=1d'])
def test_real_http_cold_archive_responds_without_a_synchronous_rebuild(tmp_path, monkeypatch, endpoint):
    import server
    import threading
    import urllib.request
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', tmp_path / 'archive.json')
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_LOADED', True)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {})
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_archive_refresh_load_blocked', lambda now: True)
    monkeypatch.setattr(server, '_archive_compute_rows', lambda *a, **kw: pytest.fail('request-thread rebuild'))
    monkeypatch.setattr(server, '_archive_list_disk_candidate', lambda *a, **kw: None)
    for name in ('_archive_overlay_codex_sessions', '_archive_overlay_acp_sessions',
                 '_archive_overlay_devin_cli_sessions', '_archive_overlay_wt_worker_sessions',
                 '_archive_overlay_claude_registry_sessions'):
        monkeypatch.setattr(server, name, lambda *a, **kw: [])
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_port}' + endpoint, timeout=5) as response:
            result = json.load(response)
        assert result['count'] == 0
        assert result['conversations'] == []
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(5)


def test_legacy_backup_is_complete_private_and_never_replaced(tmp_path):
    target = tmp_path / 'archive.json'
    target.write_text(json.dumps(data()))
    store.backup_legacy(target)
    backup = target.with_suffix('.legacy.json')
    assert json.loads(backup.read_text()) == data()
    assert backup.stat().st_mode & 0o777 == 0o600
    target.write_text('{}')
    store.backup_legacy(target)
    assert json.loads(backup.read_text()) == data()


def test_inventory_publication_is_small_current_and_complete(tmp_path):
    target = tmp_path / 'archive.json'
    original = data()
    original['entries']['base']['conversations'][0].update(
        first_message='x' * 500, parser_detail={'large': 'private body' * 1000},
        model={'nested': [1]})
    store.save(target, original, complete=True)
    version = original['schema_version']
    projected = store.load_inventory(target, 'base', schema_version=version)
    assert projected['conversations'] == [store.inventory_row(r) for r in original['entries']['base']['conversations']]
    assert len(projected['conversations'][0]['first_message']) == 160
    assert 'parser_detail' not in projected['conversations'][0]
    projected['conversations'][0]['display_name'] = 'caller mutation'
    projected['conversations'][0]['model']['nested'].append(2)
    assert 'display_name' not in store.load_inventory(target, 'base', schema_version=version)['conversations'][0]
    assert store.load_inventory(target, 'base', schema_version=version)['conversations'][0]['model'] == {'nested': [1]}
    assert store.load_inventory(target, 'base', schema_version=version + 1) is None
    newer = data(20)
    newer['entries']['base']['conversations'][0]['display_name'] = 'New title'
    store.save(target, newer, complete=True)
    assert store.load_inventory(target, 'base', schema_version=version)['conversations'][0]['display_name'] == 'New title'
    store.save(target, original, complete=True)
    assert store.load_inventory(target, 'base', schema_version=version)['cached_at'] == 20
    hot = data(30)
    hot['entries']['base']['conversations'] = hot['entries']['base']['conversations'][1:]
    store.save(target, hot)
    assert len(store.load_inventory(target, 'base', schema_version=version)['conversations']) == 4
    hot['entries']['base']['cached_at'] = 40
    store.save(target, hot, complete=True)
    assert len(store.load_inventory(target, 'base', schema_version=version)['conversations']) == 3


def test_inventory_old_or_incomplete_store_requires_compatibility_read(tmp_path):
    target = tmp_path / 'archive.json'
    original = data()
    store.save(target, original, complete=True)
    version = original['schema_version']
    con = sqlite3.connect(store.database_path(target))
    try:
        con.execute("UPDATE session_inventory SET digest='outdated' WHERE identity=(SELECT identity FROM session_inventory LIMIT 1)")
        con.commit()
        assert store.load_inventory(target, 'base', schema_version=version) is None
        con.execute('DROP TABLE session_inventory')
        con.commit()
        assert store.load_inventory(target, 'base', schema_version=version) is None
    finally:
        con.close()
    original['entries']['base']['cached_at'] += 1
    assert store.save(target, original, complete=True) == 0
    assert len(store.load_inventory(target, 'base', schema_version=version)['conversations']) == 4


def test_concurrent_cold_inventory_reads_share_decode_and_keep_their_filters(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    original = data()
    store.save(target, original, complete=True)
    waiting = threading.Event()
    entered = threading.Event()
    calls = []
    read_inventory = store._read_inventory_generation

    class ObservableFuture(Future):
        def result(self, timeout=None):
            waiting.set()
            return super().result(timeout)

    def paused_read(*args):
        calls.append(1)
        entered.set()
        assert waiting.wait(timeout=5)
        return read_inventory(*args)

    monkeypatch.setattr(store, 'Future', ObservableFuture)
    monkeypatch.setattr(store, '_read_inventory_generation', paused_read)

    def load(**filters):
        return store.load_inventory(target, 'base', schema_version=original['schema_version'],
                                    **filters)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(load, limit=1)
        assert entered.wait(timeout=5)
        second = pool.submit(load, limit=2, skip_identities={store.identity(
            original['entries']['base']['conversations'][0])})
        assert [r['session_id'] for r in first.result(timeout=5)['conversations']] == ['old']
        assert [r['session_id'] for r in second.result(timeout=5)['conversations']] == [
            'recent', 'parent']
    assert calls == [1]


def test_inventory_generation_can_retry_failed_read(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    original = data()
    store.save(target, original, complete=True)

    def unavailable(*args):
        raise sqlite3.OperationalError('temporarily unavailable')

    with monkeypatch.context() as patch:
        patch.setattr(store, '_read_inventory_generation', unavailable)
        with pytest.raises(sqlite3.OperationalError, match='temporarily unavailable'):
            store.load_inventory(target, 'base', schema_version=original['schema_version'])

    result = store.load_inventory(target, 'base', schema_version=original['schema_version'])
    assert result['conversations'][0]['session_id'] == 'old'


@pytest.mark.parametrize('body_size', [64, 33 << 10])
def test_sidebar_history_scoped_batch_matches_pinned_canonical_rows(tmp_path, monkeypatch, body_size):
    target = tmp_path / 'archive.json'
    original = data()
    rows = original['entries']['base']['conversations']
    rows.extend({'session_id': f'old-{i}', 'first_message': 'é\0' + 'a' * body_size}
                for i in range(34))
    store.save(target, original, complete=True, sidebar_key='base')
    criteria = dict(schema_version=original['schema_version'], build_id=original['build_id'],
                    max_rows=len(rows))
    scope = {row['session_id'] for row in rows}
    legacy_reads = []
    load_override = store._load_override_entry

    def record_legacy_read(*args, **kwargs):
        legacy_reads.append(True)
        return load_override(*args, **kwargs)

    monkeypatch.setattr(store, '_load_override_entry', record_legacy_read)
    updated = data(stamp=11)
    with store.sidebar_history_cut(target, 'base', **criteria) as (_, hydrate):
        store.save(target, updated, complete=True, sidebar_key='base')
        assert hydrate([], scope, check_client=lambda: None) == rows
    assert bool(legacy_reads) == (body_size > store._HISTORY_BODY_BATCH_MAX_ROW_BYTES)


def test_sidebar_history_cut_keeps_candidate_bodies_in_the_ordered_inventory_cut(tmp_path):
    target = tmp_path / 'archive.json'
    original = data()
    store.save(target, original, complete=True, sidebar_key='base')
    with closing(sqlite3.connect(store.database_path(target))) as writer, writer:
        writer.execute('DROP INDEX rows_sidebar_history')
    updated = data(stamp=11)
    updated['entries']['base']['conversations'][0]['first_message'] = 'new publication'
    criteria = dict(schema_version=original['schema_version'], build_id=original['build_id'], max_rows=4)

    with store.sidebar_history_cut(target, 'base', **criteria) as view:
        inventory, hydrate = view
        assert [row[0] for row in inventory] == ['old', 'recent', 'parent', 'pinned']
        store.save(target, updated, complete=True, sidebar_key='base')
        assert hydrate([{'session_id': 'new'}], {'old', 'new'}) == [
            original['entries']['base']['conversations'][0], {'session_id': 'new'},
        ]

    with closing(sqlite3.connect(store.database_path(target))) as reader:
        indexes = {row[1] for row in reader.execute("PRAGMA index_list('rows')")}
        assert 'rows_sidebar_history' in indexes
    with store.sidebar_history_cut(target, 'base', **criteria) as view:
        inventory, hydrate = view
        assert hydrate([], {'old'}) == [updated['entries']['base']['conversations'][0]]
        last_digest = inventory[0][3]

    # A complete publication can leave the generation's timestamp unchanged.
    edited = dict(updated['entries']['base']['conversations'][0], first_message='same timestamp, new data')
    body = json.dumps(edited)
    rid = store.identity(edited)
    with closing(sqlite3.connect(store.database_path(target))) as writer, writer:
        writer.execute(
            "UPDATE rows SET body=?, digest=? WHERE variant='base' AND identity=?",
            (body, hashlib.sha256(body.encode()).hexdigest(), rid))
        archive_preview.publish(writer, 'base', rid, edited)
        archive_preview.finish_publication(writer, 'base', 11)
    with store.sidebar_history_cut(target, 'base', **criteria) as view:
        inventory, hydrate = view
        assert inventory[0][3] != last_digest
        assert hydrate([], {'old'}) == [edited]

    with store.sidebar_history_cut(target, 'base', **{**criteria, 'max_rows': 3}) as view:
        assert view is None


@pytest.mark.parametrize('covering', [False, True])
@pytest.mark.parametrize('parent', ['parent\0id', b'\xff', None])
def test_sidebar_history_inventory_preserves_values_and_rowid_ties(parent, covering):
    with closing(sqlite3.connect(':memory:')) as con:
        con.execute('CREATE TABLE rows (variant, position, sid, identity, parent, digest)')
        if covering:
            con.execute('CREATE INDEX rows_sidebar_history '
                        'ON rows(variant, position, sid, identity, parent, digest)')
        rows = [('z-child', 'child-identity', parent, 'child-digest'),
                ('parent', 'parent-identity', '', 'parent-digest')]
        con.executemany('INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?)',
                        [('base', 0, *row) for row in rows])

        assert store._sidebar_history_inventory(con, 'base', 2) == tuple(rows)


@pytest.mark.parametrize('parent', ['parent', 'parent\0id', b'\xff', None])
@pytest.mark.parametrize('late_stall', [False, True])
def test_stalled_sidebar_inventory_batches_the_same_ordered_cut(monkeypatch, parent, late_stall):
    monkeypatch.setattr(store, '_HISTORY_INVENTORY_PROBE_ROWS', 1)
    clock = iter((0.0, 0.001, 0.001, 0.021) if late_stall else (0.0, 0.02))
    monkeypatch.setattr(store, 'time', SimpleNamespace(
        perf_counter=lambda: next(clock), thread_time=lambda: 0.0,
    ))
    batches = []
    batch_inventory = store._sidebar_history_inventory_batch

    def record_batch(con, query, params):
        result = batch_inventory(con, query, params)
        batches.append(result)
        return result

    monkeypatch.setattr(store, '_sidebar_history_inventory_batch', record_batch)
    with closing(sqlite3.connect(':memory:')) as con:
        con.execute('CREATE TABLE rows (variant, position, sid, identity, parent, digest)')
        con.execute('CREATE INDEX rows_sidebar_history '
                    'ON rows(variant, position, sid, identity, parent, digest)')
        rows = [('z-child', 'child-identity', 'parent', 'child-digest'),
                ('parent', 'parent-identity', parent, 'parent-digest')]
        offset = 1
        if late_stall:
            rows.insert(1, ('sibling', 'sibling-identity', 'parent', 'sibling-digest'))
            offset = 2
        con.executemany('INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?)',
                        [('base', 0, *row) for row in rows])

        assert store._sidebar_history_inventory(con, 'base', len(rows)) == tuple(rows)
        assert batches == [tuple(rows[offset:]) if parent == 'parent' else None]
