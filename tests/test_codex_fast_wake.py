"""Fast-path wake: turn/start without thread/resume when the daemon's view
of the thread is provably current.

The daemon reports on a thread through notifications and responses, which is
the only source of ``last_event_at``. The rollout is append-only, so a file
mtime at/below that watermark means nothing was written the daemon cannot
see — and thread/resume can be skipped. Any doubt (foreign write bumping
mtime, busy markers, missing rollout, ambiguous transport failure) falls
back to the full resume path or to the durable queue — never to a retry of
a maybe-started turn.
"""
import importlib
import time
from unittest import mock

import pytest

server = importlib.import_module("server")
from ccc_server import codex  # noqa: E402

SID = "019e2bbb-d5e0-7df2-a1f7-26fbcf363485"


@pytest.fixture(autouse=True)
def _no_live_worker():
    """Keep the resolved-state helper hermetic: no live control-plane
    socket calls, and a fresh remote-map cache per test."""
    codex._codex_thread_state_remote.update({"ts": 0.0, "map": {}})
    with mock.patch.object(server, "_control_plane_engine_call", return_value=None):
        yield
    codex._codex_thread_state_remote.update({"ts": 0.0, "map": {}})


def _state(**over):
    base = {"thread_id": SID, "status": "idle", "last_event_at": time.time()}
    base.update(over)
    return base


def _rollout(mtime_s):
    return {"path": "/tmp/rollout.jsonl", "size": 100, "mtime_ns": int(mtime_s * 1e9)}


class TestFreshnessPredicate:
    def test_fresh_when_daemon_heard_and_rollout_quiet(self):
        now = time.time()
        assert codex._codex_app_server_thread_fresh(
            SID, state=_state(last_event_at=now), rollout=_rollout(now - 2),
        ) is True

    def test_not_fresh_without_state(self):
        assert codex._codex_app_server_thread_fresh(
            SID, state={}, rollout=_rollout(time.time()),
        ) is False
        assert codex._codex_app_server_thread_fresh(SID, state=None, rollout=None) is False

    def test_not_fresh_when_rollout_moved_after_last_event(self):
        now = time.time()
        assert codex._codex_app_server_thread_fresh(
            SID, state=_state(last_event_at=now - 60), rollout=_rollout(now),
        ) is False

    def test_fresh_uses_last_activity_at_watermark(self):
        now = time.time()
        st = _state(last_event_at=0, last_activity_at=now)
        assert codex._codex_app_server_thread_fresh(
            SID, state=st, rollout=_rollout(now - 1),
        ) is True

    def test_not_fresh_when_busy(self):
        now = time.time()
        for over in (
            {"status": "active"},
            {"active_turn_id": "turn-1"},
            {"ccc_turn_start_pending": True},
        ):
            assert codex._codex_app_server_thread_fresh(
                SID, state=_state(**over), rollout=_rollout(now - 2),
            ) is False, over

    def test_not_fresh_without_rollout(self):
        assert codex._codex_app_server_thread_fresh(
            SID, state=_state(), rollout=None,
        ) is False

    def test_slack_absorbs_notify_then_flush_ordering(self):
        now = time.time()
        st = _state(last_event_at=now)
        assert codex._codex_app_server_thread_fresh(
            SID, state=st, rollout=_rollout(now + codex._CODEX_THREAD_FRESH_SLACK_S - 1),
        ) is True


class _LockedEnv:
    """Patch the world around _codex_resume_or_steer_via_app_server_locked."""

    def __init__(self, state=None, rollout=None):
        self.calls = []
        self.patches = [
            mock.patch.object(server, "_codex_app_server_is_live", return_value=True),
            mock.patch.object(server, "_codex_app_server_transport_kind", return_value="managed"),
            mock.patch.object(server, "_codex_app_server_request", side_effect=self._request),
            mock.patch.object(server, "_codex_rollout_stat", return_value=rollout),
            mock.patch.object(server, "_codex_thread_writer_snapshot", return_value={}),
            mock.patch.object(server, "_resume_ledger_append"),
            mock.patch.object(server, "_codex_telemetry_append"),
            mock.patch.object(server, "_codex_telemetry_register_turn"),
            mock.patch.object(codex, "_codex_finalize_wake_async"),
        ]
        self._state = state

    def _request(self, method, params=None, timeout=20, **kw):
        self.calls.append(method)
        return self.respond(method, params or {})

    def respond(self, method, params):  # override per test
        raise NotImplementedError

    def __enter__(self):
        saved = server._CODEX_APP_SERVER_THREAD_STATE.get(SID)
        self._saved = saved
        if self._state is not None:
            server._CODEX_APP_SERVER_THREAD_STATE[SID] = dict(self._state)
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        if self._saved is None:
            server._CODEX_APP_SERVER_THREAD_STATE.pop(SID, None)
        else:
            server._CODEX_APP_SERVER_THREAD_STATE[SID] = self._saved
        return False


