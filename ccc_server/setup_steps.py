# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""First-run setup steps: what a fresh machine needs, detected and installed.

Backs `GET /api/setup/plan` (detection) and `POST /api/setup/run` (installs,
via ccc_server.setup_jobs). Every detect() is a cheap probe — file existence,
a `--version` call with a short timeout — and every install(job) streams
human lines into the job log so a first-time user always sees what is
happening and why.

Care taken for a true novice machine:

* On macOS, /usr/bin/git and /usr/bin/python3 are CLT shims — invoking them
  without Command Line Tools pops Apple's install dialog. Detection checks
  `xcode-select -p` first and never executes the shims when CLT is absent.
* Node installs download the official v22 LTS tarball from nodejs.org and
  verify it byte-for-byte against SHASUMS256.txt before extracting. No sudo;
  the runtime lives in ~/.ccc/runtime/node so a system Node is untouched.
* Steps owned by sibling features (free router, free key, first task) are
  reported here for a single plan but run in their own screens — they are
  flagged `external` so the run endpoint skips them politely.

Stdlib-only. Imports ccc_server.paths (a leaf) for CLI candidate dirs.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from ccc_server.paths import _iter_common_cli_candidates

NODE_MIN = (20, 18)
NODE_MAX_EXCLUSIVE = (25, 0)
NODE_DIST_BASE = os.environ.get(
    "CCC_NODE_DIST_BASE", "https://nodejs.org/dist"
).rstrip("/")
NODE_LTS_LINE = "latest-v22.x"
CLAUDE_INSTALL_URL = os.environ.get(
    "CCC_CLAUDE_INSTALL_URL", "https://claude.ai/install.sh"
)
FREE_ROUTER_STATE = "free-router.json"
FREE_ROUTER_PORT = 3017

_PLAN_TTL_S = 4.0
_PLAN_LOCK = threading.Lock()
_PLAN_CACHE = {"ts": 0.0, "data": None}

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


# ------------------------------------------------------------------ probes


