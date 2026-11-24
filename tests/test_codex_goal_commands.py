import sqlite3
from unittest.mock import Mock, call

import pytest

import server
from ccc_server import codex


SID = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("action,objective", [("set", "Check the report"), ("resume", None)])
def test_disabled_goal_rpc_does_not_write_or_resume(monkeypatch, action, objective):
    monkeypatch.setenv("CCC_CODEX_APP_SERVER", "1")
    rpc = Mock(return_value={"error": {"code": -32600, "message": "goals feature is disabled"}})
    local_store = Mock()
    monkeypatch.setattr(server, "_codex_app_server_request", rpc)
    monkeypatch.setattr(server, "_codex_goal_store_update", local_store)

    result = server._codex_goal_via_app_server(SID, action, objective)

    assert not result["ok"]
    assert result["code"] == "codex_goal_unavailable"
    assert "goals feature is disabled" in result["error"]
    assert [request.args[0] for request in rpc.call_args_list] == ["thread/goal/set"]
    local_store.assert_not_called()


def test_user_goal_is_confirmed_before_loading_thread(monkeypatch):
    monkeypatch.setenv("CCC_CODEX_APP_SERVER", "1")
    goal = {"threadId": SID, "objective": "Check the report", "status": "active"}
    rpc = Mock(side_effect=[{"result": {"goal": goal}}, {"result": {"thread": {"id": SID}}}])
    local_store = Mock()
    monkeypatch.setattr(server, "_codex_app_server_request", rpc)
    monkeypatch.setattr(server, "_codex_goal_store_update", local_store)

    result = server._codex_goal_via_app_server(SID, "set", goal["objective"], user_initiated=True)

    assert result["ok"] and result["goal"] == goal
    assert rpc.call_args_list == [
        call("thread/goal/set", {"threadId": SID, "origin": "user",
                                 "status": "active", "objective": goal["objective"]}, timeout=30),
        call("thread/resume", {"threadId": SID, "excludeTurns": True}, timeout=20),
    ]
    local_store.assert_not_called()


def test_empty_rpc_result_does_not_acknowledge_the_goal(monkeypatch):
    monkeypatch.setenv("CCC_CODEX_APP_SERVER", "1")
    rpc = Mock(return_value={"result": {}})
    monkeypatch.setattr(server, "_codex_app_server_request", rpc)

    result = server._codex_goal_via_app_server(SID, "set", "Check the report")

    assert result["code"] == "codex_goal_unconfirmed"
    assert rpc.call_count == 1
    assert rpc.call_args.args[1]["origin"] == "automatic"


def test_disabled_goal_can_still_be_stopped(monkeypatch):
    monkeypatch.setenv("CCC_CODEX_APP_SERVER", "1")
    rpc = Mock(return_value={"error": {"code": -32600, "message": "goals feature is disabled"}})
    local_store = Mock(return_value={"ok": True, "action": "pause"})
    monkeypatch.setattr(server, "_codex_app_server_request", rpc)
    monkeypatch.setattr(server, "_codex_goal_store_update", local_store)

    assert server._codex_goal_via_app_server(SID, "pause")["ok"]
    local_store.assert_called_once_with(SID, "pause")
    assert rpc.call_count == 1


def test_goal_changes_update_existing_sidebar_rows_via_delta(monkeypatch, tmp_path):
    db = tmp_path / "goals.sqlite"
    monkeypatch.setattr(server, "CODEX_GOALS_DB_CANDIDATES", (db,))
    row = {"session_id": SID, "engine": "codex", "goal": "Old snapshot", "goal_status": "paused"}
    monkeypatch.setattr(server, "_archive_list_title_overlays", lambda: {})
    cache_key = server._archive_response_cache_key()
    monkeypatch.setattr(server, "_archive_serve_cache", {
        cache_key: {"rows": [row], "ver": 2, "track_since": 1, "row_rev": {SID: 1}},
    })
    rows = server._archive_serve_cache[cache_key]["rows"]
    writer = sqlite3.connect(db)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE thread_goals(thread_id TEXT PRIMARY KEY, objective TEXT, status TEXT)")
        before = server._archive_list_delta_state(cache_key, rows, "all", True)
        assert not server._archive_list_replay_payload(rows, before)["conversations"][0]["goal"]
        main_stamp = db.stat().st_mtime_ns

        writer.execute("INSERT INTO thread_goals VALUES (?, ?, ?)", (SID, "Check the report", "active"))
        writer.commit()
        active = server._archive_list_delta_state(cache_key, rows, "all", True)
        payload = server._archive_list_delta_payload(active, before["token"], rows, window="all")
        assert payload["conversations"][0]["goal"] == "Check the report"
        assert payload["conversations"][0]["goal_status"] == "active"

        with monkeypatch.context() as read_check:
            connect = Mock(side_effect=AssertionError("Unchanged goals must not reopen SQLite"))
            read_check.setattr(sqlite3, "connect", connect)
            assert server._codex_goals_snapshot()[SID]["status"] == "active"
            connect.assert_not_called()

        writer.execute("DELETE FROM thread_goals")
        writer.commit()
        cleared = server._archive_list_delta_state(cache_key, rows, "all", True)
        payload = server._archive_list_delta_payload(cleared, active["token"], rows, window="all")
        assert not payload["conversations"][0]["goal"]
        assert not payload["conversations"][0]["goal_status"]
        assert row["goal"] == "Old snapshot"
        assert db.stat().st_mtime_ns == main_stamp
    finally:
        writer.close()
        server._invalidate_codex_goals_cache()


def test_goal_snapshot_retries_after_a_temporary_read_failure(monkeypatch, tmp_path):
    db = tmp_path / "goals.sqlite"
    monkeypatch.setattr(server, "CODEX_GOALS_DB_CANDIDATES", (db,))
    writer = sqlite3.connect(db)
    try:
        with writer:
            writer.execute("CREATE TABLE thread_goals(thread_id TEXT PRIMARY KEY, objective TEXT)")
            writer.execute("INSERT INTO thread_goals VALUES (?, ?)", (SID, "First objective"))
        assert server._codex_goals_snapshot()[SID]["objective"] == "First objective"
        with writer:
            writer.execute("UPDATE thread_goals SET objective = ?", ("Revised objective",))

        with monkeypatch.context() as transient:
            transient.setattr(codex, "_sqlite_fetch_rows", Mock(side_effect=sqlite3.OperationalError("busy")))
            assert server._codex_goals_snapshot()[SID]["objective"] == "First objective"
        assert server._codex_goals_snapshot()[SID]["objective"] == "Revised objective"
    finally:
        writer.close()
        server._invalidate_codex_goals_cache()
