"""Engine gap fillers deduplicate against both stored and newly appended rows."""
import server


def test_sources_keep_precedence_and_do_not_mutate_the_archive(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_CLAUDE_REG_CACHE', {'sig': None, 'rows': []})
    base = [{'session_id': 'stored'}]
    native = {'session_id': 'new-native', 'source': 'codex'}
    terminal = {'session_id': 'new-terminal', 'source': 'interactive'}
    calls = []
    monkeypatch.setattr(server, '_archive_serve_rows_versioned', lambda *a, **kw: (base, True, 7))
    monkeypatch.setattr(server, '_archive_response_cache_key', lambda **kw: 'key')
    monkeypatch.setattr(server, '_archive_list_body_ver', lambda *a: 7)
    monkeypatch.setattr(server, '_apply_session_lane_overrides', lambda rows: None)

    def source(candidates):
        def overlay(rows, *, existing_ids=None):
            # A source that ignores the new argument still derives the same
            # membership from the current accumulated rows.
            expected = server._archive_overlay_existing_ids(rows)
            assert existing_ids == expected
            calls.append(existing_ids)
            return [row for row in candidates if row['session_id'] not in existing_ids]
        return overlay

    monkeypatch.setattr(server, '_archive_overlay_codex_sessions', source([native]))
    monkeypatch.setattr(server, '_archive_overlay_acp_sessions', source([dict(native, source='kimi')]))
    monkeypatch.setattr(server, '_archive_overlay_devin_cli_sessions', source([]))
    monkeypatch.setattr(server, '_archive_live_wt_workers', lambda: [
        {'session_id': 'new-native', 'alive': True}, {'session_id': base[0]['session_id'], 'alive': True}])
    monkeypatch.setattr(server, '_archive_claude_registry_rows', lambda **kw: [native, terminal])
    monkeypatch.setattr(server, '_load_session_registry', lambda: {'new-terminal': {}})
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    rows, cached, version = server._archive_list_source_rows_cached({})
    assert rows == [base[0], native, terminal]
    assert base == [{'session_id': 'stored'}]
    assert rows[0] is base[0]
    assert cached and version == 7
    assert all(ids is calls[0] for ids in calls)

    # A second request must honor a different snapshot, even if its length
    # and external version have not changed.
    first_ids = calls[0]
    base[0] = dict(terminal)
    calls.clear()
    again, _, _ = server._archive_list_source_rows_cached({})
    assert again == [terminal, native]
    assert calls[0] is not first_ids