def _run_capture(argv, timeout=8, env=None):
    """(rc, combined output) for a quick probe; never raises."""
    try:
        proc = subprocess.run(
            [str(a) for a in argv],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def _which(cmd):
    """Absolute path for cmd: PATH first, then well-known install dirs."""
    found = shutil.which(cmd)
    if found:
        return found
    try:
        for candidate in _iter_common_cli_candidates(cmd):
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
            except OSError:
                continue
    except Exception:
        pass
    return None


def _ccc_dir():
    return Path.home() / ".ccc"


def _runtime_node_bin():
    node = _ccc_dir() / "runtime" / "node" / "bin" / "node"
    return str(node) if node.is_file() and os.access(node, os.X_OK) else None


def _is_macos():
    return platform.system() == "Darwin"


def _parse_version(text):
    """First semver triple in text as (major, minor, patch), else None."""
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return tuple(int(g) for g in match.groups())


def _fmt_version(ver):
    return ".".join(str(p) for p in ver)


def _clt_present():
    if not _is_macos():
        return True
    rc, out = _run_capture(["xcode-select", "-p"], timeout=8)
    return rc == 0 and (out or "").strip() != ""


# ----------------------------------------------------------- detect: steps


def _detect_clt():
    if not _is_macos():
        return "ok", "Only needed on a Mac"
    if _clt_present():
        return "ok", "Installed"
    return "missing", "macOS needs these before it can build software"


def _detect_git():
    if _is_macos() and not _clt_present():
        # /usr/bin/git is a shim that triggers Apple's installer when CLT is
        # absent — don't invoke it, report through the CLT step instead.
        return "missing", "Comes free with the Mac tools above"
    git = _which("git")
    if not git:
        return "missing", "Keeps a history of every change"
    rc, out = _run_capture([git, "--version"], timeout=8)
    ver = _parse_version(out)
    if rc == 0 and ver:
        return "ok", f"v{_fmt_version(ver)}"
    return "missing", "Keeps a history of every change"


def _detect_python():
    ver = sys.version_info
    return "ok", f"v{ver.major}.{ver.minor}.{ver.micro} · running Command Center now"


def _node_versions_seen():
    """[(path, version)] for every node we can find, CCC runtime first."""
    candidates = []
    runtime = _runtime_node_bin()
    if runtime:
        candidates.append(runtime)
    for name in ("node",):
        found = _which(name)
        if found and found not in candidates:
            candidates.append(found)
    seen = []
    for path in candidates:
        rc, out = _run_capture([path, "--version"], timeout=8)
        ver = _parse_version(out)
        if rc == 0 and ver:
            seen.append((path, ver))
    return seen


def _node_supported(ver):
    return ver >= NODE_MIN and ver < NODE_MAX_EXCLUSIVE


def _detect_node():
    seen = _node_versions_seen()
    for _path, ver in seen:
        if _node_supported(ver):
            return "ok", f"v{_fmt_version(ver)}"
    if seen:
        _path, ver = seen[0]
        if ver < NODE_MIN:
            return "outdated", f"v{_fmt_version(ver)} is too old · needs v{_fmt_version(NODE_MIN)} or newer"
        return "outdated", f"v{_fmt_version(ver)} is newer than supported · needs under v{NODE_MAX_EXCLUSIVE[0]}"
    return "missing", "Runs your free AI tools"


def _claude_candidates():
    env_bin = (os.environ.get("CCC_CLAUDE_BIN") or "").strip()
    if env_bin:
        expanded = os.path.expanduser(env_bin)
        return [expanded] if (os.path.isfile(expanded) and os.access(expanded, os.X_OK)) else []
    found = []
    which_bin = _which("claude")
    if which_bin:
        found.append(which_bin)
    local = Path.home() / ".claude" / "local" / "claude"
    if local.is_file() and os.access(local, os.X_OK):
        found.append(str(local))
    return found


def _detect_claude_cli():
    for path in _claude_candidates():
        rc, out = _run_capture([path, "--version"], timeout=10)
        ver = _parse_version(out)
        if rc == 0 and ver:
            return "ok", f"v{_fmt_version(ver)}"
        if rc == 0 and out.strip():
            return "ok", out.strip().split("\n")[0][:60]
    return "missing", "Your AI coding partner"


def _detect_gh():
    gh = _which("gh")
    if not gh:
        return "missing", "Connects you to GitHub · handy, not required"
    rc, out = _run_capture([gh, "--version"], timeout=8)
    ver = _parse_version(out)
    if rc == 0 and ver:
        return "ok", f"v{_fmt_version(ver)}"
    return "ok", "Installed"


def _free_router_state():
    path = _ccc_dir() / FREE_ROUTER_STATE
    try:
        if path.is_file():
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    return None


def _free_router_status():
    """Live status from the free_router module when it exists in this build."""
    try:
        mod = importlib.import_module("ccc_server.free_router")
    except Exception:
        return None
    status_fn = getattr(mod, "status", None)
    if not callable(status_fn):
        return None
    try:
        result = status_fn()
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _port_listening(port, host="127.0.0.1", timeout=0.25):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _detect_free_router():
    live = _free_router_status()
    if live:
        if live.get("running") or live.get("healthy"):
            return "ok", "Running · free models are one click away"
        if live.get("installed"):
            return "missing", "Installed · ready to start"
    if _free_router_state() is not None:
        if _port_listening(FREE_ROUTER_PORT):
            return "ok", f"Running on port {FREE_ROUTER_PORT}"
        return "ok", "Installed · starts with its own button"
    return "missing", "Your ticket to AI models that cost $0"


def _detect_free_key():
    live = _free_router_status()
    if live:
        keys = live.get("keys") or []
        enabled = [k for k in keys if isinstance(k, dict) and k.get("enabled", True)]
        if enabled:
            return "ok", f"{len(enabled)} free key{'s' if len(enabled) != 1 else ''} saved"
        if live.get("installed") or live.get("running"):
            return "missing", "One free key unlocks the good models"
        return "missing", "Comes right after the free router"
    if _free_router_state() is not None:
        return "missing", "One free key unlocks the good models"
    return "missing", "Comes right after the free router"


def _detect_first_task():
    from ccc_server import first_task
    marker = _ccc_dir() / "first-task-done"
    try:
        if first_task._load_state().get("tasks_done") or marker.exists():
            return "ok", "Done · your agent already finished real work"
    except OSError:
        pass
    return "missing", "Watch your agent finish a tiny real project"


# ----------------------------------------------------------- install steps


def _install_clt(job):
    if not _is_macos():
        job.emit("Only needed on a Mac · skipping.")
        return False
    if _clt_present():
        job.emit("Apple's tools are already here.")
        return True
    rc = job.run_cmd(["xcode-select", "--install"], timeout_s=60)
    if _clt_present():
        job.emit("Apple's tools are already here.")
        return True
    if rc not in (0, 1):
        raise RuntimeError(
            "Couldn't open Apple's installer. Open System Settings → "
            "General → Software Update, or run 'xcode-select --install' "
            "yourself, then try again."
        )
    job.emit("A window from Apple just opened. Click Install, then Agree.")
    job.emit("We'll wait right here while it downloads · usually 5 to 10 minutes.")
    deadline = time.monotonic() + 45 * 60
    last_note = 0.0
    started = time.monotonic()
    while True:
        job.check_cancelled()
        if _clt_present():
            job.emit("Apple's tools are in. Nice.")
            job.set_step_progress(1.0)
            return True
        now = time.monotonic()
        if now > deadline:
            raise RuntimeError(
                "Apple's installer is still going. Finish the window it "
                "opened, then run this step again · we'll pick up from here."
            )
        job.set_step_progress(min(0.95, (now - started) / 600.0))
        if now - last_note > 30:
            last_note = now
            mins = int((now - started) // 60)
            job.emit(f"Still waiting on Apple's installer… ({mins} min)")
        time.sleep(5)


def _install_git(job):
    if _is_macos():
        if not _clt_present():
            job.emit("Git arrives with Apple's tools · getting those first.")
            _install_clt(job)
        git = _which("git")
        if git:
            rc, out = _run_capture([git, "--version"], timeout=8)
            if rc == 0:
                job.emit(out.strip().split("\n")[0] or "git is ready.")
                return True
        raise RuntimeError("Git didn't appear with Apple's tools · try this step again.")
    brew = _which("brew")
    if brew:
        rc = job.run_cmd([brew, "install", "git"], timeout_s=900)
        if rc == 0 and _which("git"):
            job.emit("Git is ready.")
            return True
        raise RuntimeError("Homebrew couldn't install Git · its log above has the details.")
    raise RuntimeError(
        "No automatic installer for Git on this system. Install it with your "
        "package manager (like 'sudo apt install git'), then run this step again."
    )


def _install_python(job):
    ver = sys.version_info
    job.emit(f"Python {ver.major}.{ver.minor}.{ver.micro} is already running Command Center.")
    return True


def _node_platform():
    """nodejs.org tarball platform token (e.g. darwin-arm64), or None."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        os_part = "darwin"
    elif system == "linux":
        os_part = "linux"
    else:
        return None
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "x64"
    else:
        return None
    return f"{os_part}-{arch}"


def _fetch_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "ccc-setup/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _resolve_node_tarball(plat, fetch=_fetch_text):
    """Pick the current v22 LTS tarball for plat from SHASUMS256.txt.

    Returns (version_str, filename, sha256). The sums file for the
    latest-v22.x line is authoritative for both the exact version and the
    expected digest, so nothing is hardcoded or stale.
    """
    sums = fetch(f"{NODE_DIST_BASE}/{NODE_LTS_LINE}/SHASUMS256.txt")
    token = f"-{plat}.tar.gz"
    for line in sums.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, name = parts
        if name.endswith(token) and name.startswith("node-v"):
            ver = _parse_version(name)
            if ver and ver[0] == 22:
                return _fmt_version(ver), name, sha
    raise RuntimeError(f"Couldn't find a Node.js v22 build for {plat}.")


def _verify_sha256(path, expected):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == (expected or "").lower()


def _install_node(job):
    plat = _node_platform()
    if not plat:
        raise RuntimeError(
            "Node.js can't be installed automatically on this system. "
            "Grab the LTS version from nodejs.org, then run this step again."
        )
    # A supported system Node means nothing to do.
    for path, ver in _node_versions_seen():
        if _node_supported(ver):
            job.emit(f"Node.js v{_fmt_version(ver)} is already here.")
            return True
    job.emit("Checking the official Node.js site for the right build…")
    ver_str, filename, sha = _resolve_node_tarball(plat)
    job.emit(f"Found Node.js v{ver_str} for your computer.")
    runtime = _ccc_dir() / "runtime"
    tarball = runtime / "downloads" / filename
    job.download(
        f"{NODE_DIST_BASE}/{NODE_LTS_LINE}/{filename}",
        tarball,
        label="Downloading Node.js",
    )
    job.progress_line("Checking the download is safe…")
    if not _verify_sha256(tarball, sha):
        try:
            tarball.unlink()
        except OSError:
            pass
        raise RuntimeError(
            "The Node.js download didn't match its checksum, so it was "
            "deleted. Run this step again to retry."
        )
    job.emit("Verified safe. Unpacking…")
    job.set_step_progress(0.92)
    target = runtime / f"node-v{ver_str}-{plat}"
    tmp_extract = runtime / f".extracting-{ver_str}"
    try:
        if tmp_extract.exists():
            shutil.rmtree(tmp_extract, ignore_errors=True)
        tmp_extract.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball, "r:gz") as tf:
            # filter="data" needs Python 3.12+; the tarball is sha256-verified
            # against SHASUMS256.txt anyway, so plain extraction is safe.
            if sys.version_info >= (3, 12):
                tf.extractall(tmp_extract, filter="data")
            else:
                tf.extractall(tmp_extract)
        inner = tmp_extract / filename[: -len(".tar.gz")]
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        inner.rename(target)
    finally:
        shutil.rmtree(tmp_extract, ignore_errors=True)
    link = runtime / "node"
    tmp_link = runtime / ".node-link"
    try:
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        tmp_link.symlink_to(target)
        tmp_link.replace(link)
    except OSError:
        # Filesystems without symlinks: fall back to a directory copy.
        if link.exists():
            shutil.rmtree(link, ignore_errors=True)
        shutil.copytree(target, link)
    node_bin = _runtime_node_bin()
    if not node_bin:
        raise RuntimeError("Node.js unpacked but its binary is missing · try again.")
    rc, out = _run_capture([node_bin, "--version"], timeout=10)
    if rc != 0:
        raise RuntimeError("Node.js was installed but won't run · try again.")
    job.emit(f"Node.js {out.strip()} is ready.")
    job.set_step_progress(1.0)
    try:
        tarball.unlink()
    except OSError:
        pass
    return True


def _install_claude_cli(job):
    for path in _claude_candidates():
        rc, out = _run_capture([path, "--version"], timeout=10)
        if rc == 0 and out.strip():
            job.emit(f"Claude Code is already here ({out.strip().split(chr(10))[0][:60]}).")
            return True
    tmpdir = Path(tempfile.mkdtemp(prefix="ccc-claude-install-"))
    script = tmpdir / "install.sh"
    try:
        job.download(CLAUDE_INSTALL_URL, script, label="Getting the Claude Code installer")
        rc = job.run_cmd(["bash", str(script)], timeout_s=900)
        if rc != 0:
            raise RuntimeError(
                "The Claude Code installer stopped. Try this step again · "
                "it picks up where it left off."
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    for path in _claude_candidates():
        rc, out = _run_capture([path, "--version"], timeout=10)
        if rc == 0 and out.strip():
            job.emit(f"Claude Code is ready ({out.strip().split(chr(10))[0][:60]}).")
            return True
    raise RuntimeError(
        "Claude Code installed but isn't on PATH yet. Open a fresh terminal "
        "once, then run this step again."
    )


def _install_gh(job):
    if _which("gh"):
        job.emit("GitHub CLI is already here.")
        return True
    brew = _which("brew")
    if not brew:
        job.emit("GitHub CLI is optional · you can add it later from cli.github.com.")
        return False
    rc = job.run_cmd([brew, "install", "gh"], timeout_s=900)
    if rc == 0 and _which("gh"):
        job.emit("GitHub CLI is ready.")
        return True
    raise RuntimeError("Homebrew couldn't install GitHub CLI · its log above has the details.")


# ---------------------------------------------------------------- registry

STEPS = [
    {
        "id": "clt",
        "label": "Mac tools from Apple",
        "why": "Apple's own tools · everything else builds on them",
        "est_seconds": 600,
        "needs_consent": True,
        "consent_label": "Apple shows its own install window",
        "detect": _detect_clt,
        "install": _install_clt,
        "mac_only": True,
    },
    {
        "id": "git",
        "label": "Git",
        "why": "Keeps a history of every change so nothing is lost",
        "est_seconds": 30,
        "needs_consent": True,
        "consent_label": "Installs with Apple's tools",
        "detect": _detect_git,
        "install": _install_git,
    },
    {
        "id": "python",
        "label": "Python",
        "why": "The language Command Center runs on",
        "est_seconds": 2,
        "needs_consent": False,
        "detect": _detect_python,
        "install": _install_python,
    },
    {
        "id": "node",
        "label": "Node.js",
        "why": "Runs your free AI tools",
        "est_seconds": 60,
        "needs_consent": True,
        "consent_label": "Downloads about 30 MB from nodejs.org",
        "detect": _detect_node,
        "install": _install_node,
    },
    {
        "id": "claude_cli",
        "label": "Claude Code",
        "why": "Your AI coding partner",
        "est_seconds": 90,
        "needs_consent": True,
        "consent_label": "Downloads the official installer from claude.ai",
        "detect": _detect_claude_cli,
        "install": _install_claude_cli,
    },
    {
        "id": "gh",
        "label": "GitHub helper",
        "why": "Connects you to GitHub · handy, not required",
        "est_seconds": 90,
        "needs_consent": True,
        "optional": True,
        "consent_label": "Installs through Homebrew when available",
        "detect": _detect_gh,
        "install": _install_gh,
    },
    {
        "id": "free_router",
        "label": "Free AI router",
        "why": "Connects you to AI models that cost $0",
        "est_seconds": 180,
        "needs_consent": True,
        "external": True,
        "external_hint": "Installed by the free-router setup",
        "detect": _detect_free_router,
    },
    {
        "id": "free_key",
        "label": "Free model key",
        "why": "One free key unlocks the best $0 models",
        "est_seconds": 120,
        "needs_consent": True,
        "external": True,
        "external_hint": "Added in the free-key step",
        "detect": _detect_free_key,
    },
    {
        "id": "first_task",
        "label": "Your first task",
        "why": "Watch your agent finish a tiny real project",
        "est_seconds": 180,
        "needs_consent": False,
        "external": True,
        "external_hint": "Runs as the finale of onboarding",
        "detect": _detect_first_task,
    },
]

_STEP_BY_ID = {s["id"]: s for s in STEPS}


def invalidate_plan_cache():
    with _PLAN_LOCK:
        _PLAN_CACHE["ts"] = 0.0
        _PLAN_CACHE["data"] = None


def _detect_step(step):
    try:
        status, detail = step["detect"]()
    except Exception as exc:
        status, detail = "error", f"Check failed: {exc}"
    return status, detail


def build_plan(force=False):
    """The full setup plan: every step with live status.

    Cached briefly — detection shells out to a handful of --version probes
    and the onboarding UI polls while installs run. `?refresh=1` forces a
    re-probe; a finished job also drops the cache.
    """
    now = time.monotonic()
    with _PLAN_LOCK:
        if (
            not force
            and _PLAN_CACHE["data"] is not None
            and now - _PLAN_CACHE["ts"] < _PLAN_TTL_S
        ):
            return dict(_PLAN_CACHE["data"])
    steps = []
    ready = 0
    pending_est = 0
    for step in STEPS:
        status, detail = _detect_step(step)
        entry = {
            "id": step["id"],
            "label": step["label"],
            "status": status,
            "detail": detail,
            "needs_consent": bool(step.get("needs_consent")),
            "est_seconds": int(step.get("est_seconds", 60)),
            "why": step.get("why", ""),
            "optional": bool(step.get("optional")),
            "external": bool(step.get("external")),
            "runnable": not step.get("external") and step.get("install") is not None,
        }
        if step.get("consent_label"):
            entry["consent_label"] = step["consent_label"]
        if step.get("mac_only") and not _is_macos():
            entry["applies"] = False
            entry["status"] = "ok"
            entry["detail"] = "Only needed on a Mac"
        if entry["external"] and entry["status"] != "ok" and step.get("external_hint"):
            # External steps report where they run instead of duplicating `why`.
            entry["detail"] = step["external_hint"]
        steps.append(entry)
        if entry["status"] == "ok":
            ready += 1
        else:
            pending_est += entry["est_seconds"]
    payload = {
        "steps": steps,
        "machine": {"os": platform.system(), "arch": platform.machine()},
        "summary": {
            "total": len(steps),
            "ready": ready,
            "pending": len(steps) - ready,
            "est_seconds": pending_est,
        },
    }
    with _PLAN_LOCK:
        _PLAN_CACHE["ts"] = time.monotonic()
        _PLAN_CACHE["data"] = payload
    return dict(payload)


__all__ = [
    "STEPS",
    "build_plan",
    "invalidate_plan_cache",
]
