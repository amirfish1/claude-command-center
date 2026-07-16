"""End-to-end harness for the CCC Cloud Relay.

Boots the REAL hosted relay (the ``ccc-cloud`` repo, in-process on an ephemeral
port with an isolated ``CCC_CLOUD_HOME``) and drives the REAL device client
(``cloud_relay.py`` in this repo) against it — either wired to the REAL local
CCC ``server.py`` (booted as an isolated subprocess with a redirected ``HOME``)
or to a scriptable ``FakeLocalAPI`` that implements just the loopback endpoints
the device calls.

Nothing here mocks the wire protocol: the device makes real outbound HTTPS-shape
requests, the relay validates + delivers real command envelopes, and results
flow back through the real ``/v1/result`` path. See ``docs/cloud-relay/
PROTOCOL.md`` for the contract both sides build against.

Composable pieces:

* ``CloudFixture``   — the hosted relay (in-process; supports fixed-port reuse so
  a restart lands on the same port + home, and exposes ``.store`` and window
  constants for offline/staleness tests).
* ``LocalCCCFixture`` — this repo's ``server.py`` as an isolated subprocess with
  synthetic ``~/.claude/projects`` transcripts (real session listing + real
  ``/api/inject-input``).
* ``FakeLocalAPI``   — stdlib stand-in for the loopback endpoints with a call
  journal + scriptable responses (disconnect/replay/edge scenarios).
* ``DeviceFixture``  — wires ``cloud_relay.CloudRelayManager`` (pair → confirm →
  loop) against a CloudFixture and a (LocalCCCFixture|FakeLocalAPI), with
  stop/network-cut controls.
* ``TogglableProxy`` — a severable TCP proxy so network loss can be simulated
  without killing the client.
* ``wait_until``     — poll a predicate; no bare sleeps for correctness.

Import note: the ``ccc-cloud`` service is a package named ``server`` which would
collide with this repo's top-level ``server.py`` module. We never import this
repo's ``server`` in-process (the local CCC always runs as a subprocess), and
``_import_cloud()`` pins ``server`` to the ccc-cloud package for the lifetime of
the process. Because of that pinning, ``test_cloud_relay_e2e.py`` is marked
``cloud_relay_e2e`` and excluded from the default ``pytest tests/`` run (see
``[tool.pytest.ini_options]`` in ``pyproject.toml``) so it never shares a
process with tests that expect the real ``server.py``. Run it explicitly to
opt in:

    python3 -m pytest -o addopts="" tests/test_cloud_relay.py \\
        tests/test_cloud_relay_e2e.py tests/test_tailscale_coexistence.py \\
        -p no:cacheprovider

Tests that use ``CloudFixture``/``_import_cloud()`` must call
``require_cloud_repo()`` first so a missing ``ccc-cloud`` checkout produces a
clean ``pytest.skip`` instead of a setup error.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

WORKTREE_ROOT = Path(__file__).resolve().parents[1]
CCC_CLOUD_ROOT = Path(
    os.environ.get("CCC_CLOUD_REPO", str(Path.home() / "Apps" / "ccc-cloud"))
).resolve()

# Ensure this repo's root is importable (cloud_relay, federation).
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))


# ---------------------------------------------------------------------------
# ccc-cloud import shim (pins the `server` name to the ccc-cloud package)
# ---------------------------------------------------------------------------

_CLOUD = None


def require_cloud_repo():
    """Skip the calling test cleanly if the ccc-cloud repo isn't available,
    instead of letting `_import_cloud()` raise mid-fixture (a pytest ERROR,
    not a skip). Call this before any `_import_cloud()`/`CloudFixture` use."""
    if not CCC_CLOUD_ROOT.is_dir():
        pytest.skip(
            f"ccc-cloud repo not found at {CCC_CLOUD_ROOT}; set CCC_CLOUD_REPO "
            "to run the cloud-relay e2e suite")


def _import_cloud():
    """Import the ccc-cloud server package + test harness, resolving the
    `server` name-collision with this repo's server.py deterministically."""
    global _CLOUD
    if _CLOUD is not None:
        return _CLOUD
    if not CCC_CLOUD_ROOT.is_dir():
        raise RuntimeError(
            f"ccc-cloud repo not found at {CCC_CLOUD_ROOT}; set CCC_CLOUD_REPO"
            " (tests should call require_cloud_repo() first for a clean skip)")
    root = str(CCC_CLOUD_ROOT)
    tdir = str(CCC_CLOUD_ROOT / "tests")
    for p in (tdir, root):
        if p not in sys.path:
            sys.path.insert(0, p)
    # Drop any already-imported `server` that is NOT the ccc-cloud package, so
    # `import server.*` resolves to the ccc-cloud package below.
    for name in list(sys.modules):
        if name == "server" or name.startswith("server."):
            mod = sys.modules.get(name)
            f = getattr(mod, "__file__", "") or ""
            if not os.path.abspath(f).startswith(root):
                del sys.modules[name]
    appmod = importlib.import_module("server.app")
    config_mod = importlib.import_module("server.config")
    relay = importlib.import_module("server.relay")
    commands = importlib.import_module("server.commands")
    H = importlib.import_module("server.http_util")
    harness = importlib.import_module("harness")
    _CLOUD = types.SimpleNamespace(
        app=appmod, config=config_mod, relay=relay, commands=commands,
        H=H, harness=harness)
    return _CLOUD


