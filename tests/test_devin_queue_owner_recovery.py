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
         mock.patch.object(server, "resume_session_devin") as resume:
        result = server._pump_devin_resume_queue(sid)
    assert result["started"] is True and result["via"] == "acp-prompt"
    steer.assert_called_once()
    close.assert_not_called()
    resume.assert_not_called()
