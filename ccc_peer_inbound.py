"""Inbound Claude Code peer-mesh frame parsing for CCC's own socket.

CCC is now a dialable peer (see ccc_peer_uds.build_ccc_registry_row). These
are the pure, stdlib-only helpers for reading and classifying what arrives
on that socket. Registry lookup, ask correlation, and routing side effects
(_inject_text_into_session) stay in server.py, exactly like ccc_peer_uds.py
keeps outbound wire-building separate from server.py's router.
"""

from __future__ import annotations

import json
import re
import socket
import time

_WRAPPER_RE = re.compile(r"<cross-session-message\b[^>]*>(.*?)</cross-session-message>", re.DOTALL)
_ORIG_MSG_ID_RE = re.compile(r"^orig_msg_id:\s*(\S+)\s*\n(.*)$", re.DOTALL)


def read_frames(conn, *, max_line_bytes=1024 * 1024, first_line_deadline_s=30.0):
    """Yield parsed JSON dicts from newline-delimited frames on `conn`.

    Enforces the documented 1 MiB line cap (closes the read on overflow) and
    the 30s first-line deadline (a connection with no complete line by then
    is abandoned) -- both from the Claude Code peer-mesh wire contract.
    Malformed JSON on an otherwise well-formed line is skipped, not fatal:
    one bad frame must not drop frames that already arrived cleanly after it.

    The deadline only applies before the first complete line arrives. Once a
    caller has sent at least one well-formed line, this blocks indefinitely
    waiting for the next one (a live connection with gaps between messages
    is normal for the accept-loop use case; it is not "silent" the way an
    unauthenticated connection that never sends anything is). The read ends
    only on EOF or a socket error, never on inter-message idle time.
    """
    buf = b""
    start = time.time()
    got_first = False
    while True:
        if not got_first:
            remaining = first_line_deadline_s - (time.time() - start)
            if remaining <= 0:
                return
            conn.settimeout(min(1.0, remaining))
        else:
            conn.settimeout(None)
        try:
            chunk = conn.recv(65536)
        except socket.timeout:
            # Only possible pre-first-line (see settimeout(None) above once
            # got_first is True); loop back and re-check the deadline.
            continue
        except OSError:
            return
        if not chunk:
            # flush whatever's left in buf as a final line, then stop
            if buf.strip():
                frame = _parse_line(buf)
                if frame is not None:
                    yield frame
            return
        buf += chunk
        if len(buf) > max_line_bytes and b"\n" not in buf:
            return  # oversize with no line boundary yet: drop the connection
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if len(line) > max_line_bytes:
                return
            got_first = True
            frame = _parse_line(line)
            if frame is not None:
                yield frame


def _parse_line(line):
    try:
        data = json.loads(line.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def unwrap_message(content):
    """Strip a <cross-session-message> wrapper down to its inner body."""
    raw = str(content or "")
    m = _WRAPPER_RE.search(raw)
    return m.group(1).strip() if m else raw.strip()


def parse_report_envelope(body):
    """Return the report_to JSON envelope dict, or None if `body` isn't one.

    Mirrors the exact shape the report_to curl footer already posts to
    /api/inject-input: {"session_id", "mode"?, "announced_from"?, "text"}.
    """
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if "session_id" not in data or "text" not in data:
        return None
    return data


def extract_orig_msg_id(body):
    """Split a leading `orig_msg_id: <token>` header off a reply body.

    CCC's own ask-reply convention (see server.py's ask wrapper text): when
    CCC sends an ask over the peer socket, it asks the receiver to prefix
    its reply with this header so CCC can match the reply back to the
    specific outstanding ask, even with more than one ask in flight to the
    same session. Returns (None, body) unchanged when absent.
    """
    raw = str(body or "")
    m = _ORIG_MSG_ID_RE.match(raw)
    if not m:
        return None, raw
    return m.group(1).strip(), m.group(2).strip()
