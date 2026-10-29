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
    codex_mod._CODEX_APP_SERVER_MUTATING_PENDING.clear()
    yield transport
    codex_mod._CODEX_APP_SERVER_MUTATING_PENDING.clear()
    gate.release()


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
        gate.release()
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


def test_concurrent_release_during_proof_is_not_proof(native):
    native.on_send["thread/loaded/list"] = lambda _p: gate.release()
    assert gate.acquire("test")["frozen"] is False


def test_lapsed_freeze_is_reproved_not_trusted(native):
    assert gate.acquire("test", ttl_s=5)["frozen"] is True
    assert not gate.frozen(now=time.time() + 6)
    # While lapsed a client loaded a thread; the re-run proof catches it.
    gate.release()
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
    out = gate.acquire("cold worker")
    assert out["frozen"] is True and out["live_turns"] == 0
    gate.release()


# --- Process ownership / dead transport ------------------------------------

def test_real_child_transport_and_dead_transport_proof(native, monkeypatch):
    child = _fake_child()
    try:
        transport = _NativeAppServer(proc=child)
        monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", transport)
        # Our own tracked child is not a stray.
        assert gate.acquire("test")["frozen"] is True
        gate.release()
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
    assert runtime.dispatch("codex.restart_freeze", {"enabled": True})["frozen"] is True
    assert runtime.dispatch("codex.restart_freeze", {"enabled": False})["released"] is True
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
        fh.write(flag + "\\n")
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
    calls = (tmp_path / "calls.log").read_text().split() if (tmp_path / "calls.log").exists() else []
    return proc.returncode, calls


_IDLE = {"ok": True, "active": 0, "queued": 0, "uncertain": 0, "codex_live_turns": 0}


def test_run_sh_freeze_ok_needs_freeze_and_zero_turns(tmp_path):
    frozen = {"ok": True, "frozen": True}
    assert _run_helper(tmp_path, {"--codex-restart-freeze": frozen, "--health": _IDLE},
                       "codex_restart_freeze_ok")[0] == 0
    for scenario in (
        {"--health": _IDLE},                                      # old worker: no RPC
        {"--codex-restart-freeze": {"ok": True, "frozen": False}, "--health": _IDLE},
        {"--codex-restart-freeze": frozen,
         "--health": {k: v for k, v in _IDLE.items() if k != "codex_live_turns"}},
        {"--codex-restart-freeze": frozen},                       # health unreachable
    ):
        (tmp_path / "calls.log").unlink(missing_ok=True)
        rc, calls = _run_helper(tmp_path, dict(scenario, **{"--codex-restart-release": {"ok": True}}),
                                "codex_restart_freeze_ok")
        assert rc != 0, scenario


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
