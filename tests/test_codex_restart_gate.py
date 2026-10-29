"""CCC-250: a worker restart must not abort live Codex app-server turns.

The freeze is set under the app-server condition, after which the send path
refuses everything outside a strict allow-list. acquire() then needs native
proof: no live turn in CCC's bookkeeping, no unanswered mutating request,
no untracked app-server child, and an empty `thread/loaded/list`. Codex
only runs a turn on its own on a loaded thread, and loading one needs a
request the freeze refuses.

These tests drive the real gate, transport send path, reader handler,
restart functions and run.sh helpers against a hermetic fake app-server
(and fake child processes); nothing real is started, stopped or killed.
"""

import functools
import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap
import threading
import time

import pytest
import server
from ccc_server import codex as codex_mod
from ccc_server import codex_restart_gate as gate

REPO = pathlib.Path(__file__).resolve().parents[1]


class _NativeAppServer:
    """Stand-in for `codex app-server` on the stdio transport.

    Replies go through the real reader handler. A thread becomes loaded only
    through thread/start, thread/resume or thread/fork, as in Codex, and
    tick() runs an autonomous turn (goal continuation / queued message) on
    every loaded thread -- the case a CCC-only freeze cannot see.
    """

    kind = "stdio"

    def __init__(self, proc=None):
        self.proc = proc
        self.reader_exited = False
        self.dead = False
        self.sent = []
        self.loaded = []
        self.pages = None          # optional list of pages for loaded/list
        self.silent = set()        # methods that never get a reply
        self.errors = {}           # method -> JSON-RPC error
        self.on_send = {}          # method -> hook(payload) before replying
        self.autonomous_turns = []

    def alive(self):
        if self.proc is not None:
            return self.proc.poll() is None
        return not self.dead

    def send_json(self, payload):
        self.sent.append(payload)
        method = payload["method"]
        hook = self.on_send.get(method)
        if hook:
            hook(payload)
        if method in self.silent:
            return
        if method in self.errors:
            self.reply(payload["id"], error=self.errors[method])
            return
        params = payload.get("params") or {}
        if method == "thread/loaded/list":
            if self.pages is not None:
                index = int(params.get("cursor") or 0)
                page = self.pages[index]
                cursor = str(index + 1) if index + 1 < len(self.pages) else None
                self.reply(payload["id"], {"data": page, "nextCursor": cursor})
            else:
                self.reply(payload["id"], {"data": list(self.loaded), "nextCursor": None})
            return
        if method in ("thread/start", "thread/resume", "thread/fork"):
            self.loaded.append(params.get("threadId") or "t-new")
        if method == "turn/start":
            self.reply(payload["id"], {"turn": {"id": f"u-{payload['id']}"}})
            return
        self.reply(payload["id"], {})

    def reply(self, req_id, result=None, error=None):
        message = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        server._codex_app_server_handle_message(message)

    def tick(self):
        self.autonomous_turns.extend(self.loaded)


@pytest.fixture
def native(monkeypatch):
    transport = _NativeAppServer()
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", transport)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_THREAD_STATE", {})
    monkeypatch.setattr(server, "_app_server_trace", lambda *a, **k: None)
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(gate, "_frozen_until", 0.0)
    monkeypatch.setattr(gate, "_frozen_reason", "")
    monkeypatch.setattr(gate, "_owner", "")
    codex_mod._CODEX_APP_SERVER_MUTATING_PENDING.clear()
    yield transport
    codex_mod._CODEX_APP_SERVER_MUTATING_PENDING.clear()
    gate.release(gate._owner)


def _release_owned():
    """Lift whichever lease is live, as its owner would."""
    return gate.release(gate._owner)