def import_cloud_relay():
    """Import THIS repo's device client (cloud_relay.py)."""
    return importlib.import_module("cloud_relay")


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_until(pred, timeout=10.0, interval=0.05, msg="condition"):
    """Poll ``pred`` until it returns truthy or ``timeout`` elapses. Returns the
    truthy value; raises AssertionError on timeout. No bare sleeps for
    correctness — every wait is bounded by a predicate."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {msg} "
                         f"(last={last!r})")


def speed_up_device(cr, poll_wait=2, state_interval=2, state_min=0.4,
                    backoff_min=0.25, backoff_max=1.0):
    """Shrink the device client's loop tunables so tests don't wait on the
    20s/25s production cadence. These are the module-under-test's own knobs."""
    cr.POLL_WAIT_S = poll_wait
    cr.STATE_PUSH_INTERVAL_S = state_interval
    cr.STATE_PUSH_MIN_INTERVAL_S = state_min
    cr.BACKOFF_MIN_S = backoff_min
    cr.BACKOFF_MAX_S = backoff_max


# ---------------------------------------------------------------------------
# TogglableProxy — severable TCP proxy (simulate network loss)
# ---------------------------------------------------------------------------

class TogglableProxy:
    """A tiny loopback TCP proxy forwarding to (target_host, target_port). Call
    ``sever()`` to drop all live connections and refuse new ones (network loss);
    ``restore()`` to forward again. The device client points its relay_url here
    so a cut looks exactly like a dead network, not a killed client."""

    def __init__(self, target_host: str, target_port: int):
        self.th = target_host
        self.tp = int(target_port)
        self.severed = False
        self._conns = []
        self._lock = threading.Lock()
        self._stop = False
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(64)
        self.port = self._srv.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._t = threading.Thread(target=self._accept_loop, daemon=True)
        self._t.start()

    def _accept_loop(self):
        while not self._stop:
            try:
                cli, _ = self._srv.accept()
            except OSError:
                break
            if self.severed:
                self._close(cli)
                continue
            threading.Thread(target=self._handle, args=(cli,),
                             daemon=True).start()

    def _handle(self, cli):
        try:
            up = socket.create_connection((self.th, self.tp), timeout=10)
        except OSError:
            self._close(cli)
            return
        pair = (cli, up)
        with self._lock:
            self._conns.append(pair)

        def pipe(a, b):
            try:
                while True:
                    data = a.recv(65536)
                    if not data:
                        break
                    b.sendall(data)
            except OSError:
                pass
            finally:
                self._close(a)
                self._close(b)

        threading.Thread(target=pipe, args=(cli, up), daemon=True).start()
        threading.Thread(target=pipe, args=(up, cli), daemon=True).start()

    @staticmethod
    def _close(sock):
        try:
            sock.close()
        except OSError:
            pass

    def sever(self):
        self.severed = True
        with self._lock:
            conns = self._conns[:]
            self._conns.clear()
        for cli, up in conns:
            self._close(cli)
            self._close(up)

    def restore(self):
        self.severed = False

    def stop(self):
        self._stop = True
        self.sever()
        try:
            self._srv.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# FakeLocalAPI — scriptable loopback stand-in with a call journal
# ---------------------------------------------------------------------------

