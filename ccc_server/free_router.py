# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Free-model router lifecycle: CCC's managed freellmapi install.

CCC runs a private freellmapi (MIT, github.com/tashfeenahmed/freellmapi)
on loopback so agents can run on $0 provider tiers. This module owns the
whole lifecycle and nothing else:

    install  - fetch sources (git clone at a pinned rev, or a local copy via
               CCC_FREELLMAPI_SRC), `npm ci`, `npm run build`, write `.env`
               plus the declarative admin config, then start the service and
               mint the unified API key.
    start/stop - LaunchAgent on macOS (`com.github.claude-command-center.
               free-router`), a detached child process everywhere else.
    status   - installed / running / healthy / keys for the UI.
    spawn_env(model=None) -> dict — the ANTHROPIC_* environment a $0 child
               process gets, or {} when the router cannot serve right now.
               Never route a Claude subscription OAuth token through this:
               the env is per-child only and carries the router's own
               unified key, so a child never sees account credentials.

Layout under ~/.ccc (CCC_FREE_ROUTER_HOME overrides the base for tests):

    freellmapi/             managed checkout (marker: .ccc-router-managed)
    free-router.json        state file, 0600 — port, admin creds, unified key
    free-router.config.json declarative admin config (FREEAPI_CONFIG_PATH)
    logs/                   router stdout/stderr
    free-router.pid         pidfile for the child-process supervisor

freellmapi surfaces this module consumes: GET /api/ping (liveness),
GET /livez + /readyz (health), POST /api/auth/login (admin session),
GET /api/settings/api-key (unified key), GET /api/health (key list),
POST /api/keys (add provider key), POST /v1/messages (Anthropic wire).

Stdlib only. No imports from server — this module is handler-agnostic so it
also runs from tests, the worker, and `python3 -m ccc_server.free_router`.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

REPO_URL = "https://github.com/tashfeenahmed/freellmapi.git"
PINNED_REV = "d3f6f9b9fd65a1e7a11943e17376cfaa1b7a6173"
DEFAULT_PORT = 3017
LAUNCH_AGENT_LABEL = "com.github.claude-command-center.free-router"
MANAGED_MARKER = ".ccc-router-managed"
NODE_MIN = (20, 18, 0)
NODE_MAX_EXCLUSIVE = (25, 0, 0)
ADMIN_EMAIL = "ccc-admin@ccc.local"
_STATE_BASENAME = "free-router.json"
_CONFIG_BASENAME = "free-router.config.json"
_PID_BASENAME = "free-router.pid"
_INSTALL_DIRNAME = "freellmapi"
_STATUS_TTL_S = 2.5
_NODE_CACHE_TTL_S = 60.0
_PROBE_TIMEOUT_S = 1.5
_HTTP_TIMEOUT_S = 8.0
_START_WAIT_S = float(os.environ.get("CCC_FREE_ROUTER_START_TIMEOUT_S", "120"))
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".env", ".env.*", "node_modules", "dist", "release",
    ".turbo", ".next", "coverage", "*.log",
)


# ---------------------------------------------------------------------------
# Paths — all resolved at call time so tests can point CCC_FREE_ROUTER_HOME
# at a tmp dir.
# ---------------------------------------------------------------------------

