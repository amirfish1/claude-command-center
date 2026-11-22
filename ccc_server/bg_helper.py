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
  arriving, and returns its change version with a complete board or a patch
  against a known board revision when the memo changes.
* uxq-health-refresh: ``ux_health`` returns the child's payload (rebuilt
  when older than the TTL, coalesced); the dashboard only parses it.
* ship-graph-warm: ``ship_sync`` starts the full index sync in the child.
  The index is on-disk SQLite, so dashboard reads see the result.
* session-fts-warm: ``fts_sync`` does the same for the recall index
  (MEMORY-20); the dashboard re-reads vectors only when ``semb`` changed.
* daily throughput: ``throughput_turn_stats`` scans transcripts and returns
  the daily summary. ``throughput_ticket_counts`` aggregates the queues.
* productivity: ``productivity_snapshot`` builds the 16-week datasets here;
  the dashboard retains refresh state, publication and live sampler health.
* runaway detection: ``runaway_scan`` scans active transcripts and returns
  the flagged sessions; the dashboard applies its current leases.
* worker fingerprint: ``worker_closure`` walks imports for the diagnostic
  staleness comparison without occupying the dashboard's GIL.

Fallback: ``CCC_BG_HELPER=0`` disables the child. Any connect, timeout or
protocol error runs the old in-process path, so behaviour degrades to what
it was before, never to stale data.

Wire protocol (one request per connection): one JSON line ``{"op", ...}``
in, one JSON line out.
"""

from __future__ import annotations

import hashlib
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
_BOARD_MANIFEST_MAX_ITEMS = 32768
CHILD_ENV = "CCC_BG_HELPER_CHILD"

_lock = threading.Lock()
_state = {"sock": None, "proc": None, "ready": False, "errors": 0,
          "started_at": 0.0}
# CCC-319: the live-status build (liveness + activity) runs in its own child
# (role "live"), apart from the warmers above. Those warmers (full FTS and
# ship-graph index syncs) would compete for that child's GIL and recreate the
# stalls the move is meant to remove.
_live_state = {"sock": None, "proc": None, "ready": False, "errors": 0,
               "started_at": 0.0}
_STATES = {"main": _state, "live": _live_state}
_stats = {"calls": 0, "errors": 0}
# A live build that takes longer than this is treated as a helper failure: the
# dashboard builds in-process instead of waiting (and the child is recycled
# after repeated failures).
LIVE_CALL_TIMEOUT_S = float(os.environ.get("CCC_LIVE_HELPER_TIMEOUT_S", "20") or 20)


def enabled():
    return (os.environ.get("CCC_BG_HELPER", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def is_child():
    return os.environ.get(CHILD_ENV) == "1"


def socket_path(parent_pid=None, role="main"):
    tag = "" if role == "main" else f"-{role}"
    return COMMAND_CENTER_STATE_DIR / f"bg-helper{tag}-{parent_pid or os.getpid()}.sock"


# ---------------------------------------------------------------- dashboard


def start():
    """Spawn and supervise the helper child. Called once from server.main()."""
    if not enabled() or is_child() or os.environ.get("CCC_CONV_READER_CHILD") == "1":
        return None
    with _lock:
        _state["started_at"] = time.monotonic()
        _live_state["started_at"] = time.monotonic()
    if live_enabled():
        threading.Thread(target=_supervise, args=("live",),
                         name="bg-helper-live-supervisor", daemon=True).start()
    thread = threading.Thread(target=_supervise, name="bg-helper-supervisor",
                              daemon=True)
    thread.start()
    return thread


def live_enabled():
    return (os.environ.get("CCC_LIVE_HELPER", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def ready():
    with _lock:
        return bool(_state["ready"])


def starting():
    """True while a supervised child is expected but not ready yet (boot)."""
    with _lock:
        return (not _state["ready"] and _state["started_at"] > 0
                and time.monotonic() - _state["started_at"] < READY_TIMEOUT_S)


def _spawn(path, role="main"):
    env = dict(os.environ)
    env[CHILD_ENV] = "1"
    try:
        path.unlink()
    except OSError:
        pass
    return subprocess.Popen(
        [sys.executable, "-m", "ccc_server.bg_helper",
         "--sock", str(path), "--parent", str(os.getpid()), "--role", role],
        cwd=str(PROJECT_ROOT), env=env, stdin=subprocess.DEVNULL,
    )


def _supervise(role="main"):
    state = _STATES[role]
    path = socket_path(role=role)
    backoff = 2.0
    while True:
        started = time.monotonic()
        try:
            proc = _spawn(path, role)
        except Exception as exc:
            print(f"  [bg-helper:{role}] spawn failed: {exc}", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        with _lock:
            state.update(sock=str(path), proc=proc, ready=False, errors=0)
        deadline = time.monotonic() + READY_TIMEOUT_S
        while proc.poll() is None and time.monotonic() < deadline:
            if path.exists():
                with _lock:
                    state["ready"] = True
                print(f"  [bg-helper:{role}] pid {proc.pid} ready on {path}", flush=True)
                break
            time.sleep(0.1)
        if proc.poll() is None and not path.exists():
            proc.kill()
        proc.wait()
        with _lock:
            state.update(ready=False, proc=None)
        print(f"  [bg-helper:{role}] pid {proc.pid} exited rc={proc.returncode}",
              flush=True)
        if time.monotonic() - started > 300:
            backoff = 2.0
        time.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


def _note_error(state=None):
    state = _state if state is None else state
    with _lock:
        _stats["errors"] += 1
        state["errors"] += 1
        proc = state.get("proc")
        if state["errors"] >= MAX_CONSECUTIVE_ERRORS and proc is not None:
            state["ready"] = False
            try:
                proc.kill()
            except Exception:
                pass


def call(op, timeout=None, role="main", **fields):
    """The child's JSON reply to `op`, or None (caller runs in-process)."""
    state = _STATES[role]
    with _lock:
        path = state["sock"] if state["ready"] else None
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
        _note_error(state)
        return None
    finally:
        sock.close()
    with _lock:
        _stats["calls"] += 1
        state["errors"] = 0
    return reply