def _fresh_env(request_handler):
    env = _LockedEnv(state=_state(), rollout=_rollout(time.time() - 2))
    env.respond = request_handler
    return env


def test_fresh_thread_sends_turn_start_without_resume():
    def respond(method, params):
        if method == "turn/start":
            return {"result": {"turn": {"id": "turn-fast-1"}}}
        raise AssertionError(f"unexpected method: {method}")

    with _fresh_env(respond) as env:
        result = codex._codex_resume_or_steer_via_app_server_locked(SID, "hi")

    assert result["ok"] is True
    assert result["via"] == "codex-app-turn"
    assert result["turn_id"] == "turn-fast-1"
    assert result["resume_skipped"] is True
    assert env.calls == ["turn/start"]


def test_thread_not_found_falls_back_to_resume_then_start():
    seen = {"resume": 0, "start": 0}

    def respond(method, params):
        if method == "turn/start":
            seen["start"] += 1
            if seen["start"] == 1:
                return {"error": {"message": "thread not found: " + SID}}
            return {"result": {"turn": {"id": "turn-after-resume"}}}
        if method == "thread/resume":
            seen["resume"] += 1
            return {"result": {"thread": {"status": {"type": "idle"}, "turns": []}}}
        raise AssertionError(f"unexpected method: {method}")

    with _fresh_env(respond) as env:
        result = codex._codex_resume_or_steer_via_app_server_locked(SID, "hi")

    assert result["ok"] is True
    assert result["turn_id"] == "turn-after-resume"
    assert not result.get("resume_skipped")
    assert env.calls == ["turn/start", "thread/resume", "turn/start"]
    assert seen == {"resume": 1, "start": 2}


def test_ambiguous_turn_start_failure_queues_without_retry_or_resume():
    def respond(method, params):
        if method == "turn/start":
            return {"error": {"message": "transport write timed out"}}
        raise AssertionError(f"unexpected method: {method}")

    with _fresh_env(respond) as env:
        result = codex._codex_resume_or_steer_via_app_server_locked(SID, "hi")

    assert result["ok"] is False
    assert result["fallback"] == "queue"
    assert result["stage"] == "turn/start"
    assert env.calls == ["turn/start"]


def test_busy_error_falls_through_to_resume_which_reports_active():
    def respond(method, params):
        if method == "turn/start":
            return {"error": {"message": "Invalid request: Cannot launch a new turn while another turn (ID 7) is active"}}
        if method == "thread/resume":
            return {"result": {"thread": {
                "status": {"type": "active"},
                "turns": [{"id": "turn-7", "status": "inProgress"}],
            }}}
        raise AssertionError(f"unexpected method: {method}")

    with _fresh_env(respond) as env:
        result = codex._codex_resume_or_steer_via_app_server_locked(SID, "hi")

    assert result["ok"] is False
    assert result["fallback"] == "queue"
    assert "active" in result["error"]
    assert env.calls == ["turn/start", "thread/resume"]


def test_stale_rollout_takes_the_resume_path():
    def respond(method, params):
        if method == "thread/resume":
            return {"result": {"thread": {"status": {"type": "idle"}, "turns": []}}}
        if method == "turn/start":
            return {"result": {"turn": {"id": "turn-resumed"}}}
        raise AssertionError(f"unexpected method: {method}")

    env = _LockedEnv(state=_state(last_event_at=time.time() - 3600), rollout=_rollout(time.time()))
    env.respond = respond
    with env:
        result = codex._codex_resume_or_steer_via_app_server_locked(SID, "hi")

    assert result["ok"] is True
    assert env.calls == ["thread/resume", "turn/start"]


