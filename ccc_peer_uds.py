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
from pathlib import Path

MIN_PEER_VERSION = (2, 1, 234)
MAX_LINE_BYTES = 1024 * 1024
_CLOSE_TAG = "</cross-session-message>"


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
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect(socket_path)
        for line in lines:
            sock.sendall(line)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        return {"ok": True, "error": ""}
    except (OSError, socket.timeout) as exc:
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}
    finally:
        try:
            sock.close()
        except OSError:
            pass