class FakeLocalAPI:
    """Implements just the loopback endpoints cloud_relay.py calls. Records a
    call journal and lets tests script inject/answer responses. Matches the
    LocalAPI interface (get/post/call) so it drops straight into a
    CloudRelayManager."""

    def __init__(self, sessions=None):
        self.journal = []           # list of (method, path, body_or_query)
        self.inject_calls = 0
        self.answer_calls = 0
        self.inject_response = {"ok": True}
        self.answer_response = {"ok": True}
        self.attention = []
        self.queues = []
        self.sessions = sessions if sessions is not None else [
            self._default_session()]

    @staticmethod
    def _default_session():
        return {
            "id": "sess-1",
            "session_id": "sess-1",
            "display_name": "Demo session",
            "engine": "claude",
            "is_live": True,
            "mtime": time.time(),
            "state": "waiting",
            "question_waiting": True,
            "question_id": "q-1",
            "question_text": "Deploy to prod now?",
            "question_options": ["Yes", "No"],
            "folder_label": "demo-repo",
            "jsonl_path": "/nonexistent/sess-1.jsonl",
        }

    # -- LocalAPI interface --------------------------------------------------

    def get(self, path, query=None):
        self.journal.append(("GET", path, query))
        if path == "/api/sessions":
            return 200, {"ok": True, "sessions": list(self.sessions)}
        if path == "/api/attention":
            return 200, {"ok": True, "items": list(self.attention)}
        if path == "/api/ux-fixes/health":
            return 200, {"ok": True, "queues": list(self.queues)}
        return 200, {"ok": True}

    def post(self, path, body=None):
        self.journal.append(("POST", path, body))
        if path == "/api/inject-input":
            self.inject_calls += 1
            return 200, dict(self.inject_response)
        if path == "/api/answer-question":
            self.answer_calls += 1
            return 200, dict(self.answer_response)
        return 200, {"ok": True}

    def call(self, method, path, body=None, query=None, timeout=None):
        if method == "GET":
            return self.get(path, query)
        return self.post(path, body)

    # -- journal helpers -----------------------------------------------------

    def count(self, method, path):
        return sum(1 for m, p, _ in self.journal if m == method and p == path)


# ---------------------------------------------------------------------------
# CloudFixture — the hosted relay, in-process
# ---------------------------------------------------------------------------

