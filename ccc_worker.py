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
import uuid
from pathlib import Path

from control_plane import (
    ControlPlaneClient, WorkLedger, authenticated, ensure_token, socket_path,
    token_path,
)


MAX_REQUEST_BYTES = 4 * 1024 * 1024


class WorkerRuntime:
    def __init__(self, ledger=None, token=None):
        self.epoch = str(uuid.uuid4())
        self.pid = os.getpid()
        self.ledger = ledger or WorkLedger()
        self.token = token or ensure_token()
        self.recovered = self.ledger.recover_orphaned_running(self.epoch)
        self._engine_host = None
        self._engine_host_lock = threading.Lock()

    def dispatch(self, method, params):
        params = params if isinstance(params, dict) else {}
        if method == "health":
            return {
                "ok": True,
                "worker": {
                    "pid": self.pid,
                    "epoch": self.epoch,
                    "recovered_uncertain": len(self.recovered),
                    "capabilities": [
                        "engine-execution-v1",
                        "work-graph-v1",
                        "safe-drain-v1",
                    ],
                },
                **self.ledger.summary(),
            }
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
            reconciled = self._engines().reconcile_uncertain()
            return {
                "ok": True,
                "reconciled": len(reconciled),
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
    runtime = WorkerRuntime(token=token)
    server = WorkerServer(path, runtime)
    os.chmod(path, 0o600)

    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        f"CCC worker running pid={runtime.pid} epoch={runtime.epoch} socket={path}",
        flush=True,
    )
    if (
        not runtime.ledger.drain_state().get("enabled")
        and (
            runtime.ledger.summary().get("queued")
            or runtime.ledger.summary().get("uncertain")
        )
    ):
        def recover():
            runtime._engines().reconcile_uncertain()
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
