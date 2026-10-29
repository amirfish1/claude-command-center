# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Persistent SSH pipe for federated memory reads (multi-machine S5).

A per-call SSH peer request pays ~90 ms for the ssh session and ~270 ms to
start python3 + urllib on the peer, on top of the peer's own handler time.
Measured 2026-10-29, memory_recall, 20 samples per run, p50: Mac ->
hermes-gcp cold (no mux) 1168-2256 ms, warm mux 505-1234 ms, this pipe
284-328 ms; hermes-gcp -> Mac cold 1086 ms, warm 263-561 ms, pipe 162 ms.
The S5 gate was a warm p50 above 800 ms, so this module keeps one
long-lived `ssh <peer> python3 -u -c <server>` process per peer and sends
memory route requests over it as NDJSON.

  - One pipe per paired ssh peer, keyed by node id + transport. It rides
    the peer's existing ControlMaster socket, host key, key file and
    BatchMode settings (ssh_multiplexer), and the request carries the same
    X-CCC-Peer / X-CCC-Peer-Token headers to the same authenticated
    /api/federation/v1/* route on the peer's loopback. The remote side
    refuses any other path.
  - Frames are one JSON object per line, capped at MAX_FRAME_BYTES each
    way. Each request has an id; responses can arrive in any order and are
    matched by id. A late response for a timed-out request is dropped.
    A malformed or oversize frame means the stream is out of sync: the
    pipe closes and every waiting request falls back to the per-call path.
  - At most MAX_INFLIGHT requests per pipe; more fall back.
  - The request thread never waits for a pipe to start. With no live pipe
    it schedules one start in the background (coalesced per peer, with a
    60 s -> 10 min backoff after a failed start) and uses the per-call
    path for this request.
  - prewarm loop (dashboard only, started from server.main): every
    REFRESH_S it starts missing pipes for paired ssh peers whose memory
    breaker is closed, and closes pipes for peers that were removed or
    changed transport. An open pipe is an ssh client of the mux, so the
    ControlMaster never idles out under ControlPersist=1h.
  - Only memory reads use it (PeerClient.request(pipe=True)). They are
    read-only, so falling back after a send can't duplicate a mutation.
"""

from __future__ import annotations

import atexit
import itertools
import json
import subprocess
import threading
import time

MAX_FRAME_BYTES = 1024 * 1024
MAX_INFLIGHT = 4
START_TIMEOUT_S = 15.0
REFRESH_S = 300.0
_BACKOFF_BASE_S = 60.0
_BACKOFF_MAX_S = 600.0


class PipeUnavailable(Exception):
    """The pipe can't carry this request; use the per-call ssh path."""


# Runs ON the peer: `python3 -u -c <this>`. Reads one JSON request per line
# on stdin, answers one JSON line per request on stdout, in any order.
_REMOTE_PIPE_SERVER = r"""
import http.client, json, os, re, socket, sys, threading
MAX = %(max_frame)d
lock = threading.Lock()
slots = threading.BoundedSemaphore(%(max_inflight)d)

def emit(obj):
    line = json.dumps(obj)
    if len(line.encode()) > MAX:
        line = json.dumps({"id": obj.get("id"), "status": 502,
                           "body": {"error": "response_too_large"}})
    with lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

def discover_port():
    pf = os.path.expanduser("~/.claude/command-center/port.txt")
    try:
        raw = open(pf).read().strip().splitlines()[0].strip()
    except Exception:
        return None
    m = re.search(r"(\d+)\s*$", raw)
    return int(m.group(1)) if m else None

def call(env, port):
    if not port:
        return {"status": 0, "error": "no usable port.txt on peer"}
    data = None if env.get("body") is None else json.dumps(env["body"])
    headers = dict(env.get("headers") or {})
    headers["Content-Type"] = "application/json"
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port),
                                          timeout=float(env.get("timeout") or 30))
        conn.request(env.get("method") or "POST", env["path"], body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        conn.close()
    except ConnectionRefusedError as e:
        return {"status": 0, "error": str(e)[:300], "refused": True}
    except (TimeoutError, socket.timeout):
        return {"status": 0, "error": "peer CCC timed out", "timeout": True}
    except Exception as e:
        return {"status": 0, "error": str(e)[:300]}
    try:
        body = json.loads(raw)
    except Exception:
        body = raw
    return {"status": resp.status, "body": body}

def handle(env):
    try:
        out = call(env, env.get("port") or discover_port())
        if out.get("refused") and env.get("port"):
            # Pinned port is stale after a peer restart: retry via port.txt.
            out = call(env, discover_port())
        out["id"] = env.get("id")
        emit(out)
    except Exception as e:
        emit({"id": env.get("id"), "status": 0, "error": str(e)[:300]})
    finally:
        slots.release()

emit({"ready": True, "proto": 1})
while True:
    line = sys.stdin.buffer.readline(MAX + 1)
    if not line:
        break
    if len(line) > MAX:
        break
    try:
        env = json.loads(line)
    except Exception:
        break
    if not isinstance(env, dict):
        break
    if env.get("ping"):
        emit({"id": env.get("id"), "pong": True})
        continue
    if not str(env.get("path") or "").startswith("/api/federation/v1/"):
        emit({"id": env.get("id"), "status": 403, "body": {"error": "path_not_allowed"}})
        continue
    slots.acquire()
    threading.Thread(target=handle, args=(env,), daemon=True).start()
""" % {"max_frame": MAX_FRAME_BYTES, "max_inflight": MAX_INFLIGHT}


class PeerPipe:
    """One live `ssh peer python3 -u -c <server>` process."""

    def __init__(self, popen_fn, name: str = ""):
        self._popen_fn = popen_fn  # (remote_args, **popen_kwargs) -> Popen
        self.name = name
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._ids = itertools.count(1)
        self._ready = threading.Event()
        self._closed = False
        self.dropped = 0  # responses with no waiter (late, or unknown id)

    # -- lifecycle -----------------------------------------------------------

    def start(self, timeout: float = START_TIMEOUT_S) -> bool:
        """Blocking. Only call from a background thread."""
        try:
            self._proc = self._popen_fn(
                ["python3", "-u", "-c", _REMOTE_PIPE_SERVER],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0)
        except Exception:
            self._closed = True
            return False
        threading.Thread(target=self._read_loop, daemon=True,
                         name=f"ccc-fed-pipe-{self.name}").start()
        if not self._ready.wait(timeout) or self._closed:
            self.close()
            return False
        return True

    def alive(self) -> bool:
        p = self._proc
        return (not self._closed and self._ready.is_set()
                and p is not None and p.poll() is None)

    def close(self) -> None:
        with self._lock:
            if self._closed and self._proc is None:
                return
            self._closed = True
            waiting = list(self._pending.values())
            self._pending.clear()
            proc, self._proc = self._proc, None
        for slot in waiting:
            slot["error"] = "pipe closed"
            slot["event"].set()
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # -- I/O -----------------------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        out = proc.stdout if proc else None
        try:
            while out is not None:
                line = out.readline(MAX_FRAME_BYTES + 1)
                if not line or len(line) > MAX_FRAME_BYTES:
                    break  # EOF, or a frame over the cap: stream is unusable
                try:
                    msg = json.loads(line)
                except ValueError:
                    break  # out of sync: never guess which request it was
                if not isinstance(msg, dict):
                    break
                if msg.get("ready"):
                    self._ready.set()
                    continue
                with self._lock:
                    slot = self._pending.pop(msg.get("id"), None)
                if slot is None:
                    self.dropped += 1
                    continue
                slot["response"] = msg
                slot["event"].set()
        except Exception:
            pass
        self.close()

    def request(self, envelope: dict, timeout: float) -> dict:
        """Send one {method,path,headers,body,port} request. Returns the
        transport result {status, body|error}. Raises PipeUnavailable when
        the pipe can't carry it, TimeoutError when no answer in time."""
        slot = {"event": threading.Event()}
        with self._lock:
            if not self.alive():
                raise PipeUnavailable("pipe not live")
            if len(self._pending) >= MAX_INFLIGHT:
                raise PipeUnavailable("pipe busy")
            rid = next(self._ids)
            self._pending[rid] = slot
        frame = json.dumps({**envelope, "id": rid, "timeout": timeout}).encode() + b"\n"
        if len(frame) > MAX_FRAME_BYTES:
            with self._lock:
                self._pending.pop(rid, None)
            raise PipeUnavailable("request over frame cap")
        try:
            with self._write_lock:
                self._proc.stdin.write(frame)
                self._proc.stdin.flush()
        except Exception:
            self.close()
            raise PipeUnavailable("pipe write failed")
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)  # a late answer is dropped
            raise TimeoutError(f"no pipe answer within {timeout:g}s")
        if "response" not in slot:
            raise PipeUnavailable(slot.get("error") or "pipe closed")
        resp = dict(slot["response"])
        resp.pop("id", None)
        if resp.get("timeout"):
            raise TimeoutError(f"peer CCC did not answer within {timeout:g}s")
        return resp


# -- per-peer manager ---------------------------------------------------------

_LOCK = threading.Lock()
_PIPES: dict[str, tuple[str, PeerPipe]] = {}  # node_id -> (fingerprint, pipe)
_STARTING: set[str] = set()
_BACKOFF: dict[str, dict] = {}
_STOP = threading.Event()
_PREWARM_STARTED = False


def _fingerprint(peer: dict) -> str:
    t = peer.get("transport") or {}
    return json.dumps({k: t.get(k) for k in ("type", "host", "user", "ssh_port", "key_path")},
                      sort_keys=True)


def supported(peer: dict) -> bool:
    return (peer.get("transport") or {}).get("type") == "ssh" and bool(peer.get("node_id"))


def _popen_for(peer: dict):
    import federation

    mux = federation.PeerClient(peer)._ssh_mux()
    return mux.popen


def _start(peer: dict, popen_factory) -> None:
    node = peer["node_id"]
    ok = False
    pipe = None
    try:
        if not _STOP.is_set():
            pipe = PeerPipe(popen_factory(peer), name=(peer.get("name") or node[:8]))
            ok = pipe.start()
    except Exception:
        ok = False
    finally:
        with _LOCK:
            _STARTING.discard(node)
            if ok and not _STOP.is_set():
                old = _PIPES.get(node)
                _PIPES[node] = (_fingerprint(peer), pipe)
                _BACKOFF.pop(node, None)
            else:
                old = None
                b = _BACKOFF.setdefault(node, {"backoff": 0.0, "until": 0.0})
                b["backoff"] = min(_BACKOFF_MAX_S, b["backoff"] * 2 or _BACKOFF_BASE_S)
                b["until"] = time.time() + b["backoff"]
        if old is not None:
            old[1].close()
        if pipe is not None and not ok:
            pipe.close()
        elif pipe is not None and _STOP.is_set():
            pipe.close()


def schedule_start(peer: dict, popen_factory=None, now: float | None = None) -> bool:
    """Start a pipe for `peer` in the background unless one is live, one is
    already starting, or a failed start is still backing off. Never blocks."""
    if not supported(peer) or _STOP.is_set():
        return False
    node = peer["node_id"]
    now = time.time() if now is None else now
    with _LOCK:
        cur = _PIPES.get(node)
        if cur and cur[0] == _fingerprint(peer) and cur[1].alive():
            return False
        if node in _STARTING:
            return False
        if _BACKOFF.get(node, {}).get("until", 0) > now:
            return False
        _STARTING.add(node)
    threading.Thread(target=_start, args=(peer, popen_factory or _popen_for),
                     daemon=True, name="ccc-fed-pipe-start").start()
    return True


def get_live(peer: dict, popen_factory=None) -> PeerPipe | None:
    """The live pipe for `peer`, or None (a start is scheduled then)."""
    if not supported(peer):
        return None
    with _LOCK:
        cur = _PIPES.get(peer["node_id"])
    if cur and cur[0] == _fingerprint(peer) and cur[1].alive():
        return cur[1]
    schedule_start(peer, popen_factory)
    return None


def refresh(peers: list[dict], popen_factory=None, skip=None) -> None:
    """One prewarm tick: close pipes for removed/changed peers, start the
    missing ones. `skip(node_id)` -> True leaves a peer alone (its memory
    breaker is open, e.g. a sleeping laptop)."""
    want = {p["node_id"]: p for p in peers if supported(p)}
    stale = []
    with _LOCK:
        for node, (fp, pipe) in list(_PIPES.items()):
            if node not in want or fp != _fingerprint(want[node]) or not pipe.alive():
                stale.append(_PIPES.pop(node)[1])
        for node in list(_BACKOFF):
            if node not in want:
                _BACKOFF.pop(node, None)
    for pipe in stale:
        pipe.close()
    for node, peer in want.items():
        if skip is not None and skip(node):
            continue
        schedule_start(peer, popen_factory)


def _memory_breaker_open(node: str) -> bool:
    try:
        from ccc_server import memory_fanout
        return memory_fanout._breaker_open(node, time.time()) is not None
    except Exception:
        return False


def _prewarm_loop() -> None:
    import federation

    while not _STOP.is_set():
        try:
            refresh(federation.load_peers(), skip=_memory_breaker_open)
        except Exception:
            pass
        _STOP.wait(REFRESH_S)


def start_prewarm() -> None:
    """Dashboard startup: warm every paired ssh peer's pipe in the
    background and keep it warm. Returns at once; idempotent."""
    global _PREWARM_STARTED
    with _LOCK:
        if _PREWARM_STARTED:
            return
        _PREWARM_STARTED = True
    threading.Thread(target=_prewarm_loop, daemon=True, name="ccc-fed-pipe-prewarm").start()


def shutdown() -> None:
    _STOP.set()
    with _LOCK:
        pipes = [p for _fp, p in _PIPES.values()]
        _PIPES.clear()
    for pipe in pipes:
        pipe.close()


def reset_state() -> None:
    """Tests: close everything and allow a fresh start."""
    global _PREWARM_STARTED
    shutdown()
    with _LOCK:
        _STARTING.clear()
        _BACKOFF.clear()
        _PREWARM_STARTED = False
    _STOP.clear()


atexit.register(shutdown)