def _fake_child(*extra):
    """A real child process whose command line looks like an app-server."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)",
         "app-server", "--listen", "stdio://", *extra],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
    )


def _reap(proc):
    proc.kill()
    proc.wait(timeout=5)


# --- Finding 1: silence never expires an uncompleted turn -----------------

@pytest.mark.parametrize("silent_s", [599, 601, 3_600, 30 * 86_400])
def test_long_silent_turn_still_blocks(native, silent_s):
    server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {
        "active_turn_id": "u1", "last_activity_at": time.time() - silent_s,
        "turn_started_at": time.time() - silent_s,
    }
    out = gate.acquire("test")
    assert out["frozen"] is False and out["threads"] == ["t1"]
    assert not gate.frozen()
    assert server._codex_app_server_live_turns(now=time.time() + 10 * 86_400) == ["t1"]


@pytest.mark.parametrize("state", [
    {"ccc_turn_start_pending": True},                      # admitted, unanswered
    {"status": "active", "active_turn_id": None},          # terminal seen, status not
    {"status": "idle", "active_turn_id": "u1"},            # status idle, turn open
    {"status": "ACTIVE"},
])
def test_pending_and_terminal_mismatches_count_as_live(native, state):
    server._CODEX_APP_SERVER_THREAD_STATE["t1"] = dict(state)
    assert gate.acquire("test")["frozen"] is False


def test_only_completion_clears_a_turn(native):
    server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {"active_turn_id": "u1"}
    assert gate.acquire("test")["frozen"] is False
    server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {"active_turn_id": None, "status": "idle"}
    assert gate.acquire("test")["frozen"] is True


# --- Finding 2: native admission barrier ----------------------------------

def test_loaded_idle_thread_refuses_autonomous_case(native):
    """No CCC turn is live, but a loaded thread can start a goal
    continuation or queued turn on its own at any moment."""
    native.loaded = ["t-goal"]
    out = gate.acquire("test")
    assert out["frozen"] is False
    assert out["reason"] == "threads_loaded" and out["threads"] == ["t-goal"]
    assert not gate.frozen()  # the refusal releases the freeze


def test_empty_loaded_list_freezes(native):
    out = gate.acquire("test")
    assert out["frozen"] is True and out["reason"] == "no_loaded_threads"
    assert [m["method"] for m in native.sent] == ["thread/loaded/list"]


def test_loaded_list_follows_every_page(native):
    native.pages = [[], [], ["t-late"]]
    out = gate.acquire("test")
    assert out["frozen"] is False and out["threads"] == ["t-late"]
    assert [m["params"].get("cursor") for m in native.sent] == [None, "1", "2"]


def test_loaded_list_error_or_timeout_is_unknown(native, monkeypatch):
    native.errors["thread/loaded/list"] = {"code": -32601, "message": "nope"}
    assert gate.acquire("test")["reason"].startswith("loaded_list_failed")
    native.errors.clear()
    native.silent.add("thread/loaded/list")
    monkeypatch.setattr(
        server, "_codex_app_server_restart_barrier",
        functools.partial(codex_mod._codex_app_server_restart_barrier, timeout=0.1),
    )
    out = gate.acquire("test")
    assert out["frozen"] is False and out["reason"].startswith("loaded_list_failed")
    assert not gate.frozen()


def test_freeze_is_a_strict_allow_list(native):
    assert gate.acquire("test")["frozen"] is True
    for method in ("turn/start", "turn/steer", "thread/resume", "thread/start",
                   "thread/fork", "thread/read", "thread/goal/set",
                   "thread/goal/get", "thread/unsubscribe"):
        refused = server._codex_app_server_request_to_transport(
            native, method, {"threadId": "t1"}, timeout=0.1,
        )
        assert refused["code"] == "codex_restart_pending", method
        refused = server._codex_app_server_request_once(
            method, {"threadId": "t1"}, timeout=0.1, _route=False,
        )
        assert refused["code"] == "codex_restart_pending", method
    assert native.sent == [{"jsonrpc": "2.0", "id": native.sent[0]["id"],
                            "method": "thread/loaded/list",
                            "params": {"limit": 100}}]
    assert native.loaded == []
    for method in ("turn/interrupt", "thread/list", "thread/loaded/list"):
        reply = server._codex_app_server_request_to_transport(
            native, method, {"threadId": "t1", "turnId": "u1"}, timeout=0.5,
        )
        assert "code" not in reply, method


def test_unanswered_mutating_request_refuses_until_reply(native):
    native.silent.add("thread/resume")
    reply = server._codex_app_server_request_to_transport(
        native, "thread/resume", {"threadId": "t1"}, timeout=0.05,
    )
    assert reply.get("ok") is False  # timed out, reply may still land
    out = gate.acquire("test")
    assert out["frozen"] is False and out["reason"] == "requests_unanswered:thread/resume"
    # The late reply arrives through the real reader path and clears it.
    req_id = native.sent[-1]["id"]
    native.reply(req_id, {})
    assert gate.acquire("test")["frozen"] is True


def test_turn_admitted_during_proof_refuses(native):
    def admit(_payload):
        server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {"ccc_turn_start_pending": True}
    native.on_send["thread/loaded/list"] = admit
    out = gate.acquire("test")
    assert out["frozen"] is False and out["reason"] == "admitted_during_proof"


def test_transport_swapped_during_proof_refuses(native, monkeypatch):
    other = _NativeAppServer()
    native.on_send["thread/loaded/list"] = (
        lambda _p: setattr(server, "_CODEX_APP_SERVER_TRANSPORT", other)
    )
    assert gate.acquire("test")["reason"] == "transport_changed"


def test_admission_race_never_both_sends_and_freezes(native):
    """A turn/start that won the lock first is counted; one that lost is
    refused. No interleaving lets both the freeze and the send succeed."""
    for _ in range(50):
        _release_owned()
        server._CODEX_APP_SERVER_THREAD_STATE.clear()
        native.sent.clear()
        results = {}

        def start():
            results["send"] = server._codex_app_server_request_once(
                "turn/start", {"threadId": "t1"}, timeout=0.05, _route=False,
            )

        def freeze():
            results["freeze"] = gate.acquire("race")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(server, "_ensure_codex_app_server", lambda **k: native)
            threads = [threading.Thread(target=start), threading.Thread(target=freeze)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        sent = any(m.get("method") == "turn/start" for m in native.sent)
        assert not (sent and results["freeze"]["frozen"])


# --- Freeze TTL / race failure paths ---------------------------------------

def test_freeze_lapsing_during_proof_is_not_proof(native):
    native.on_send["thread/loaded/list"] = lambda _p: setattr(gate, "_frozen_until", 0.0)
    out = gate.acquire("test")
    assert out["frozen"] is False and out["reason"] == "freeze_lapsed"


def test_owner_release_during_proof_is_not_proof(native):
    native.on_send["thread/loaded/list"] = lambda _p: _release_owned()
    assert gate.acquire("test")["frozen"] is False


@pytest.mark.parametrize("token", [None, "", "f" * 32])
def test_foreign_or_tokenless_release_never_lifts(native, token):
    out = gate.acquire("A")
    assert out["frozen"] is True
    assert gate.release(token) == {"ok": True, "released": False, "reason": "not_owner"}
    assert gate.frozen() and gate.blocks("thread/resume")
    # The owner's own release still works, and only once.
    assert gate.release(out["token"])["released"] is True
    assert gate.release(out["token"])["released"] is False


def test_second_acquire_is_refused_and_leaves_lease_intact(native):
    a = gate.acquire("A")
    before = (gate._frozen_until, gate._owner)
    b = gate.acquire("B")
    assert b["frozen"] is False and b["reason"] == "restart_in_progress"
    assert "token" not in b
    assert (gate._frozen_until, gate._owner) == before
    # A forged renewal is refused the same way.
    forged = gate.acquire("B", token="0" * 32)
    assert forged["frozen"] is False and forged["reason"] == "lease_lost"
    assert (gate._frozen_until, gate._owner) == before
    assert gate.acquire("A final", token=a["token"])["frozen"] is True


def test_refused_renewal_releases_only_its_own_lease(native):
    a = gate.acquire("A")
    native.loaded = ["t-goal"]
    # A's own renewal fails its proof: A's lease goes, nobody else's.
    out = gate.acquire("A final", token=a["token"])
    assert out["frozen"] is False and not gate.frozen()
    native.loaded = []
    b = gate.acquire("B")
    assert b["frozen"] is True
    # A's stale token can neither renew nor lift B's lease.
    assert gate.acquire("A again", token=a["token"])["reason"] == "lease_lost"
    assert gate.release(a["token"])["released"] is False
    assert gate._owner == b["token"] and gate.frozen()


def test_lapsed_lease_token_renews_nothing(native):
    a = gate.acquire("A", ttl_s=5)
    setattr(gate, "_frozen_until", time.time() - 1)
    out = gate.acquire("A final", token=a["token"])
    assert out["frozen"] is False and out["reason"] == "lease_lost"
    assert not gate.frozen()


def test_lapsed_freeze_is_reproved_not_trusted(native):
    assert gate.acquire("test", ttl_s=5)["frozen"] is True
    assert not gate.frozen(now=time.time() + 6)
    # While lapsed a client loaded a thread; the re-run proof catches it.
    _release_owned()
    server._codex_app_server_request_to_transport(
        native, "thread/resume", {"threadId": "t-x"}, timeout=0.5,
    )
    assert gate.acquire("final")["frozen"] is False


def test_barrier_exception_releases(native, monkeypatch):
    monkeypatch.setattr(server, "_codex_app_server_restart_barrier",
                        lambda: 1 / 0)
    out = gate.acquire("test")
    assert out["frozen"] is False and out["reason"].startswith("barrier_error")
    assert not gate.frozen()


def test_no_server_module_freezes_with_zero_turns(monkeypatch):
    monkeypatch.setattr(gate, "_app_server_lock", lambda: None)
    monkeypatch.setattr(gate, "_frozen_until", 0.0)
    monkeypatch.setattr(gate, "_owner", "")
    out = gate.acquire("cold worker")
    assert out["frozen"] is True and out["live_turns"] == 0
    assert gate.acquire("second")["reason"] == "restart_in_progress"
    assert gate.release(out["token"])["released"] is True


# --- Process ownership / dead transport ------------------------------------

def test_real_child_transport_and_dead_transport_proof(native, monkeypatch):
    child = _fake_child()
    try:
        transport = _NativeAppServer(proc=child)
        monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", transport)
        # Our own tracked child is not a stray.
        assert gate.acquire("test")["frozen"] is True
        _release_owned()
        transport.silent.add("turn/start")
        server._codex_app_server_request_to_transport(
            transport, "turn/start", {"threadId": "t1"}, timeout=0.05,
        )
        server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {"active_turn_id": "u1"}
        assert gate.acquire("test")["frozen"] is False
        # Authoritative death: the child exited and its reader drained.
        _reap(child)
        transport.reader_exited = True
        out = gate.acquire("test")
        assert out["frozen"] is True and out["reason"] == "no_app_server"
        assert codex_mod._CODEX_APP_SERVER_MUTATING_PENDING == {}
    finally:
        if child.poll() is None:
            _reap(child)


def test_dead_proc_with_undrained_reader_keeps_pending(native, monkeypatch):
    child = _fake_child()
    transport = _NativeAppServer(proc=child)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", transport)
    transport.silent.add("thread/resume")
    server._codex_app_server_request_to_transport(
        transport, "thread/resume", {"threadId": "t1"}, timeout=0.05,
    )
    _reap(child)
    assert gate.acquire("test")["reason"].startswith("requests_unanswered")


def test_untracked_app_server_child_refuses(native, monkeypatch):
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", None)
    child = _fake_child()
    try:
        out = gate.acquire("test")
        assert out["frozen"] is False
        assert out["reason"] == f"untracked_app_server:{child.pid}"
    finally:
        _reap(child)
    assert gate.acquire("test")["frozen"] is True


def test_unreadable_process_table_refuses(native, monkeypatch):
    def boom(*a, **k):
        raise OSError("no ps")
    monkeypatch.setattr(codex_mod.subprocess, "check_output", boom)
    assert gate.acquire("test")["reason"] == "process_table_unreadable"


def test_managed_transport_gets_the_same_proof(native):
    native.kind = "managed-unix"
    native.loaded = ["t-other-client"]
    assert gate.acquire("test")["frozen"] is False


# --- Worker RPC and the real restart path ----------------------------------

@pytest.fixture
def runtime(tmp_path):
    from ccc_worker import WorkerRuntime
    from control_plane import WorkLedger
    return WorkerRuntime(ledger=WorkLedger(tmp_path / "l.sqlite3"), token="d" * 64)


def test_worker_rpc_round_trip(native, runtime):
    server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {"active_turn_id": "u1"}
    busy = runtime.dispatch("codex.restart_freeze", {"enabled": True})
    assert busy["frozen"] is False and busy["live_turns"] == 1
    assert runtime.dispatch("health", {})["codex_live_turns"] == 1
    server._CODEX_APP_SERVER_THREAD_STATE.clear()
    native.loaded = ["t1"]
    assert runtime.dispatch("codex.restart_freeze", {"enabled": True})["frozen"] is False
    native.loaded = []
    lease = runtime.dispatch("codex.restart_freeze", {"enabled": True})
    assert lease["frozen"] is True and lease["token"]
    assert runtime.dispatch("codex.restart_freeze", {"enabled": True})["reason"] == "restart_in_progress"
    assert runtime.dispatch("codex.restart_freeze", {"enabled": False})["released"] is False
    renewed = runtime.dispatch("codex.restart_freeze",
                               {"enabled": True, "token": lease["token"]})
    assert renewed["frozen"] is True and renewed["token"] == lease["token"]
    assert runtime.dispatch("codex.restart_freeze",
                            {"enabled": False, "token": lease["token"]})["released"] is True
    assert "codex-turn-gate-v1" in runtime.dispatch("health", {})["worker"]["capabilities"]


def _restart_through_worker(monkeypatch, runtime, kill):
    """Run the real freeze + _restart_worker_process against the worker RPC,
    with the systemd restart replaced by `kill`."""
    monkeypatch.setattr(
        server, "_control_plane_request",
        lambda method, params=None, **k: runtime.dispatch(method, params or {}),
    )
    monkeypatch.setattr(server, "_worker_systemd_scope", lambda pid: ("unit", True))
    monkeypatch.setattr(server, "_restart_systemd_worker", kill)
    frozen, refusal = server._codex_restart_freeze("test-roll")
    assert frozen, refusal
    return server._restart_worker_process(codex_final_check=True)


def test_thread_loaded_after_precheck_aborts_before_kill(native, runtime, monkeypatch):
    kills = []
    # Between the precheck and the kill a request admitted before the
    # freeze lands and loads a thread that can run autonomously.
    monkeypatch.setattr(server, "_worker_systemd_scope",
                        lambda pid: (native.loaded.append("t-goal"), ("unit", True))[1])
    monkeypatch.setattr(
        server, "_control_plane_request",
        lambda method, params=None, **k: runtime.dispatch(method, params or {}),
    )
    monkeypatch.setattr(server, "_restart_systemd_worker",
                        lambda *a, **k: kills.append(a) or {"restarted": True})
    assert server._codex_restart_freeze("test-roll")[0] is True
    outcome = server._restart_worker_process(codex_final_check=True)
    assert outcome == {"restarted": False, "reason": "codex_turns_running",
                       "codex_live_turns": 1}
    assert kills == []


def test_nothing_can_start_after_final_check_before_kill(native, runtime, monkeypatch):
    seen = {}

    def kill(*a, **k):
        # Last instant before the kill: every client path that could load a
        # thread is refused, so no autonomous turn exists to abort.
        for method in ("thread/resume", "thread/start", "turn/start"):
            seen[method] = server._codex_app_server_request_once(
                method, {"threadId": "t1"}, timeout=0.1, _route=False,
            ).get("code")
        native.tick()
        seen["loaded"] = list(native.loaded)
        seen["autonomous"] = list(native.autonomous_turns)
        return {"restarted": True}

    outcome = _restart_through_worker(monkeypatch, runtime, kill)
    assert outcome == {"restarted": True}
    assert seen == {"thread/resume": "codex_restart_pending",
                    "thread/start": "codex_restart_pending",
                    "turn/start": "codex_restart_pending",
                    "loaded": [], "autonomous": []}


def _route_to_worker(monkeypatch, runtime, adopt=None):
    """Dashboard control-plane calls go to the real worker RPC; engine.adopt
    and drain.set answer from `adopt` (default: worker accepts)."""
    def request(method, params=None, **k):
        if method == "engine.adopt" and adopt is not None:
            return adopt
        if method in ("engine.adopt", "drain.set"):
            return {"ok": True, "available": True}
        return runtime.dispatch(method, params or {})
    monkeypatch.setattr(server, "_control_plane_request", request)
    monkeypatch.setattr(server, "_dashboard_owned_active_executions", lambda: [])
    monkeypatch.setattr(server, "_worker_systemd_scope", lambda pid: ("unit", True))


def _in_thread(fn):
    """Run fn on its own thread, as a second HTTP handler would."""
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("out", fn()))
    t.start()
    t.join(timeout=30)
    return box["out"]


def test_overlapping_restart_cannot_release_after_final_proof(native, runtime, monkeypatch):
    """Verifier witness (verify:2): restart A passes its final proof; at the
    kill boundary restart B runs the real precheck, the worker refuses to
    adopt, and B runs the real release. A's freeze must hold: no thread can
    load and no autonomous turn exists when A's kill lands."""
    _route_to_worker(monkeypatch, runtime,
                     adopt={"ok": False, "available": True, "error": "adopt failed"})
    seen = {}

    def kill(*a, **k):
        def restart_b():
            precheck = server._safe_worker_restart_precheck()
            server._codex_restart_release("B did not restart")
            return precheck
        seen["b"] = _in_thread(restart_b)
        seen["frozen_after_b"] = gate.frozen()
        seen["resume"] = server._codex_app_server_request_to_transport(
            native, "thread/resume", {"threadId": "t-race"}, timeout=0.2,
        ).get("code")
        native.tick()
        seen["loaded"] = list(native.loaded)
        seen["autonomous"] = list(native.autonomous_turns)
        return {"restarted": True}

    monkeypatch.setattr(server, "_restart_systemd_worker", kill)
    assert server._codex_restart_freeze("A")[0] is True
    outcome = server._restart_worker_process(codex_final_check=True)
    assert outcome == {"restarted": True}
    ok, err, _, _ = seen["b"]
    assert ok is False and err["code"] == "codex_restart_in_progress"
    assert seen["frozen_after_b"] is True
    assert seen["resume"] == "codex_restart_pending"
    assert seen["loaded"] == [] and seen["autonomous"] == []


