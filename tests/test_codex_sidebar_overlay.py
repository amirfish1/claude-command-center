from unittest import mock

import server


def test_indexed_threads_fill_stale_sidebar_without_transcript_or_engine_io(tmp_path, monkeypatch):
    graph = server._SessionGraph(tmp_path / "graph.json")
    graph.add_edge("dispatcher", "new-thread", engine="codex")
    monkeypatch.setattr(server, "_session_graph", graph)
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: (set(), {"trashed"}))
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    fetch = mock.Mock(return_value=[
        {"id": "known", "updated_at": 100000},
        {"id": "new-thread", "title": "Review changes", "updated_at": 100000, "model": "test-model"},
        {"id": "trashed", "updated_at": 100000},
        {"id": "archived", "updated_at": 100000, "archived": 1},
        {"id": "old", "updated_at": 1},
    ])
    monkeypatch.setattr(server, "_codex_fetch_threads", fetch)
    forbidden = mock.Mock(side_effect=AssertionError("No per-thread I/O in sidebar overlay"))
    monkeypatch.setattr(server, "_extract_codex_tail_meta", forbidden)
    monkeypatch.setattr(server, "_control_plane_engine_call", forbidden)
    rows = server._archive_overlay_codex_sessions([{"session_id": "known"}], now=100100)
    assert [r["session_id"] for r in rows] == ["new-thread"]
    assert rows[0]["parent_session_id"] == "dispatcher"
    assert rows[0]["display_name"] == "Review changes"
    assert rows[0]["model"] == "test-model"
    fetch.assert_called_once()
    assert fetch.call_args.kwargs["limit"] == 200
    assert "first_user_message" not in fetch.call_args.kwargs["columns"]
    forbidden.assert_not_called()