def _base_dir() -> Path:
    override = os.environ.get("CCC_FREE_ROUTER_HOME", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / ".ccc"


def install_dir() -> Path:
    return _base_dir() / _INSTALL_DIRNAME


def state_path() -> Path:
    return _base_dir() / _STATE_BASENAME


def config_path() -> Path:
    return _base_dir() / _CONFIG_BASENAME


def pid_path() -> Path:
    return _base_dir() / _PID_BASENAME


def log_dir() -> Path:
    return _base_dir() / "logs"


def _runtime_node() -> Path:
    # Where the setup installer drops the managed Node tarball (L02).
    return _base_dir() / "runtime" / "node" / "bin" / "node"


def router_port(state: dict | None = None) -> int:
    env = os.environ.get("CCC_FREE_ROUTER_PORT", "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    if state is None:
        state = _load_state()
    try:
        return int(state.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def base_url() -> str:
    return f"http://127.0.0.1:{router_port()}"


# ---------------------------------------------------------------------------
# State file — 0600, holds admin credentials and the unified key. Never log
# the secret fields; _mask() is the only way they may appear in job lines.
# ---------------------------------------------------------------------------

def _mask(secret: str) -> str:
    s = str(secret or "")
    return f"…{s[-4:]}" if len(s) >= 4 else "…"


def _load_state() -> dict:
    try:
        raw = state_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=1, sort_keys=True))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _update_state(**fields) -> dict:
    st = _load_state()
    st.update(fields)
    _save_state(st)
    return st


# ---------------------------------------------------------------------------
# Node.js — freellmapi needs >=20.18 <25 (server/package.json engines).
# Preference: explicit env, the managed runtime, then PATH.
# ---------------------------------------------------------------------------

_NODE_CACHE = {"at": 0.0, "result": None}
_NODE_CACHE_LOCK = threading.Lock()


def _parse_node_version(text: str):
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def _node_version(node_path: str):
    try:
        out = subprocess.run(
            [node_path, "--version"],
            capture_output=True, text=True, timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return _parse_node_version(out.stdout.strip() or out.stderr.strip())


def _version_in_range(ver) -> bool:
    if not ver:
        return False
    return NODE_MIN <= ver < NODE_MAX_EXCLUSIVE


def find_node():
    """Return (node_path, version_tuple) for a usable Node, else (None, None)."""
    now = time.time()
    with _NODE_CACHE_LOCK:
        if _NODE_CACHE["result"] is not None and now - _NODE_CACHE["at"] < _NODE_CACHE_TTL_S:
            return _NODE_CACHE["result"]
        if _NODE_CACHE["result"] is None and now - _NODE_CACHE["at"] < 15:
            return (None, None)
    result = _find_node_uncached()
    with _NODE_CACHE_LOCK:
        _NODE_CACHE["at"] = now
        _NODE_CACHE["result"] = result
    return result


def _find_node_uncached():
    candidates = []
    env_node = os.environ.get("CCC_FREE_ROUTER_NODE", "").strip()
    if env_node:
        candidates.append(os.path.expanduser(env_node))
    candidates.append(str(_runtime_node()))
    on_path = shutil.which("node")
    if on_path:
        candidates.append(on_path)
    seen = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if not os.path.isfile(cand) or not os.access(cand, os.X_OK):
            continue
        ver = _node_version(cand)
        if _version_in_range(ver):
            return (cand, ver)
    return (None, None)


def _find_npm(node_path: str | None):
    if node_path:
        sibling = Path(node_path).resolve().parent / "npm"
        if sibling.is_file():
            return str(sibling)
    return shutil.which("npm")


def _node_env(node_path: str) -> dict:
    env = dict(os.environ)
    node_bin = str(Path(node_path).resolve().parent)
    env["PATH"] = node_bin + os.pathsep + env.get("PATH", "")
    return env


# ---------------------------------------------------------------------------
# HTTP client for the router's own API — stdlib urllib, loopback only.
# ---------------------------------------------------------------------------

def _request(method: str, path: str, body=None, token: str | None = None,
             timeout: float = _HTTP_TIMEOUT_S, port: int | None = None):
    """Return (http_status, parsed_json_or_None, error_str_or_None)."""
    url = f"http://127.0.0.1:{port or router_port()}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(512 * 1024)
            try:
                return resp.status, json.loads(raw.decode("utf-8", "replace")), None
            except ValueError:
                return resp.status, None, None
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(256 * 1024)
            parsed = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, OSError):
            parsed = None
        return e.code, parsed, None
    except (urllib.error.URLError, OSError) as e:
        return 0, None, str(e)


def _ping(timeout: float = _PROBE_TIMEOUT_S) -> bool:
    code, data, err = _request("GET", "/api/ping", timeout=timeout)
    return code == 200 and isinstance(data, dict) and data.get("status") == "ok"


def _livez(timeout: float = _PROBE_TIMEOUT_S):
    code, data, err = _request("GET", "/livez", timeout=timeout)
    if code == 200 and isinstance(data, dict):
        return data
    return None


def _readyz(timeout: float = _PROBE_TIMEOUT_S):
    code, data, err = _request("GET", "/readyz", timeout=timeout)
    if code == 200 and isinstance(data, dict):
        return data
    if isinstance(data, dict):
        return data  # 503 still carries {status, reason} — useful detail
    return None


def running() -> bool:
    return _ping()


def _admin_login(state: dict):
    """Fresh admin session token, or None. Updates the state file on success."""
    email = state.get("admin_email") or ""
    password = state.get("admin_password") or ""
    if not email or not password:
        return None
    code, data, err = _request(
        "POST", "/api/auth/login",
        body={"email": email, "password": password}, timeout=_HTTP_TIMEOUT_S,
    )
    if code == 200 and isinstance(data, dict) and data.get("token"):
        _update_state(admin_token=data["token"])
        return data["token"]
    return None


def _admin_request(method: str, path: str, body=None, timeout=_HTTP_TIMEOUT_S):
    """One admin-authed request with a single re-login retry on 401.

    Returns (http_status, parsed_json, error). Session tokens are cached in
    the state file so polling never piles up login rows in the router DB.
    """
    state = _load_state()
    token = state.get("admin_token") or _admin_login(state)
    if not token:
        return 0, None, "no_admin_credentials"
    code, data, err = _request(method, path, body=body, token=token, timeout=timeout)
    if code == 401:
        token = _admin_login(_load_state())
        if not token:
            return code, data, err
        code, data, err = _request(method, path, body=body, token=token, timeout=timeout)
    return code, data, err


def list_keys():
    """Key metadata from GET /api/health — never key material. [] on failure."""
    code, data, err = _admin_request("GET", "/api/health", timeout=4.0)
    if code != 200 or not isinstance(data, dict):
        return []
    out = []
    for k in data.get("keys") or []:
        if not isinstance(k, dict):
            continue
        out.append({
            "platform": k.get("platform"),
            "label": k.get("label") or "",
            "enabled": bool(k.get("enabled")),
            "status": k.get("status") or "unknown",
        })
    return out


def add_platform_key(platform: str, key: str | None = None,
                     label: str | None = None):
    """Add one provider key through the admin API.

    Keyless providers (kilo) pass key=None — the router stores its anonymous
    sentinel. Returns (ok, error_or_None). The raw key is sent to the router
    and never stored, logged, or echoed by this module.
    """
    platform = (platform or "").strip()
    if not platform:
        return False, "missing platform"
    body = {"platform": platform}
    if key:
        body["key"] = key
    if label:
        body["label"] = label
    code, data, err = _admin_request("POST", "/api/keys", body=body, timeout=15.0)
    if code in (200, 201):
        return True, None
    detail = ""
    if isinstance(data, dict):
        detail = (data.get("error") or {}).get("message") or ""
    return False, detail or err or f"router returned HTTP {code}"


def set_anthropic_map(mapping: dict):
    """PUT /api/settings/anthropic-map — L05 pins the top free model here."""
    if not isinstance(mapping, dict) or not mapping:
        return False, "empty mapping"
    code, data, err = _admin_request(
        "PUT", "/api/settings/anthropic-map", body=mapping, timeout=10.0)
    if code == 200:
        return True, None
    detail = ""
    if isinstance(data, dict):
        detail = (data.get("error") or {}).get("message") or ""
    return False, detail or err or f"router returned HTTP {code}"


def fetch_unified_key():
    code, data, err = _admin_request("GET", "/api/settings/api-key", timeout=6.0)
    if code == 200 and isinstance(data, dict) and data.get("apiKey"):
        return data["apiKey"]
    return None


# ---------------------------------------------------------------------------
# Install pipeline — each step is (id, label, fn(log)) and logs plain-words
# progress a novice can follow.
# ---------------------------------------------------------------------------

def _run(cmd, cwd=None, env=None, log=None, timeout=1800):
    """Run a command, streaming combined output to `log`. Returns exit code."""
    if log:
        log("$ " + " ".join(str(c) for c in cmd))
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
        )
    except OSError as e:
        if log:
            log(f"could not start {cmd[0]}: {e}")
        return 127
    tail = []
    deadline = time.time() + timeout
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            if log:
                log(line)
            if time.time() > deadline:
                proc.kill()
                if log:
                    log("step timed out")
                return 124
        proc.wait(timeout=max(1, int(deadline - time.time())))
    except (subprocess.SubprocessError, OSError):
        proc.kill()
        return 124
    return proc.returncode or 0


