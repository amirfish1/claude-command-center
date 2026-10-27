"""Operator presence for the cloud relay snapshot.

"Human input" means the person typed a prompt into a terminal session, or sent
one from the dashboard composer. Anything CCC itself delivered (cloud relay,
peer messages, WatchTower, API callers) is machine input and never counts.

Signals:
  1. Dashboard composer sends, stamped in memory by the inject-input handler
     (`note_inject`). Only source "composer" counts as human.
  2. Terminal prompts, read from the transcript tail of live sessions only.
     A user turn counts when it is typed text (not a tool result, not meta, not
     a sub-agent turn, not a CCC provenance marker, not a system-injected tag)
     and does not match text CCC itself injected (`note_inject` keeps a short
     snippet of every non-composer inject).

Perf: the transcript scan is gated to live, non-sub-agent sessions, memoised
per session on the row mtime (steady state costs nothing), and the parse is
cached by (mtime, size) in relay_context._cached.

Stdlib only. Nothing here leaves the machine except timestamps.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

try:
    from . import relay_context as _rc
except ImportError:  # pragma: no cover - flat import in odd test layouts
    from ccc_server import relay_context as _rc

PRESENT_WINDOW_S = 300
TAIL_BYTES = 128 * 1024
MAX_SCANS_PER_BUILD = 40
_SNIPPET_LEN = 60
_MACHINE_KEEP = 20
_HUMAN_SOURCES = ("composer",)

# Text that marks input CCC or the harness produced, not the person.
_MACHINE_PREFIXES = (
    "[From ", "[Sent from a remote client through CCC Cloud Relay",
    "Announced from:", "[Message attribution v1:", "<system-reminder", "<task-notification",
    "<local-command-", "<command-name>", "<command-message>", "Caveat:",
    "[Request interrupted", "Stop hook feedback", "<user-prompt-submit-hook",
)
_MACHINE_MARKERS = ("CCC Cloud Relay", "Announced from:", "[Message attribution v1:")

_lock = threading.Lock()
_ui_input: dict = {}       # sid -> epoch of the last composer send
_machine: dict = {}        # sid -> [(epoch, snippet)]
_memo: dict = {}           # sid -> (row_mtime, last_human_epoch or None)


def _sid(value) -> str:
    s = str(value or "")
    return s[len("session_"):] if s.startswith("session_") else s


def _snip(text) -> str:
    return " ".join(str(text or "").split())[:_SNIPPET_LEN]


def note_inject(session_id, text, source, now=None) -> None:
    """Called by /api/inject-input for every delivery. Composer sends are human
    input; every other source is remembered so its transcript echo is ignored."""
    sid = _sid(session_id)
    if not sid:
        return
    now = time.time() if now is None else now
    src = str(source or "").strip().lower()
    with _lock:
        if src in _HUMAN_SOURCES:
            _ui_input[sid] = now
            _memo.pop(sid, None)
            return
        snip = _snip(text)
        if snip:
            lst = _machine.setdefault(sid, [])
            lst.append((now, snip))
            del lst[:-_MACHINE_KEEP]


def _is_machine_text(sid, text) -> bool:
    t = text.lstrip()
    if not t:
        return True
    import sender_envelope as attribution
    attr = attribution.lookup(text=t)
    if attr:
        return attr["author"]["kind"] != "human"
    if t.startswith(_MACHINE_PREFIXES):
        return True
    if any(m in t[:400] for m in _MACHINE_MARKERS):
        return True
    flat = " ".join(t.split())
    with _lock:
        snippets = [s for _, s in _machine.get(sid, ())]
    return any(s and s in flat for s in snippets)


def _epoch(ts) -> float | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _typed_text(obj):
    """Typed text of a user event, or None when it is not a human-style turn."""
    if obj.get("type") not in (None, "user") or obj.get("isMeta") \
            or obj.get("isSidechain"):
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result"
               for b in content):
            return None
    text = _rc.content_to_text(content)
    return text if text and text.strip() else None


def _scan(path, sid):
    """Epoch of the newest human-typed user turn in the transcript tail."""
    events, whole = _rc.read_tail_lines(path, TAIL_BYTES)
    users = [(o, _typed_text(o)) for o in events]
    users = [(o, t) for o, t in users if t is not None]
    if whole and len(users) == 1:
        return None  # the lone first turn is the spawn prompt
    for obj, text in reversed(users):
        if _is_machine_text(sid, text):
            continue
        ep = _epoch(obj.get("timestamp"))
        if ep is not None:
            return ep
    return None


def last_human_input(sid, row_mtime, path_of, now=None):
    """Epoch of the last human input to `sid` (terminal or composer), or None."""
    sid = _sid(sid)
    with _lock:
        ui = _ui_input.get(sid)
        memo = _memo.get(sid)
    if memo is not None and memo[0] == row_mtime:
        term = memo[1]
    else:
        path = path_of(sid)
        term = _rc._cached("human_input:" + sid, path,
                           lambda: _scan(path, sid)) if path else None
        with _lock:
            _memo[sid] = (row_mtime, term)
    vals = [v for v in (ui, term) if v]
    return max(vals) if vals else None


def _iso(ep) -> str:
    return datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute(rows, path_of, now=None):
    """({ref: iso}, presence) for the snapshot. `rows` are (ref, is_live,
    row_mtime) tuples; only live non-sub-agent sessions are scanned. Composer
    stamps apply to any session."""
    now = time.time() if now is None else now
    out = {}
    scans = 0
    for ref, is_live, mtime in rows:
        sid = _sid(ref)
        with _lock:
            has_ui = sid in _ui_input
        if not (is_live or has_ui) or ":agent-" in sid or sid.startswith("agent-"):
            continue
        with _lock:
            fresh = (_memo.get(sid) or (None,))[0] == mtime
        if not fresh and not has_ui:
            if scans >= MAX_SCANS_PER_BUILD:
                continue
            scans += 1
        ep = last_human_input(sid, mtime, path_of, now)
        if ep:
            out[ref] = _iso(ep)
    newest = max((_epoch(v) for v in out.values()), default=None)
    presence = {
        "present": bool(newest and 0 <= now - newest < PRESENT_WINDOW_S),
        "last_input_at": _iso(newest) if newest else None,
    }
    return out, presence


def reset_for_tests() -> None:
    with _lock:
        _ui_input.clear()
        _machine.clear()
        _memo.clear()
