"""Dead sessions: a turn that stopped without finishing, with nobody told.

A session "dies" when its process goes away mid-turn (crash, OOM, a lost
permission, a turn that never started) or its last turn is an API error.
Without this, the dashboard's leave-"working" diff toasts "X finished" for a
session that never finished anything.

Notify-only. Nothing here wakes or resumes a session; an opted-in rerun
(auto_rerun.py, through the wake guard) is decided by notify's emitter.

False positives are the expensive mistake, so a death is only reported when:
  * the transcript tail is dead-shaped: an open turn, or an API error that is
    not a usage limit (usage limits have their own banner and auto-resume);
  * no interrupt marker follows it (Esc, Stop, a rejected tool call);
  * CCC did not stop the process on purpose (see `note_stop`);
  * WatchTower does not own the session (it has its own liveness backstop);
  * the machine did not restart under it (the restart-recovery card owns that);
  * after GRACE_S the session is still not running and its transcript has
    not moved (a force-restart or resume would have touched it).
Each incident, keyed by transcript size, is reported once.

Cost: work happens only on a transition out of "working" (one tail read) and
for the few pending candidates afterwards (one stat each), never per session
per tick.
"""

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from ccc_server import restart_recovery as _rr
from ccc_server import test_isolation_active
from ccc_server.paths import COMMAND_CENTER_STATE_DIR

if test_isolation_active():
    STATE_DIR = Path(tempfile.gettempdir()) / "ccc-test-session-death"
else:
    STATE_DIR = COMMAND_CENTER_STATE_DIR
STATE_FILE = STATE_DIR / "session-death.json"
STOPS_FILE = STATE_DIR / "session-stops.json"
WT_WORKERS_FILE = Path.home() / ".watchtower" / "workers.json"
# Every session WatchTower ever ran as a worker; workers.json drops reaped ones.
WT_SESSIONS_FILE = Path.home() / ".watchtower" / "worker-sessions.json"

GRACE_S = 90.0
TAIL_BYTES = 64 * 1024
STOP_WINDOW_S = 15 * 60
ALERTED_KEEP_S = 7 * 24 * 3600
PENDING_MAX_AGE_S = 3600.0

_LIMIT = re.compile(r"rate.?limit|usage.?limit|quota|usage_limit_exceeded|reached[^.]*limit|"
                    r"too many requests|429", re.I)
_FINAL_TOOLS = {"StructuredOutput"}
_OWNED_PROMPTS = ("[WATCHTOWER]",)
_INTERRUPT_MARKERS = ("[Request interrupted by user", "The user doesn't want to proceed")

_LOCK = threading.Lock()
_STATE = None  # {"working": [sid], "pending": {sid: {...}}, "alerted": {sid: {...}}}


# ── transcript shape ─────────────────────────────────────────────────────


def _texts(rec):
    """Every text string in a record, including tool_result bodies."""
    out = []
    for block in _rr._blocks(rec):
        if block.get("type") == "text":
            out.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_result":
            body = block.get("content")
            if isinstance(body, str):
                out.append(body)
            elif isinstance(body, list):
                out.extend(str(b.get("text") or "") for b in body if isinstance(b, dict))
    return out


def _interrupted(rec):
    return rec.get("type") == "user" and any(
        m in t for t in _texts(rec) for m in _INTERRUPT_MARKERS)


def classify(records):
    """Return {"kind": "api_error"|"cut_off", ...} if the tail looks dead, else None."""
    records = [r for r in records if not r.get("isSidechain")
               and r.get("type") in ("user", "assistant")]
    if not records:
        return None
    last = records[-1]
    # An interrupt marker anywhere after the last assistant reply means a
    # person (or CCC on their behalf) stopped the turn.
    for rec in reversed(records):
        if rec.get("type") == "assistant":
            break
        if _interrupted(rec):
            return None
    if last.get("type") == "assistant" and last.get("isApiErrorMessage"):
        detail = " ".join(" ".join(_texts(last)).split())
        if _LIMIT.search(detail):
            return None
        return {"kind": "api_error", "detail": detail[:200], "at": last.get("timestamp")}
    step = _rr.open_turn(records)
    if step is None:
        return None
    if step["kind"] == "awaiting_model" and _last_tool(records) in _FINAL_TOOLS:
        return None  # `claude -p --json-schema` ends its turn on this result
    if step["kind"] == "prompt_unanswered" and any(
            t.lstrip().startswith(_OWNED_PROMPTS) for t in _texts(last)):
        return None  # WatchTower messaging a worker it already reaped
    return {"kind": "cut_off", "step": step, "at": step.get("at")}


def _last_tool(records):
    for rec in reversed(records):
        if rec.get("type") == "assistant":
            uses = [b for b in _rr._blocks(rec) if b.get("type") == "tool_use"]
            return str(uses[-1].get("name") or "") if uses else ""
    return ""


def _tail(path, size):
    with open(path, "rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            raw = fh.read()
            return raw[raw.find(b"\n") + 1:]
        return fh.read()


def _find_path(sid):
    try:
        from ccc_server import session_graph
        return session_graph._find_session_jsonl(sid)
    except Exception:
        return None


# ── persisted state ──────────────────────────────────────────────────────


def _read_json(path, default):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path, data):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    except OSError:
        pass


def _state():
    global _STATE
    if _STATE is None:
        data = _read_json(STATE_FILE, {})
        _STATE = {
            "working": list(data.get("working") or []),
            "pending": dict(data.get("pending") or {}),
            "alerted": dict(data.get("alerted") or {}),
        }
    return _STATE


def _save():
    _write_json(STATE_FILE, _state())