def test_overlapping_restart_b_adoption_failure_after_owning_lease(native, runtime, monkeypatch):
    """B that owns its own lease and fails adoption releases exactly that
    lease; A, which never got one, has no kill authority."""
    _route_to_worker(monkeypatch, runtime,
                     adopt={"ok": False, "available": True, "error": "adopt failed"})
    kills = []
    monkeypatch.setattr(server, "_restart_systemd_worker",
                        lambda *a, **k: kills.append(a) or {"restarted": True})
    b_lease = gate.acquire("B (another dashboard)")
    assert b_lease["frozen"] is True
    a_ok, a_err = server._codex_restart_freeze("A")
    assert a_ok is False and a_err["code"] == "codex_restart_in_progress"
    assert server._restart_worker_process(codex_final_check=True)["restarted"] is False
    server._codex_restart_release("A refused")      # A holds no token: no-op
    assert gate._owner == b_lease["token"] and gate.frozen()
    assert kills == []


def test_restart_that_lost_its_lease_has_no_kill_authority(native, runtime, monkeypatch):
    _route_to_worker(monkeypatch, runtime)
    kills = []
    monkeypatch.setattr(server, "_restart_systemd_worker",
                        lambda *a, **k: kills.append(a) or {"restarted": True})
    assert server._codex_restart_freeze("A")[0] is True
    a_token = server._codex_restart_lease_token()
    # A's lease lapses (a slow pull), and restart B takes a lease of its own.
    gate._frozen_until = time.time() - 1
    b = _in_thread(lambda: (server._codex_restart_freeze("B"),
                            server._codex_restart_lease_token()))
    assert b[0][0] is True and b[1] != a_token
    outcome = server._restart_worker_process(codex_final_check=True)
    assert outcome["restarted"] is False and outcome["codex_live_turns"] != 0
    server._codex_restart_release("A did not restart")
    assert kills == []
    assert gate._owner == b[1] and gate.frozen()