def _mark_managed(rev: str) -> None:
    try:
        (install_dir() / MANAGED_MARKER).write_text(
            json.dumps({"pinned_rev": rev, "managed_by": "ccc"}), encoding="utf-8")
    except OSError:
        pass


def _is_managed() -> bool:
    return (install_dir() / MANAGED_MARKER).is_file()


def _installed_rev() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(install_dir()), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _pkg_version() -> str:
    try:
        pkg = install_dir() / "server" / "package.json"
        data = json.loads(pkg.read_text(encoding="utf-8"))
        return str(data.get("version") or "")
    except (OSError, ValueError):
        return ""


def installed() -> bool:
    return (install_dir() / "server" / "dist" / "index.js").is_file()


def _copy_source(src: Path, log) -> None:
    dest = install_dir()
    if dest.exists():
        if _is_managed():
            shutil.rmtree(dest)
        elif not any(dest.iterdir()):
            pass  # empty dir is safe to fill
        else:
            raise RuntimeError(
                f"{dest} already exists and was not installed by CCC. "
                "Move it aside or remove it, then try again."
            )
    shutil.copytree(src, dest, ignore=_COPY_IGNORE)
    _mark_managed(PINNED_REV)


def _clone_source(log) -> None:
    dest = install_dir()
    if dest.exists():
        if _is_managed() and (dest / ".git").is_dir():
            rc = _run(["git", "-C", str(dest), "checkout", "--detach", PINNED_REV],
                      log=log, timeout=120)
            if rc == 0:
                return
            log("checkout failed, fetching a fresh copy")
            shutil.rmtree(dest)
        elif _is_managed():
            shutil.rmtree(dest)
        elif not any(dest.iterdir()):
            pass
        else:
            raise RuntimeError(
                f"{dest} already exists and was not installed by CCC. "
                "Move it aside or remove it, then try again."
            )
    rc = _run(["git", "clone", REPO_URL, str(dest)], log=log, timeout=900)
    if rc != 0:
        raise RuntimeError("git clone failed — check your internet connection and try again")
    rc = _run(["git", "-C", str(dest), "checkout", "--detach", PINNED_REV],
              log=log, timeout=120)
    if rc != 0:
        raise RuntimeError("could not check out the pinned router version")