class _SteerEnv:
    """Patch the world around _codex_steer_via_app_server."""

    def __init__(self, respond, state=None):
        self.calls = []
        self._respond = respond
        self.patches = [
            mock.patch.object(server, "_codex_app_server_is_live", return_value=True),
            mock.patch.object(server, "_codex_app_server_request", side_effect=self._request),
            mock.patch.object(codex, "_bind_codex_queued_steer_ack_suppression"),
        ]
        self._state = state

    def _request(self, method, params=None, timeout=20, **kw):
        self.calls.append(method)
        return self._respond(method, params or {})

    def __enter__(self):
        self._saved = server._CODEX_APP_SERVER_THREAD_STATE.get(SID)
        if self._state is not None:
            server._CODEX_APP_SERVER_THREAD_STATE[SID] = dict(self._state)
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        if self._saved is None:
            server._CODEX_APP_SERVER_THREAD_STATE.pop(SID, None)
        else:
            server._CODEX_APP_SERVER_THREAD_STATE[SID] = self._saved
        return False


def test_steer_uses_known_active_turn_without_resume():
    def respond(method, params):
        if method == "turn/steer":
            assert params["expectedTurnId"] == "turn-live-9"
            return {"result": {"turnId": "turn-live-9"}}
        raise AssertionError(f"unexpected method: {method}")

    with _SteerEnv(respond, state=_state(status="active", active_turn_id="turn-live-9")) as env:
        result = codex._codex_steer_via_app_server(SID, "nudge")

    assert result["ok"] is True
    assert result["via"] == "codex-steer"
    assert result["resume_skipped"] is True
    assert env.calls == ["turn/steer"]


def test_steer_falls_back_to_resume_on_thread_miss():
    def respond(method, params):
        if method == "turn/steer":
            return {"error": {"message": "thread not found: " + SID}}
        if method == "thread/resume":
            return {"result": {"thread": {"status": {"type": "idle"}, "turns": []}}}
        raise AssertionError(f"unexpected method: {method}")

    with _SteerEnv(respond, state=_state(status="active", active_turn_id="turn-gone")) as env:
        result = codex._codex_steer_via_app_server(SID, "nudge")

    assert result["ok"] is False
    assert result["code"] == "codex_no_active_turn"
    assert env.calls == ["turn/steer", "thread/resume"]


def test_steer_without_known_turn_resumes_first():
    def respond(method, params):
        if method == "thread/resume":
            return {"result": {"thread": {"status": {"type": "idle"}, "turns": []}}}
        raise AssertionError(f"unexpected method: {method}")

    with _SteerEnv(respond, state=_state()) as env:
        result = codex._codex_steer_via_app_server(SID, "nudge")

    assert result["code"] == "codex_no_active_turn"
    assert env.calls == ["thread/resume"]


def test_writer_snapshot_uses_worker_state_when_local_map_empty():
    """The dashboard's local thread map is empty when the worker owns the
    app-server — CCC's own turn writes then looked like an external writer."""
    worker_state = {
        "thread_id": SID,
        "status": "active",
        "active_turn_id": "turn-ccc-1",
        "active_writer": "ccc",
        "ccc_turn_start_pending": True,
        "last_event_at": time.time(),
        "last_activity_at": time.time(),
    }
    captured = {}

    def fake_engine_call(engine, operation, args, **kw):
        captured["operation"] = operation
        return {"ok": True, "states": {SID: dict(worker_state)}}

    saved = server._CODEX_APP_SERVER_THREAD_STATE.pop(SID, None)
    try:
        with mock.patch.object(server, "_codex_app_server_thread_state", return_value={}), \
             mock.patch.object(server, "_control_plane_engine_call", side_effect=fake_engine_call), \
             mock.patch.object(codex, "_codex_ccc_exec_child_running", return_value=False), \
             mock.patch.object(server, "_codex_shared_state_db_holders", return_value=[]), \
             mock.patch.object(server, "_codex_rollout_stat", return_value=_rollout(time.time())), \
             mock.patch.object(codex, "_codex_desktop_attached_rollouts", return_value={}):
            snap = codex._codex_thread_writer_snapshot(SID)
    finally:
        if saved is not None:
            server._CODEX_APP_SERVER_THREAD_STATE[SID] = saved

    assert captured["operation"] == "thread_states"
    assert snap["writer"] == "ccc"
    assert snap["external_active"] is False