def test_final_check_refuses_a_lease_too_short_for_the_kill(native, runtime, monkeypatch):
    _route_to_worker(monkeypatch, runtime)
    kills = []
    monkeypatch.setattr(server, "_restart_systemd_worker",
                        lambda *a, **k: kills.append(a) or {"restarted": True})
    monkeypatch.setattr(server, "_CODEX_KILL_WINDOW_S", gate.MAX_TTL_S + 1)
    assert server._codex_restart_freeze("A")[0] is True
    assert server._restart_worker_process(codex_final_check=True)["restarted"] is False
    assert kills == []


def test_final_check_without_a_lease_is_unknown(native, runtime, monkeypatch):
    _route_to_worker(monkeypatch, runtime)
    server._CODEX_RESTART_LEASE.token = ""
    assert server._codex_restart_final_check() == -1
    assert not gate.frozen()


def test_kill_exception_keeps_freeze_until_owner_releases(native, runtime, monkeypatch):
    """A kill that raises leaves the lease in place (fail closed: Codex
    waits, nothing is admitted); a foreign release cannot lift it and the
    owner's later release does."""
    _route_to_worker(monkeypatch, runtime)

    def kill(*a, **k):
        raise OSError("systemctl vanished")

    monkeypatch.setattr(server, "_restart_systemd_worker", kill)
    assert server._codex_restart_freeze("A")[0] is True
    with pytest.raises(OSError):
        server._restart_worker_process(codex_final_check=True)
    assert gate.frozen() and gate.blocks("thread/resume")
    _in_thread(lambda: server._codex_restart_release("foreign"))
    assert gate.frozen()
    assert runtime.dispatch("codex.restart_freeze", {"enabled": False})["released"] is False
    server._codex_restart_release("A cleanup")
    assert not gate.frozen()
    # Released twice is a no-op, never an error.
    server._codex_restart_release("A cleanup again")