def _normalize_lockfiles(log) -> None:
    """Rewrite mirror-registry tarball URLs in package-lock.json to npmjs.

    npm 12's `allow-remote=none` default refuses "remote" tarballs — any
    `resolved` URL whose host is not the configured registry. The upstream
    lock was generated behind a mirror (registry.npmmirror.com), so a stock
    `npm ci` fails with EALLOWREMOTE on a novice's machine. Every entry is
    still pinned by its sha512 integrity hash, so changing the host is a
    content-preserving normalization, not a dependency change.
    """
    for lock in install_dir().glob("package-lock.json"):
        try:
            text = lock.read_text(encoding="utf-8")
        except OSError:
            continue
        fixed = re.sub(
            r"https://registry\.npmmirror\.com/",
            "https://registry.npmjs.org/",
            text,
        )
        if fixed != text:
            try:
                lock.write_text(fixed, encoding="utf-8")
                log(f"normalized registry hosts in {lock.name}")
            except OSError:
                pass


def _step_check(log) -> None:
    node, ver = find_node()
    if not node:
        raise RuntimeError(
            "Node.js 20.18 or newer is needed for the free engine and was not "
            "found. Run CCC's setup steps first — it can install Node for you."
        )
    npm = _find_npm(node)
    if not npm:
        raise RuntimeError("Node.js was found but npm is missing — reinstall Node.js")
    _update_state(node_path=node)
    log(f"Node.js v{'.'.join(map(str, ver))} found at {node}")
    log(f"npm found at {npm}")


def _step_source(log) -> None:
    src = os.environ.get("CCC_FREELLMAPI_SRC", "").strip()
    _base_dir().mkdir(parents=True, exist_ok=True)
    if src:
        src_path = Path(os.path.expanduser(src))
        if not (src_path / "package.json").is_file():
            raise RuntimeError(f"CCC_FREELLMAPI_SRC does not look like freellmapi: {src_path}")
        log(f"copying freellmapi from {src_path}")
        _copy_source(src_path, log)
        _update_state(pinned_rev=PINNED_REV, source=str(src_path))
    else:
        if not shutil.which("git"):
            raise RuntimeError("git is needed to download the free engine")
        log(f"downloading freellmapi (pinned {PINNED_REV[:8]})")
        _clone_source(log)
        _update_state(pinned_rev=_installed_rev() or PINNED_REV)
    _normalize_lockfiles(log)


def _step_deps(log) -> None:
    node, _ver = find_node()
    npm = _find_npm(node)
    rc = _run([npm, "ci", "--no-audit", "--no-fund"], cwd=install_dir(),
              env=_node_env(node), log=log, timeout=1800)
    if rc != 0:
        raise RuntimeError("npm ci failed — see the log above for the first error")


def _step_build(log) -> None:
    node, _ver = find_node()
    npm = _find_npm(node)
    rc = _run([npm, "run", "build"], cwd=install_dir(),
              env=_node_env(node), log=log, timeout=1800)
    if rc != 0 or not installed():
        raise RuntimeError("the build did not finish — see the log above")


