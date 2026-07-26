"""Extracted from server.py (originally lines 64215-64431).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import os
import shlex
import signal
import subprocess
import threading
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# In-UI terminal — one-shot subprocess runner with cwd tracking.
#
# SECURITY: this is the most powerful endpoint in CCC. /api/term/run executes
# arbitrary shell as the user with no permission prompt — strictly more
# capable than /api/inject-input (which goes through Claude). It is gated
# only by _check_same_origin. Do NOT enable network bind without a trusted
# network. See SECURITY.md.
# ---------------------------------------------------------------------------

_TERM_STATES = {}  # repo_path -> {cwd, popen, pgid}
_TERM_LOCK = threading.Lock()


def _term_state(repo_path):
    repo_path = _core.resolve_repo_path(repo_path)
    return _TERM_STATES.setdefault(repo_path, {"cwd": Path(repo_path), "popen": None, "pgid": None})


def _term_cwd(repo_path):
    """Current terminal cwd for one concrete repo."""
    state = _term_state(repo_path)
    cwd = state["cwd"]
    if cwd is None or not Path(cwd).is_dir():
        state["cwd"] = Path(repo_path)
        cwd = Path(repo_path)
    return Path(cwd)


def _term_rel(repo_path):
    """cwd as a path relative to repo_path, or "" if cwd == repo_path."""
    try:
        rel = str(_term_cwd(repo_path).relative_to(Path(repo_path)))
        return "" if rel == "." else rel
    except ValueError:
        return ""


def _term_resolve_cwd_change(repo_path, target):
    """Resolve a `cd <target>` against the current cwd, clamped to repo_path.

    Returns the new Path, or raises ValueError with a user-facing message.
    Empty target resets to repo_path.
    """
    if not target or target == "~":
        return Path(repo_path)
    if target == "-":
        # `cd -` would need a previous-cwd memory; we don't keep one.
        raise ValueError("cd - is not supported in the in-UI terminal")
    base = _term_cwd(repo_path)
    raw = Path(target)
    candidate = (raw if raw.is_absolute() else (base / raw)).resolve()
    try:
        candidate.relative_to(Path(repo_path).resolve())
    except ValueError:
        raise ValueError(
            f"refusing to cd outside repo ({repo_path}): {candidate}"
        )
    if not candidate.is_dir():
        raise ValueError(f"not a directory: {candidate}")
    return candidate


_KIMI_SETUP_STATUS_MEMO = {"ts": 0.0, "data": None}
_KIMI_SETUP_DOCS = {
    "membership": "https://www.kimi.com/code/docs/en/kimi-code/membership.html",
    "third_party_setup": "https://www.kimi.com/code/docs/en/third-party-tools/other-coding-agents.html",
}


def _kimi_cli_version(bin_path):
    """`kimi --version`, first line only, bounded — never raises."""
    try:
        proc = subprocess.run(
            [bin_path, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        line = (proc.stdout or "").strip().splitlines()
        return (line[0].strip() if line else "") or None
    except (OSError, subprocess.SubprocessError):
        return None


def _kimi_setup_status():
    """Kimi CLI setup snapshot for the 'Add Kimi engine' guided flow:
    installed? where from? which version? Memoized briefly — bin probing
    hits PATH + the managed install dir on every call otherwise."""
    now = time.time()
    memo = _core._KIMI_SETUP_STATUS_MEMO
    if memo["data"] is not None and now - memo["ts"] < 60.0:
        return dict(memo["data"])
    resolved = _core._acp_resolve_bin("kimi")
    data = {
        "ok": True,
        "installed": bool(resolved.get("available")),
        "bin": resolved.get("bin"),
        "source": resolved.get("source"),
        "reason": resolved.get("reason"),
        "model": _core._spawn_model_for_engine("kimi"),
        "docs": dict(_KIMI_SETUP_DOCS),
    }
    if data["installed"]:
        data["version"] = _core._kimi_cli_version(resolved["bin"])
    memo["ts"] = now
    memo["data"] = dict(data)
    return data


def _kimi_setup_verify():
    """Proof the Kimi setup works end-to-end: one ACP session/new roundtrip
    (no prompt, so no tokens). Returns the spawn-kimi result shape."""
    routed = _core._control_plane_engine_call(
        "kimi", "verify", {},
        idempotency_key=_core._take_control_plane_action_id(),
    )
    if routed is not None:
        return routed
    resolved = _core._acp_resolve_bin("kimi")
    if not resolved.get("available"):
        return {"ok": False, "error": resolved.get("reason") or "kimi CLI not found"}
    result = _core._acp_session_new("kimi", os.getcwd())
    if result.get("ok"):
        result["verified"] = True
        result["version"] = _core._kimi_cli_version(resolved["bin"])
    return result


def _term_split_leading_cd(cmd):
    """If `cmd` begins with `cd <path>` (alone or followed by `&&`),
    return (target, remainder). Otherwise (None, cmd).

    Recognises:
      cd foo
      cd foo && rest
      cd "foo bar" && rest
      cd
    Does NOT recognise `cd` embedded inside a complex line (`for d in
    *; do cd $d; done`); those run as a normal subprocess.
    """
    stripped = cmd.lstrip()
    if not stripped.startswith("cd"):
        return None, cmd
    after = stripped[2:]
    if after and after[0] not in (" ", "\t", "&", ";"):
        # `cdwhatever` — not a cd at all.
        return None, cmd
    after = after.lstrip()
    if not after or after.startswith(("&&", ";")):
        # `cd` with no args (optionally followed by && rest)
        rest = after
        if rest.startswith("&&"):
            rest = rest[2:].lstrip()
        elif rest.startswith(";"):
            rest = rest[1:].lstrip()
        return "", rest
    # Use shlex to peel the first token off, respecting quotes.
    try:
        lex = shlex.shlex(after, posix=True)
        lex.whitespace_split = True
        lex.commenters = ""
        target = next(lex, None)
    except ValueError as e:
        raise ValueError(f"could not parse cd target: {e}")
    if target is None:
        return "", ""
    # Find where the target ends in the original string so we can keep
    # the remainder verbatim (preserving quoting, &&, etc.).
    consumed = lex.instream.tell() if hasattr(lex.instream, "tell") else None
    if consumed is None:
        # Fallback: re-find the target in the source.
        idx = after.find(target) + len(target)
    else:
        idx = consumed
    rest = after[idx:].lstrip()
    if rest.startswith("&&"):
        rest = rest[2:].lstrip()
    elif rest.startswith(";"):
        rest = rest[1:].lstrip()
    elif rest:
        # `cd foo bar` — extra args we don't understand. Treat as not a
        # leading cd; let bash error on it.
        return None, cmd
    return target, rest


def _term_kill_running(state):
    """Kill the currently running terminal subprocess, if any. Returns True
    if something was killed. Caller must hold _TERM_LOCK or accept races."""
    popen = state.get("popen")
    pgid = state.get("pgid")
    if not popen or popen.poll() is not None:
        return False
    try:
        if pgid:
            os.killpg(pgid, signal.SIGTERM)
        else:
            popen.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        return False
    # Give it 2s to wind down; then SIGKILL the group.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if popen.poll() is not None:
            return True
        time.sleep(0.05)
    try:
        if pgid:
            os.killpg(pgid, signal.SIGKILL)
        else:
            popen.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    return True




