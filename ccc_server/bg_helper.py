"""Background-helper process: run the dashboard's heaviest warmers off its GIL.

CCC-142: after the list work itself became cached, slow /list reads were
waiting for the dashboard's GIL (cpu/wall ~0.18) while three background
loops ran in the same process: gh-queue-watch (~12% of a core), ship-graph-
warm (~10%) and uxq-health-refresh (~8.5%). The owner chose to move them to
a separate process that produces snapshots the dashboard only reads.

Same shape as ccc_server.conv_reader: the dashboard spawns one child
(``python -m ccc_server.bg_helper``) that imports server.py and runs the
same code on its own GIL; a supervisor thread respawns it with backoff, and
the child exits when its parent goes away.

* gh-queue-watch: the dashboard's watch loop becomes a 1s proxy tick
  (``gh_tick``). The child runs the real 5s poll loop while ticks keep
  arriving, and returns its change version plus the board memo only when
  the memo changed.
* uxq-health-refresh: ``ux_health`` returns the child's payload (rebuilt
  when older than the TTL, coalesced); the dashboard only parses it.
* ship-graph-warm: ``ship_sync`` starts the full index sync in the child.
  The index is on-disk SQLite, so dashboard reads see the result.

Fallback: ``CCC_BG_HELPER=0`` disables the child. Any connect, timeout or
protocol error runs the old in-process path, so behaviour degrades to what
it was before, never to stale data.

Wire protocol (one request per connection): one JSON line ``{"op", ...}``
in, one JSON line out.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    from ccc_server.paths import COMMAND_CENTER_STATE_DIR
except Exception:  # pragma: no cover - import-path fallback
    COMMAND_CENTER_STATE_DIR = Path.home() / ".claude" / "command-center"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONNECT_TIMEOUT_S = 0.5
# A cold ux-health build (WatchTower list + worker logs) can take seconds.
READ_TIMEOUT_S = float(os.environ.get("CCC_BG_HELPER_TIMEOUT_S", "60") or 60)
READY_TIMEOUT_S = 60.0
MAX_CONSECUTIVE_ERRORS = 3
# The child keeps the GitHub watch loop alive this long after the last tick.
GH_TICK_GRACE_S = 15.0
CHILD_ENV = "CCC_BG_HELPER_CHILD"

_lock = threading.Lock()
_state = {"sock": None, "proc": None, "ready": False, "errors": 0,
          "started_at": 0.0}
_stats = {"calls": 0, "errors": 0}


def enabled():
    return (os.environ.get("CCC_BG_HELPER", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def is_child():
    return os.environ.get(CHILD_ENV) == "1"


def socket_path(parent_pid=None):
    return COMMAND_CENTER_STATE_DIR / f"bg-helper-{parent_pid or os.getpid()}.sock"


# ---------------------------------------------------------------- dashboard


def start():
    """Spawn and supervise the helper child. Called once from server.main()."""
    if not enabled() or is_child() or os.environ.get("CCC_CONV_READER_CHILD") == "1":
        return None
    with _lock:
        _state["started_at"] = time.monotonic()
    thread = threading.Thread(target=_supervise, name="bg-helper-supervisor",
                              daemon=True)
    thread.start()
    return thread


def ready():
    with _lock:
        return bool(_state["ready"])


def starting():
    """True while a supervised child is expected but not ready yet (boot)."""
    with _lock:
        return (not _state["ready"] and _state["started_at"] > 0
                and time.monotonic() - _state["started_at"] < READY_TIMEOUT_S)


def _spawn(path):
    env = dict(os.environ)
    env[CHILD_ENV] = "1"
    try:
        path.unlink()
    except OSError:
        pass
    return subprocess.Popen(
        [sys.executable, "-m", "ccc_server.bg_helper",
         "--sock", str(path), "--parent", str(os.getpid())],
        cwd=str(PROJECT_ROOT), env=env, stdin=subprocess.DEVNULL,
    )


def _supervise():
    path = socket_path()
    backoff = 2.0
    while True:
        started = time.monotonic()
        try:
            proc = _spawn(path)
        except Exception as exc:
            print(f"  [bg-helper] spawn failed: {exc}", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        with _lock:
            _state.update(sock=str(path), proc=proc, ready=False, errors=0)
        deadline = time.monotonic() + READY_TIMEOUT_S
        while proc.poll() is None and time.monotonic() < deadline:
            if path.exists():
                with _lock:
                    _state["ready"] = True
                print(f"  [bg-helper] pid {proc.pid} ready on {path}", flush=True)
                break
            time.sleep(0.1)
        if proc.poll() is None and not path.exists():
            proc.kill()
        proc.wait()
        with _lock:
            _state.update(ready=False, proc=None)
        print(f"  [bg-helper] pid {proc.pid} exited rc={proc.returncode}",
              flush=True)
        if time.monotonic() - started > 300:
            backoff = 2.0
        time.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


def _note_error():
    with _lock:
        _stats["errors"] += 1
        _state["errors"] += 1
        proc = _state.get("proc")
        if _state["errors"] >= MAX_CONSECUTIVE_ERRORS and proc is not None:
            _state["ready"] = False
            try:
                proc.kill()
            except Exception:
                pass


def call(op, timeout=None, **fields):
    """The child's JSON reply to `op`, or None (caller runs in-process)."""
    with _lock:
        path = _state["sock"] if _state["ready"] else None
    if not path:
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(CONNECT_TIMEOUT_S)
        sock.connect(path)
        sock.settimeout(timeout or READ_TIMEOUT_S)
        sock.sendall(json.dumps(dict(fields, op=op)).encode() + b"\n")
        line = sock.makefile("rb").readline()
        reply = json.loads(line or b"{}")
        if not isinstance(reply, dict) or not reply.get("ok"):
            raise ConnectionError(f"bg helper declined {op}")
    except Exception:
        _note_error()
        return None
    finally:
        sock.close()
    with _lock:
        _stats["calls"] += 1
        _state["errors"] = 0
    return reply


