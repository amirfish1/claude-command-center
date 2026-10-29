"""Phone swipe semantics: Background keeps a session running and wakes it
when it next needs the user; Done on a mid-turn session stops it only after
the turn ends."""

import pytest

import server
from ccc_server import archive_deferred
from tests.test_conversation_lifecycle import _post_json


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", tmp_path / "archived.json")
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    monkeypatch.setattr(server, "SIDECAR_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "LOG_VIEWER_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "_archive_grace", {})
    monkeypatch.setattr(server, "_save_archive_grace", lambda: None)
    monkeypatch.setattr(server, "_log_archive_event", lambda *args: None)
    monkeypatch.setattr(server, "_restamp_archive_serve_cache_after_mutation", lambda: None)
    monkeypatch.setattr(server, "find_session_cwd", lambda sid: None)
    monkeypatch.setattr(server, "_read_notification_state", lambda sid: None)
    monkeypatch.setattr(server, "_pending_ask_user_question_for_session", lambda sid: None)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")
    monkeypatch.setattr(server, "_resolve_conversation_reader", lambda sid, *a: (transcript, None))
    state = {"status": {"live": True, "status": "busy"}, "kills": [], "transcript": transcript}
    monkeypatch.setattr(server, "session_live_status", lambda sid, cwd: dict(state["status"]))
    monkeypatch.setattr(server, "_kill_session_by_id", lambda sid: state["kills"].append(sid) or {"ok": True})
    return state


def _archive(payload):
    return _post_json("/api/conversations/sid-a/archive", dict({"session_id": "sid-a", "archived": True}, **payload))


def test_background_archives_without_stopping(lifecycle):
    status, body = _archive({"keep_running": True})
    assert status == 200 and body["archived"] is True
    assert body["deferred"] == "background"
    assert lifecycle["kills"] == []
    assert archive_deferred.pending("sid-a") == "background"


def test_background_wakes_when_turn_ends(lifecycle):
    _archive({"keep_running": True})
    # Still working: stays hidden.
    lifecycle["transcript"].write_text("{}\n{}\n")
    assert archive_deferred.sweep_once() == []
    # Turn over: back in Active, never stopped.
    lifecycle["status"] = {"live": True, "status": "idle"}
    assert archive_deferred.sweep_once() == [("sid-a", "woke")]
    assert "sid-a" not in server._load_archived_conversations(sweep=False)
    assert lifecycle["kills"] == []
    assert archive_deferred.pending("sid-a") is None


def test_background_idle_session_stays_hidden_until_it_does_something(lifecycle):
    lifecycle["status"] = {"live": True, "status": "idle"}
    _archive({"keep_running": True})
    assert archive_deferred.sweep_once() == []
    assert "sid-a" in server._load_archived_conversations(sweep=False)


def test_background_wakes_on_permission_prompt(lifecycle, monkeypatch):
    _archive({"keep_running": True})
    monkeypatch.setattr(server, "_pending_ask_user_question_for_session", lambda sid: {"q": 1})
    assert archive_deferred.sweep_once() == [("sid-a", "woke")]


def test_done_mid_turn_stops_only_after_turn(lifecycle):
    status, body = _archive({})
    assert body["deferred"] == "stop" and body["killed"] is None
    assert archive_deferred.sweep_once() == []
    assert lifecycle["kills"] == []
    lifecycle["status"] = {"live": True, "status": "idle"}
    assert archive_deferred.sweep_once() == [("sid-a", "stopped")]
    assert lifecycle["kills"] == ["sid-a"]
    assert "sid-a" in server._load_archived_conversations(sweep=False)


def test_done_on_idle_session_stops_now(lifecycle):
    lifecycle["status"] = {"live": True, "status": "idle"}
    status, body = _archive({})
    assert body["deferred"] is None
    assert lifecycle["kills"] == ["sid-a"]


def test_moving_back_to_active_cancels_deferred(lifecycle):
    _archive({"keep_running": True})
    _archive({"archived": False})
    assert archive_deferred.pending("sid-a") is None
