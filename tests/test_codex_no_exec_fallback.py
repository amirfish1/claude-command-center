"""`codex exec` is never a transport fallback: failures queue durably.

CCC used to spawn a one-shot `codex exec`/`codex exec resume` process when the
app-server transport was unavailable — a fire-and-forget run CCC cannot steer
and Codex Desktop cannot resume cleanly. Both directions now keep the message
in the durable queue (retry via the queue pump) or surface a visible error.
"""
import importlib
import os
from unittest import mock

server = importlib.import_module("server")
from ccc_server import codex  # noqa: E402

SID = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"


def _resume_patches(queue_spy=None):
    """Mock the environment down to the app-server boundary for a resume."""
    queue_calls = []

    def fake_queue(session_id, text, pid=None, reason=None, *, only_if_pending=False, **kw):
        if only_if_pending and not server._pending_resume_queue.get(session_id):
            return None
        queue_calls.append({"sid": session_id, "text": text, "reason": reason})
        return {
            "ok": True,
            "queued": True,
            "via": "codex-resume-queued",
            "queued_reason": reason,
            "error": reason,
        }

    patches = [
        mock.patch.object(server, "_control_plane_engine_call", return_value=None),
        mock.patch.object(server, "_pending_writer_compatibility_status", return_value={"ok": True}),
        mock.patch.object(
            server, "_resolve_codex_bin",
            return_value={"available": True, "bin": "/usr/bin/codex-test"},
        ),
        mock.patch.object(server, "_codex_thread_row", return_value={"cwd": "/repo"}),
        mock.patch.object(server, "_codex_capture_thread_row", return_value={}),
        mock.patch.object(server, "_resolve_codex_rollout_path", return_value=None),
        mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value="/repo"),
        mock.patch.object(server, "_get_session_override", return_value=None),
        mock.patch.object(server, "_codex_thread_writer_snapshot", return_value={}),
        mock.patch.object(
            server, "_codex_resume_or_steer_via_app_server",
            return_value={"ok": False, "fallback": "queue", "error": "transport down"},
        ),
        mock.patch.object(server, "_queue_codex_resume", side_effect=fake_queue),
        mock.patch.object(
            server.subprocess, "Popen",
            side_effect=AssertionError("codex exec fallback must not run"),
        ),
        mock.patch("ccc_server.codex_client.resume_desktop_conversation", return_value=None),
    ]
    return patches, queue_calls


def test_resume_queues_message_when_app_server_fails():
    """A failed resume parks the text in the durable queue — no exec spawn."""
    patches, queue_calls = _resume_patches()
    original_spawns = list(server._spawned_sessions)
    server._spawned_sessions.clear()
    try:
        for patch in patches:
            patch.start()
        try:
            result = server.resume_session_codex(SID, "still waiting")
        finally:
            for patch in reversed(patches):
                patch.stop()
    finally:
        server._spawned_sessions.clear()
        server._spawned_sessions.extend(original_spawns)

    assert result["ok"] and result["queued"]
    assert queue_calls == [{"sid": SID, "text": "still waiting", "reason": "transport down"}]


def test_resume_from_queue_stays_queued_when_app_server_fails():
    """The pump's own retry also parks the message instead of exec'ing."""
    patches, queue_calls = _resume_patches()
    original_spawns = list(server._spawned_sessions)
    server._spawned_sessions.clear()
    try:
        for patch in patches:
            patch.start()
        try:
            result = server.resume_session_codex(SID, "still waiting", _from_queue=True)
        finally:
            for patch in reversed(patches):
                patch.stop()
    finally:
        server._spawned_sessions.clear()
        server._spawned_sessions.extend(original_spawns)

    assert result["ok"] and result["queued"]
    # _from_queue must not re-enqueue: the claim restore owns durability.
    assert queue_calls == []


def test_locked_resume_reports_queue_fallback_on_thread_resume_error():
    """thread/resume failures are labeled queue, not exec."""
    with mock.patch.object(
        server, "_codex_app_server_request",
        return_value={"error": {"message": "thread not found: t"}},
    ), mock.patch.object(server, "_resume_ledger_append"), \
         mock.patch.object(server, "_codex_telemetry_append"), \
         mock.patch.object(server, "_codex_app_server_is_live", return_value=True), \
         mock.patch.object(server, "_codex_app_server_transport_kind", return_value="stdio"):
        result = codex._codex_resume_or_steer_via_app_server_locked(
            SID, "hi", cwd="/repo", model="gpt-test",
        )
    assert result["ok"] is False
    assert result["fallback"] == "queue"
    assert result["stage"] == "thread/resume"


def test_spawn_returns_error_when_app_server_spawn_disabled(tmp_path):
    """CCC_CODEX_SPAWN_APP_SERVER=0 fails the spawn — no exec process."""
    with mock.patch.dict(os.environ, {"CCC_CODEX_SPAWN_APP_SERVER": "0"}), \
         mock.patch.object(server, "_control_plane_engine_call", return_value=None), \
         mock.patch.object(server, "_set_session_model"), \
         mock.patch.object(server.subprocess, "Popen", side_effect=AssertionError("exec must not run")) as popen:
        result = server.spawn_session_codex("hi", name="t", repo_path=str(tmp_path))
    assert result["ok"] is False
    assert result["code"] == "codex_app_server_disabled"
    popen.assert_not_called()


def test_spawn_returns_error_when_thread_start_fails(tmp_path):
    """A failed thread/start surfaces as a spawn error — no exec spawn."""
    def fake_request(method, params=None, timeout=20):
        if method == "thread/start":
            return {"error": {"message": "boom"}}
        raise AssertionError(f"unexpected method: {method}")

    with mock.patch.object(server, "_control_plane_engine_call", return_value=None), \
         mock.patch.object(server, "_set_session_model"), \
         mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request), \
         mock.patch.object(server, "_codex_app_server_is_live", return_value=False), \
         mock.patch.object(server, "_codex_app_server_transport_kind", return_value="stdio"), \
         mock.patch.object(server.subprocess, "Popen", side_effect=AssertionError("exec must not run")) as popen:
        result = server.spawn_session_codex("hi", name="t", repo_path=str(tmp_path))
    assert result["ok"] is False
    assert result["code"] == "codex_app_spawn_failed"
    assert "boom" in result["error"]
    popen.assert_not_called()