def status():
    with _lock:
        proc = _state.get("proc")
        live_proc = _live_state.get("proc")
        return {"enabled": enabled(), "ready": bool(_state["ready"]),
                "pid": proc.pid if proc is not None else None,
                "live_ready": bool(_live_state["ready"]),
                "live_pid": live_proc.pid if live_proc is not None else None,
                **dict(_stats)}


# -------------------------------------------------------------------- child


def _encode_board_snapshot(reply):
    """Encode the full gh_tick reply and keep the row bytes for its manifest."""
    header = {key: value for key, value in reply.items() if key != "items"}
    parts = [json.dumps(header, default=str).encode()[:-1], b', "items": [']
    rows = []
    for row in reply["items"]:
        raw = json.dumps(row, default=str).encode()
        rows.append(raw)
        parts.extend((raw, b", "))
    if rows:
        parts.pop()
    parts.append(b"]}\n")
    return b"".join(parts), rows


def _board_manifest(items, *, encoded_rows=None):
    """Guard exact JSON values and order without retaining another board."""
    if len(items) > _BOARD_MANIFEST_MAX_ITEMS:
        return None
    manifest = {}
    for index, row in enumerate(items):
        ref = row.get("ref") if isinstance(row, dict) else None
        if not isinstance(ref, str) or not ref or ref in manifest:
            return None
        raw = (json.dumps(row, default=str).encode()
               if encoded_rows is None else encoded_rows[index])
        manifest[ref] = (index, hashlib.sha256(raw).digest())
    return manifest