def _step_config(log) -> None:
    env_file = install_dir() / ".env"
    port = router_port()
    state = _load_state()
    encryption_key = state.get("encryption_key") or secrets.token_hex(32)
    admin_email = state.get("admin_email") or ADMIN_EMAIL
    admin_password = state.get("admin_password") or secrets.token_urlsafe(24)
    env_lines = [
        "HOST=127.0.0.1",
        f"PORT={port}",
        f"ENCRYPTION_KEY={encryption_key}",
        f"FREEAPI_CONFIG_PATH={config_path()}",
        "",
    ]
    fd = os.open(str(env_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(env_lines))
    cfg = {"admin": {"email": admin_email, "password": admin_password}}
    fd = os.open(str(config_path()), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, indent=1))
    _update_state(
        port=port,
        admin_email=admin_email,
        admin_password=admin_password,
        encryption_key=encryption_key,
    )
    log("loopback-only config written; admin account ready")


def _step_start(log) -> None:
    # A reinstall must cycle the process — the new build only takes effect
    # after a restart. First install: _ping() is False and stop() is a no-op.
    if _ping():
        log("restarting on the new build")
        stop(log)
    result = start(wait=True, log=log)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "the free engine did not start")


def _step_unified_key(log) -> None:
    deadline = time.time() + 60
    key = None
    while time.time() < deadline:
        key = fetch_unified_key()
        if key:
            break
        time.sleep(1.5)
    if not key:
        raise RuntimeError("the router is up but did not hand out a unified key")
    version = (_livez() or {}).get("version") or _pkg_version()
    _update_state(unified_key=key, version=version,
                  installed_at=datetime.now(timezone.utc).isoformat())
    log(f"your free key is ready ({_mask(key)})")


def install_steps():
    """Ordered (id, label, fn) list — the same list any job runner can drive."""
    return [
        ("check", "Checking Node.js", _step_check),
        ("source", "Getting the free engine", _step_source),
        ("deps", "Installing pieces (npm ci)", _step_deps),
        ("build", "Building the engine", _step_build),
        ("config", "Locking it down", _step_config),
        ("start", "Starting it up", _step_start),
        ("key", "Creating your free key", _step_unified_key),
    ]


# ---------------------------------------------------------------------------
# Job runner — one background thread per job, ring buffer of log lines,
# shape identical to the setup-jobs contract so the UI can poll it the same
# way. If ccc_server.setup_jobs lands with a compatible runner, install can
# delegate through _submit_job without changing the HTTP contract.
# ---------------------------------------------------------------------------

_JOB_MAX_LINES = 400
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


class _Job:
    def __init__(self, kind: str):
        self.id = f"{kind}-{uuid.uuid4().hex[:12]}"
        self.kind = kind
        self.status = "running"
        self.step = ""
        self.progress = 0.0
        self.lines: list = []
        self.error = None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at = None

    def log(self, line: str) -> None:
        line = str(line).rstrip("\n")
        if not line:
            return
        with _JOBS_LOCK:
            self.lines.append(line)
            if len(self.lines) > _JOB_MAX_LINES:
                del self.lines[: len(self.lines) - _JOB_MAX_LINES]

    def as_dict(self) -> dict:
        with _JOBS_LOCK:
            lines = list(self.lines[-200:])
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "step": self.step,
            "progress": self.progress,
            "lines": lines,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _job_worker(job: _Job, steps) -> None:
    try:
        total = len(steps)
        for idx, (sid, label, fn) in enumerate(steps):
            job.step = label
            job.progress = round(idx / max(total, 1), 3)
            job.log(f"— {label} —")
            try:
                fn(job.log)
            except Exception as e:  # surface a plain-words failure to the UI
                job.status = "error"
                job.error = str(e)[:400]
                job.finished_at = datetime.now(timezone.utc).isoformat()
                job.log(f"failed: {e}")
                return
        job.progress = 1.0
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        job.log("done — your free engine is ready")
    except Exception as e:  # last-resort guard; a job thread must never die silently
        job.status = "error"
        job.error = str(e)[:400]
        job.finished_at = datetime.now(timezone.utc).isoformat()


def _active_job(kind: str):
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job.kind == kind and job.status == "running":
                return job
    return None


