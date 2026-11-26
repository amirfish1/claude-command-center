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

memory_recall runs here too (MEMORY-20); the dashboard keeps owning the
index sync and only forwards the query.

Direct port (CCC-143): the child also serves GET /api/conversations/<id> over
HTTP on 127.0.0.1:<dashboard port + 10000> (``CCC_CONV_READER_PORT`` sets
it; ``0`` turns it off). Forwarding still made every open wait for the
dashboard to accept the connection and start a handler thread, which a busy
or booting dashboard delays by seconds. The browser fetches opens from this
port on its own connection; when the page is served through
``tailscale serve``, that port needs its own HTTPS serve entry too. It
reuses the dashboard's handler class, so the proxy-secret, PIN and origin
checks are the same code. CORS headers go only to origins that pass
``_check_same_origin``. Successive children share the port with
SO_REUSEPORT, so the previous child keeps answering while the next one
imports server.py after a dashboard restart.

Wire protocol (one request per connection): the client sends one JSON line
``{"conv_id", "query", "accept"}``; the child answers one JSON line
``{"ok": true, "len": N, "encoding", "cache"}`` followed by N body bytes, or
``{"ok": false}`` to decline.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
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
_state = {"sock": None, "proc": None, "ready": False, "errors": 0,
          "http_port": None}
_stats = {"served": 0, "declined": 0, "errors": 0}


def decode_conversation_id(segment):
    """Decode one URL segment, keeping conversation IDs inside that segment."""
    conversation_id = urllib.parse.unquote(segment)
    if any(char in conversation_id for char in ("/", "\\", "\x00")):
        raise ValueError("invalid conversation id")
    return conversation_id