def test_old_worker_without_rpc_never_restarts(monkeypatch):
    def old_worker(method, params=None, **k):
        if method == "codex.restart_freeze":
            return {"ok": False, "code": "method_not_found"}
        return {"ok": True, "active": 0, "queued": 0, "uncertain": 0,
                "worker": {"pid": 4242, "capabilities": ["engine-execution-v1"]}}
    kills = []
    monkeypatch.setattr(server, "_control_plane_request", old_worker)
    monkeypatch.setattr(server, "_worker_systemd_scope", lambda pid: ("unit", True))
    monkeypatch.setattr(server, "_restart_systemd_worker",
                        lambda *a, **k: kills.append(a) or {"restarted": True})
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    frozen, refusal = server._codex_restart_freeze("x")
    assert not frozen and refusal["code"] == "codex_gate_unsupported"
    assert server._restart_worker_process(codex_final_check=True)["restarted"] is False
    assert kills == []


# --- run.sh: the real helpers and parser against a fake worker CLI ---------

_FAKE_WORKER = textwrap.dedent('''
    import json, os, sys
    here = os.path.dirname(os.path.abspath(__file__))
    scenario = json.load(open(os.path.join(here, "scenario.json")))
    flag = sys.argv[1] if len(sys.argv) > 1 else ""
    with open(os.path.join(here, "calls.log"), "a") as fh:
        fh.write(" ".join(sys.argv[1:]) + "\\n")
    reply = scenario.get(flag)
    if reply is None:
        sys.exit(1)
    print(json.dumps(reply))
    sys.exit(0 if reply.get("ok") and reply.get("frozen", True) else 1)
''')