def _submit_job(kind: str, steps) -> str:
    """Start a job and return its id.

    When L02's ccc_server.setup_jobs is present and exposes a compatible
    `run_steps(kind, steps)` entry point, the job lands in the shared
    registry and /api/setup/jobs/<id> answers for it. Otherwise an internal
    thread registry serves /api/free-router/jobs/<id> with the same shape.
    """
    try:
        from ccc_server import setup_jobs as _sj  # type: ignore
    except Exception:
        _sj = None
    if _sj is not None:
        runner = getattr(_sj, "run_steps", None)
        if callable(runner):
            try:
                jid = runner(kind, steps)
                if isinstance(jid, str) and jid:
                    return jid
            except TypeError:
                pass  # signature mismatch — keep the internal runner
    job = _Job(kind)
    with _JOBS_LOCK:
        _JOBS[job.id] = job
        # Bound the registry: drop finished jobs older than a day.
        cutoff = time.time() - 86400
        for jid, old in list(_JOBS.items()):
            if old.status != "running" and old.finished_at:
                try:
                    ts = datetime.fromisoformat(old.finished_at).timestamp()
                except ValueError:
                    ts = 0
                if ts < cutoff:
                    _JOBS.pop(jid, None)
    threading.Thread(target=_job_worker, args=(job, steps), daemon=True).start()
    return job.id


