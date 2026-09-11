import sqlite3
from unittest import mock

import server
from ccc_server import codex, codex_client


def _isolate_lifecycle(tmp_path, monkeypatch):
    monkeypatch.delenv("CCC_EPHEMERAL", raising=False)
    monkeypatch.setattr(server, "CODEX_THREAD_REGISTRY_FILE", tmp_path / "codex-thread-registry.json")
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", tmp_path / "archived.json")
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    monkeypatch.setattr(server, "SIDECAR_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "CODEX_APP_SERVER_STATE_FILE", tmp_path / "codex-app-server-state.json")
    monkeypatch.setattr(server, "_archive_grace", {})
    monkeypatch.setattr(server, "_save_archive_grace", lambda: None)
    monkeypatch.setattr(server, "_log_archive_event", lambda *args: None)
    monkeypatch.setattr(server, "_restamp_archive_serve_cache_after_mutation", lambda **kwargs: None)
    monkeypatch.setattr(server, "_invalidate_dashboard", lambda *args, **kwargs: None)


def _rehydrate_one(sid, monkeypatch):
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    monkeypatch.setattr(server, "_load_verified_conversations", lambda: [])
    monkeypatch.setattr(server, "_load_pinned_conversations", lambda: [])
    monkeypatch.setattr(server, "_spawn_registry_entries_by_session", lambda: {})
    monkeypatch.setattr(server, "_discover_live_session_ids", lambda: set())
    return server._rehydrate_archive_cached_rows([
        {"session_id": sid, "engine": "codex", "mtime": 1, "archived": False, "trashed": False}
    ])[0]


def test_native_archive_and_unarchive_drive_actual_sidecar_overlay(tmp_path, monkeypatch):
    _isolate_lifecycle(tmp_path, monkeypatch)
    codex_client._client_sync_lifecycle("thread/archive", {"threadId": "native-child"},
        {"result": {}}, "/test")
    assert _rehydrate_one("native-child", monkeypatch)["archived"] is True

    server._save_trashed_conversations(["native-child"])
    codex_client._client_sync_lifecycle("thread/unarchive", {"threadId": "native-child"},
        {"result": {}}, "/test")
    row = _rehydrate_one("native-child", monkeypatch)
    assert row["archived"] is False
    assert row["trashed"] is False


def test_native_delete_removes_registry_graph_and_legacy_state(tmp_path, monkeypatch):
    _isolate_lifecycle(tmp_path, monkeypatch)
    graph = server._SessionGraph(tmp_path / "session-graph.json")
    graph.add_edge("parent", "deleted-child", source="codex-native", engine="codex")
    monkeypatch.setattr(server, "_session_graph", graph)
    monkeypatch.setattr(server, "_apply_pending_input_operations", lambda *args, **kwargs: {"ok": True})
    server._codex_thread_registry_upsert("deleted-child", source="test", parent_session_id="parent")
    server._CODEX_APP_SERVER_THREAD_STATE["deleted-child"] = {
        "pending_approval_request": {"secret": "approval"},
        "compaction_recovery": {"secret": "recovery"},
    }
    server._CODEX_APP_SERVER_TURN_THREAD["turn"] = "deleted-child"

    server._codex_sync_native_lifecycle("thread/deleted", {"deleted-child"})

    assert "deleted-child" not in server._codex_thread_registry_entries()
    assert "deleted-child" in server._codex_deleted_thread_ids()
    assert graph.parent_of("deleted-child") is None
    assert "deleted-child" not in server._CODEX_APP_SERVER_THREAD_STATE
    assert "turn" not in server._CODEX_APP_SERVER_TURN_THREAD


def test_deleted_marker_filters_native_sql_rows_across_fresh_reads(tmp_path, monkeypatch):
    _isolate_lifecycle(tmp_path, monkeypatch)
    db = tmp_path / "state.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, updated_at INTEGER, created_at INTEGER)")
        con.executemany("INSERT INTO threads VALUES (?, ?, ?)", [("alive", 2, 2), ("deleted", 1, 1)])
    monkeypatch.setattr(codex, "_codex_state_db_candidates", lambda: [db])
    server._codex_thread_registry_upsert("deleted", source="test")
    server._codex_thread_registry_delete({"deleted"})

    assert [row["id"] for row in server._codex_fetch_threads()] == ["alive"]


def test_deleted_markers_filter_stale_native_spawn_edges(tmp_path, monkeypatch):
    _isolate_lifecycle(tmp_path, monkeypatch)
    db = tmp_path / "state.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT)")
        con.executemany("INSERT INTO thread_spawn_edges VALUES (?, ?)", [
            ("parent", "alive-child"), ("parent", "deleted-child")])
    monkeypatch.setattr(codex, "_codex_state_db_candidates", lambda: [db])
    server._codex_thread_registry_delete({"deleted-child"})

    assert server._codex_spawn_parent_by_child() == {"alive-child": "parent"}


def test_deleted_marker_filters_only_codex_rows_from_cold_cache(tmp_path, monkeypatch):
    _isolate_lifecycle(tmp_path, monkeypatch)
    server._codex_thread_registry_delete({"same-id"})
    rows = [
        {"session_id": "same-id", "engine": "codex", "archived": False},
        {"session_id": "same-id", "engine": "claude", "archived": False},
    ]

    server._overlay_conversation_lifecycle_flags(rows)

    assert rows == [{"session_id": "same-id", "engine": "claude",
                     "archived": False, "trashed": False}]


def test_registry_write_failure_keeps_native_delete_sync_retryable(tmp_path, monkeypatch):
    _isolate_lifecycle(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_codex_thread_registry_delete", lambda ids: None)

    try:
        server._codex_sync_native_lifecycle("thread/deleted", {"deleted"})
    except OSError as error:
        assert "registry" in str(error).lower()
    else:
        raise AssertionError("registry failure was silently accepted")


def test_ephemeral_lifecycle_never_writes_real_sidecars(monkeypatch):
    with monkeypatch.context() as patch, \
         mock.patch.object(server, "_codex_sync_native_lifecycle") as sync:
        patch.setenv("CCC_EPHEMERAL", "1")
        codex_client._client_sync_lifecycle("thread/archive", {"threadId": "preview"},
            {"result": {}}, "/test")
        codex_client._client_sync_lifecycle("thread/delete", {"threadId": "preview"},
            {"result": {}}, "/test")
    assert sync.call_count == 0
