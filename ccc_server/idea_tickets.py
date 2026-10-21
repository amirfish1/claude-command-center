"""Idea tickets (experimental, opt-in ``idea_tickets`` flag).

Every session the user starts from the CCC UI gets a WatchTower ticket after
its first reply, so the work has a handle that outlives any one session.

- CCC owns the ticket. The agent never writes to it, so it costs no turns:
  this module files it, appends the session log, and closes it on archive.
- After the first reply, Jev (TypeSafe System One) labels the session an idea
  or a small bug. With no Jev key it falls back to an idea; the sidebar row
  menu moves it.
- Continuations and child sessions inherit their origin's ticket and are
  logged on it ("continued in X", "child Y spawned"). CCC's own lineage stays
  the source of truth for session-to-session edges; the ticket is the idea's
  append-only log.

State lives in ``~/.claude/command-center/idea-tickets.json``::

    {"sessions": {sid: {"ref", "kind", "from", "at"} | {"skip": reason}},
     "tickets":  {ref: {"queue", "sessions": [sid, ...], "closed": bool}},
     "pending":  {sid: {"at"}}}

Only sessions recorded in ``pending`` at UI spawn time (``note_ui_spawn``,
called from the one place every spawn route tags its origin) can get a NEW
ticket, so turning the flag on never backfills tickets for old sessions.

Rows read it through ``idea_ticket_rows_map()`` (one mtime-cached file read
per list build, a dict lookup per row). Every ``wt`` call runs on a daemon
thread, never on a request or list path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from ccc_server import core as _core

FLAG = "idea_tickets"
STATE_FILE_NAME = "idea-tickets.json"
QUEUES = {"idea": "IDEAS", "small_bug": "SMALL-BUGS"}
QUEUE_TYPES = {"IDEAS": "feature", "SMALL-BUGS": "bug"}

JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_KEY_ENV = "JEV_API_KEY"
JEV_TIMEOUT_S = 8

_lock = threading.Lock()
_inflight = set()
_queues_ready = set()
_state_cache = {"key": None, "data": None}

_FILED_RE = re.compile(r"^FILED:\s+(\S+)", re.M)
_EDITED_RE = re.compile(r"^EDITED:\s+(\S+)", re.M)
# Session-scoped env vars that would make `wt` attribute the ticket to the
# server's own session instead of the one we pass with --worker.
_SESSION_ENV_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


def _enabled():
    try:
        return bool(_core._feature_flag(FLAG))
    except Exception:
        return False


def _state_path():
    return Path(_core.COMMAND_CENTER_STATE_DIR) / STATE_FILE_NAME


def _empty_state():
    return {"sessions": {}, "tickets": {}, "pending": {}}


def load_state():
    """The state dict, cached by the file's (mtime, size)."""
    path = _state_path()
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return _empty_state()
    if _state_cache["key"] == key and _state_cache["data"] is not None:
        return _state_cache["data"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = _empty_state()
    data.setdefault("sessions", {})
    data.setdefault("tickets", {})
    data.setdefault("pending", {})
    _state_cache["key"] = key
    _state_cache["data"] = data
    return data


def _save_state(data):
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    _state_cache["key"] = None


def _mutate(fn):
    """Apply ``fn(state)`` under the lock and persist. Returns fn's result."""
    with _lock:
        data = json.loads(json.dumps(load_state()))
        result = fn(data)
        _save_state(data)
        return result


def ticket_for_session(sid):
    """``{"ref", "queue", "sessions"}`` for a ticketed session, else None."""
    data = load_state()
    entry = data["sessions"].get(str(sid or ""))
    ref = entry.get("ref") if isinstance(entry, dict) else None
    ticket = data["tickets"].get(ref) if ref else None
    if not ticket:
        return None
    return {
        "ref": ref,
        "queue": ticket.get("queue") or "",
        "sessions": list(ticket.get("sessions") or []),
        "closed": bool(ticket.get("closed")),
    }


def idea_ticket_rows_map():
    """sid -> row payload for every ticketed session; {} when the flag is off."""
    if not _enabled():
        return {}
    data = load_state()
    out = {}
    for sid, entry in data["sessions"].items():
        ref = entry.get("ref") if isinstance(entry, dict) else None
        ticket = data["tickets"].get(ref) if ref else None
        if not ticket:
            continue
        out[sid] = {
            "ref": ref,
            "queue": ticket.get("queue") or "",
            "sessions": len(ticket.get("sessions") or []),
            "closed": bool(ticket.get("closed")),
        }
    return out


# ── wt plumbing ────────────────────────────────────────────────────────────

def _wt_env():
    env = dict(os.environ)
    for name in _SESSION_ENV_VARS:
        env.pop(name, None)
    return env


def _wt(args, *, cwd=None, timeout=30):
    """Run ``wt`` and return (returncode, stdout). (-1, "") when unavailable."""
    wt = _core._wt_cli_path()
    if not wt:
        return -1, ""
    run_cwd = cwd if cwd and os.path.isdir(cwd) else None
    try:
        proc = subprocess.run(
            [wt] + list(args), capture_output=True, text=True,
            timeout=timeout, cwd=run_cwd, env=_wt_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _ensure_queue(queue):
    # Idea queues are records, not work: drain stays off so no worker claims
    # a ticket. `wt drain off` also creates the queue on first use.
    if queue in _queues_ready:
        return
    rc, _ = _wt(["drain", "off", queue])
    if rc == 0:
        _queues_ready.add(queue)


# ── classification ─────────────────────────────────────────────────────────

def _jev_key():
    try:
        from ccc_server import vault as _vault
        for name in ("jev-api-key", "jev_api_key", "jev"):
            value = _vault.vault_get_value(name)
            if value:
                return value
        for entry in _vault.vault_list_entries():
            if str(entry.get("env_var") or "") == JEV_KEY_ENV:
                value = _vault.vault_get_value(entry.get("name"))
                if value:
                    return value
    except Exception:
        pass
    return os.environ.get(JEV_KEY_ENV) or None


def classify(text, *, key=None, opener=None):
    """``"idea"`` or ``"small_bug"`` for a session's opening message.

    Jev answers a two-way choice. No key, a failed call, or an unexpected
    answer falls back to ``"idea"``; the row menu moves a wrong label.
    """
    key = key if key is not None else _jev_key()
    if not key or not str(text or "").strip():
        return "idea"
    body = json.dumps({
        "model": JEV_MODEL,
        "state": {"first_message": str(text)[:4000]},
        "questions": {
            "kind": {
                "type": "choice",
                "instructions": (
                    "This is the opening message of a coding-agent session. "
                    "Is the user reporting a specific defect to fix, or "
                    "exploring, designing or building something?"
                ),
                "criteria": {
                    "small_bug": "A concrete bug, regression or broken behavior to fix.",
                    "idea": "A feature, design question, exploration, research or anything else.",
                },
            },
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        JEV_API_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(req, timeout=JEV_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        choice = (((payload or {}).get("answers") or {}).get("kind") or {}).get("choice")
    except Exception:
        return "idea"
    return choice if choice in QUEUES else "idea"


# ── session metadata ───────────────────────────────────────────────────────

def _spawn_meta(sid):
    """(spawned_via, parent_session_id, cwd) from the registry, then the marker."""
    via = parent = cwd = ""
    try:
        entry = _core._spawn_registry_entry_for_session(sid) or {}
    except Exception:
        entry = {}
    via = str(entry.get("spawned_via") or "")
    parent = str(entry.get("parent_session_id") or "")
    cwd = str(entry.get("cwd") or entry.get("repo_path") or "")
    try:
        marker = json.loads((Path(_core.SPAWN_MARKERS_DIR) / f"{sid}.json").read_text())
    except (OSError, ValueError):
        marker = {}
    if isinstance(marker, dict):
        via = via or str(marker.get("spawned_via") or "")
        parent = parent or str(marker.get("parent_session_id") or "")
    return via, parent, cwd


def _first_user_text(jsonl_path):
    try:
        with open(jsonl_path, "r") as fh:
            for i, line in enumerate(fh):
                if i >= 200:
                    break
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") != "user" or ev.get("isMeta"):
                    continue
                text = str(_core._extract_user_prompt_text(ev) or "").strip()
                if text:
                    return text
    except OSError:
        pass
    return ""


def _title_from(text):
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:80]
    return "Untitled session"


# ── lifecycle ──────────────────────────────────────────────────────────────

def note_ui_spawn(session_id, via):
    """Spawn-time entry point: remember a session the user started from the
    UI, so its first reply can file a ticket. Any engine."""
    sid = str(session_id or "").strip()
    if not sid or str(via or "") != "ui" or not _enabled():
        return
    if sid in load_state()["pending"]:
        return
    _mutate(lambda d: d["pending"].__setitem__(sid, {"at": int(time.time())}))


SWEEP_MAX_PER_CALL = 5


def sweep_rows(rows):
    """List-build entry point for engines without a Stop hook (Codex etc.):
    queue the ticket step for candidate rows that already have a reply.

    Candidacy is a dict lookup per row: a pending UI spawn, or a session whose
    continuation origin or parent already has a ticket. Known sessions are
    skipped, and at most SWEEP_MAX_PER_CALL threads start per call.
    """
    if not _enabled():
        return 0
    data = load_state()
    known, pending = data["sessions"], data["pending"]

    def _ticketed(sid):
        entry = known.get(sid) if sid else None
        return isinstance(entry, dict) and bool(entry.get("ref"))

    started = 0
    for row in rows or []:
        sid = str(row.get("session_id") or row.get("id") or "")
        if not sid or sid in known or sid in _inflight:
            continue
        # Task-tool subagents ("agent-…") are helpers inside a session, not
        # sessions of their own; they don't join the ticket's log.
        if sid.startswith("agent-"):
            continue
        if not (sid in pending
                or _ticketed(row.get("continued_from_session_id"))
                or _ticketed(row.get("parent_session_id"))):
            continue
        # "Has replied": Claude rows carry a reply preview; Codex rows only
        # record that the last event was a turn result.
        if not (row.get("last_assistant_preview") or row.get("last_assistant_text")
                or row.get("last_event_type") == "result"):
            continue
        queued = on_turn_end(
            sid,
            continued_from=str(row.get("continued_from_session_id") or ""),
            parent=str(row.get("parent_session_id") or ""),
            text=str(row.get("first_message") or ""),
        )
        if queued.get("queued"):
            started += 1
            if started >= SWEEP_MAX_PER_CALL:
                break
    return started

def on_turn_end(session_id, *, continued_from="", parent="", text=""):
    """Stop-hook entry point. Returns at once; the work runs on a thread.
    ``continued_from`` / ``parent`` are lineage hints from a list row."""
    sid = str(session_id or "").strip()
    if not sid or not _enabled():
        return {"queued": False, "reason": "disabled"}
    if sid.startswith("agent-"):
        return {"queued": False, "reason": "subagent"}
    if sid in load_state()["sessions"]:
        return {"queued": False, "reason": "known"}
    with _lock:
        if sid in _inflight:
            return {"queued": False, "reason": "inflight"}
        _inflight.add(sid)
    threading.Thread(
        target=_attach_or_file_safe, args=(sid, continued_from, parent, text), daemon=True,
    ).start()
    return {"queued": True}


def _attach_or_file_safe(sid, continued_from="", parent="", text=""):
    try:
        attach_or_file(sid, continued_from_hint=continued_from, parent_hint=parent,
                       text_hint=text)
    except Exception as exc:
        try:
            _core._log_activity("idea", "IDEA_TICKET_ERR", f"{sid} {exc}")
        except Exception:
            pass
    finally:
        with _lock:
            _inflight.discard(sid)


def attach_or_file(sid, *, classify_fn=None, continued_from_hint="", parent_hint="",
                   text_hint=""):
    """Give ``sid`` its ticket: inherit from a continuation origin or parent,
    file a new one for a UI-started session, or record a skip."""
    jsonl = None
    try:
        jsonl = _core._find_session_jsonl(sid)
    except Exception:
        jsonl = None
    continued_from = ""
    if jsonl:
        try:
            continued_from = _core._continued_from_session_id_from_transcript(jsonl) or ""
        except Exception:
            continued_from = ""
    via, parent, cwd = _spawn_meta(sid)
    continued_from = continued_from or str(continued_from_hint or "")
    parent = parent or str(parent_hint or "")

    state = load_state()
    for origin, kind in ((continued_from, "continued"), (parent, "child")):
        origin_ticket = ticket_for_session(origin) if origin else None
        if origin_ticket:
            return _attach(sid, origin_ticket["ref"], kind, origin)
    if sid not in state["pending"]:
        _mutate(lambda d: d["sessions"].__setitem__(
            sid, {"skip": f"not a UI spawn (spawned_via={via or 'unknown'})", "at": int(time.time())}))
        return {"ok": True, "skipped": True}
    if sid in state["sessions"]:
        return {"ok": True, "known": True}

    text = (_first_user_text(jsonl) if jsonl else "") or str(text_hint or "")
    label = (classify_fn or classify)(text)
    queue = QUEUES.get(label, "IDEAS")
    _ensure_queue(queue)
    rc, out = _wt([
        "take", "-q", queue, "--worker", sid,
        "--readiness", "needs-spec", "--type", QUEUE_TYPES[queue],
        "--title", _title_from(text),
        "--text", f"Started in CCC session {sid}.\n\n{text[:2000]}",
    ], cwd=cwd)
    m = _FILED_RE.search(out or "")
    if rc != 0 or not m:
        _core._log_activity("idea", "IDEA_TICKET_FAIL", f"{sid} rc={rc}")
        return {"ok": False, "error": "wt take failed"}
    ref = m.group(1)

    def _record(d):
        d["tickets"][ref] = {"queue": queue, "sessions": [sid], "closed": False}
        d["sessions"][sid] = {"ref": ref, "kind": "started", "at": int(time.time())}
        d["pending"].pop(sid, None)
    _mutate(_record)
    _restamp_rows()
    _core._log_activity("idea", "IDEA_TICKET", f"{sid} -> {ref} ({label})")
    return {"ok": True, "ref": ref, "queue": queue}


def _restamp_rows():
    # The sidebar serves cached rows; restamp so the new ticket shows at once.
    try:
        _core._restamp_archive_serve_cache_after_mutation()
    except Exception:
        pass


def _attach(sid, ref, kind, origin):
    def _record(d):
        ticket = d["tickets"].setdefault(ref, {"queue": "", "sessions": [], "closed": False})
        if sid not in ticket["sessions"]:
            ticket["sessions"].append(sid)
        d["sessions"][sid] = {"ref": ref, "kind": kind, "from": origin, "at": int(time.time())}
        d["pending"].pop(sid, None)
    _mutate(_record)
    _restamp_rows()
    note = (f"Continued in session {sid} (from {origin})." if kind == "continued"
            else f"Child session {sid} spawned by {origin}.")
    _wt(["comment", ref, note, "--by", "system"])
    return {"ok": True, "ref": ref, "kind": kind}


def on_archive(session_id, archived):
    """Archiving a ticket's latest session closes it; unarchiving reopens it.
    Archiving an earlier session only logs it."""
    sid = str(session_id or "").strip()
    if not sid or not _enabled():
        return
    ticket = ticket_for_session(sid)
    if not ticket:
        return
    threading.Thread(
        target=_archive_worker, args=(sid, ticket, bool(archived)), daemon=True,
    ).start()


def _archive_worker(sid, ticket, archived):
    ref = ticket["ref"]
    latest = ticket["sessions"][-1] if ticket["sessions"] else sid
    try:
        if archived and sid == latest and not ticket["closed"]:
            rc, _ = _wt(["close", ref, "--force", "--worker", sid, "--no-code",
                         "--summary", f"Session {sid} archived in CCC."])
            if rc == 0:
                _mutate(lambda d: d["tickets"][ref].__setitem__("closed", True))
        elif archived:
            _wt(["comment", ref, f"Session {sid} archived.", "--by", "system"])
        elif ticket["closed"]:
            rc, _ = _wt(["reopen", ref, "--reason", f"Session {sid} unarchived in CCC."])
            if rc == 0:
                _mutate(lambda d: d["tickets"][ref].__setitem__("closed", False))
    except Exception as exc:
        _core._log_activity("idea", "IDEA_TICKET_ERR", f"archive {sid} {exc}")


def move_ticket(session_id, queue):
    """Move a session's ticket to IDEAS or SMALL-BUGS. Synchronous: the row
    menu waits for the new ref so the sidebar regroups at once."""
    sid = str(session_id or "").strip()
    queue = str(queue or "").strip().upper()
    if queue not in QUEUE_TYPES:
        return {"ok": False, "error": "queue must be IDEAS or SMALL-BUGS"}
    ticket = ticket_for_session(sid)
    if not ticket:
        return {"ok": False, "error": "session has no idea ticket"}
    old = ticket["ref"]
    if ticket["queue"] == queue:
        return {"ok": True, "ref": old, "queue": queue}
    _ensure_queue(queue)
    rc, out = _wt(["edit", old, "--queue", queue])
    m = _EDITED_RE.search(out or "")
    new = m.group(1) if m else old
    if rc != 0:
        return {"ok": False, "error": "wt edit failed"}

    def _record(d):
        t = d["tickets"].pop(old, None) or {"sessions": [sid], "closed": False}
        t["queue"] = queue
        d["tickets"][new] = t
        for s in t.get("sessions") or []:
            if isinstance(d["sessions"].get(s), dict):
                d["sessions"][s]["ref"] = new
    _mutate(_record)
    return {"ok": True, "ref": new, "queue": queue}