def note_stop(sid, reason="", now=None):
    """CCC is stopping this session on purpose; its open turn is not a death.

    Called from the kill/retire paths, which may run in the worker process,
    so the record goes to disk rather than memory."""
    if not sid:
        return
    now = time.time() if now is None else float(now)
    with _LOCK:
        stops = _read_json(STOPS_FILE, {})
        stops = {k: v for k, v in stops.items()
                 if isinstance(v, dict) and now - float(v.get("ts") or 0) < STOP_WINDOW_S}
        stops[str(sid)] = {"ts": now, "reason": str(reason or "")[:60]}
        _write_json(STOPS_FILE, stops)


def _stopped_on_purpose(sid, since):
    rec = _read_json(STOPS_FILE, {}).get(sid)
    return isinstance(rec, dict) and float(rec.get("ts") or 0) >= since - STOP_WINDOW_S


def _watchtower_owned(sid):
    data = _read_json(WT_WORKERS_FILE, {})
    workers = data.get("workers") if isinstance(data, dict) else None
    if any(isinstance(w, dict) and w.get("session_id") == sid for w in workers or []):
        return True
    return sid in (_read_json(WT_SESSIONS_FILE, {}).get("session_ids") or [])


# ── the dashboard tick hooks ─────────────────────────────────────────────


def consider(sid, cur_state, now=None, find_path=None):
    """`sid` just left "working" for `cur_state` ("idle", "ended" or "gone").

    Returns True when the tail looks dead, so the caller should not toast
    "finished"; the session then waits out GRACE_S in `pending`."""
    now = time.time() if now is None else float(now)
    path = (find_path or _find_path)(sid)
    if path is None:
        return False
    try:
        size = os.stat(path).st_size
        records = list(_rr._records(_tail(path, size)))
    except OSError:
        return False
    verdict = classify(records)
    if verdict is None:
        return False
    # A live, idle process after an open turn is a stream-json child between
    # events, not a death; only an API error counts while it is still up.
    if cur_state == "idle" and verdict["kind"] != "api_error":
        return False
    with _LOCK:
        st = _state()
        seen = st["alerted"].get(sid)
        if isinstance(seen, dict) and seen.get("size") == size:
            return True
        st["pending"][sid] = {"since": now, "size": size, "path": str(path),
                              "verdict": verdict, "state": cur_state,
                              "cwd": _cwd(records), "title": _rr._title(path, records)}
        _save()
    return True


def _cwd(records):
    for rec in reversed(records):
        if rec.get("cwd"):
            return str(rec["cwd"])
    return ""


def track_working(current):
    """Persist the set of working sessions so a dashboard (re)connect can
    notice ones that died while nobody was watching. Writes only on change."""
    working = sorted(sid for sid, v in (current or {}).items()
                     if (v or {}).get("state") == "working")
    with _LOCK:
        st = _state()
        if st["working"] != working:
            st["working"] = working
            _save()


def missed_while_away(current):
    """Sessions that were working at the last tick we saw and are not live now."""
    with _LOCK:
        before = list(_state()["working"])
    out = []
    for sid in before:
        cur = (current or {}).get(sid)
        if cur is None:
            out.append((sid, "gone"))
        elif cur.get("state") == "ended":
            out.append((sid, "ended"))
    return out


def due(current, now=None, boot=None):
    """Pending candidates that survived the grace period: [incident]."""
    now = time.time() if now is None else float(now)
    with _LOCK:
        pending = dict(_state()["pending"])
    if not pending:
        return []
    boot = _rr.boot_time() if boot is None else boot
    ready, drop = [], []
    for sid, cand in pending.items():
        since = float(cand.get("since") or 0)
        if now - since > PENDING_MAX_AGE_S:
            drop.append(sid)
            continue
        cur = (current or {}).get(sid) or {}
        state = cur.get("state")
        if state == "working" or (state in ("idle", "waiting") and cand["verdict"]["kind"] != "api_error"):
            drop.append(sid)  # it came back
            continue
        if now - since < GRACE_S:
            continue
        try:
            st = os.stat(cand["path"])
        except OSError:
            drop.append(sid)
            continue
        if st.st_size != cand.get("size"):
            drop.append(sid)  # resumed, restarted or answered
            continue
        if boot and st.st_mtime < boot:
            drop.append(sid)  # the restart-recovery card covers reboots
            continue
        if _stopped_on_purpose(sid, since) or _watchtower_owned(sid):
            drop.append(sid)
            continue
        ready.append(dict(cand, session_id=sid))
        drop.append(sid)
    if drop:
        with _LOCK:
            st = _state()
            for sid in drop:
                st["pending"].pop(sid, None)
            for inc in ready:
                st["alerted"][inc["session_id"]] = {"size": inc["size"], "ts": now}
            st["alerted"] = {k: v for k, v in st["alerted"].items()
                             if isinstance(v, dict) and now - float(v.get("ts") or 0) < ALERTED_KEEP_S}
            _save()
    return ready


def describe(incident):
    """(title suffix, body) for the toast."""
    verdict = incident.get("verdict") or {}
    if verdict.get("kind") == "api_error":
        detail = verdict.get("detail") or "The API returned an error."
        return "hit an API error", f"Its last turn failed: {detail}"
    step = verdict.get("step") or {}
    if step.get("kind") == "tool_running":
        what = step.get("tool") or "a tool"
        if step.get("detail"):
            what += f" ({step['detail'][:80]})"
        body = f"Its process stopped while running {what}."
    elif step.get("kind") == "awaiting_model":
        body = "Its process stopped while waiting for the model's next step."
    else:
        body = "Its process stopped before answering the last message."
    return "stopped mid-turn", body + " Nothing will restart it on its own."


def reset_for_tests():
    global _STATE
    _STATE = None
    if test_isolation_active():
        for path in (STATE_FILE, STOPS_FILE):
            try:
                path.unlink()
            except OSError:
                pass
