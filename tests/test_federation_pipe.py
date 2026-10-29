"""Persistent federation pipe (multi-machine S5).

The remote pipe server is the exact script that ships over ssh, run locally
with sys.executable against a stub loopback CCC. Only the ssh hop itself is
out of scope: the popen factory runs the remote command locally.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import federation
from ccc_server import federation_pipe as fp


class _Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.seen.append((self.path, body, self.headers.get("X-CCC-Peer-Token")))
        args = body.get("args") or {}
        if self.headers.get("X-CCC-Peer-Token") != "good-secret":
            code, out = 403, {"error": "unpaired_peer"}
        elif args.get("big"):
            code, out = 200, {"ok": True, "result": {"blob": "x" * (fp.MAX_FRAME_BYTES + 10)}}
        else:
            time.sleep(float(args.get("sleep") or 0))
            code, out = 200, {"ok": True, "result": {"ok": True, "echo": args, "hops": body.get("hops")}}
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def stub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _reset():
    fp.reset_state()
    yield
    fp.reset_state()


_ISOLATED_HOME = tempfile.mkdtemp(prefix="ccc-fed-pipe-test-")


def local_popen(remote_args, **kw):
    # remote_args == ["python3", "-u", "-c", script]; run it here instead,
    # with a HOME that has no port.txt, so nothing reaches a real CCC.
    env = {**os.environ, "HOME": _ISOLATED_HOME}
    return subprocess.Popen([sys.executable, *remote_args[1:]], env=env, **kw)


def make_peer(stub, node="node-a", secret="good-secret", **transport):
    t = {"type": "ssh", "host": "user@peer.example", "port": stub.server_address[1]}
    t.update(transport)
    return {"node_id": node, "name": node, "secret": secret, "transport": t}


def started_pipe(popen=local_popen):
    pipe = fp.PeerPipe(popen, name="t")
    assert pipe.start(timeout=10)
    return pipe


def env(stub, args, path="/api/federation/v1/route", token="good-secret"):
    return {"method": "POST", "path": path,
            "headers": {"X-CCC-Peer": "me", "X-CCC-Peer-Token": token},
            "body": {"action": "memory_recall", "args": args, "hops": 1},
            "port": stub.server_address[1]}


def test_round_trip_and_forced_local_hops(stub):
    pipe = started_pipe()
    out = pipe.request(env(stub, {"q": "hello"}), timeout=5)
    assert out["status"] == 200
    assert out["body"]["result"]["echo"] == {"q": "hello"}
    # The envelope passes through untouched: hops=1, so the peer answers
    # local-only and can never fan out again.
    assert out["body"]["result"]["hops"] == 1
    pipe.close()


def test_concurrent_out_of_order_responses_correlate(stub):
    pipe = started_pipe()
    results = {}

    def go(i, delay):
        results[i] = pipe.request(env(stub, {"i": i, "sleep": delay}), timeout=10)

    threads = [threading.Thread(target=go, args=(i, d)) for i, d in enumerate([0.6, 0.3, 0.0])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for i in range(3):
        assert results[i]["body"]["result"]["echo"]["i"] == i
    pipe.close()


_LATE = r"""
import json, sys, time
print('{"ready": true}', flush=True)
first = json.loads(sys.stdin.readline())
time.sleep(0.8)
print(json.dumps({"id": first["id"], "status": 200, "body": {"n": 1}}), flush=True)
second = json.loads(sys.stdin.readline())
print(json.dumps({"id": 999, "status": 200, "body": {}}), flush=True)  # unknown id
print(json.dumps({"id": second["id"], "status": 200, "body": {"n": 2}}), flush=True)
sys.stdin.readline()
"""


def test_timeout_drops_late_and_unknown_answers_and_pipe_stays_usable():
    def popen(_remote_args, **kw):
        return subprocess.Popen([sys.executable, "-c", _LATE], **kw)

    pipe = started_pipe(popen)
    req = {"method": "POST", "path": "/api/federation/v1/route", "body": {}}
    with pytest.raises(TimeoutError):
        pipe.request(req, timeout=0.2)
    assert pipe._pending == {}  # orphan cleaned up at the deadline
    time.sleep(1.0)
    assert pipe.request(req, timeout=5)["body"] == {"n": 2}
    assert pipe.dropped == 2  # the late answer and the unknown id
    pipe.close()


def test_remote_http_timeout_cancels_on_peer(stub):
    pipe = started_pipe()
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):  # local wait or the peer's own timeout
        pipe.request(env(stub, {"sleep": 2.0}), timeout=0.3)
    assert time.monotonic() - t0 < 1.0
    time.sleep(0.4)
    assert pipe.alive() and len(stub.seen) == 1  # no retry after a timeout
    pipe.close()


def test_remote_refuses_non_federation_paths(stub):
    pipe = started_pipe()
    out = pipe.request(env(stub, {}, path="/api/sessions"), timeout=5)
    assert out["status"] == 403 and out["body"]["error"] == "path_not_allowed"
    assert stub.seen == []
    pipe.close()


def test_oversize_response_is_capped_not_streamed(stub):
    pipe = started_pipe()
    out = pipe.request(env(stub, {"big": True}), timeout=10)
    assert out["status"] == 502 and out["body"]["error"] == "response_too_large"
    assert pipe.alive()
    pipe.close()


def test_inflight_cap(stub):
    pipe = started_pipe()
    threads = [threading.Thread(target=pipe.request, args=(env(stub, {"sleep": 0.8}), 5))
               for _ in range(fp.MAX_INFLIGHT)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    with pytest.raises(fp.PipeUnavailable):
        pipe.request(env(stub, {}), timeout=5)
    for t in threads:
        t.join()
    pipe.close()


_GARBAGE = r"""
import sys
print('{"ready": true}', flush=True)
sys.stdin.readline()
print("not json at all", flush=True)
sys.stdin.readline()
"""

_OVERSIZE = r"""
import sys
print('{"ready": true}', flush=True)
sys.stdin.readline()
sys.stdout.write("x" * (%d + 5) + "\n"); sys.stdout.flush()
sys.stdin.readline()
""" % fp.MAX_FRAME_BYTES


@pytest.mark.parametrize("script", [_GARBAGE, _OVERSIZE])
def test_malformed_or_oversize_frame_closes_pipe(script):
    def popen(_remote_args, **kw):
        return subprocess.Popen([sys.executable, "-c", script], **kw)

    pipe = started_pipe(popen)
    with pytest.raises(fp.PipeUnavailable):
        pipe.request({"method": "POST", "path": "/api/federation/v1/route", "body": {}}, timeout=5)
    assert not pipe.alive()


def test_remote_exit_fails_waiters_fast():
    def popen(_remote_args, **kw):
        return subprocess.Popen([sys.executable, "-c",
                                 "import sys; print('{\"ready\": true}', flush=True); sys.stdin.readline()"],
                                **kw)

    pipe = started_pipe(popen)
    t0 = time.monotonic()
    with pytest.raises(fp.PipeUnavailable):
        pipe.request({"method": "POST", "path": "/api/federation/v1/route", "body": {}}, timeout=5)
    assert time.monotonic() - t0 < 3
    assert not pipe.alive()


def _wait(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_get_live_never_blocks_and_coalesces_starts(stub):
    peer = make_peer(stub)
    starts = []

    def factory(_peer):
        starts.append(1)
        def popen(remote_args, **kw):
            time.sleep(0.3)  # a slow ssh connect
            return local_popen(remote_args, **kw)
        return popen

    t0 = time.monotonic()
    assert all(fp.get_live(peer, factory) is None for _ in range(10))
    assert time.monotonic() - t0 < 0.2
    assert _wait(lambda: fp.get_live(peer, factory) is not None)
    assert len(starts) == 1


def test_failed_start_backs_off():
    peer = {"node_id": "n", "transport": {"type": "ssh", "host": "h"}}
    calls = []

    def factory(_peer):
        calls.append(1)
        raise OSError("ssh: connect timed out")

    assert fp.schedule_start(peer, factory)
    assert _wait(lambda: "n" in fp._BACKOFF)
    assert fp._BACKOFF["n"]["backoff"] == fp._BACKOFF_BASE_S
    assert not fp.schedule_start(peer, factory)  # still backing off
    assert fp.schedule_start(peer, factory, now=time.time() + fp._BACKOFF_BASE_S + 1)
    assert _wait(lambda: fp._BACKOFF["n"]["backoff"] == 2 * fp._BACKOFF_BASE_S)
    assert len(calls) == 2


def test_unsupported_peers_get_no_pipe():
    for t in ({"type": "loopback", "port": 1}, {"type": "unconfigured"}, {}):
        assert fp.get_live({"node_id": "x", "transport": t}, lambda p: pytest.fail("spawned")) is None
    assert fp._STARTING == set()


def test_refresh_closes_removed_and_changed_peers_and_skips_breaker(stub):
    a, b = make_peer(stub, "a"), make_peer(stub, "b")
    fp.refresh([a, b], lambda p: local_popen)
    assert _wait(lambda: fp.get_live(a) and fp.get_live(b))
    pipe_a, pipe_b = fp.get_live(a), fp.get_live(b)
    b2 = make_peer(stub, "b", ssh_port=2222)  # transport changed
    fp.refresh([b2], lambda p: local_popen, skip=lambda node: True)
    assert not pipe_a.alive() and not pipe_b.alive()
    assert fp._PIPES == {} and fp._STARTING == set()  # skipped: breaker open


def test_shutdown_closes_owned_processes(stub):
    peer = make_peer(stub)
    fp.schedule_start(peer, lambda p: local_popen)
    assert _wait(lambda: fp.get_live(peer) is not None)
    proc = fp.get_live(peer)._proc
    fp.shutdown()
    assert proc.wait(timeout=5) is not None
    assert not fp.schedule_start(peer, lambda p: local_popen)  # stopped


def test_peer_client_uses_pipe_and_maps_auth_denial(stub, monkeypatch):
    good, bad = make_peer(stub, "g"), make_peer(stub, "b", secret="wrong")
    for p in (good, bad):
        fp.schedule_start(p, lambda _p: local_popen)
    assert _wait(lambda: fp.get_live(good) and fp.get_live(bad))
    monkeypatch.setattr(federation.PeerClient, "_request_ssh",
                        lambda *a, **k: pytest.fail("fell back to per-call ssh"))
    envelope = federation.make_route_envelope("memory_recall", {"q": "x"}, hops=1)
    out = federation.PeerClient(good, self_node_id="me").request(
        "POST", "/api/federation/v1/route", envelope, timeout=5, pipe=True)
    assert out["result"]["echo"] == {"q": "x"}
    with pytest.raises(federation.PeerError) as e:
        federation.PeerClient(bad, self_node_id="me").request(
            "POST", "/api/federation/v1/route", envelope, timeout=5, pipe=True)
    assert e.value.kind == "unpaired_peer"


def test_peer_client_falls_back_without_live_pipe(stub, monkeypatch):
    peer = make_peer(stub)
    used = []
    monkeypatch.setattr(federation.PeerClient, "_request_ssh",
                        lambda self, *a: used.append(1) or {"status": 200, "body": {"ok": True}})
    monkeypatch.setattr(fp, "_popen_for", lambda p: local_popen)
    out = federation.PeerClient(peer, self_node_id="me").request(
        "POST", "/api/federation/v1/route", {}, timeout=5, pipe=True)
    assert out == {"ok": True} and used == [1]
    # Without pipe=True (handoff and other mutating calls) no pipe is touched.
    fp.reset_state()
    federation.PeerClient(peer, self_node_id="me").request("POST", "/api/federation/v1/route", {}, timeout=5)
    assert fp._STARTING == set() and fp._PIPES == {}


def test_refused_pinned_port_retries_via_port_txt(stub, tmp_path):
    (tmp_path / ".claude" / "command-center").mkdir(parents=True)
    (tmp_path / ".claude" / "command-center" / "port.txt").write_text(
        "http://127.0.0.1:%d\n" % stub.server_address[1])

    def popen(remote_args, **kw):
        return subprocess.Popen([sys.executable, *remote_args[1:]],
                                env={**os.environ, "HOME": str(tmp_path)}, **kw)

    pipe = started_pipe(popen)
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()
    out = pipe.request({**env(stub, {"q": "r"}), "port": dead_port}, timeout=5)
    assert out["status"] == 200 and out["body"]["result"]["echo"] == {"q": "r"}
    pipe.close()