def test_state_fields_writer_comes_from_worker_state():
    """codex_writer feeds the status pill + steer gating — it must see the
    worker's thread map, not the dashboard's empty local one."""
    import types

    now = time.time()
    worker_state = {
        "thread_id": SID,
        "status": "active",
        "active_turn_id": "turn-ccc-1",
        "active_writer": "ccc",
        "last_event_at": now,
        "last_activity_at": now,
    }
    st = types.SimpleNamespace(
        st_mtime=now, st_size=100, st_mtime_ns=int(now * 1e9),
    )

    def fake_engine_call(engine, operation, args, **kw):
        return {"ok": True, "states": {SID: dict(worker_state)}}

    saved = server._CODEX_APP_SERVER_THREAD_STATE.pop(SID, None)
    try:
        with mock.patch.object(server, "_codex_app_server_thread_state", return_value={}), \
             mock.patch.object(server, "_control_plane_engine_call", side_effect=fake_engine_call), \
             mock.patch.object(codex, "_codex_ccc_exec_child_running", return_value=False), \
             mock.patch.object(server, "_codex_shared_state_db_holders", return_value=[]), \
             mock.patch.object(codex, "_codex_desktop_attached_rollouts", return_value={}):
            fields = server._codex_state_fields(
                SID, rollout_path="/tmp/rollout.jsonl", rollout_stat=st,
            )
    finally:
        if saved is not None:
            server._CODEX_APP_SERVER_THREAD_STATE[SID] = saved

    assert fields["codex_writer"] == "ccc"


def test_turn_started_without_turn_id_keeps_ccc_writer():
    """turn/started whose params carry no turn id must not downgrade the
    "ccc" writer stamped by the turn/start response path — the notification
    is dispatched ~1ms after the response is processed, so pending is
    already popped; without the guard every CCC turn became
    external_turn_started."""
    thread_id = SID + "-noid"
    server._CODEX_APP_SERVER_THREAD_STATE[thread_id] = {
        "thread_id": thread_id,
        "status": "idle",
        "active_turn_id": "turn-live-1",
        "active_writer": "ccc",
        "turn_started_at": time.time(),
    }
    try:
        codex._codex_app_server_handle_notification("turn/started", {
            "threadId": thread_id,
        })
        state = server._codex_app_server_thread_state(thread_id)
        assert state["active_writer"] == "ccc"
        assert state["status"] == "active"
    finally:
        server._CODEX_APP_SERVER_THREAD_STATE.pop(thread_id, None)


def test_turn_started_with_foreign_turn_id_stays_unknown():
    """A turn/started carrying a DIFFERENT turn id is genuinely external:
    the ccc-writer guard must not claim it."""
    thread_id = SID + "-foreign"
    server._CODEX_APP_SERVER_THREAD_STATE[thread_id] = {
        "thread_id": thread_id,
        "status": "idle",
        "active_turn_id": "turn-ours",
        "active_writer": "ccc",
    }
    try:
        codex._codex_app_server_handle_notification("turn/started", {
            "threadId": thread_id, "turnId": "turn-foreign",
        })
        state = server._codex_app_server_thread_state(thread_id)
        assert state["active_writer"] == "unknown"
        assert state["active_turn_id"] == "turn-foreign"
    finally:
        server._CODEX_APP_SERVER_THREAD_STATE.pop(thread_id, None)


def test_writer_snapshot_local_state_used_when_remote_empty():
    """Local entries still count when the worker reports nothing for the
    thread — the merge is remote-wins-per-key, not remote-only."""
    now = time.time()
    saved = server._CODEX_APP_SERVER_THREAD_STATE.get(SID)
    server._CODEX_APP_SERVER_THREAD_STATE[SID] = {
        "thread_id": SID, "status": "idle",
        "last_event_at": now, "last_activity_at": now,
    }
    try:
        # fixture stubs _control_plane_engine_call -> None (remote empty)
        with mock.patch.object(codex, "_codex_ccc_exec_child_running", return_value=False), \
             mock.patch.object(server, "_codex_rollout_stat", return_value=_rollout(now)), \
             mock.patch.object(codex, "_codex_desktop_attached_rollouts", return_value={}):
            snap = codex._codex_thread_writer_snapshot(SID)
    finally:
        if saved is None:
            server._CODEX_APP_SERVER_THREAD_STATE.pop(SID, None)
        else:
            server._CODEX_APP_SERVER_THREAD_STATE[SID] = saved

    assert snap["external_active"] is False


