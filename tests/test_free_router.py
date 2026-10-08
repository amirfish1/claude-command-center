"""Tests for ccc_server.free_router — the managed freellmapi lifecycle.

The fakes here are real processes, not module mocks: a `node`/`npm` pair of
executable scripts, a minimal freellmapi source tree, and a fake router that
is a real HTTP server speaking the freellmapi API surface (/api/ping, /livez,
/readyz, /api/auth/login, /api/settings/api-key, /api/health, /api/keys,
/v1/messages). Install exercises copy, config files, child-process spawn and
the unified-key handshake end to end.
"""
import json
import os
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ccc_server import free_router


# ---------------------------------------------------------------------------
# Fake router — a real loopback HTTP server standing in for freellmapi.
# ---------------------------------------------------------------------------

FAKE_ROUTER_SOURCE = r'''
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "3017"))
CONFIG_PATH = os.environ.get("FREEAPI_CONFIG_PATH", "")
KEYS = []
SESSIONS = set()
UNIFIED = "ccc-unified-testkey-0001"


def _admin_creds():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        a = cfg.get("admin") or {}
        return a.get("email"), a.get("password")
    except Exception:
        return None, None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _authed(self):
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        return tok in SESSIONS

    def do_GET(self):
        p = self.path.split("?", 1)[0].rstrip("/") or "/"
        if p == "/api/ping":
            return self._json(200, {"status": "ok"})
        if p == "/livez":
            return self._json(200, {"status": "ok", "version": "9.9.9-test", "uptime_s": 1})
        if p == "/readyz":
            if any(k.get("enabled") for k in KEYS):
                return self._json(200, {"status": "ok", "ready_upstreams": 1})
            return self._json(503, {"status": "unavailable", "reason": "no_upstreams_configured"})
        if p == "/api/settings/api-key":
            if not self._authed():
                return self._json(401, {"error": {"message": "auth"}})
            return self._json(200, {"apiKey": UNIFIED})
        if p == "/api/health":
            if not self._authed():
                return self._json(401, {"error": {"message": "auth"}})
            return self._json(200, {"keys": list(KEYS), "platforms": []})
        return self._json(404, {"error": {"message": "nf"}})

    def do_POST(self):
        p = self.path.split("?", 1)[0].rstrip("/") or "/"
        if p == "/api/auth/login":
            body = self._body()
            email, pw = _admin_creds()
            if body.get("email") == email and body.get("password") == pw:
                SESSIONS.add("sess-" + str(len(SESSIONS)))
                return self._json(200, {"token": sorted(SESSIONS)[-1], "email": email})
            return self._json(401, {"error": {"message": "bad login"}})
        if p == "/api/keys":
            if not self._authed():
                return self._json(401, {"error": {"message": "auth"}})
            body = self._body()
            rec = {"id": len(KEYS) + 1, "platform": body.get("platform"),
                   "label": body.get("label") or "", "enabled": True,
                   "status": "healthy"}
            KEYS.append(rec)
            return self._json(200, rec)
        if p == "/v1/messages":
            tok = (self.headers.get("x-api-key")
                   or (self.headers.get("Authorization") or "").replace("Bearer ", ""))
            if tok != UNIFIED:
                return self._json(401, {"error": {"message": "bad key"}})
            return self._json(200, {"id": "msg_test", "type": "message",
                                    "role": "assistant", "content": [
                                        {"type": "text", "text": "hello from $0"}],
                                    "model": "fake-free-model"})
        return self._json(404, {"error": {"message": "nf"}})


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
'''

# A fake `node`: answers --version, expands --env-file into the environment,
# then execs the script argument with the current interpreter (the "JS" file
# is a python file inside the fake source tree).
FAKE_NODE = '''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args and args[0] == "--version":
    print("v22.10.0")
    sys.exit(0)
rest = []
for a in args:
    if a.startswith("--env-file"):
        p = a.split("=", 1)[1] if "=" in a else None
        if p and os.path.isfile(p):
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k] = v
    else:
        rest.append(a)
if not rest:
    sys.exit(0)
os.execvp(sys.executable, [sys.executable] + rest)
'''