def status():
    with _lock:
        proc = _state.get("proc")
        return {"enabled": enabled(), "ready": bool(_state["ready"]),
                "pid": proc.pid if proc is not None else None, **dict(_stats)}


# -------------------------------------------------------------------- child


class _Child:
    def __init__(self, server_mod):
        self.server = server_mod
        self.lock = threading.Lock()
        self.gh_last_tick = 0.0
        self.gh_entered = False
        self.board_key = None
        self.board_rev = 0
        self.ux_lock = threading.Lock()
        self.ux = {"ts": 0.0, "body": None, "rev": 0}

    # gh-queue-watch ------------------------------------------------------
    def gh_tick(self, req):
        q = self.server
        with self.lock:
            self.gh_last_tick = time.monotonic()
            if not self.gh_entered:
                self.gh_entered = True
                q.gh_queue_watch_enter()
        with q._ux_fixes_list_cache_lock:
            entry = q._ux_fixes_list_cache.get(("", ""))
            key = (entry.get("all_sig"), id(entry.get("items"))) if entry else None
            items = entry.get("items") if entry else None
            entry_ts = entry.get("ts") if entry else None
        with self.lock:
            if key is not None and key != self.board_key:
                self.board_key = key
                self.board_rev += 1
            board_rev = self.board_rev
        reply = {"ok": True, "version": q.gh_queue_version(),
                 "board_rev": board_rev, "board_ts": entry_ts}
        have = req.get("have_board_rev")
        if items is not None and have != "skip" and have != board_rev:
            reply["items"] = items
        return reply

    def gh_reaper(self):
        while True:
            time.sleep(2.0)
            with self.lock:
                idle = (self.gh_entered
                        and time.monotonic() - self.gh_last_tick > GH_TICK_GRACE_S)
                if idle:
                    self.gh_entered = False
            if idle:
                self.server.gh_queue_watch_exit()

    # uxq-health-refresh --------------------------------------------------
    def ux_health(self, req):
        ttl = float(getattr(self.server, "_UX_FIXES_HEALTH_TTL", 15.0))
        with self.ux_lock:
            fresh = (self.ux["body"] is not None
                     and time.time() - self.ux["ts"] < ttl)
            if req.get("force") or not fresh:
                data = self.server._build_ux_fixes_health_payload_uncached()
                body = json.dumps(data, default=str)
                if body != self.ux["body"]:
                    self.ux["rev"] += 1
                self.ux.update(ts=time.time(), body=body)
            body, rev, ts = self.ux["body"], self.ux["rev"], self.ux["ts"]
        reply = {"ok": True, "rev": rev, "ts": ts}
        if int(req.get("have_rev") or -1) != rev:
            reply["data"] = json.loads(body)
        return reply

    # ship-graph-warm -----------------------------------------------------
    def ship_sync(self, req):
        from ccc_server import ship_graph
        ship_graph._start_background_sync()
        return {"ok": True, "indexing": bool(ship_graph.is_indexing())}

    def handle(self, conn):
        try:
            conn.settimeout(READ_TIMEOUT_S)
            req = json.loads(conn.makefile("rb").readline() or b"{}")
            op = str(req.get("op") or "")
            fn = {"gh_tick": self.gh_tick, "ux_health": self.ux_health,
                  "ship_sync": self.ship_sync,
                  "ping": lambda r: {"ok": True}}.get(op)
            try:
                reply = fn(req) if fn else {"ok": False, "error": "unknown op"}
            except Exception as exc:
                print(f"  [bg-helper] {op}: {exc!r}", flush=True)
                reply = {"ok": False, "error": str(exc)}
            conn.sendall(json.dumps(reply, default=str).encode() + b"\n")
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def _socket_inode(path):
    """Identity of the file at `path` (inode + ctime: inode numbers recycle)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_ctime_ns)


def _watch_parent(parent_pid, path, inode=None):
    """Exit when the parent dies, or when our socket path no longer is ours.

    The dashboard restarts in place (exec keeps its pid), so getppid() alone
    never notices: the new dashboard spawns a fresh child on the same path
    and this one would linger forever, a full server.py import per restart.
    """
    while True:
        if os.getppid() != parent_pid:
            try:
                path.unlink()
            except OSError:
                pass
            os._exit(0)
        if inode is not None and _socket_inode(path) != inode:
            os._exit(0)
        time.sleep(1.0)


def serve(path, parent_pid):
    os.umask(0o077)
    path = Path(path)
    sys.path.insert(0, str(PROJECT_ROOT))
    import server as server_mod  # noqa: E402  (heavy; child only)
    child = _Child(server_mod)
    threading.Thread(target=child.gh_reaper, name="bg-helper-gh-reaper",
                     daemon=True).start()
    try:
        path.unlink()
    except OSError:
        pass
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.unlink()
    except OSError:
        pass
    listener.bind(str(tmp))
    listener.listen(64)
    os.replace(tmp, path)
    threading.Thread(target=_watch_parent,
                     args=(parent_pid, path, _socket_inode(path)),
                     name="bg-helper-parent-watch", daemon=True).start()
    # The dashboard defers its boot-time ship-graph warm to this process.
    try:
        child.ship_sync({})
    except Exception:
        pass
    while True:
        conn, _ = listener.accept()
        threading.Thread(target=child.handle, args=(conn,),
                         name="bg-helper-req", daemon=True).start()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[argv.index("--sock") + 1]
    parent = int(argv[argv.index("--parent") + 1])
    serve(path, parent)


if __name__ == "__main__":
    main()
