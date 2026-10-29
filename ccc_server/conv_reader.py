"""Conversation-reader process: serve GET /api/conversations/<id> off the
dashboard's GIL.

The dashboard is one Python process. In the evening ~15 background loops and
polled routes keep it at ~100% of one core, so a conversation open that needs
~100ms of CPU waited seconds for the GIL (CCC-143: server time 1.6-4.9s
against <0.4s of CPU, with most of the host's cores idle).

The dashboard spawns one child (``python -m ccc_server.conv_reader``) that
imports server.py (no background threads start on import) and builds open
responses with the same code (``server._conversation_open_response``). The
dashboard handler forwards the request over a private unix socket, which
costs a few socket calls instead of hundreds of GIL slices.

* Process model: one child per dashboard, socket path keyed by the dashboard
  pid. The child exits when its parent goes away (ccc.service uses
  KillMode=process, so systemd does not reap it); a supervisor thread
  respawns it with backoff.
* Caches: the child has its own in-memory caches; disk caches are shared.
  Queued inputs are re-read from pending-inputs.json; Codex thread state
  comes from the worker, as it does in the dashboard.
* Fallback: ``CCC_CONV_READER=0`` disables the child. Any connect, timeout
  or protocol error, or a session the child declines (ACP; Codex with
  engine routing off), is built in-process exactly as before.

Wire protocol (one request per connection): the client sends one JSON line
``{"conv_id", "query", "accept"}``; the child answers one JSON line
``{"ok": true, "len": N, "encoding", "cache"}`` followed by N body bytes, or
``{"ok": false}`` to decline.
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
# A cold Devin parse without its disk cache can take tens of seconds; past
# this the dashboard builds the response itself.
READ_TIMEOUT_S = float(os.environ.get("CCC_CONV_READER_TIMEOUT_S", "45") or 45)
READY_TIMEOUT_S = 30.0
MAX_CONSECUTIVE_ERRORS = 3

_lock = threading.Lock()
_state = {"sock": None, "proc": None, "ready": False, "errors": 0}
_stats = {"served": 0, "declined": 0, "errors": 0}


def enabled():
    return (os.environ.get("CCC_CONV_READER", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def socket_path(parent_pid=None):
    return COMMAND_CENTER_STATE_DIR / f"conv-reader-{parent_pid or os.getpid()}.sock"


# ---------------------------------------------------------------- dashboard


def start():
    """Spawn and supervise the reader child. Called once from server.main()."""
    if not enabled() or os.environ.get("CCC_CONV_READER_CHILD") == "1":
        return None
    thread = threading.Thread(target=_supervise, name="conv-reader-supervisor",
                              daemon=True)
    thread.start()
    return thread


def _spawn(path):
    env = dict(os.environ)
    env["CCC_CONV_READER_CHILD"] = "1"
    try:
        path.unlink()
    except OSError:
        pass
    return subprocess.Popen(
        [sys.executable, "-m", "ccc_server.conv_reader",
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
            print(f"  [conv-reader] spawn failed: {exc}", flush=True)
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
                print(f"  [conv-reader] pid {proc.pid} ready on {path}", flush=True)
                break
            time.sleep(0.1)
        if proc.poll() is None and not path.exists():
            proc.kill()
        proc.wait()
        with _lock:
            _state.update(ready=False, proc=None)
        print(f"  [conv-reader] pid {proc.pid} exited rc={proc.returncode}",
              flush=True)
        # A child that stayed up a while earns a quick respawn.
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
            # Wedged child: kill it; the supervisor respawns a fresh one.
            _state["ready"] = False
            try:
                proc.kill()
            except Exception:
                pass


def forward(conv_id, query, accept):
    """{body, encoding, cache} from the reader, or None to build in-process."""
    with _lock:
        path = _state["sock"] if _state["ready"] else None
    if not path:
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(CONNECT_TIMEOUT_S)
        sock.connect(path)
        sock.settimeout(READ_TIMEOUT_S)
        sock.sendall(json.dumps({"conv_id": conv_id, "query": query or "",
                                 "accept": accept or ""}).encode() + b"\n")
        reader = sock.makefile("rb")
        header = json.loads(reader.readline() or b"{}")
        if not header.get("ok"):
            with _lock:
                _stats["declined"] += 1
                _state["errors"] = 0
            return None
        length = int(header["len"])
        body = reader.read(length)
        if len(body) != length:
            raise ConnectionError("short body from conv reader")
    except Exception:
        _note_error()
        return None
    finally:
        sock.close()
    with _lock:
        _stats["served"] += 1
        _state["errors"] = 0
    cache = header.get("cache")
    return {"body": body, "encoding": header.get("encoding"),
            "cache": f"reader-{cache}" if cache else "reader"}


def status():
    with _lock:
        proc = _state.get("proc")
        return {"enabled": enabled(), "ready": bool(_state["ready"]),
                "pid": proc.pid if proc is not None else None,
                **dict(_stats)}


# -------------------------------------------------------------------- child


def _handle(conn, server_mod):
    try:
        conn.settimeout(READ_TIMEOUT_S)
        reader = conn.makefile("rb")
        req = json.loads(reader.readline() or b"{}")
        conv_id = str(req.get("conv_id") or "")
        out = None
        if conv_id:
            try:
                out = server_mod._conversation_open_response(
                    conv_id, req.get("query") or "", req.get("accept") or "",
                    in_reader=True)
            except Exception as exc:
                print(f"  [conv-reader] {conv_id}: {exc!r}", flush=True)
                out = None
        if out is None:
            conn.sendall(b'{"ok": false}\n')
            return
        body = out["body"]
        head = {"ok": True, "len": len(body), "encoding": out.get("encoding"),
                "cache": out.get("cache")}
        conn.sendall(json.dumps(head).encode() + b"\n" + body)
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
    # Publish atomically: the supervisor treats the path's existence as ready.
    os.replace(tmp, path)
    threading.Thread(target=_watch_parent,
                     args=(parent_pid, path, _socket_inode(path)),
                     name="conv-reader-parent-watch", daemon=True).start()
    while True:
        conn, _ = listener.accept()
        threading.Thread(target=_handle, args=(conn, server_mod),
                         name="conv-reader-req", daemon=True).start()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[argv.index("--sock") + 1]
    parent = int(argv[argv.index("--parent") + 1])
    serve(path, parent)


if __name__ == "__main__":
    main()