def get_job(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        return job.as_dict()
    try:
        from ccc_server import setup_jobs as _sj  # type: ignore
        getter = getattr(_sj, "get_job", None) if _sj is not None else None
        if callable(getter):
            data = getter(job_id)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Supervisor — LaunchAgent on macOS, detached child elsewhere. `external`
# means "the process is managed outside this module" (tests, dev).
# ---------------------------------------------------------------------------

def _supervisor_kind() -> str:
    forced = os.environ.get("CCC_FREE_ROUTER_SUPERVISOR", "").strip().lower()
    if forced in ("launchd", "child", "external", "none"):
        return forced
    if platform.system() == "Darwin":
        return "launchd"
    return "child"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def _gui_target() -> str:
    return f"gui/{os.getuid()}"


def _write_plist(node_path: str) -> Path:
    log_dir().mkdir(parents=True, exist_ok=True)
    plist = _plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    env_file = install_dir() / ".env"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{_xml_escape(LAUNCH_AGENT_LABEL)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{_xml_escape(node_path)}</string>
    <string>--env-file={_xml_escape(str(env_file))}</string>
    <string>server/dist/index.js</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{_xml_escape(str(install_dir()))}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>StandardOutPath</key>
  <string>{_xml_escape(str(log_dir() / "free-router.out.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{_xml_escape(str(log_dir() / "free-router.err.log"))}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>FREEAPI_ENV_PATH</key>
    <string>{_xml_escape(str(env_file))}</string>
    <key>FREEAPI_CONFIG_PATH</key>
    <string>{_xml_escape(str(config_path()))}</string>
    <key>PATH</key>
    <string>{_xml_escape(str(Path(node_path).resolve().parent) + ":/usr/bin:/bin:/usr/local/bin")}</string>
  </dict>
</dict>
</plist>
"""
    plist.write_text(xml, encoding="utf-8")
    return plist


def _launchd_loaded() -> bool:
    try:
        out = subprocess.run(
            ["launchctl", "print", f"{_gui_target()}/{LAUNCH_AGENT_LABEL}"],
            capture_output=True, text=True, timeout=8,
        )
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _start_launchd(node_path: str, log) -> None:
    plist = _write_plist(node_path)
    if _launchd_loaded():
        _run(["launchctl", "kickstart", "-k",
              f"{_gui_target()}/{LAUNCH_AGENT_LABEL}"], log=log, timeout=30)
        return
    rc = _run(["launchctl", "bootstrap", _gui_target(), str(plist)],
              log=log, timeout=30)
    if rc != 0:
        # bootstrap exits non-zero when the job is already loaded; kickstart
        # covers a stale registration from an earlier install.
        _run(["launchctl", "kickstart", "-k",
              f"{_gui_target()}/{LAUNCH_AGENT_LABEL}"], log=log, timeout=30)


def _start_child(node_path: str, log) -> None:
    env_file = install_dir() / ".env"
    log_dir().mkdir(parents=True, exist_ok=True)
    env = _node_env(node_path)
    env["FREEAPI_ENV_PATH"] = str(env_file)
    env["FREEAPI_CONFIG_PATH"] = str(config_path())
    out = open(log_dir() / "free-router.out.log", "ab", buffering=0)
    proc = subprocess.Popen(
        [node_path, f"--env-file={env_file}", "server/dist/index.js"],
        cwd=str(install_dir()),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_path().write_text(str(proc.pid), encoding="utf-8")
    if log:
        log(f"started router process (pid {proc.pid})")


def _stop_child(log=None) -> bool:
    try:
        pid = int(pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        try:
            pid_path().unlink(missing_ok=True)
        except OSError:
            pass
        return False
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.3)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        pid_path().unlink(missing_ok=True)
    except OSError:
        pass
    if log:
        log(f"stopped router process (pid {pid})")
    return True


def _wait_healthy(timeout_s: float, log=None) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _livez():
            return True
        time.sleep(0.75)
    if log:
        tail = _tail_logs(6)
        for line in tail:
            log("router log: " + line)
    return False


def start(wait: bool = True, log=None) -> dict:
    """Start the router. {ok, supervisor, error?}."""
    if _ping():
        return {"ok": True, "supervisor": _supervisor_kind(), "already_running": True}
    if not installed():
        return {"ok": False, "error": "not installed — run install first"}
    kind = _supervisor_kind()
    if kind in ("external", "none"):
        ok = _wait_healthy(_START_WAIT_S if wait else 0, log)
        return {"ok": ok, "supervisor": kind,
                "error": None if ok else "router not reachable on " + base_url()}
    node, _ver = find_node()
    if not node:
        return {"ok": False, "error": "Node.js 20.18+ not found"}
    try:
        if kind == "launchd":
            _start_launchd(node, log)
        else:
            _start_child(node, log)
    except Exception as e:
        return {"ok": False, "supervisor": kind, "error": str(e)[:300]}
    _update_state(supervisor=kind)
    if wait and not _wait_healthy(_START_WAIT_S, log):
        return {"ok": False, "supervisor": kind,
                "error": "router did not become healthy — see logs"}
    return {"ok": True, "supervisor": kind}


def stop(log=None) -> dict:
    kind = _supervisor_kind()
    stopped = False
    if kind == "launchd" and _launchd_loaded():
        _run(["launchctl", "bootout", f"{_gui_target()}/{LAUNCH_AGENT_LABEL}"],
             log=log, timeout=30)
        stopped = True
    if _stop_child(log):
        stopped = True
    # Whatever was running may linger briefly; give the port a moment.
    deadline = time.time() + 5
    while _ping() and time.time() < deadline:
        time.sleep(0.4)
    return {"ok": True, "stopped": stopped, "running": _ping()}


def uninstall(log=None) -> dict:
    """Stop and remove the managed install. Refuses to delete a directory
    that lacks the CCC marker — never delete what we did not create."""
    stop(log)
    removed = False
    dest = install_dir()
    if dest.exists():
        if not _is_managed():
            return {"ok": False,
                    "error": f"{dest} was not installed by CCC — not touching it"}
        shutil.rmtree(dest)
        removed = True
    for p in (state_path(), config_path(), pid_path()):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    if log:
        log("free engine removed")
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------------------
# Status + spawn_env — the two contracts the rest of the build consumes.
# ---------------------------------------------------------------------------

_STATUS_CACHE = {"at": 0.0, "data": None}
_STATUS_LOCK = threading.Lock()


def status() -> dict:
    now = time.time()
    with _STATUS_LOCK:
        if _STATUS_CACHE["data"] is not None and now - _STATUS_CACHE["at"] < _STATUS_TTL_S:
            return dict(_STATUS_CACHE["data"])
    st = _load_state()
    node, node_ver = find_node()
    ping = _ping()
    live = _livez() if ping else None
    ready = _readyz() if ping else None
    healthy = bool(live and live.get("status") == "ok")
    keys = list_keys() if healthy and st.get("admin_email") else []
    ready_ok = bool(ready and ready.get("status") == "ok")
    version = ""
    if live:
        version = str(live.get("version") or "")
    if not version:
        version = st.get("version") or _pkg_version()
    inst = installed() or _is_managed()
    if not inst:
        state = "missing"
    elif not ping:
        state = "stopped"
    elif not healthy:
        state = "starting"
    elif not st.get("unified_key"):
        state = "needs_setup"
    elif not keys:
        state = "needs_key"
    elif not ready_ok:
        state = "degraded"
    else:
        state = "ready"
    data = {
        "installed": inst,
        "running": ping,
        "healthy": healthy,
        "port": router_port(st),
        "version": version,
        "node_ok": node is not None,
        "node_version": ".".join(map(str, node_ver)) if node_ver else None,
        "keys": [{"platform": k["platform"], "label": k["label"],
                  "enabled": k["enabled"]} for k in keys],
        "key_status": keys,
        "unified_key": bool(st.get("unified_key")),
        "base_url": base_url() if (inst or ping) else None,
        "ready": ready_ok and bool(st.get("unified_key")),
        "ready_reason": (ready or {}).get("reason"),
        "supervisor": _supervisor_kind(),
        "state": state,
        "install_dir": str(install_dir()) if inst else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with _STATUS_LOCK:
        _STATUS_CACHE["at"] = now
        _STATUS_CACHE["data"] = dict(data)
    return data


def invalidate_status_cache() -> None:
    with _STATUS_LOCK:
        _STATUS_CACHE["at"] = 0.0
        _STATUS_CACHE["data"] = None


def spawn_env(model: str | None = None) -> dict:
    """Environment for a $0 child process. {} when the router can't serve.

    Ready means: the router answers liveness, readiness says at least one
    upstream can serve, and we hold the unified key. Anything less returns
    {} so a caller (L04's "runtime": "free") can degrade explicitly instead
    of spawning a run that fails upstream. The returned env is complete —
    ANTHROPIC_BASE_URL points at loopback and ANTHROPIC_AUTH_TOKEN is the
    router's own unified key, never an Anthropic credential.
    """
    st = _load_state()
    key = st.get("unified_key") or ""
    if not key or not _ping():
        return {}
    live = _livez()
    if not live:
        return {}
    ready = _readyz()
    if not ready or ready.get("status") != "ok":
        return {}
    env = {
        "ANTHROPIC_BASE_URL": base_url(),
        "ANTHROPIC_AUTH_TOKEN": key,
    }
    chosen = (model or st.get("default_model") or "").strip()
    if chosen:
        env["ANTHROPIC_MODEL"] = chosen
    return env


def _tail_logs(n: int = 12):
    out = []
    for name in ("free-router.err.log", "free-router.out.log"):
        p = log_dir() / name
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            out.extend(f"{name}: {l}" for l in lines[-n:])
        except OSError:
            continue
    return out[-n:]


# ---------------------------------------------------------------------------
# HTTP handlers — called from server.py's dispatch; handler only needs
# send_json(payload, status=200).
# ---------------------------------------------------------------------------

_JOB_PATH_RE = re.compile(r"^/api/free-router/jobs/([A-Za-z0-9._-]+)$")


def handle_api_get(handler, path: str) -> bool:
    if path in ("/api/free-router", "/api/free-router/status"):
        handler.send_json(status())
        return True
    if path == "/api/free-router/spawn-ready":
        env = spawn_env()
        handler.send_json({
            "ready": bool(env),
            "base_url": env.get("ANTHROPIC_BASE_URL"),
            "model": env.get("ANTHROPIC_MODEL"),
        })
        return True
    m = _JOB_PATH_RE.match(path)
    if m:
        job = get_job(m.group(1))
        if job is None:
            handler.send_json({"error": "job not found"}, 404)
        else:
            handler.send_json(job)
        return True
    if path == "/api/free-router/logs":
        handler.send_json({"lines": _tail_logs(80)})
        return True
    return False


def handle_api_post(handler, path: str) -> bool:
    if path == "/api/free-router/install":
        active = _active_job("install")
        if active is not None:
            handler.send_json({"job_id": active.id, "already_running": True})
            return True
        job_id = _submit_job("install", install_steps())
        handler.send_json({"job_id": job_id})
        return True
    if path == "/api/free-router/start":
        result = start(wait=True)
        invalidate_status_cache()
        handler.send_json(result, 200 if result.get("ok") else 409)
        return True
    if path == "/api/free-router/stop":
        result = stop()
        invalidate_status_cache()
        handler.send_json(result)
        return True
    if path == "/api/free-router/uninstall":
        result = uninstall()
        invalidate_status_cache()
        handler.send_json(result, 200 if result.get("ok") else 409)
        return True
    return False


def _main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=1, sort_keys=True))
    elif cmd == "spawn-env":
        print(json.dumps(spawn_env(), indent=1, sort_keys=True))
    elif cmd == "install":
        job_id = _submit_job("install", install_steps())
        print(f"job {job_id}")
        while True:
            job = get_job(job_id)
            if job and job["status"] != "running":
                print(json.dumps(job, indent=1))
                return 0 if job["status"] == "done" else 1
            time.sleep(1)
    elif cmd == "start":
        print(json.dumps(start()))
    elif cmd == "stop":
        print(json.dumps(stop()))
    else:
        print("usage: python3 -m ccc_server.free_router [status|spawn-env|install|start|stop]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