def _run_sh_functions(*names):
    src = (REPO / "run.sh").read_text()
    out = []
    for name in names:
        m = re.search(rf"^{name}\(\) {{\n.*?^}}\n", src, re.S | re.M)
        assert m, name
        out.append(m.group(0))
    return "\n".join(out)


def _run_helper(tmp_path, scenario, call):
    (tmp_path / "ccc_worker.py").write_text(_FAKE_WORKER)
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))
    script = _run_sh_functions("codex_restart_freeze_ok", "worker_stop_safe")
    proc = subprocess.run(
        ["bash", "-c", f"{script}\n{call}"],
        env={**os.environ, "HERE": str(tmp_path), "PYTHON": sys.executable},
        capture_output=True, text=True, timeout=30,
    )
    calls = (tmp_path / "calls.log").read_text().splitlines() if (tmp_path / "calls.log").exists() else []
    return proc.returncode, calls


_IDLE = {"ok": True, "active": 0, "queued": 0, "uncertain": 0, "codex_live_turns": 0}


def test_run_sh_freeze_ok_needs_freeze_and_zero_turns(tmp_path):
    frozen = {"ok": True, "frozen": True, "token": "tok-A"}
    assert _run_helper(tmp_path, {"--codex-restart-freeze": frozen, "--health": _IDLE},
                       "codex_restart_freeze_ok")[0] == 0
    for scenario in (
        {"--health": _IDLE},                                      # old worker: no RPC
        {"--codex-restart-freeze": {"ok": True, "frozen": False}, "--health": _IDLE},
        {"--codex-restart-freeze": frozen,
         "--health": {k: v for k, v in _IDLE.items() if k != "codex_live_turns"}},
        {"--codex-restart-freeze": frozen},                       # health unreachable
        {"--codex-restart-freeze": {"ok": True, "frozen": True},   # no lease token
         "--health": _IDLE},
    ):
        (tmp_path / "calls.log").unlink(missing_ok=True)
        rc, calls = _run_helper(tmp_path, dict(scenario, **{"--codex-restart-release": {"ok": True}}),
                                "codex_restart_freeze_ok")
        assert rc != 0, scenario
        # A refusal releases only the lease this shell took, never a
        # token-less (anyone's) release.
        for call in calls:
            if call.startswith("--codex-restart-release"):
                assert call == "--codex-restart-release --codex-restart-token tok-A"


