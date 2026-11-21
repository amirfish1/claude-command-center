import json
import sqlite3
from unittest.mock import patch

import server
from ccc_server import archive_store, continuation_topics


def test_indexed_preview_preserves_order_counts_and_live_context(tmp_path, monkeypatch):
    now = 2_000_000_000
    key = server._archive_response_cache_key()
    path = tmp_path / "archive.json"
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", path)
    monkeypatch.setattr(server._idea_tickets, "idea_ticket_rows_map", lambda: {
        "archived-child": {"explicitly_archived": True},
    })
    monkeypatch.setattr(server, "_archive_list_ticket_updates", lambda *args: None)
    monkeypatch.setattr(server, "_trace_row_flicker", lambda *args, **kwargs: None)
    monkeypatch.setattr(continuation_topics, "attach_topics", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets",
                        lambda: ({"archived"}, {"trashed-child"}))
    monkeypatch.setattr(server, "_codex_deleted_thread_ids", lambda: {"deleted"})
    monkeypatch.setattr(server, "_load_session_lane_overrides", lambda: {"archived": "other"})
    monkeypatch.setattr(server, "_load_usage_limit_resumes", lambda: {})
    monkeypatch.setattr(server, "_archive_list_row_overlays", lambda: {
        "old-root": {"task_title": "Live title update"},
    })
    monkeypatch.setattr(server._session_graph, "parent_snapshot", lambda: {
        "graph-child": "old-root",
    })
    monkeypatch.setattr(server, "queued_wait_snapshot", lambda **kwargs: {
        "old-root": {"items": 2, "wait_s": 7.5},
        "archived": {"items": 1, "wait_s": 3},
        "missing": {"items": 1, "wait_s": 2},
    })
    older = [
        {"session_id": "old-root", "mtime": now - 20_000, "state": "working"},
        {"session_id": "old-lane", "mtime": 2, "continued_from_session_id": "old-root"},
        {"session_id": "graph-child", "mtime": 3},
        {"session_id": "pin", "mtime": 5, "pinned": True},
        {"session_id": "trashed-child", "mtime": 6, "parent_session_id": "old-root"},
        {"session_id": "archived-child", "mtime": 7, "parent_session_id": "old-root"},
        {"session_id": "archived", "mtime": 8, "codex_state": "working"},
        {"session_id": "deleted", "mtime": now + 1, "engine": "codex", "state": "working"},
        {"session_id": "moved", "mtime": 1, "idea_ticket": {"moved_at": now}},
    ]
    recent = [{"session_id": f"row-{i}", "mtime": now - i} for i in range(540)]
    rows = older + recent
    schema = server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION
    build = server._ARCHIVE_RESPONSE_CACHE_BUILD_ID
    snapshot = {"schema_version": schema, "build_id": build, "entries": {
        key: {"cached_at": now, "conversations": rows},
    }}
    archive_store.save(path, snapshot, complete=True, sidebar_key=key)

    overlay = {
        "session_id": "new-overlay", "mtime": now + 1, "parent_session_id": "old-root",
    }
    hot = [dict(recent[0], mtime=2), {"session_id": "new-hot", "mtime": now + 2}]
    monkeypatch.setattr(server, "_archive_serve_hot_rows_versioned",
                        lambda *args, **kwargs: (hot, True, 3))
    seen = set()

    def engine_overlays(rows, ids, phase):
        seen.update(ids)
        return [overlay], [overlay]

    monkeypatch.setattr(server, "_archive_list_source_overlays", engine_overlays)
    expected = archive_store.load_with_overrides(
        path, key, hot, schema_version=schema, build_id=build, min_cached_at=0,
    )["conversations"]
    expected = server._overlay_conversation_lifecycle_flags(expected) + [overlay]
    normal = server._archive_list_initial_payload(expected, window="all", now=now, cached=True)
    indexed = server._archive_list_indexed_initial_payload({}, phases={})
    assert indexed is not None
    assert seen == server._archive_overlay_existing_ids(expected) - {"new-overlay"}
    assert json.dumps(indexed) == json.dumps(normal)

    # Older code can publish ordinary archive updates. A direct body write
    # with the original metadata timestamp must also invalidate the index.
    with sqlite3.connect(archive_store.database_path(path)) as conn:
        conn.execute("UPDATE rows SET body=? WHERE variant=? AND sid=?",
                     (json.dumps(dict(older[0], mtime=now + 3)), key, "old-root"))
    assert server._archive_list_indexed_initial_payload({}, phases={}) is None


def test_legacy_database_allows_index_fallback(tmp_path, monkeypatch):
    key = server._archive_response_cache_key()
    path = tmp_path / "archive.json"
    rows = [{"session_id": "legacy"}]
    archive_store.save(path, {"entries": {key: {"cached_at": 1, "conversations": rows}}})
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", path)
    monkeypatch.setattr(server, "_archive_serve_hot_rows_versioned",
                        lambda *args, **kwargs: (rows, True, 1))
    assert server._archive_list_indexed_initial_payload({}, phases={}) is None



