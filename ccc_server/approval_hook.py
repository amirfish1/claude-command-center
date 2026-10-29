"""Device side of the cloud-approval hook handshake (stdlib only).

The CCC PermissionRequest hook (hooks/permission-request.py) parks while a
permission prompt is open and watches this directory for a decision. The cloud
relay executor writes the decision here after it re-checked the pending action.
Files:

  enabled                     touched by the relay loop on every push; the hook
                              only waits while this is fresh (relay not running
                              = the hook exits at once)
  waiting/<sid>.<sha16>.json  written by the hook while it waits; its mtime is
                              a heartbeat. <sha16> = first 16 hex of the
                              canonical-input hash the hook is about to run
  decisions/<sid>.<sha16>.json written by the relay: {sha256, decision,
                              created, expires}. Single use: the hook claims it
                              with an atomic rename.

The hash is the sha256 of the canonical tool input (relay_context.
canonical_input). The hook builds the same text from its own stdin, so a
decision applies only to the exact tool call Claude Code is about to run.
"""

from __future__ import annotations

import json
import os
import re
import time

DECISION_TTL_S = 120
ENABLED_FRESH_S = 90
WAITING_FRESH_S = 6
ACK_WAIT_S = 4.0
_SID_RE = re.compile(r"[A-Za-z0-9_\-]{1,100}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")


def base_dir():
    override = os.environ.get("CCC_APPROVAL_HOOK_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "command-center",
                        "cloud-approvals")


def _name(session_id, sha):
    return f"{session_id}.{sha[:16]}.json"


def _valid(session_id, sha):
    return bool(_SID_RE.fullmatch(str(session_id or ""))
                and _SHA_RE.fullmatch(str(sha or "")))


def _write_atomic(path, doc):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, path)


def touch_enabled():
    """Called by the relay loop each push. Never raises."""
    try:
        path = os.path.join(base_dir(), "enabled")
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def waiting(session_id, sha, now=None):
    """True when a hook for exactly this tool call is parked right now."""
    if not _valid(session_id, sha):
        return False
    path = os.path.join(base_dir(), "waiting", _name(session_id, sha))
    try:
        if (now or time.time()) - os.stat(path).st_mtime > WAITING_FRESH_S:
            return False
        with open(path) as f:
            return json.load(f).get("sha256") == sha
    except (OSError, ValueError, AttributeError):
        return False


def post_decision(session_id, sha, decision, now=None):
    """Write a single-use decision for the hook parked on (session, sha)."""
    if not _valid(session_id, sha) or decision not in ("allow", "deny"):
        raise ValueError("invalid decision")
    now = now or time.time()
    _write_atomic(os.path.join(base_dir(), "decisions", _name(session_id, sha)),
                  {"sha256": sha, "decision": decision, "created": now,
                   "expires": now + DECISION_TTL_S})


def withdraw(session_id, sha):
    """Take back a decision. True = nobody consumed it (safe to fall back);
    False = the hook already claimed it (it WILL be applied)."""
    try:
        os.unlink(os.path.join(base_dir(), "decisions", _name(session_id, sha)))
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def deliver(session_id, sha, decision, ack_s=None, sleep=time.sleep):
    """Post a decision and wait briefly for the hook to claim it. Returns
    "applied" or "unclaimed" (decision withdrawn; caller may fall back)."""
    post_decision(session_id, sha, decision)
    path = os.path.join(base_dir(), "decisions", _name(session_id, sha))
    deadline = time.time() + (ACK_WAIT_S if ack_s is None else ack_s)
    while time.time() < deadline:
        if not os.path.exists(path):
            return "applied"
        sleep(0.1)
    return "unclaimed" if withdraw(session_id, sha) else "applied"