def test_run_sh_final_check_renews_its_own_lease(tmp_path):
    frozen = {"ok": True, "frozen": True, "token": "tok-A"}
    rc, calls = _run_helper(
        tmp_path, {"--codex-restart-freeze": frozen, "--health": _IDLE},
        "codex_restart_freeze_ok && codex_restart_freeze_ok",
    )
    assert rc == 0
    assert calls == ["--codex-restart-freeze", "--health",
                     "--codex-restart-freeze --codex-restart-token tok-A", "--health"]


def test_run_sh_stop_safe_refuses_running_unreachable_worker(tmp_path):
    # Nothing answers and nothing of this install runs: safe.
    assert _run_helper(tmp_path, {}, "worker_stop_safe")[0] == 0
    # A worker of this install runs but does not answer: unknown, refused.
    (tmp_path / "ccc_worker.py").write_text(_FAKE_WORKER)
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)",
         str(tmp_path / "ccc_worker.py")],
    )
    try:
        assert _run_helper(tmp_path, {}, "worker_stop_safe")[0] != 0
    finally:
        _reap(sleeper)


def _run_sh_idle_parser():
    src = (REPO / "run.sh").read_text()
    start = src.index("read -r existing_worker_pid existing_worker_idle worker_compatible <<EOF")
    body = src[start:]
    body = body[body.index("-c '") + 4:]
    return body[:body.index("\n')\nEOF")]


