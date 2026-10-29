"""Regression coverage for steering an active Codex thread."""

from unittest import mock

import server


def test_codex_steer_attempts_native_rpc_despite_external_writer_snapshot():
    session_id = "019fca00-af1d-7771-bffa-bc81f46b4b53"
    with (
        mock.patch.object(
            server,
            "_codex_thread_writer_snapshot",
            return_value={"external_active": True, "writer": "unknown"},
        ),
        mock.patch.object(server, "_codex_note_external_writer_transition"),
        mock.patch.object(
            server,
            "_codex_app_server_request",
            side_effect=[
                {
                    "result": {
                        "thread": {
                            "status": {"type": "active"},
                            "turns": [{"id": "turn-1", "status": "inProgress"}],
                        }
                    }
                },
                {"result": {"turnId": "turn-1"}},
            ],
        ) as request,
    ):
        result = server._codex_steer_via_app_server(session_id, "Please steer now")

    assert result["ok"]
    assert result["via"] == "codex-steer"
    assert request.call_args_list[0] == mock.call(
        "thread/resume", {"threadId": session_id, "excludeTurns": False}, timeout=20
    )
    steer_args, steer_kwargs = request.call_args_list[1]
    assert steer_args[0] == "turn/steer"
    assert steer_args[1]["threadId"] == session_id
    assert steer_args[1]["expectedTurnId"] == "turn-1"
    assert steer_kwargs == {"timeout": 20}


def test_codex_steer_fallback_send_uses_a_key_the_ledger_has_not_burned():
    """Steer on an idle Codex thread must fall back to a real send.

    The steer attempt is already recorded as failed under the caller's
    idempotency key; reusing it dedupes the fallback turn/start back to that
    failure, so the message is never sent and the UI shows "No running Codex
    turn to steer".
    """
    sid = "codex-steer-idle-session"
    calls = []

    def _resume(session_id, text, **kwargs):
        calls.append(kwargs)
        if kwargs.get("steer"):
            return {
                "ok": False,
                "code": "codex_no_active_turn",
                "error": "No running Codex turn to steer",
            }
        return {"ok": True, "via": "codex-resume"}

    with mock.patch.object(server, "_is_codex_session", return_value=True), \
         mock.patch.object(server, "find_session_cwd", return_value="/tmp"), \
         mock.patch.object(server, "session_live_status", return_value={
             "live": True, "status": "idle", "kind": "codex",
             "tty": None, "terminal_app": None,
         }), \
         mock.patch.object(server, "resume_session_codex", side_effect=_resume), \
         mock.patch.object(server, "_consume_matching_pending_input"):
        result = server._inject_text_into_session(
            sid, "actually, stop", mode="steer",
            idempotency_key="inject:deadbeef",
        )

    assert len(calls) == 2
    assert calls[0]["idempotency_key"] == "inject:deadbeef"
    assert calls[1]["idempotency_key"] != calls[0]["idempotency_key"]
    assert result["ok"]


def test_writer_snapshot_holder_classified_as_own_exec_resume():
    """A shared-state-DB holder whose argv carries this sid through
    `codex exec resume` is CCC's own worker child — the snapshot must report
    writer 'ccc' instead of falling to the rollout-mtime 'external' heuristic."""
    import time as _time
    from ccc_server import codex as codex_mod

    sid = "019fca00-af1d-7771-bffa-bc81f46b4b53"
    now = _time.time()
    holders = [{"pid": 6440, "command": "codex"}]
    classified = [{
        "pid": 6440, "command": "codex",
        "argv": "node /Users/x/.local/bin/codex exec resume --json " + sid + " hi",
        "kind": "ccc-exec-resume", "this_thread": True,
    }]
    with mock.patch.object(
        codex_mod, "_codex_ccc_exec_child_running", return_value=False
    ), mock.patch.object(
        codex_mod, "_codex_classify_state_holders", return_value=classified
    ), mock.patch.object(
        server, "_codex_app_server_thread_state", return_value={}
    ):
        snap = server._codex_thread_writer_snapshot(
            sid, now,
            app_state={},
            rollout={"path": "/tmp/rollout.jsonl", "mtime_ns": int(now * 1e9)},
            attached={},
            holders=holders,
        )
    assert snap["writer"] == "ccc"
    assert snap["external_active"] is False


def test_writer_snapshot_foreign_holder_stays_external():
    """A holder for a different sid must not be claimed as ours."""
    import time as _time
    from ccc_server import codex as codex_mod

    sid = "019fca00-af1d-7771-bffa-bc81f46b4b53"
    now = _time.time()
    classified = [{
        "pid": 999, "command": "codex",
        "argv": "codex exec resume --json other-sid hi",
        "kind": "ccc-exec-resume", "this_thread": False,
    }]
    with mock.patch.object(
        codex_mod, "_codex_ccc_exec_child_running", return_value=False
    ), mock.patch.object(
        codex_mod, "_codex_classify_state_holders", return_value=classified
    ), mock.patch.object(
        server, "_codex_app_server_thread_state", return_value={}
    ):
        snap = server._codex_thread_writer_snapshot(
            sid, now,
            app_state={"status": "idle"},
            rollout={"path": "/tmp/rollout.jsonl", "mtime_ns": int(now * 1e9)},
            attached={},
            holders=[{"pid": 999, "command": "codex"}],
        )
    assert snap["writer"] is None
    assert snap["external_active"] is False


def test_holder_argv_cache_batches_ps_calls():
    """Per-row writer attribution must not fork /bin/ps once per session:
    the argv map is cached by (pid tuple, TTL)."""
    from ccc_server import codex as codex_mod

    sid = "019fca00-af1d-7771-bffa-bc81f46b4b53"
    codex_mod._CODEX_HOLDER_ARGV_CACHE.update(pids=None, at=0.0, argv={})
    calls = []

    def fake_ps(args, **kw):
        calls.append(args)
        r = mock.Mock()
        pid_field = args[2] if len(args) > 2 else ""
        first = str(pid_field).split(",")[0]
        r.stdout = first + " codex exec resume --json " + sid + " hi\n"
        return r

    with mock.patch.object(codex_mod.subprocess, "run", side_effect=fake_ps):
        for _ in range(5):
            rows = codex_mod._codex_classify_state_holders(
                [{"pid": 6440, "command": "codex"}], sid)
        assert len(calls) == 1
        assert rows[0]["kind"] == "ccc-exec-resume"
        assert rows[0]["this_thread"] is True
        rows = codex_mod._codex_classify_state_holders(
            [{"pid": 7000, "command": "codex"}], sid)
        assert len(calls) == 2
        assert rows[0]["kind"] == "ccc-exec-resume"
