"""Cold topic context must retain list replay and incremental delivery."""
import time

import server
from ccc_server import archive_store


def test_late_cold_parent_keeps_replay_and_delta_tracking(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    key = server._archive_response_cache_key()
    now = time.time()
    child = {'session_id': 'child', 'mtime': now, 'display_name': 'Child'}
    parent = {'session_id': 'parent', 'mtime': now - 30 * 86400,
              'display_name': 'Older topic'}
    snapshot = {
        'schema_version': server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
        'build_id': server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
        'entries': {key: {'cached_at': now, 'conversations': [child, parent]}},
    }
    archive_store.save(target, snapshot, complete=True)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    server._archive_serve_cache_store(key, [child], server._archive_serve_generation)
    base = server._archive_serve_cache[key]['rows']
    monkeypatch.setattr(server, '_archive_serve_hot_rows_versioned',
                        lambda *a, **kw: (base, True, server._archive_serve_cache[key]['ver']))
    parents = {'child': 'parent'}
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: dict(parents))
    monkeypatch.setattr(server, '_pinned_rank_map', lambda: {})
    monkeypatch.setattr(server._idea_tickets, 'idea_ticket_rows_map', lambda: {})
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda: set())
    monkeypatch.setattr(server, '_apply_session_lane_overrides', lambda rows: None)
    monkeypatch.setattr(server, '_apply_pinned_conversation_fields', lambda rows: None)
    for name in ('_archive_overlay_codex_sessions', '_archive_overlay_acp_sessions',
                 '_archive_overlay_devin_cli_sessions', '_archive_overlay_wt_worker_sessions',
                 '_archive_overlay_claude_registry_sessions'):
        monkeypatch.setattr(server, name, lambda rows, **kw: [])

    rows, cached, version = server._archive_list_source_rows_cached({}, window='1d')
    assert [r['session_id'] for r in rows] == ['child', 'parent']
    assert rows[0] is base[0]
    assert version  # contextual cold rows must not disable replay/deltas
    state = server._archive_list_delta_state(key, rows, '1d', cached)
    assert state is not None
    again, _, again_version = server._archive_list_source_rows_cached({}, window='1d')
    assert again_version == version

    # Updating the cold parent changes the body version, and the delta
    # carries its new content while retaining both ordered ids.
    parent['display_name'] = 'Renamed topic'
    snapshot['entries'][key]['cached_at'] = now + 1
    archive_store.save(target, snapshot, complete=True)
    updated, _, updated_version = server._archive_list_source_rows_cached({}, window='1d')
    assert updated_version != version
    fresh = server._archive_list_delta_state(key, updated, '1d', cached)
    delta = server._archive_list_delta_payload(fresh, state['token'], updated,
                                             window='1d', now=now)
    assert delta['ids'] == ['child', 'parent']
    assert delta['conversations'][0]['display_name'] == 'Renamed topic'

    # Losing the late edge must also remove the old parent from the ids.
    parents.clear()
    removed, _, _ = server._archive_list_source_rows_cached({}, window='1d')
    fresh = server._archive_list_delta_state(key, removed, '1d', cached)
    delta = server._archive_list_delta_payload(fresh, state['token'], removed,
                                             window='1d', now=now)
    assert delta['ids'] == ['child']


def test_copied_or_reordered_snapshot_rows_cannot_replay(monkeypatch):
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    rows = [{'session_id': 'one'}, {'session_id': 'two'}]
    server._archive_serve_cache_store('base', rows, server._archive_serve_generation)
    base = server._archive_serve_cache['base']['rows']
    assert server._archive_list_body_ver('base', [dict(r) for r in base], []) == 0
    assert server._archive_list_body_ver('base', list(reversed(base)), []) == 0
