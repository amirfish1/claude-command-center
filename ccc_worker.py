#!/usr/bin/env python3
"""Persistent execution worker for Claude Command Center.

The dashboard is intentionally a client of this process.  Engine ownership is
migrated here incrementally so a dashboard restart does not terminate work.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socketserver
import sys
import threading
import time
import uuid
from pathlib import Path

from control_plane import (
    ControlPlaneClient, WorkLedger, authenticated, ensure_token, socket_path,
    token_path, worker_pid_path,
)


MAX_REQUEST_BYTES = 4 * 1024 * 1024
WANTED_OPEN_FILES = 4096


def _raise_open_file_limit(target=WANTED_OPEN_FILES):
    """Lift RLIMIT_NOFILE above launchd's 256 default.

    The worker holds one long-lived descriptor per managed engine process plus
    SQLite handles. At 256 an exhausted table makes every accept() fail, which
    looks exactly like a hung worker: the socket stays bound, requests get an
    empty reply, and the dashboard restart path cannot take over.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return None
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft >= target:
        return soft
    wanted = target if hard == resource.RLIM_INFINITY else min(target, hard)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))
    except (ValueError, OSError):
        return soft
    return wanted


class WorkerRuntime:
    def __init__(self, ledger=None, token=None):
        self.epoch = str(uuid.uuid4())
        self.pid = os.getpid()
        self.started_at = time.time()
        self.ledger = ledger or WorkLedger()
        self.token = token or ensure_token()
        self.recovered = self.ledger.recover_orphaned_running(self.epoch)
        self._engine_host = None
        self._engine_host_lock = threading.Lock()

    def dispatch(self, method, params):
        params = params if isinstance(params, dict) else {}
        if method == "health":
            # Version of the `server` module CURRENTLY LOADED in this process
            # (the worker imports it lazily on first engine RPC). None means
            # "never imported" -- the first RPC will pick up whatever is on
            # disk, so there is no stale code to restart away. run.sh compares
            # this against the repo version on every launch and kickstarts the
            # worker when they differ, so upgrades actually reach worker-owned
            # code paths (e.g. the Codex app-server liveness probe).
            server_mod = sys.modules.get("server")
            return {
                "ok": True,
                "worker": {
                    "pid": self.pid,
                    "epoch": self.epoch,
                    "started_at": self.started_at,
                    "recovered_uncertain": len(self.recovered),
                    "server_version": getattr(server_mod, "__version__", None) if server_mod else None,
                    "server_content_hash": getattr(server_mod, "_ccc_content_hash", None) if server_mod else None,
                    "capabilities": [
                        "engine-execution-v1",
                        "work-graph-v1",
                        "safe-drain-v1",
                    ],
                },
                **self.ledger.summary(),
            }
        if method == "system.app_server":
            # The Codex transport lives HERE, not in the dashboard:
            # _control_plane_routes_engines() defaults on, so worker_engines
            # drives it through this process's lazily-imported copy of
            # `server`. The dashboard asking its own module global always got
            # live:false on a perfectly healthy system, which is why the
            # System status panel used to say "idle" mid-Codex-session.
            server_mod = sys.modules.get("server")
            if server_mod is None:
                return {
                    "ok": False,
                    "code": "not_loaded",
                    "error": "worker has not imported server yet",
                }
            return {"ok": True, "status": server_mod._system_app_server_status()}
        if method == "drain.set":
            state = self.ledger.set_drain(
                params.get("enabled"), params.get("reason") or ""
            )
            replayed = []
            if not state.get("enabled"):
                queued = self.ledger.list(states=["queued"], limit=2000)
                dispatchable = any(
                    isinstance(item.get("payload"), dict)
                    and item["payload"].get("operation")
                    and isinstance(item["payload"].get("args"), dict)
                    for item in queued
                )
                if self._engine_host is not None or dispatchable:
                    replayed = self._engines().dispatch_queued()
            return {
                "ok": True,
                "drain": state,
                "replayed": len(replayed),
                **self.ledger.summary(),
            }
        if method == "work.submit":
            item, created = self.ledger.submit(**params)
            draining = bool(self.ledger.drain_state().get("enabled"))
            return {
                "ok": True,
                "created": created,
                "deferred": draining,
                "code": "draining" if draining else None,
                "work": item,
            }
        if method == "work.get":
            item = self.ledger.get(params.get("work_id"))
            return (
                {"ok": True, "work": item}
                if item else
                {"ok": False, "code": "not_found", "error": "work item not found"}
            )
        if method == "work.list":
            return {"ok": True, "work": self.ledger.list(
                states=params.get("states"),
                session_id=params.get("session_id"),
                limit=params.get("limit", 200),
            )}
        if method == "work.transition":
            work_id = params.pop("work_id", None)
            item = self.ledger.transition(work_id, **params)
            return {"ok": True, "work": item}
        if method == "work.renew":
            item = self.ledger.renew_lease(
                params.get("work_id"),
                params.get("owner_epoch") or self.epoch,
                params.get("lease_seconds", 30),
            )
            return {"ok": True, "work": item}
        if method == "work.graph":
            return {"ok": True, "graph": self.ledger.graph(params.get("root_id"))}
        if method == "work.reconcile":
            host = self._engines()
            reconciled = host.reconcile_uncertain()
            # Reclaim first, retire second: anything with live evidence has
            # already left `uncertain` by the time the retirement pass reads
            # the table, so the two can never fight over the same item.
            retired = host.retire_stale_uncertain()
            return {
                "ok": True,
                "reconciled": len(reconciled),
                "retired": len(retired),
                "work": reconciled,
                **self.ledger.summary(),
            }
        if method == "work.resolve":
            return self._engines().resolve_uncertain(
                params.get("work_id"), params.get("action")
            )
        if method == "engine.adopt":
            return self._engines().adopt_registry()
        if method in ("engine.execute", "engine.query"):
            host = self._engines()
            if method == "engine.execute":
                return host.execute(params)
            return host.query(params)
        return {"ok": False, "code": "method_not_found", "error": "unknown method"}

    def _engines(self):
        if self._engine_host is None:
            with self._engine_host_lock:
                if self._engine_host is None:
                    from worker_engines import EngineHost
                    self._engine_host = EngineHost(self)
        return self._engine_host


class WorkerRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            self._send({"ok": False, "error": "request too large"})
            return
        try:
            request = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            self._send({"ok": False, "error": "invalid JSON"})
            return
        runtime = self.server.runtime
        if not authenticated(request.get("auth"), runtime.token):
            self._send({"ok": False, "code": "unauthorized", "error": "unauthorized"})
            return
        try:
            response = runtime.dispatch(
                str(request.get("method") or ""), request.get("params") or {}
            )
        except (KeyError, TypeError, ValueError) as exc:
            response = {"ok": False, "code": "invalid_request", "error": str(exc)}
        except Exception as exc:
            response = {"ok": False, "code": "worker_error", "error": str(exc)}
        except BaseException as exc:
            # SystemExit and friends must not close the connection without a
            # reply: the client cannot distinguish that from a dead worker,
            # and the restart path then refuses to replace us.
            import traceback
            traceback.print_exc()
            sys.stderr.flush()
            response = {
                "ok": False,
                "code": "worker_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        try:
            self._send(response)
        except TypeError as exc:
            # Response contained something json.dumps cannot encode; still
            # answer with a parseable error instead of dropping the socket.
            self._send({
                "ok": False,
                "code": "worker_error",
                "error": f"unserializable response: {exc}",
            })

    def _send(self, payload):
        self.wfile.write(
            json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8") + b"\n"
        )


class WorkerServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path, runtime):
        self.runtime = runtime
        super().__init__(str(path), WorkerRequestHandler)


def _release_stale_restart_drain(runtime):
    """Lift a "worker-restart:" drain left over from the restart window.

    That drain protects the restart that produced THIS worker process; once
    the worker is serving, the window is over. The dashboard's restart
    handler normally lifts it, but if that dashboard died mid-restart nobody
    else would -- every later submit would defer forever.
    """
    drain = runtime.ledger.drain_state()
    if drain.get("enabled") and str(drain.get("reason") or "").startswith(
        "worker-restart:"
    ):
        runtime.ledger.set_drain(False, "worker online")
        return True
    return False


def serve(path=None):
    path = Path(path or socket_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    token = ensure_token(token_path())
    if path.exists() or path.is_socket():
        live = ControlPlaneClient(path=path, token_file=token_path()).request("health")
        if live.get("available"):
            raise RuntimeError(f"CCC worker is already running at {path}")
        try:
            path.unlink()
        except OSError:
            pass
    open_files = _raise_open_file_limit()
    runtime = WorkerRuntime(token=token)
    server = WorkerServer(path, runtime)
    os.chmod(path, 0o600)
    pidfile = worker_pid_path()
    try:
        pidfile.write_text(f"{runtime.pid}\n", encoding="utf-8")
    except OSError:
        pidfile = None

    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        f"CCC worker running pid={runtime.pid} epoch={runtime.epoch} "
        f"socket={path} nofile={open_files}",
        flush=True,
    )
    _release_stale_restart_drain(runtime)
    if (
        not runtime.ledger.drain_state().get("enabled")
        and (
            runtime.ledger.summary().get("queued")
            or runtime.ledger.summary().get("uncertain")
        )
    ):
        def recover():
            runtime._engines().reconcile_uncertain()
            runtime._engines().retire_stale_uncertain()
            runtime._engines().dispatch_queued()
        threading.Thread(
            target=recover,
            daemon=True,
            name="ccc-reconcile",
        ).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        try:
            path.unlink()
        except OSError:
            pass
        if pidfile is not None:
            try:
                if pidfile.read_text(encoding="utf-8").strip() == str(runtime.pid):
                    pidfile.unlink()
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="CCC persistent execution worker")
    parser.add_argument("--socket", help="Unix socket path")
    parser.add_argument(
        "--health", action="store_true",
        help="query the running worker and exit",
    )
    args = parser.parse_args(argv)
    if args.health:
        response = ControlPlaneClient(path=args.socket).request("health")
        print(json.dumps(response, indent=2, sort_keys=True))
        return 0 if response.get("ok") else 1
    serve(args.socket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