# A fake `npm`: `run build` materializes server/dist/index.js (the fake
# router); `ci` is a no-op success, like a warm npm cache.
FAKE_NPM = '''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args[:2] == ["run", "build"]:
    dist = os.path.join("server", "dist")
    os.makedirs(dist, exist_ok=True)
    with open(os.path.join(dist, "index.js"), "w") as f:
        f.write(os.environ["FAKE_ROUTER_SRC"])
    print("built fake router")
sys.exit(0)
'''


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _write_exe(path: Path, text: str):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated ~/.ccc, fake node/npm on a temp PATH, temp free port."""
    home = tmp_path / "ccc-home"
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    _write_exe(fakebin / "node", FAKE_NODE)
    _write_exe(fakebin / "npm", FAKE_NPM)
    src = tmp_path / "freesrc"
    (src / "server" / "src").mkdir(parents=True)
    (src / "package.json").write_text(json.dumps(
        {"name": "freellmapi-fake", "workspaces": ["server"]}))
    (src / "package-lock.json").write_text(json.dumps({
        "packages": {"x": {"resolved": "https://registry.npmmirror.com/x/-/x-1.0.0.tgz",
                           "integrity": "sha512-fake"}}}))
    (src / "server" / "package.json").write_text(json.dumps(
        {"name": "@freellmapi/server", "version": "0.2.1"}))
    port = _free_port()
    monkeypatch.setenv("CCC_FREE_ROUTER_HOME", str(home))
    monkeypatch.setenv("CCC_FREE_ROUTER_PORT", str(port))
    monkeypatch.setenv("CCC_FREELLMAPI_SRC", str(src))
    monkeypatch.setenv("CCC_FREE_ROUTER_SUPERVISOR", "child")
    monkeypatch.setenv("CCC_FREE_ROUTER_NODE", str(fakebin / "node"))
    monkeypatch.setenv("FAKE_ROUTER_SRC", FAKE_ROUTER_SOURCE)
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(free_router, "_START_WAIT_S", 30)
    free_router._NODE_CACHE.update({"at": 0.0, "result": None})
    free_router._STATUS_CACHE.update({"at": 0.0, "data": None})
    yield {"home": home, "bin": fakebin, "src": src, "port": port}
    # never leave a spawned fake router behind
    free_router.stop()


@pytest.fixture
def handler():
    class H:
        def __init__(self):
            self.calls = []
        def send_json(self, data, status=200):
            self.calls.append((status, data))
        @property
        def last(self):
            return self.calls[-1] if self.calls else (None, None)
    return H()


# ---------------------------------------------------------------------------
# Unit-level: paths, node detection, state file
# ---------------------------------------------------------------------------

def test_status_missing(env):
    st = free_router.status()
    assert st["installed"] is False
    assert st["running"] is False
    assert st["healthy"] is False
    assert st["state"] == "missing"
    assert st["unified_key"] is False
    assert st["keys"] == []
    assert st["port"] == env["port"]
    assert st["node_ok"] is True  # fake node is on PATH
    assert st["node_version"] == "22.10.0"


def test_find_node_range(env):
    node, ver = free_router.find_node()
    assert node.endswith("/node")
    assert ver == (22, 10, 0)


def test_state_file_is_0600(env):
    free_router._update_state(admin_email="x@x.local", admin_password="pw123456")
    mode = stat.S_IMODE(free_router.state_path().stat().st_mode)
    assert mode == 0o600
    assert free_router._load_state()["admin_email"] == "x@x.local"


def test_spawn_env_empty_when_not_installed(env):
    assert free_router.spawn_env() == {}


# ---------------------------------------------------------------------------
# Handlers (stub handler, no server needed)
# ---------------------------------------------------------------------------

def test_handle_get_status(env, handler):
    assert free_router.handle_api_get(handler, "/api/free-router/status") is True
    code, data = handler.last
    assert code == 200 and data["state"] == "missing"


def test_handle_get_unknown_falls_through(env, handler):
    assert free_router.handle_api_get(handler, "/api/free-router/providers") is False
    assert handler.calls == []


def test_handle_post_unknown_falls_through(env, handler):
    assert free_router.handle_api_post(handler, "/api/free-router/keys") is False


def test_handle_get_missing_job(env, handler):
    assert free_router.handle_api_get(handler, "/api/free-router/jobs/nope") is True
    code, data = handler.last
    assert code == 404


# ---------------------------------------------------------------------------
# Full lifecycle — real subprocesses, real HTTP, no module-level mocks
# ---------------------------------------------------------------------------

def test_install_lifecycle(env, handler):
    # kick off install through the POST handler, like the UI does
    assert free_router.handle_api_post(handler, "/api/free-router/install") is True
    code, data = handler.last
    assert code == 200 and data.get("job_id")
    job_id = data["job_id"]

    deadline = time.time() + 120
    job = None
    while time.time() < deadline:
        job = free_router.get_job(job_id)
        if job and job["status"] != "running":
            break
        time.sleep(0.5)
    assert job is not None, "install job never appeared"
    assert job["status"] == "done", f"install failed: {job['error']}\n" + "\n".join(job["lines"][-15:])
    assert job["progress"] == 1.0

    # install artifacts — including the npm-12-safe lockfile normalization
    lock = json.loads((free_router.install_dir() / "package-lock.json").read_text())
    assert "registry.npmjs.org" in lock["packages"]["x"]["resolved"]
    assert free_router.installed()
    env_file = free_router.install_dir() / ".env"
    assert env_file.is_file()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    text = env_file.read_text()
    assert f"PORT={env['port']}" in text and "ENCRYPTION_KEY=" in text
    cfg = json.loads(free_router.config_path().read_text())
    assert cfg["admin"]["email"] == free_router.ADMIN_EMAIL
    assert stat.S_IMODE(free_router.config_path().stat().st_mode) == 0o600
    state = free_router._load_state()
    assert state["unified_key"] == "ccc-unified-testkey-0001"
    assert state["version"] == "9.9.9-test"

    # a second install request returns a fresh job (the finished one does
    # not count as active) and completes as an idempotent reinstall that
    # restarts the router on the new build
    assert free_router.handle_api_post(handler, "/api/free-router/install") is True
    code2, data2 = handler.last
    assert code2 == 200 and data2.get("job_id")
    deadline = time.time() + 120
    second = None
    while time.time() < deadline:
        second = free_router.get_job(data2["job_id"])
        if second and second["status"] != "running":
            break
        time.sleep(0.5)
    assert second is not None
    assert second["status"] == "done", f"reinstall failed: {second['error']}"

    # status now reports a live, keyless-but-ready-to-configure router
    deadline = time.time() + 15
    while time.time() < deadline:
        free_router.invalidate_status_cache()
        st = free_router.status()
        if st["running"] and st["healthy"]:
            break
        time.sleep(0.5)
    assert st["running"] is True
    assert st["healthy"] is True
    assert st["version"] == "9.9.9-test"
    assert st["unified_key"] is True
    assert st["base_url"] == f"http://127.0.0.1:{env['port']}"
    assert st["state"] in ("needs_key", "ready")

    # no provider keys yet → spawn_env is {} (router cannot serve)
    assert free_router.spawn_env() == {}

    # add a keyless provider, then a $0 child env appears
    ok, err = free_router.add_platform_key("kilo")
    assert ok, err
    free_router.invalidate_status_cache()
    st = free_router.status()
    assert st["keys"] == [{"platform": "kilo", "label": "", "enabled": True}]
    assert st["ready"] is True
    assert st["state"] == "ready"

    env_dict = free_router.spawn_env()
    assert env_dict["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:{env['port']}"
    assert env_dict["ANTHROPIC_AUTH_TOKEN"] == "ccc-unified-testkey-0001"
    assert "ANTHROPIC_MODEL" not in env_dict
    env_named = free_router.spawn_env(model="fake-free-model")
    assert env_named["ANTHROPIC_MODEL"] == "fake-free-model"

    # the env really authenticates against the router's Anthropic surface
    req = urllib.request.Request(
        env_dict["ANTHROPIC_BASE_URL"] + "/v1/messages",
        data=json.dumps({"model": "fake-free-model", "max_tokens": 8,
                         "messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers={"x-api-key": env_dict["ANTHROPIC_AUTH_TOKEN"],
                 "anthropic-version": "2023-06-01",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read())
    assert payload["content"][0]["text"] == "hello from $0"

    # stop / start through the HTTP handlers
    assert free_router.handle_api_post(handler, "/api/free-router/stop") is True
    code, data = handler.last
    assert data["ok"] is True
    deadline = time.time() + 10
    while free_router._ping() and time.time() < deadline:
        time.sleep(0.4)
    assert free_router._ping() is False

    assert free_router.handle_api_post(handler, "/api/free-router/start") is True
    code, data = handler.last
    assert code == 200 and data["ok"] is True
    assert free_router._ping() is True


def test_install_delegates_to_setup_jobs(env, handler, monkeypatch):
    """With L02's runner present, install lands in the shared registry so
    /api/setup/jobs/<id> answers for it, and a second POST returns the same
    job instead of starting a duplicate install."""
    try:
        from ccc_server import setup_jobs
    except Exception:
        pytest.skip("setup_jobs not present in this tree")
    gate = threading.Event()
    real_build = free_router._step_build
    def slow_build(log):
        gate.wait(20)
        real_build(log)
    monkeypatch.setattr(free_router, "_step_build", slow_build)
    try:
        assert free_router.handle_api_post(handler, "/api/free-router/install") is True
        _code, data = handler.last
        jid = data["job_id"]
        assert not data.get("already_running")
        # the shared runner owns and describes this job
        desc = setup_jobs.describe_job(jid)
        assert desc is not None and desc["status"] == "running"
        assert desc["kind"] == "install"
        # my jobs endpoint resolves it too
        mine = free_router.get_job(jid)
        assert mine is not None and mine["job_id"] == jid
        # double-install guard returns the same job
        assert free_router.handle_api_post(handler, "/api/free-router/install") is True
        _c2, data2 = handler.last
        assert data2["job_id"] == jid and data2.get("already_running") is True
    finally:
        gate.set()
    deadline = time.time() + 120
    job = None
    while time.time() < deadline:
        job = free_router.get_job(jid)
        if job and job["status"] != "running":
            break
        time.sleep(0.5)
    assert job is not None and job["status"] == "done", job and job.get("error")


def test_install_refuses_foreign_dir(env, handler):
    # a non-CCC directory at the install path must never be deleted
    dest = free_router.install_dir()
    dest.mkdir(parents=True)
    (dest / "precious.txt").write_text("do not touch")
    lines = []
    with pytest.raises(RuntimeError, match="not installed by CCC"):
        free_router._copy_source(Path(env["src"]), lines.append)


def test_uninstall_only_managed(env, handler):
    dest = free_router.install_dir()
    dest.mkdir(parents=True)
    (dest / "precious.txt").write_text("do not touch")
    res = free_router.uninstall()
    assert res["ok"] is False
    assert dest.exists()  # still there


def test_unmarked_upstream_clone_is_adopted(env, handler):
    # a leftover clone of the upstream repo inside ~/.ccc (e.g. a partial
    # install killed before the marker was written) is provably CCC's own
    # work — adopt and remove it instead of refusing forever
    dest = free_router.install_dir()
    dest.mkdir(parents=True)
    subprocess.run(["git", "init", str(dest)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(dest), "remote", "add", "origin",
                    "https://github.com/tashfeenahmed/freellmapi.git"],
                   check=True, capture_output=True)
    (dest / "partial.txt").write_text("stale")
    assert free_router._looks_like_our_clone(dest)
    res = free_router.uninstall()
    assert res["ok"] is True
    assert not dest.exists()


def test_wrong_remote_is_not_adopted(env, handler):
    # a clone of some other repo at the install path is foreign — never delete
    dest = free_router.install_dir()
    dest.mkdir(parents=True)
    subprocess.run(["git", "init", str(dest)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(dest), "remote", "add", "origin",
                    "https://github.com/example/unrelated.git"],
                   check=True, capture_output=True)
    (dest / "precious.txt").write_text("do not touch")
    assert not free_router._looks_like_our_clone(dest)
    res = free_router.uninstall()
    assert res["ok"] is False
    assert dest.exists()


def test_admin_login_uses_config_creds(env, handler):
    free_router._update_state(admin_email="nobody@nowhere.local",
                              admin_password="wrong")
    # no router running → login fails cleanly, not an exception
    assert free_router._admin_login(free_router._load_state()) is None


@pytest.mark.parametrize("forced", ["", "launchd"])
def test_isolated_home_never_uses_shared_launchd(tmp_path, monkeypatch, forced):
    monkeypatch.setattr(free_router.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv("CCC_FREE_ROUTER_HOME", raising=False)
    monkeypatch.setenv("CCC_FREE_ROUTER_SUPERVISOR", forced)
    assert free_router._supervisor_kind() == "child"


def test_router_state_override_never_uses_shared_launchd(tmp_path, monkeypatch):
    import pwd
    native_home = pwd.getpwuid(os.getuid()).pw_dir
    monkeypatch.setattr(free_router.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("HOME", native_home)
    monkeypatch.setenv("CCC_FREE_ROUTER_HOME", str(tmp_path / "router-state"))
    monkeypatch.setenv("CCC_FREE_ROUTER_SUPERVISOR", "launchd")
    assert free_router._supervisor_kind() == "child"


def test_normal_home_keeps_existing_launch_agent(monkeypatch):
    import pwd
    native_home = pwd.getpwuid(os.getuid()).pw_dir
    monkeypatch.setattr(free_router.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("HOME", native_home)
    monkeypatch.delenv("CCC_FREE_ROUTER_HOME", raising=False)
    monkeypatch.delenv("CCC_FREE_ROUTER_SUPERVISOR", raising=False)
    assert free_router._supervisor_kind() == "launchd"
    assert free_router._plist_path().name == free_router.LAUNCH_AGENT_LABEL + ".plist"
