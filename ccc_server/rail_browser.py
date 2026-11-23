"""Remote-rendered browser for the status-rail preview pane.

Spawns scripts/rail-browser/browser_helper.js (Node + puppeteer) on demand
and proxies its loopback HTTP API so the stdlib-only dashboard can drive a
real headless Chromium without a websocket dependency. The dashboard polls
/api/browser/frame for the latest JPEG and forwards input to
/api/browser/input.

Navigation policy: http/https only, and hostnames resolving to link-local
or cloud-metadata addresses are refused. Everything else — localhost,
private ranges (dev servers), and the public web — is allowed; the helper
runs with an ephemeral profile so no cookies or logins leak between uses.
"""

import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path


_STATE_FILE = Path.home() / ".claude" / "command-center" / "rail-browser.json"
_HELPER = Path(__file__).resolve().parent.parent / "scripts" / "rail-browser" / "browser_helper.js"
_SPAWN_LOCK = threading.Lock()
_PROC = None  # subprocess.Popen of the helper we spawned, if still ours
_BLOCKED_NETS = (
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + cloud metadata
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
    ipaddress.ip_network("0.0.0.0/8"),
)
_PROXY_TIMEOUT = 35  # navigate can legitimately take ~30s


def _state():
    try:
        return json.loads(_STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _probe(port):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/status", timeout=1.5
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _helper_port():
    global _PROC
    state = _state()
    port = state.get("port")
    if port and _probe(port):
        return port
    with _SPAWN_LOCK:
        # Re-check inside the lock: another thread may have spawned meanwhile.
        state = _state()
        port = state.get("port")
        if port and _probe(port):
            return port
        if _PROC is not None and _PROC.poll() is not None:
            _PROC = None
        try:
            _PROC = subprocess.Popen(
                ["node", str(_HELPER)],
                cwd=str(_HELPER.parent.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return None
        deadline = time.time() + 15
        while time.time() < deadline:
            port = _state().get("port")
            if port and _probe(port):
                return port
            if _PROC.poll() is not None:
                return None
            time.sleep(0.15)
    return None


def _nav_allowed(url):
    """http/https only; refuse link-local / cloud-metadata targets."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    if not host or urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unresolvable: let the browser show its own error page
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if any(ip in net for net in _BLOCKED_NETS):
            return False
    return True


def _forward(port, method, sub, body=None):
    url = f"http://127.0.0.1:{port}{sub}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as resp:
        return resp.status, dict(resp.headers), resp.read()


def _read_json_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 64 * 1024:
            return None
        data = json.loads(handler.rfile.read(length))
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        return None


def _send_upstream_error(handler, err):
    handler.send_json({"ok": False, "error": f"browser helper unavailable: {err}"}, 502)


def handle(handler, method, path):
    """Route /api/browser/* requests. Returns True when handled."""
    sub = "/" + path[len("/api/browser/"):].lstrip("/")
    if method == "GET" and sub == "/frame":
        port = _helper_port()
        if not port:
            handler.send_json({"ok": False, "error": "browser helper unavailable"}, 503)
            return True
        try:
            status, headers, data = _forward(port, "GET", "/frame")
        except Exception as e:
            _send_upstream_error(handler, e)
            return True
        if status != 200:
            handler.send_response(status)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return True
        handler.send_response(200)
        handler.send_header("Content-Type", "image/jpeg")
        handler.send_header("Cache-Control", "no-store")
        if headers.get("X-Frame-Seq"):
            handler.send_header("X-Frame-Seq", headers["X-Frame-Seq"])
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True

    if method == "GET" and sub == "/status":
        port = _helper_port()
        if not port:
            handler.send_json({"ok": True, "available": False})
            return True
        try:
            _, _, data = _forward(port, "GET", "/status")
            payload = json.loads(data)
            payload["available"] = True
            handler.send_json(payload)
        except Exception as e:
            _send_upstream_error(handler, e)
        return True

    if method == "POST" and sub in (
        "/open", "/nav", "/back", "/forward", "/reload", "/resize", "/input", "/close"
    ):
        body = _read_json_body(handler)
        if body is None:
            body = {}
        if sub in ("/open", "/nav") and not _nav_allowed(str(body.get("url") or "")):
            handler.send_json({"ok": False, "error": "navigation target not allowed"}, 403)
            return True
        port = _helper_port()
        if not port:
            handler.send_json({"ok": False, "error": "browser helper failed to start"}, 503)
            return True
        try:
            status, _, data = _forward(port, "POST", sub, body)
            payload = json.loads(data) if data else {"ok": status < 400}
            handler.send_json(payload, status)
        except Exception as e:
            _send_upstream_error(handler, e)
        return True

    handler.send_json({"ok": False, "error": "not found"}, 404)
    return True