class _Child:
    def __init__(self, server_mod):
        self.server = server_mod
        self.lock = threading.Lock()
        self.gh_last_tick = 0.0
        self.gh_entered = False
        self.board_key = None
        self.board_items = None
        self.board_rev = 0
        self.board_epoch = os.urandom(16).hex()
        self.board_manifest = (0, None)
        self.ux_lock = threading.Lock()
        self.ux = {"ts": 0.0, "body": None, "rev": 0}
        self.live_lock = threading.Lock()
        self.live = {"ts": 0.0, "data": None, "detail": None, "ms": 0.0}
        self.productivity_meta_lock = threading.Lock()
        self.productivity_meta_loaded = False

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
                key = entry.get("all_sig") if entry else None
                items = entry.get("items") if entry else None
                entry_ts = entry.get("ts") if entry else None
            if key != self.board_key or items is not self.board_items:
                self.board_key, self.board_items = key, items
                self.board_rev += 1
            board_rev = self.board_rev
            base_rev, base = self.board_manifest
        reply = {"ok": True, "version": q.gh_queue_version(),
                 "board_rev": board_rev, "board_ts": entry_ts,
                 "board_epoch": self.board_epoch}
        have = req.get("have_board_rev")
        wants_patch = req.get("board_delta") is True
        same_epoch = req.get("board_epoch") == self.board_epoch
        if items is None or have == "skip" or (have == board_rev and (same_epoch or not wants_patch)):
            return reply
        use_base = (wants_patch and same_epoch and req.get("board_base") is True
                    and have == base_rev and base is not None)
        if use_base:
            manifest = self._remember_board(board_rev, items)
            if manifest is not None:
                patch = []
                replaced = 0
                for ref, (index, digest) in manifest.items():
                    previous = base.get(ref)
                    if previous is not None and previous[1] == digest:
                        patch.append(previous[0])
                    else:
                        patch.append(items[index])
                        replaced += 1
                if replaced * 2 <= len(items):
                    reply.update(board_patch=patch, base_rev=base_rev)
                    return reply
        reply["items"] = items
        return reply

    def _remember_board(self, revision, items, *, encoded_rows=None):
        with self.lock:
            if revision != self.board_rev:
                return None
            if self.board_manifest[0] == revision:
                return self.board_manifest[1]
        manifest = _board_manifest(items, encoded_rows=encoded_rows)
        with self.lock:
            if revision == self.board_rev:
                self.board_manifest = (revision, manifest)
        return manifest

    def gh_reaper(self):
        while True:
            time.sleep(2.0)
            with self.lock:
                idle = (self.gh_entered
                        and time.monotonic() - self.gh_last_tick > GH_TICK_GRACE_S)
                if idle:
                    self.gh_entered = False
                    self.board_key = self.board_items = None
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

    # live-activity (CCC-319) ---------------------------------------------
    def live_activity(self, req):
        """Liveness + activity for every candidate session, built on this
        process's own GIL. One build at a time; a build newer than
        ``max_age_s`` is shared, so concurrent callers do not stack builds.
        ``built_at`` is when the data was read, so the dashboard can report
        its true age rather than the age of the reply."""
        max_age = max(0.0, float(req.get("max_age_s") or 0.0))
        with self.live_lock:
            live = self.live
            if live["data"] is None or time.time() - live["ts"] >= max_age:
                detail = {}
                t0 = time.perf_counter()
                started = time.time()
                data = self.server._build_live_sessions_activity_uncached(detail)
                live.update(ts=started, data=data, detail=detail,
                            ms=(time.perf_counter() - t0) * 1000.0)
            return {"ok": True, "built_at": live["ts"], "build_ms": live["ms"],
                    "detail": live["detail"], "data": live["data"],
                    "pid": os.getpid()}

    def system_health(self, req):
        return {"ok": True, "data": self.server._build_system_health_uncached()}

    def throughput_ticket_counts(self, req):
        from ccc_server import usage_stats
        return {"ok": True, "data": usage_stats._throughput_daily_ticket_counts_local(
            float(req["start_epoch"]), float(req["end_epoch"]))}

    def throughput_turn_stats(self, req):
        from ccc_server import usage_stats
        return {"ok": True, "data": usage_stats._throughput_daily_turn_stats_local(
            float(req["start_epoch"]), float(req["end_epoch"]), req["engine"], req["final"],
        )}

    def productivity_snapshot(self, req):
        # Importing server does not run the dashboard startup loader. Seed
        # the parser cache before the first historical scan in this process.
        with self.productivity_meta_lock:
            if not self.productivity_meta_loaded:
                self.server._load_conv_meta_cache()
                self.productivity_meta_loaded = True
        return {"ok": True, "data": self.server._productivity_build_snapshot()}

    def worker_closure(self, req):
        from ccc_server.import_closure import compute_closure
        return {"ok": True, "hash": compute_closure(self.server._install_dir())["hash"]}

    def runaway_scan(self, req):
        from ccc_server import background_work, runaway
        with background_work.slot():
            flagged, _ = runaway.scan()
        return {"ok": True, "sessions": flagged}

    # ship-graph-warm -----------------------------------------------------
    def ship_sync(self, req):
        from ccc_server import ship_graph
        ship_graph._start_background_sync()
        return {"ok": True, "indexing": bool(ship_graph.is_indexing())}

    # session-fts-warm (MEMORY-20) ---------------------------------------
    def fts_sync(self, req):
        from ccc_server import session_fts
        session_fts._start_background_sync()
        return {"ok": True, "indexing": bool(session_fts.is_indexing())}

    def handle(self, conn):
        try:
            conn.settimeout(READ_TIMEOUT_S)
            req = json.loads(conn.makefile("rb").readline() or b"{}")
            op = str(req.get("op") or "")
            fn = {"gh_tick": self.gh_tick, "ux_health": self.ux_health,
                  "ship_sync": self.ship_sync, "fts_sync": self.fts_sync,
                  "live_activity": self.live_activity,
                  "system_health": self.system_health,
                  "throughput_ticket_counts": self.throughput_ticket_counts,
                  "throughput_turn_stats": self.throughput_turn_stats,
                  "worker_closure": self.worker_closure,
                  "productivity_snapshot": self.productivity_snapshot,
                  "runaway_scan": self.runaway_scan,
                  "ping": lambda r: {"ok": True}}.get(op)
            try:
                reply = fn(req) if fn else {"ok": False, "error": "unknown op"}
            except Exception as exc:
                print(f"  [bg-helper] {op}: {exc!r}", flush=True)
                reply = {"ok": False, "error": str(exc)}
            if op == "live_activity" and reply.get("ok"):
                reply["ready_at"] = time.time()
            items = reply.get("items") if op == "gh_tick" and req.get("board_delta") is True else None
            encoded_rows = None
            if isinstance(items, list) and len(items) <= _BOARD_MANIFEST_MAX_ITEMS:
                body, encoded_rows = _encode_board_snapshot(reply)
            else:
                body = json.dumps(reply, default=str).encode() + b"\n"
            conn.sendall(body)
            del body
            if items is not None:
                self._remember_board(reply["board_rev"], items, encoded_rows=encoded_rows)
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


def serve(path, parent_pid, role="main"):
    from ccc_server.cpu_priority import set_background_priority
    set_background_priority(role)
    os.umask(0o077)
    path = Path(path)
    sys.path.insert(0, str(PROJECT_ROOT))
    from ccc_server import memory_release
    memory_release.start()
    import server as server_mod  # noqa: E402  (heavy; child only)
    child = _Child(server_mod)
    if role == "main":
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
    # The dashboard defers its boot-time ship-graph and session-fts warms
    # to this process.
    for warm in (child.ship_sync, child.fts_sync) if role == "main" else ():
        try:
            warm({})
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
    role = argv[argv.index("--role") + 1] if "--role" in argv else "main"
    serve(path, parent, role)


if __name__ == "__main__":
    main()
