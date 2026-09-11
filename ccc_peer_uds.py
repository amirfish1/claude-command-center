"""Claude Code peer-mesh (UDS) client helpers. Stdlib only, no server imports.

Claude Code 2.1.224+ binds one Unix socket per session and publishes it in
~/.claude/sessions/<pid>.json (messagingSocketPath, peerProtocol, version).
A sender connects, optionally authenticates with the session's peerToken
(key file next to the registry row), and writes newline-delimited JSON.
Protocol details: docs at code.claude.com/docs/en/cross-session-messaging.

These helpers are pure so tests can drive them against a fake socket. The
router in server.py owns registry lookup, eligibility, and delivery receipts.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import socket
import time
import uuid as _uuid
from pathlib import Path

MIN_PEER_VERSION = (2, 1, 234)
MAX_LINE_BYTES = 1024 * 1024
_CLOSE_TAG = "</cross-session-message>"

# The registry row's `version` field is read by senders' own peer-protocol
# compatibility gates (see MIN_PEER_VERSION above). CCC is not a Claude Code
# build, so this is NOT "CCC's product version" -- it is the protocol version
# CCC asserts wire-compatibility with, kept as one constant so a future
# protocol bump only needs one edit. Observed on this machine 2026-08-29.
CCC_PEER_COMPAT_VERSION = "2.1.251"

# The keys every consumer in this codebase (resolve_target, _load_session_registry
# staleness filter, _try_uds_peer_delivery) actually reads off a registry row.
# validate_registry_row_shape checks these are present on both CCC's candidate
# row and at least one real observed row, so a future Claude Code registry
# format change is caught loudly instead of silently producing a row real
# peers can't parse.
REQUIRED_REGISTRY_KEYS = ("pid", "sessionId", "cwd", "messagingSocketPath", "peerProtocol", "version")


def _ccc_session_id_for(pid, socket_path):
    """Deterministic pseudo-sessionId for CCC's own registry row.

    Real Claude sessionIds are random UUIDs; CCC's is derived (uuid5) from
    (pid, socket_path) instead of `uuid.uuid4()` so re-publishing on the same
    bind (e.g. a retry within one process lifetime) is idempotent and tests
    can assert a stable value without a random seed.
    """
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"ccc:{pid}:{socket_path}"))


def build_ccc_registry_row(pid, socket_path, cwd, *, name="ccc",
                            version=None, started_at_epoch_ms=None, proc_start=None):
    """Build CCC's own ~/.claude/sessions/<pid>.json row.

    Shaped to match a real Claude Code registry row field-for-field (see
    REQUIRED_REGISTRY_KEYS and validate_registry_row_shape) so existing
    peer clients -- which read this file directly off disk, not through
    CCC's own memoized _load_session_registry() -- can dial CCC exactly like
    any other peer. `peerFeatures` is deliberately empty: CCC does not
    implement notify_when_idle, reply_across_default_dirs, or artifact_yield,
    and claiming otherwise would mislead a real sender.
    """
    now_ms = started_at_epoch_ms if started_at_epoch_ms is not None else int(time.time() * 1000)
    return {
        "pid": int(pid),
        "sessionId": _ccc_session_id_for(pid, socket_path),
        "cwd": str(cwd),
        "startedAt": now_ms,
        "procStart": proc_start or time.ctime(now_ms / 1000.0),
        "version": version or CCC_PEER_COMPAT_VERSION,
        "peerProtocol": 1,
        "peerFeatures": [],
        "kind": "background",
        "entrypoint": "ccc",
        "pidDomain": "darwin" if os.name == "posix" else "nt",
        "messagingSocketPath": str(socket_path),
        "name": name,
        "nameSource": "system",
        "nameSince": now_ms,
        "updatedAt": now_ms,
    }


def ccc_key_payload(token, proc_start=None, pid_domain=None):
    payload = {"peerToken": str(token)}
    if proc_start:
        payload["procStart"] = str(proc_start)
    if pid_domain:
        payload["pidDomain"] = str(pid_domain)
    return payload


def validate_registry_row_shape(candidate, reference_rows):
    """Cross-check CCC's candidate row against real, currently-observed
    Claude Code registry rows before CCC publishes it.

    Two independent checks, either of which can fail:
    1. Every REQUIRED_REGISTRY_KEYS entry is present on `candidate` itself.
    2. If any reference rows were supplied (real rows from other pids on
       this machine), at least one of them must also carry every required
       key -- if Claude Code's own format has drifted so that NONE of the
       locally observed rows match what CCC expects, that's the "impersonation
       risk" the spec calls out, and the caller (Task 3) must not publish.

    No reference rows at all (a machine that has never run an interactive
    Claude Code session) is NOT a failure -- there is nothing to compare
    against, so CCC proceeds unverified and the caller logs that fact.
    Never raises: always returns {"ok": bool, "reason": str}.
    """
    candidate = candidate if isinstance(candidate, dict) else {}
    missing = [k for k in REQUIRED_REGISTRY_KEYS if k not in candidate]
    if missing:
        return {"ok": False, "reason": f"candidate missing keys: {', '.join(missing)}"}
    refs = [r for r in (reference_rows or []) if isinstance(r, dict)]
    if not refs:
        return {"ok": True, "reason": "no_reference_rows"}
    for row in refs:
        if all(k in row for k in REQUIRED_REGISTRY_KEYS):
            return {"ok": True, "reason": ""}
    return {"ok": False, "reason": "no reference row on this machine carries all of: " + ", ".join(REQUIRED_REGISTRY_KEYS)}


def version_tuple(v):
    parts = []
    for piece in str(v or "").strip().split("."):
        if not piece.isdigit():
            return ()
        parts.append(int(piece))
    return tuple(parts)


def wrap(body, from_addr="", from_name="", from_mode=""):
    """Build the <cross-session-message> wrapper Claude expects in content."""
    attrs = []
    if from_addr:
        attrs.append('from="%s"' % html.escape(str(from_addr), quote=True))
    if from_name:
        attrs.append('from-name="%s"' % html.escape(str(from_name), quote=True))
    if from_mode:
        attrs.append('from-mode="%s"' % html.escape(str(from_mode), quote=True))
    open_tag = "<cross-session-message" + (" " + " ".join(attrs) if attrs else "") + ">"
    safe_body = str(body or "").replace(_CLOSE_TAG, "&lt;/cross-session-message&gt;")
    return open_tag + "\n" + safe_body + "\n" + _CLOSE_TAG


def key_path_for(sessions_dir, pid, socket_path):
    digest = hashlib.sha256(str(socket_path).encode("utf-8")).hexdigest()
    return Path(sessions_dir) / ("%d.%s.key" % (int(pid), digest))


def load_peer_token(sessions_dir, pid, socket_path):
    try:
        data = json.loads(key_path_for(sessions_dir, pid, socket_path).read_text())
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("peerToken") or "")


def resolve_target(row):
    """Decide whether a registry row is a dialable peer. Never raises."""
    row = row if isinstance(row, dict) else {}
    socket_path = str(row.get("messagingSocketPath") or "").strip()
    out = {"ok": False, "reason": "", "socket_path": socket_path, "pid": 0,
           "version": str(row.get("version") or "")}
    try:
        out["pid"] = int(row.get("pid") or 0)
    except (TypeError, ValueError):
        out["pid"] = 0
    if not socket_path:
        out["reason"] = "no_socket_path"
        return out
    if row.get("peerProtocol") != 1:
        out["reason"] = "peer_protocol"
        return out
    if version_tuple(out["version"]) < MIN_PEER_VERSION:
        out["reason"] = "version_too_old"
        return out
    if not os.path.exists(socket_path):
        out["reason"] = "socket_missing"
        return out
    out["ok"] = True
    return out


def build_frame_lines(content, *, token="", from_addr="", msg_id, priority="next"):
    lines = []
    if token:
        lines.append(json.dumps({"type": "auth", "token": token}, ensure_ascii=False).encode("utf-8") + b"\n")
    user = {
        "type": "user",
        "message": {"role": "user", "content": str(content)},
        "msg_id": str(msg_id),
        "priority": priority if priority in ("now", "next", "later") else "next",
    }
    if from_addr:
        user["from"] = str(from_addr)
    line = json.dumps(user, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(line) > MAX_LINE_BYTES:
        raise ValueError("peer frame exceeds the 1 MiB line cap")
    lines.append(line)
    return lines


def send_lines(socket_path, lines, timeout_s=3.0):
    """Connect, write every line, half-close, and return {"ok", "error"}."""
    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout_s)
        sock.connect(socket_path)
        for line in lines:
            sock.sendall(line)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        return {"ok": True, "error": ""}
    except (OSError, socket.timeout, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