def test_signature_match_backfills_existing_sidebar_generation(tmp_path, monkeypatch):
    path = tmp_path / "archive.json"
    result_path = tmp_path / "refresh-result.json"
    key = server._archive_response_cache_key()
    version = server._ARCHIVE_SIDEBAR_PACKED_VERSION
    rows = [{"session_id": "cold", "mtime": 1, "last_assistant_text": "Old reply"}]
    snapshot = {
        "schema_version": server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
        "build_id": server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
        "entries": {key: {"cached_at": 2_000_000_000, "conversations": rows}},
    }
    archive_store.save(path, snapshot, complete=True)
    before = archive_store.load(path)
    assert not archive_store.sidebar_pages_available(path, key, version)
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", path)
    monkeypatch.setattr(server, "_load_conv_meta_cache", lambda: None)
    monkeypatch.setattr(server, "_archive_compute_rows", lambda *args: (rows, True))
    monkeypatch.setattr(server, "_ARCHIVE_COMPUTE_LAST_PHASES", {"tier": "match"})
    monkeypatch.setattr(server, "_WT_DISPLAY_SNAPSHOT_ONLY", False)
    with patch("ccc_server.cpu_priority.set_background_priority"):
        assert server._archive_refresh_worker_main("e30=", result_path) == 0
    assert archive_store.sidebar_pages_available(path, key, version)
    result, _ = server._read_archive_refresh_result(result_path)
    assert "sidebar_backfill_ms" in result["phases"]
    assert archive_store.load(path) == before
    assert not archive_store.backfill_sidebar_pages(path, key, version, server._archive_list_pack_static)


def test_packed_history_is_exact_after_live_lifecycle_changes(tmp_path, monkeypatch):
    now = 2_000_000_000
    key = server._archive_response_cache_key()
    path = tmp_path / "archive.json"
    schema = server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION
    build = server._ARCHIVE_RESPONSE_CACHE_BUILD_ID
    version = server._ARCHIVE_SIDEBAR_PACKED_VERSION
    hot = {"session_id": "hot", "mtime": now}
    cold = {"session_id": "cold", "mtime": 10, "cwd": "/tmp/te-side-demo",
            "question_options": ("x", "y"), "last_assistant_text": "A **reply**"}
    deleted = {"session_id": "deleted", "engine": "codex", "mtime": 1}
    rows = [cold, hot, deleted]
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: ({"cold"}, set()))
    monkeypatch.setattr(server, "_load_session_lane_overrides", lambda: {})
    monkeypatch.setattr(server, "_codex_deleted_thread_ids", lambda: {"deleted"})

    def publish(stamp):
        return archive_store.save(path, {
            "schema_version": schema,
            "build_id": build,
            "entries": {key: {"cached_at": stamp, "conversations": rows}},
        }, complete=True, sidebar_key=key, sidebar_pages=(version, server._archive_list_pack_static))

    assert publish(now) == 3

    def load(packed_version):
        return archive_store.load_with_overrides(path, key, [hot], schema_version=schema,
            build_id=build, min_cached_at=0, reuse_sidebar_history=True, copy_history=False,
            packed_version=packed_version)["conversations"]

    try:
        raw = server._overlay_conversation_lifecycle_flags(load(None), copy_on_change=True)
        packed = server._overlay_conversation_lifecycle_flags(load(version), copy_on_change=True)
        assert len(packed) == 2
        assert isinstance(packed[0], server._archive_sidebar_pages.StoredRow)
        assert packed[0]["all_lane_override"] == "other"
        assert packed[0]["archived"] is True
        normal = server._archive_list_project_rows([raw[0]], _retain_templates=False)
        compact = server._archive_list_project_rows([packed[0]], _retain_templates=False)
        assert json.dumps(compact) == json.dumps(normal)
        assert compact[0]["last_assistant_preview"] == "A reply"
        assert compact[0]["question_options"] == ["x", "y"]

        # A peer modifying a body directly invalidates the current generation,
        # including when the canonical digest and publication timestamp stay put.
        with sqlite3.connect(archive_store.database_path(path)) as conn:
            conn.execute("UPDATE rows SET body=? WHERE variant=? AND sid=?",
                         (json.dumps(dict(cold, last_assistant_text="Peer reply")), key, "cold"))
        assert load(version)[0]["last_assistant_text"] == "Peer reply"
        assert publish(now + 1) == 0
        assert load(version)[0]["last_assistant_preview"] == "Peer reply"
    finally:
        reader = archive_store._SIDEBAR_HISTORY_READER
        with reader.lock:
            reader._close()