@pytest.mark.parametrize("health, idle", [
    (dict(_IDLE, worker={"pid": 7}), "1"),
    ({"ok": True, "active": 0, "queued": 0, "uncertain": 0, "worker": {"pid": 7}}, "0"),
    (dict(_IDLE, ok=False), "0"),
    (dict(_IDLE, codex_live_turns=-1), "0"),
    (dict(_IDLE, codex_live_turns=2), "0"),
    ({}, "0"),
    ("not json", "0"),
])
def test_run_sh_idle_parser_never_treats_unknown_as_idle(health, idle):
    payload = health if isinstance(health, str) else json.dumps(health)
    out = subprocess.run(
        [sys.executable, "-c", _run_sh_idle_parser()],
        input=payload, capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[1] == idle


def test_run_sh_every_worker_kill_is_gated():
    src = (REPO / "run.sh").read_text()
    compat = src[src.index('if [ "${worker_compatible:-0}" != "1" ] \\\n      && [ "${existing_worker_idle:-0}" = "1" ]'):]
    assert compat.index("&& codex_restart_freeze_ok; then") < compat.index('kill "$existing_worker_pid"')
    fallback = src[src.index("# Never start a second worker beside the unit's own"):]
    assert fallback.index("! worker_stop_safe") < fallback.index("restart_worker_via_systemd")
    roll = src[src.index("worker_kicked=0"):]
    assert roll.index("if ! codex_restart_freeze_ok; then") < roll.index("restart_worker_via_systemd")
    install = src[src.index("install_service_linux() {"):]
    assert install.index("! worker_stop_safe") < install.index('systemctl --user stop "$SYSTEMD_UNIT_NAME" "$WORKER_SYSTEMD_UNIT_NAME"')