class CloudFixture:
    """The real ccc-cloud service on an ephemeral (or fixed) port with an
    isolated CCC_CLOUD_HOME. Exposes ``.store`` for direct event/audit
    assertions and window setters for offline/staleness scenarios."""

    def __init__(self, home=None, port=0, env="staging"):
        self.cloud = _import_cloud()
        self._own_home = home is None
        if home is None:
            home = tempfile.mkdtemp(prefix="ccc-cloud-home-")
        self.home = str(home)
        os.environ["CCC_CLOUD_HOME"] = self.home
        os.environ["CCC_CLOUD_ENV"] = env
        os.environ.pop("CCC_CLOUD_PORT", None)
        os.environ["CCC_CLOUD_BASE_URL"] = "http://127.0.0.1"
        self.config = self.cloud.config.load_config()
        self.app = self.cloud.app.build_app(self.config)
        self.httpd = self.cloud.app.make_server(self.app, "127.0.0.1", port)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.config.base_url = self.base
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        daemon=True)
        self._thread.start()

    @property
    def store(self):
        return self.app.store

    @property
    def outbox(self):
        return self.config.outbox_dir

    def client(self):
        return self.cloud.harness.Client(self.base)

    # -- window constants (offline / staleness) ------------------------------

    def set_online_window(self, seconds):
        self.cloud.H.DEVICE_ONLINE_WINDOW_S = seconds

    def set_snapshot_stale(self, seconds):
        self.cloud.relay.SNAPSHOT_STALE_S = seconds

    # -- direct DB reads (subprocess-safe would open the file; in-process uses
    #    the live store) ---------------------------------------------------

    def event_count(self, device_id, name):
        row = self.store.query_one(
            "SELECT COUNT(*) AS n FROM events WHERE device_ref=? AND name=?",
            (device_id, name))
        return row["n"] if row else 0

    def command_state(self, request_id):
        row = self.store.query_one(
            "SELECT state FROM commands WHERE id=?", (request_id,))
        return row["state"] if row else None

    def device_last_seen(self, device_id):
        row = self.store.query_one(
            "SELECT last_seen_at FROM devices WHERE id=?", (device_id,))
        return row["last_seen_at"] if row else None

    def stop(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            # Release the listening socket synchronously so a same-port restart
            # (Acceptance 19) can rebind immediately.
            self.httpd.server_close()
        except Exception:
            pass
        try:
            self.app.store.close()
        except Exception:
            pass

    def cleanup(self):
        self.stop()
        if self._own_home:
            shutil.rmtree(self.home, ignore_errors=True)


# ---------------------------------------------------------------------------
# Browser-side helpers (cookie + CSRF client from the cloud harness)
# ---------------------------------------------------------------------------

def sign_in_browser(cloud: CloudFixture, email: str):
    """Return an authenticated browser Client (cookie + CSRF)."""
    client, _obj = cloud.cloud.harness.sign_in(cloud, email)
    return client


def pair_start(browser) -> str:
    """Mint a single-use pair code via the signed-in browser."""
    status, obj = browser.request("POST", "/v1/pair/start",
                                  headers=browser.csrf_headers())
    assert status == 200, (status, obj)
    return obj["pair_code"]


# ---------------------------------------------------------------------------
# LocalCCCFixture — this repo's server.py as an isolated subprocess
# ---------------------------------------------------------------------------

class LocalCCCFixture:
    """Boots server.py with an isolated HOME (so ~/.claude/* lands in a temp
    dir), ephemeral loopback port, and synthetic session transcripts. Real
    session listing (`GET /api/sessions?all=1`) and real `/api/inject-input`."""

    def __init__(self, extra_env=None, cloud_disabled=True):
        self.base_dir = Path(tempfile.mkdtemp(prefix="ccc-local-")).resolve()
        self.home = self.base_dir / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.projects = self.home / ".claude" / "projects"
        self.projects.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.log_path = self.base_dir / "server.log"
        self.proc = None
        self._log_fh = None
        self._extra_env = dict(extra_env or {})
        self._cloud_disabled = cloud_disabled

    def start(self):
        env = {
            **os.environ,
            "HOME": str(self.home),
            "PORT": str(self.port),
            "CCC_EPHEMERAL": "1",
            "CCC_SKIP_SKILL_INSTALL": "1",
            "CCC_TELEMETRY_DISABLED": "1",
            "CCC_CHAT_ORCHESTRATOR": "builtin",
        }
        if self._cloud_disabled:
            env["CCC_CLOUD_DISABLED"] = "1"
        env.pop("CCC_SSH_HOST", None)
        # Do NOT leak the ccc-cloud CCC_CLOUD_* vars into the local server.
        for k in ("CCC_CLOUD_HOME", "CCC_CLOUD_ENV", "CCC_CLOUD_BASE_URL",
                  "CCC_CLOUD_PORT"):
            env.pop(k, None)
        env.update(self._extra_env)
        self._log_fh = open(self.log_path, "w")
        self.proc = subprocess.Popen(
            [sys.executable, str(WORKTREE_ROOT / "server.py")],
            cwd=str(WORKTREE_ROOT), env=env,
            stdout=self._log_fh, stderr=subprocess.STDOUT)
        self.wait_ready()
        return self

    def wait_ready(self, timeout=45.0):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(
                    f"local server exited rc={self.proc.returncode}: "
                    f"{self.log_tail()}")
            try:
                status, _ = self.request("GET", "/api/health")
                if status and status < 500:
                    return
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = e
            time.sleep(0.2)
        raise RuntimeError(f"local server not ready: {last}; {self.log_tail()}")

    def log_tail(self, lines=20):
        try:
            return "\n".join(self.log_path.read_text().splitlines()[-lines:])
        except OSError:
            return "<no log>"

    def request(self, method, path, body=None, headers=None, timeout=30.0):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", f"http://127.0.0.1:{self.port}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}

    # -- synthetic sessions --------------------------------------------------

    def add_session(self, session_id, text, cwd=None, when=None,
                    branch="main"):
        """Write a minimal but real Claude transcript under ~/.claude/projects
        so it is discovered by /api/sessions?all=1. Returns the jsonl path."""
        cwd = cwd or f"/tmp/ccc-e2e/{session_id}"
        enc = cwd.replace("/", "-")
        d = self.projects / enc
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{session_id}.jsonl"
        ts = when if when is not None else time.time()
        iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))
        events = [
            {"parentUuid": None, "isSidechain": False, "type": "user",
             "message": {"role": "user",
                         "content": [{"type": "text", "text": text}]},
             "uuid": f"u-{session_id[:8]}", "timestamp": iso,
             "userType": "external", "entrypoint": "sdk-cli", "cwd": cwd,
             "sessionId": session_id, "version": "2.1.119",
             "gitBranch": branch},
            {"parentUuid": f"u-{session_id[:8]}", "isSidechain": False,
             "message": {"model": "claude-opus-4-7", "role": "assistant",
                         "type": "message",
                         "content": [{"type": "text",
                                      "text": "On it — working now."}],
                         "stop_reason": "end_turn"},
             "type": "assistant", "uuid": f"a-{session_id[:8]}",
             "timestamp": iso, "userType": "external", "entrypoint": "sdk-cli",
             "cwd": cwd, "sessionId": session_id, "version": "2.1.119",
             "gitBranch": branch},
        ]
        with open(path, "w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")
        os.utime(path, (ts, ts))
        return path

    def list_sessions_all(self):
        status, obj = self.request("GET", "/api/sessions?all=1")
        assert status == 200, (status, obj)
        return obj.get("sessions") or obj.get("conversations") or []

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        try:
            self._log_fh.close()
        except Exception:
            pass

    def cleanup(self):
        self.stop()
        shutil.rmtree(self.base_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# DeviceFixture — cloud_relay.CloudRelayManager wired to cloud + local
# ---------------------------------------------------------------------------

class DeviceFixture:
    """Pairs a device against a CloudFixture and runs the real outbound loop.

    ``local_api`` is a FakeLocalAPI (disconnect/replay/edge scenarios) or None to
    use the real LocalAPI against ``local_port`` (a LocalCCCFixture). ``relay_url``
    is the endpoint the loop talks to — pass a TogglableProxy.base to simulate
    network loss without killing the client.
    """

    def __init__(self, cloud: CloudFixture, browser, *, local_api=None,
                 local_port=8090, relay_url=None, display="Test Mac",
                 state_dir=None, autostart=True, speed=True):
        self.cloud = cloud
        self.browser = browser
        self.cr = import_cloud_relay()
        if speed:
            speed_up_device(self.cr)
        self._own_state = state_dir is None
        if state_dir is None:
            state_dir = tempfile.mkdtemp(prefix="ccc-device-")
        self.state_dir = str(state_dir)
        self.local_api = local_api
        self.relay_url = (relay_url or cloud.base).rstrip("/")
        self.display = display
        self.local_port = local_port
        self.mgr = self.cr.CloudRelayManager(
            local_port, ccc_version="9.9.9", state_dir=self.state_dir)
        if local_api is not None:
            self.mgr.local_api = local_api
            self.mgr.executor.local_api = local_api
        self.device_id = None
        if autostart:
            self.pair_and_start()

    def pair_and_start(self, display=None):
        code = pair_start(self.browser)
        res = self.mgr.start_pairing(code, self.relay_url)
        assert res.get("ok"), f"start_pairing failed: {res}"
        # Pin the loop to the exact endpoint we chose (proxy or direct) rather
        # than the relay_base_url the cloud echoes back.
        self.mgr._pending["relay_url"] = self.relay_url
        res2 = self.mgr.confirm_pairing()
        assert res2.get("ok"), f"confirm_pairing failed: {res2}"
        self.device_id = self.mgr.config["device_id"]
        return self.device_id

    @property
    def executor(self):
        return self.mgr.executor

    @property
    def loop_state(self):
        return self.mgr.loop.state if self.mgr.loop else "off"

    def stop(self):
        try:
            self.mgr._stop_loop()
        except Exception:
            pass

    def restart_client(self):
        """Reconstruct the manager on the SAME state_dir (simulates a device
        process restart; idempotency.sqlite3 + credentials persist)."""
        self.stop()
        self.mgr = self.cr.CloudRelayManager(
            self.local_port, ccc_version="9.9.9", state_dir=self.state_dir)
        if self.local_api is not None:
            self.mgr.local_api = self.local_api
            self.mgr.executor.local_api = self.local_api
        self.mgr.maybe_start()
        return self.mgr

    def cleanup(self):
        self.stop()
        if self._own_state:
            shutil.rmtree(self.state_dir, ignore_errors=True)


def make_envelope(cr, *, request_id, seq, capability="session.send_input",
                  session_ref="sess-1", text="hello", expires_delta=300,
                  question_id=None):
    """Craft a §4.3 command envelope for direct executor drive-through."""
    now = time.time()
    payload = {"session_ref": session_ref, "text": text}
    if question_id is not None:
        payload["question_id"] = question_id
    return {
        "v": 1, "request_id": request_id, "capability": capability,
        "created": cr._iso_from_epoch(now),
        "expires": cr._iso_from_epoch(now + expires_delta),
        "seq": seq, "idempotency_key": request_id, "payload": payload,
        "requested_by": {"kind": "user", "ua_class": "mobile"},
    }