def test_remote_state_shadows_stale_local_entry():
    """Routed call responses (thread/list refresh, thread/read results)
    get record_thread'ed into the CALLER's map too, so a dashboard-local
    entry is a stale snapshot — the worker's live state must win."""
    now = time.time()
    saved = server._CODEX_APP_SERVER_THREAD_STATE.get(SID)
    server._CODEX_APP_SERVER_THREAD_STATE[SID] = {
        "thread_id": SID, "status": "notLoaded", "last_event_at": now - 3600,
    }
    worker_state = {
        "thread_id": SID, "status": "active",
        "active_turn_id": "turn-ccc-9", "active_writer": "ccc",
        "last_event_at": now, "last_activity_at": now,
    }

    def fake_engine_call(engine, operation, args, **kw):
        return {"ok": True, "states": {SID: dict(worker_state)}}

    try:
        with mock.patch.object(server, "_control_plane_engine_call", side_effect=fake_engine_call), \
             mock.patch.object(codex, "_codex_ccc_exec_child_running", return_value=False), \
             mock.patch.object(server, "_codex_rollout_stat", return_value=_rollout(now)), \
             mock.patch.object(codex, "_codex_desktop_attached_rollouts", return_value={}):
            snap = codex._codex_thread_writer_snapshot(SID)
    finally:
        if saved is None:
            server._CODEX_APP_SERVER_THREAD_STATE.pop(SID, None)
        else:
            server._CODEX_APP_SERVER_THREAD_STATE[SID] = saved

    assert snap["writer"] == "ccc"
    assert snap["external_active"] is False


_STEER_TIMEOUT = {
    "ok": False,
    "error": "Codex app-server request timed out: turn/steer",
    "fallback": "queue",
    "ambiguous": True,
}


def test_timed_out_steer_found_in_rollout_is_delivered():
    """CCC-238: Codex applied the steer but its reply came after the timeout."""
    def respond(method, params):
        if method == "turn/steer":
            return dict(_STEER_TIMEOUT)
        raise AssertionError(f"unexpected method: {method}")

    with _SteerEnv(respond, state=_state(status="active", active_turn_id="turn-live-9")) as env, \
            mock.patch.object(server, "_codex_rollout_stat", return_value={"path": "r", "size": 1}), \
            mock.patch.object(server, "_codex_rollout_contains_user_text_since", return_value=True):
        result = codex._codex_steer_via_app_server(SID, "nudge")

    assert result["ok"] is True
    assert result["reconciled"] == "rollout"
    assert env.calls == ["turn/steer"]


def test_timed_out_steer_missing_from_rollout_is_unknown_not_failed():
    def respond(method, params):
        if method == "turn/steer":
            return dict(_STEER_TIMEOUT)
        raise AssertionError(f"unexpected method: {method}")

    with _SteerEnv(respond, state=_state(status="active", active_turn_id="turn-live-9")) as env, \
            mock.patch.object(codex, "_CODEX_STEER_RECONCILE_S", 0.0), \
            mock.patch.object(server, "_codex_rollout_stat", return_value=None), \
            mock.patch.object(server, "_codex_rollout_contains_user_text_since", return_value=False):
        result = codex._codex_steer_via_app_server(SID, "nudge")

    assert result["ok"] is False
    assert result["code"] == "codex_steer_unknown"
    assert result["uncertain"] is True
    assert env.calls == ["turn/steer"]


def test_definite_steer_error_stays_failed():
    def respond(method, params):
        if method == "turn/steer":
            return {"error": {"message": "boom"}}
        raise AssertionError(f"unexpected method: {method}")

    with _SteerEnv(respond, state=_state(status="active", active_turn_id="turn-live-9")):
        result = codex._codex_steer_via_app_server(SID, "nudge")

    assert result["code"] == "codex_steer_failed"
    assert not result.get("uncertain")
