"""CCC-250: a worker restart must not abort live Codex app-server turns.

The freeze counts in-flight turns and sets itself in one critical section
under the app-server condition; the send path checks it under that same
condition, so no CCC-issued turn can start between the count and the kill.
"""

import threading
import time

import pytest
import server
from ccc_server import codex_restart_gate as gate


class _Transport:
    kind = "stdio"
    proc = None

    def __init__(self):
        self.sent = []

    def alive(self):
        return True

    def send_json(self, payload):
        self.sent.append(payload)


@pytest.fixture
def app_server(monkeypatch):
    transport = _Transport()
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_TRANSPORT", transport)
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_THREAD_STATE", {})
    monkeypatch.setattr(gate, "_frozen_until", 0.0)
    monkeypatch.setattr(gate, "_frozen_reason", "")
    yield transport
    gate.release()


def test_acquire_refuses_while_a_turn_runs(app_server):
    server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {
        "active_turn_id": "u1", "last_activity_at": time.time(),
    }
    out = gate.acquire("test")
    assert out == {"ok": True, "frozen": False, "live_turns": 1, "threads": ["t1"]}
    assert not gate.frozen()


def test_admitted_but_unanswered_turn_start_counts(app_server):
    server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {
        "ccc_turn_start_pending": True, "ccc_turn_start_pending_at": time.time(),
    }
    assert gate.acquire("test")["frozen"] is False


def test_freeze_blocks_new_turns_but_not_interrupts(app_server):
    assert gate.acquire("test")["frozen"] is True
    refused = server._codex_app_server_request_to_transport(
        app_server, "turn/start", {"threadId": "t1"}, timeout=0.1,
    )
    assert refused["code"] == "codex_restart_pending"
    assert refused["fallback"] == "queue"
    assert app_server.sent == []
    # Intentional cancellation still goes out while frozen.
    server._codex_app_server_request_to_transport(
        app_server, "turn/interrupt", {"threadId": "t1", "turnId": "u1"}, timeout=0.05,
    )
    assert [m["method"] for m in app_server.sent] == ["turn/interrupt"]
    # Read-only calls are untouched.
    server._codex_app_server_request_to_transport(
        app_server, "thread/list", {}, timeout=0.05,
    )
    assert app_server.sent[-1]["method"] == "thread/list"


def test_release_and_ttl_lift_the_freeze(app_server):
    gate.acquire("test")
    assert gate.release()["released"] is True
    assert not gate.frozen()
    gate.acquire("test", ttl_s=5)
    assert gate.frozen()
    assert not gate.frozen(now=time.time() + 6)


def test_acquire_is_atomic_with_send_admission(app_server):
    """A turn/start that won the lock first is counted; one that lost is
    refused. There is no interleaving where both the freeze and the send
    succeed."""
    for _ in range(50):
        gate.release()
        server._CODEX_APP_SERVER_THREAD_STATE.clear()
        app_server.sent.clear()
        results = {}

        def start():
            # Mirror _codex_app_server_request: mark pending under the lock,
            # then send through the gated transport path.
            with server._CODEX_APP_SERVER_LOCK:
                state = server._CODEX_APP_SERVER_THREAD_STATE.setdefault("t1", {})
                if not gate.blocks("turn/start"):
                    state["ccc_turn_start_pending"] = True
                    state["ccc_turn_start_pending_at"] = time.time()
            results["send"] = server._codex_app_server_request_to_transport(
                app_server, "turn/start", {"threadId": "t1"}, timeout=0.01,
            )

        def freeze():
            results["freeze"] = gate.acquire("race")

        threads = [threading.Thread(target=start), threading.Thread(target=freeze)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        sent = any(m.get("method") == "turn/start" for m in app_server.sent)
        assert not (sent and results["freeze"]["frozen"])


def test_no_server_module_freezes_with_zero_turns(monkeypatch):
    monkeypatch.setattr(gate, "_app_server_lock", lambda: None)
    monkeypatch.setattr(gate, "_frozen_until", 0.0)
    out = gate.acquire("cold worker")
    assert out["frozen"] is True and out["live_turns"] == 0
    gate.release()


def test_worker_rpc_round_trip(app_server, tmp_path):
    from ccc_worker import WorkerRuntime
    from control_plane import WorkLedger

    runtime = WorkerRuntime(ledger=WorkLedger(tmp_path / "l.sqlite3"), token="d" * 64)
    server._CODEX_APP_SERVER_THREAD_STATE["t1"] = {
        "active_turn_id": "u1", "last_activity_at": time.time(),
    }
    busy = runtime.dispatch("codex.restart_freeze", {"enabled": True})
    assert busy["frozen"] is False and busy["live_turns"] == 1
    assert runtime.dispatch("health", {})["codex_live_turns"] == 1
    server._CODEX_APP_SERVER_THREAD_STATE.clear()
    assert runtime.dispatch("codex.restart_freeze", {"enabled": True})["frozen"] is True
    assert runtime.dispatch("codex.restart_freeze", {"enabled": False})["released"] is True
    assert "codex-turn-gate-v1" in runtime.dispatch("health", {})["worker"]["capabilities"]