def enabled():
    return (os.environ.get("CCC_CONV_READER", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def socket_path(parent_pid=None):
    return COMMAND_CENTER_STATE_DIR / f"conv-reader-{parent_pid or os.getpid()}.sock"


def http_port(dashboard_port=None):
    """Loopback port for direct opens, or None when turned off."""
    raw = (os.environ.get("CCC_CONV_READER_PORT") or "").strip().lower()
    if raw in ("0", "off", "no", "false"):
        return None
    if raw.isdigit() and 0 < int(raw) <= 65535:
        return int(raw)
    try:
        port = int(dashboard_port or os.environ.get("PORT") or 8090) + 10000
    except (TypeError, ValueError):
        return None
    return port if port <= 65535 else None


def is_child():
    return os.environ.get("CCC_CONV_READER_CHILD") == "1"


def ready():
    with _lock:
        return bool(_state["ready"])


# ---------------------------------------------------------------- dashboard


def start(dashboard_port=None, acp_state=None):
    """Spawn and supervise the reader child. Called once from server.main().

    `acp_state` returns the dashboard's in-memory ACP session state; the
    child needs a copy of it to serve sidebar list reads (CCC-679)."""
    if not enabled() or os.environ.get("CCC_CONV_READER_CHILD") == "1":
        return None
    with _lock:
        _state["dashboard_port"] = dashboard_port
        _state["http_port"] = http_port(dashboard_port)
    thread = threading.Thread(target=_supervise, name="conv-reader-supervisor",
                              daemon=True)
    thread.start()
    from ccc_server import list_reader
    if acp_state is not None and list_reader.enabled():
        threading.Thread(target=_publish_acp, args=(acp_state,),
                         name="conv-reader-acp", daemon=True).start()
    return thread


ACP_PUBLISH_INTERVAL_S = 1.0


def _publish_acp(acp_state):
    """Keep the child's copy of the ACP session state current. The child
    declines list reads until the first copy lands. A few scalar fields per
    attached session; compared before sending, so idle ticks cost a dict walk."""
    from ccc_server import list_reader
    sent = (None, None)
    while True:
        time.sleep(ACP_PUBLISH_INTERVAL_S)
        with _lock:
            proc = _state.get("proc")
            pid = proc.pid if proc is not None and _state["ready"] else None
        if pid is None:
            sent = (None, None)
            continue
        try:
            snapshot = list_reader.acp_snapshot(acp_state())
            signature = list_reader.acp_signature(snapshot)
        except Exception:
            continue
        if sent == (pid, signature):
            continue
        if call("list_acp", timeout=2.0, state=snapshot) is not None:
            sent = (pid, signature)


def list_mutation(kind, archived=None, trashed=None, sids=None):
    """Forward a serve-cache clear or restamp to the child before the
    mutation's request answers, so the next list read the child serves
    already reflects it. The child also watches the sidecars as a fallback."""
    from ccc_server import list_reader
    if is_child() or not list_reader.enabled() or not ready():
        return False

    def as_list(value):
        return sorted(str(v) for v in value) if value is not None else None

    return call("list_mutation", timeout=1.0, kind=kind, archived=as_list(archived),
                trashed=as_list(trashed), sids=as_list(sids)) is not None


def _spawn(path):
    env = dict(os.environ)
    env["CCC_CONV_READER_CHILD"] = "1"
    try:
        path.unlink()
    except OSError:
        pass
    with _lock:
        port, dash_port = _state.get("http_port"), _state.get("dashboard_port")
    extra = ["--http-port", str(port)] if port else []
    if dash_port:
        extra += ["--port", str(dash_port)]
    return subprocess.Popen(
        [sys.executable, "-m", "ccc_server.conv_reader",
         "--sock", str(path), "--parent", str(os.getpid())] + extra,
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
    return _forward_binary({"conv_id": conv_id, "query": query or "", "accept": accept or ""})


def forward_page(path, query, accept, asset_hints=False, *, http_port=None):
    """Read-only app HTML uses the same private channel and local fallback."""
    return _forward_binary({"op": "app_page", "path": path, "query": query or "",
                            "accept": accept or "", "asset_hints": asset_hints,
                            "http_port": http_port}, timeout=0.75)


def _forward_binary(request, timeout=None):
    with _lock:
        path = _state["sock"] if _state["ready"] else None
    if not path:
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(CONNECT_TIMEOUT_S)
        sock.connect(path)
        sock.settimeout(timeout or READ_TIMEOUT_S)
        sock.sendall(json.dumps(request).encode() + b"\n")
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
    result = {"body": body, "encoding": header.get("encoding"),
              "cache": f"reader-{cache}" if cache else "reader"}
    if request.get("op") == "app_page":
        if isinstance(header.get("page_timing"), dict):
            result["page_timing"] = header["page_timing"]
        if isinstance(header.get("asset_hints"), list):
            result["asset_hints"] = tuple(header["asset_hints"])
    return result


def call(op, timeout=None, **fields):
    """The child's JSON reply to `op` (one JSON line each way), or None so
    the caller runs it in-process. Same fallback shape as forward()."""
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
        reply = json.loads(sock.makefile("rb").readline() or b"{}")
        if not isinstance(reply, dict) or not reply.get("ok"):
            raise ConnectionError(f"conv reader declined {op}")
    except Exception:
        _note_error()
        return None
    finally:
        sock.close()
    with _lock:
        _stats["served"] += 1
        _state["errors"] = 0
    return reply


def status():
    with _lock:
        proc = _state.get("proc")
        return {"enabled": enabled(), "ready": bool(_state["ready"]),
                "pid": proc.pid if proc is not None else None,
                "http_port": _state.get("http_port"),
                **dict(_stats)}


# -------------------------------------------------------------------- child


def _handle(conn, server_mod):
    try:
        conn.settimeout(READ_TIMEOUT_S)
        reader = conn.makefile("rb")
        req = json.loads(reader.readline() or b"{}")
        if str(req.get("op") or "").startswith("list_"):
            from ccc_server import list_reader
            conn.sendall(json.dumps(list_reader.handle_op(req)).encode() + b"\n")
            return
        if req.get("op") and req.get("op") != "app_page":
            conn.sendall(json.dumps(_handle_op(req)).encode() + b"\n")
            return
        conv_id = str(req.get("conv_id") or "")
        out = None
        if req.get("op") == "app_page":
            options = {"asset_hints": True} if req.get("asset_hints") is True else {}
            http_port = req.get("http_port")
            if type(http_port) is int and 1 <= http_port <= 65535:
                options["http_port"] = http_port
            out = server_mod._app_page_response(str(req.get("path") or ""),
                                                req.get("query") or "", req.get("accept") or "",
                                                **options)
        elif conv_id:
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
        if req.get("op") == "app_page" and isinstance(out.get("page_timing"), dict):
            head["page_timing"] = out["page_timing"]
        if req.get("op") == "app_page" and "asset_hints" in out:
            head["asset_hints"] = out["asset_hints"]
        conn.sendall(json.dumps(head).encode() + b"\n" + body)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _handle_op(req):
    """JSON ops (call() above). memory_recall (MEMORY-20): the query is ~60
    SQLite calls, each a GIL round trip; on a busy dashboard those cost a
    switch interval each (71 ms alone, 0.5-2.5 s live on a busy host)."""
    op = str(req.get("op") or "")
    try:
        if op == "memory_recall":
            from ccc_server import memory_api
            return {"ok": True, "result": memory_api.recall_in_process(
                str(req.get("q") or ""), limit=int(req.get("limit") or 20))}
    except Exception as exc:
        print(f"  [conv-reader] {op}: {exc!r}", flush=True)
    return {"ok": False}


_OPEN_PATH = re.compile(r"^/api/conversations/(?!all$|list$|order$)[^/]+$")
LIST_PATH = "/api/conversations/list"
# Response headers the sidebar reads from a cross-origin list response.
_LIST_EXPOSE = "Server-Timing, ETag, X-CCC-List-Body"
_http = {"server": None, "active": 0}
_http_lock = threading.Lock()


def _http_handler(server_mod):
    """The dashboard's handler class, narrowed to direct opens."""
    base = server_mod.CommandCenterHandler

    class ReaderHandler(base):
        _ccc_list_cors = False

        def _cors_headers(self, expose="Server-Timing"):
            origin = (self.headers.get("Origin") or "").strip()
            if origin and self._check_same_origin():
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Access-Control-Expose-Headers", expose)
                self.send_header("Timing-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def end_headers(self):
            # List responses are written by the dashboard's own senders;
            # add the CORS headers they lack just before the blank line.
            if self._ccc_list_cors:
                self._ccc_list_cors = False
                self._cors_headers(_LIST_EXPOSE)
            return super().end_headers()

        def _reply(self, status, body, encoding=None, cache=None):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self._send_server_timing(cache=cache)
            if encoding:
                self.send_header("Content-Encoding", encoding)
                self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            with _http_lock:
                _http["active"] += 1
            try:
                self._direct_get()
            finally:
                with _http_lock:
                    _http["active"] -= 1

        def _direct_get(self):
            if not self._check_proxy_secret():
                return
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            if self._phone_pin_blocked(path):
                return
            if path == "/api/conv-reader/ping":
                from ccc_server import list_reader
                self._reply(200, json.dumps({"ok": True, "pid": os.getpid(),
                                             "list": list_reader.ready()}).encode())
                return
            if path == LIST_PATH:
                self._direct_list()
                return
            if not _OPEN_PATH.match(path):
                self._reply(404, b'{"error": "not found"}')
                return
            try:
                conv_id = decode_conversation_id(path.split("/")[-1])
            except ValueError:
                self._reply(400, b'{"error": "invalid conversation id"}')
                return
            accept = self.headers.get("Accept-Encoding", "") or ""
            try:
                out = server_mod._conversation_open_response(
                    conv_id, parsed.query, accept, in_reader=True)
            except Exception as exc:
                print(f"  [conv-reader] direct {conv_id}: {exc!r}", flush=True)
                out = None
            if out is None:
                # Declined (ACP, dashboard-only Codex state) or failed: the
                # client retries on the dashboard, which builds it in-process.
                self._reply(503, b'{"error": "declined"}')
                return
            cache = out.get("cache")
            self._reply(200, out["body"], encoding=out.get("encoding"),
                        cache=f"direct-{cache}" if cache else "direct")

        def _direct_list(self):
            """The dashboard's own list branch, on this process's GIL."""
            from ccc_server import list_reader
            if not (list_reader.enabled() and list_reader.ready()):
                list_reader.note_served(declined=True)
                self._reply(503, b'{"error": "declined"}')
                return
            list_reader.sync()
            list_reader.note_served()
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            list_reader.note_window(query.get("window", [""])[0],
                                    layouts=query.get("row_layouts") == ["1"])
            self._ccc_list_cors = True
            base._do_GET(self)

        def do_OPTIONS(self):
            # CORS preflight for list reads: they carry If-None-Match and
            # X-CCC-* request headers, which opens never send.
            path = urllib.parse.urlparse(self.path).path.rstrip("/")
            origin = (self.headers.get("Origin") or "").strip()
            if path != LIST_PATH or not origin or not self._check_same_origin():
                self._refuse()
                return
            requested = self.headers.get("Access-Control-Request-Headers", "")
            allowed = ", ".join(h for h in (part.strip() for part in requested.split(","))
                                if re.fullmatch(r"[A-Za-z0-9-]{1,64}", h))
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET")
            if allowed:
                self.send_header("Access-Control-Allow-Headers", allowed)
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _refuse(self):
            self._reply(405, b'{"error": "method not allowed"}')

        do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

    return ReaderHandler


def _serve_http(server_mod, port, dashboard_port=None):
    """Bind the direct-open port (shared with the previous child via
    SO_REUSEPORT) and serve it on a daemon thread. Returns the server or
    None when the port is unavailable; opens then go through the dashboard."""
    import http.server
    import socketserver

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 128

        def server_bind(self):
            if hasattr(socket, "SO_REUSEPORT"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            # HTTPServer.server_bind would call getfqdn(), a DNS lookup.
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    try:
        # The origin allow-list the handler checks is resolved in the
        # dashboard's main(); resolve it the same way here.
        bind_host, origins, info = server_mod._resolve_runtime_network(
            int(dashboard_port or server_mod.PORT), None)
        server_mod.ALLOWED_ORIGINS[:] = origins
        server_mod.RUNTIME_NETWORK_INFO = info
    except Exception as exc:
        print(f"  [conv-reader] origin allow-list: {exc!r}", flush=True)
    try:
        srv = Server(("127.0.0.1", int(port)), _http_handler(server_mod))
    except OSError as exc:
        print(f"  [conv-reader] direct port {port} unavailable: {exc}", flush=True)
        return None
    _http["server"] = srv
    threading.Thread(target=srv.serve_forever, name="conv-reader-http",
                     daemon=True).start()
    print(f"  [conv-reader] direct opens on 127.0.0.1:{srv.server_address[1]}", flush=True)
    return srv


def _save_list_lineage():
    # The next child answers this child's delta tokens from the saved
    # lineage (CCC-702); save the final versions before exiting.
    try:
        from ccc_server import list_reader
        list_reader.save_lineage()
    except Exception:
        pass


def _hand_off(grace_s=10.0):
    """Stop taking direct connections, let in-flight opens finish, exit."""
    srv = _http.get("server")
    if srv is not None:
        try:
            srv.shutdown()
            srv.server_close()
        except OSError:
            pass
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            with _http_lock:
                if _http["active"] <= 0:
                    break
            time.sleep(0.05)
    _save_list_lineage()
    os._exit(0)


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
            _save_list_lineage()
            os._exit(0)
        if inode is not None and _socket_inode(path) != inode:
            # The next child is up and shares the direct port.
            _hand_off()
        time.sleep(1.0)


def serve(path, parent_pid, port=None, dashboard_port=None):
    os.umask(0o077)
    path = Path(path)
    sys.path.insert(0, str(PROJECT_ROOT))
    from ccc_server import memory_release
    memory_release.start()
    import server as server_mod  # noqa: E402  (heavy; child only)
    from ccc_server import list_reader
    if list_reader.enabled():
        list_reader.setup(server_mod)
        try:
            # A service stop signals the whole group: save the delta-token
            # lineage first, as on hand-off (CCC-702).
            import signal
            signal.signal(signal.SIGTERM, lambda *_: (_save_list_lineage(), os._exit(0)))
        except (ValueError, OSError):
            pass
    # The first compact sender header would otherwise index the journals inside
    # someone's open; the previous child still answers while this runs.
    try:
        import sender_envelope
        sender_envelope.lookup(message_id="conv-reader-warm")
    except Exception:
        pass
    if port:
        _serve_http(server_mod, port, dashboard_port)
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

    def opt(name):
        return int(argv[argv.index(name) + 1]) if name in argv else None
    serve(path, parent, opt("--http-port"), opt("--port"))


if __name__ == "__main__":
    main()
