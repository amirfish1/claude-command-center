"""Recover a Devin writer held by CCC without a loaded-session marker."""
import importlib
from unittest import mock

import pytest


@pytest.mark.parametrize("owner_pid,closed,waiting", [
    (1234, True, "owner-released"),
    (1234, False, "busy"),
    (5678, True, "external-owner"),
])
def test_pump_recovers_only_its_own_idle_connection(owner_pid, closed, waiting):
    server = importlib.import_module("server")
    sid = "devincli-owner-test"
    queue = {sid: ["first", "second"]}
    with mock.patch.object(server, "_pending_resume_retry_due", return_value=True), \
         mock.patch.object(server, "_resume_queue_engine_busy", return_value=False), \
         mock.patch.object(server, "_find_live_spawn_entry_for_session", return_value=None), \
         mock.patch.object(server, "_devin_cli_session_live", return_value=True), \
         mock.patch.object(server, "_devin_acp_session_loaded", return_value=False), \
         mock.patch.object(server, "_devin_cli_lock_pid", return_value=owner_pid), \
         mock.patch.object(server, "_acp_conn_pid", return_value=1234), \
         mock.patch.object(server, "_acp_session_snapshot", return_value={}), \
         mock.patch.object(server, "_devin_acp_try_steer", return_value=None) as steer, \
         mock.patch.object(server, "_acp_close_idle_conn", return_value={"closed": closed}) as close, \
         mock.patch.object(server, "_pending_resume_queue", queue), \
         mock.patch.object(server, "resume_session_devin") as resume:
        result = server._pump_devin_resume_queue(sid)
    assert result["waiting"] == waiting
    assert queue[sid] == ["first", "second"]
    resume.assert_not_called()
    if owner_pid == 1234:
        close.assert_called_once_with("devin")
    else:
        close.assert_not_called()


def test_pump_reattaches_through_its_own_conn_before_recycling():
    """A conn that lost its loaded marker still owns the lock: deliver through it."""
    server = importlib.import_module("server")
    sid = "devincli-owner-reattach"
    queue = {sid: ["first"]}
    with mock.patch.object(server, "_pending_resume_retry_due", return_value=True), \
         mock.patch.object(server, "_resume_queue_engine_busy", return_value=False), \
         mock.patch.object(server, "_find_live_spawn_entry_for_session", return_value=None), \
         mock.patch.object(server, "_devin_cli_session_live", return_value=True), \
         mock.patch.object(server, "_devin_acp_session_loaded", return_value=False), \
         mock.patch.object(server, "_devin_cli_lock_pid", return_value=1234), \
         mock.patch.object(server, "_acp_conn_pid", return_value=1234), \
         mock.patch.object(server, "_acp_session_snapshot", return_value={}), \
         mock.patch.object(server, "_devin_acp_try_steer", return_value={"ok": True}) as steer, \
         mock.patch.object(server, "_acp_close_idle_conn") as close, \
         mock.patch.object(server, "_apply_pending_input_operations", return_value={"ok": True}), \
         mock.patch.object(server, "_pending_resume_queue", queue), \
         mock.patch("ccc_server.pending_inputs._inject_receipts.close_receipt") as close_receipt, \
         mock.patch.object(server, "resume_session_devin") as resume:
        result = server._pump_devin_resume_queue(sid)
    close_receipt.assert_called_once_with(sid, text="first")
    assert result["started"] is True and result["via"] == "acp-prompt"
    steer.assert_called_once()
    close.assert_not_called()
    resume.assert_not_called()


def test_delivery_proof_accepts_the_user_message_in_the_session_transcript(tmp_path):
    """A headless -p run that never writes prompt_history still proves delivery."""
    import json
    import sqlite3
    eng = importlib.import_module("ccc_server.engines")
    db = tmp_path / "sessions.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE prompt_history (id INTEGER PRIMARY KEY, content TEXT, timestamp INTEGER, session_id TEXT, is_shell INT)")
    con.execute("CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY, session_id TEXT, node_id INT, chat_message TEXT, created_at INT)")
    con.execute("INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES (?,?,?,?)",
                ("raw", 1, json.dumps({"role": "user", "content": "hello"}), 1000))
    con.execute("INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES (?,?,?,?)",
                ("raw", 2, json.dumps({"role": "assistant", "content": "hello"}), 1001))
    con.commit()
    con.close()

    def connect():  # the function under test closes what it is given
        fresh = sqlite3.connect(db)
        fresh.row_factory = sqlite3.Row
        return fresh

    with mock.patch.object(eng._core, "_devin_cli_connect", side_effect=connect):
        assert eng._devin_cli_prompt_history_count("raw", "hello", 999) == 1
        assert eng._devin_cli_prompt_history_count("raw", "hello", 1002) == 0   # older than the send
        assert eng._devin_cli_prompt_history_count("raw", "other", 999) == 0


def test_acp_session_accepts_a_cwd_for_state_that_has_none():
    """A state recreated without a cwd must take the one the caller supplies."""
    server = importlib.import_module("server")
    harness, sid = "devin", "cwd-seed-test"
    sessions = server._ACP_SESSION_STATE.setdefault(harness, {})
    try:
        state = server._acp_session(harness, sid, create=True)          # no cwd known
        assert not state.get("cwd")
        again = server._acp_session(harness, sid, create=True, cwd="/work/dir")
        assert again is state and state["cwd"] == "/work/dir"
        server._acp_session(harness, sid, create=True, cwd="/other")    # never overwritten
        assert state["cwd"] == "/work/dir"
    finally:
        sessions.pop(sid, None)
