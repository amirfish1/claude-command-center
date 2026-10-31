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
called from the one place every spawn route tags its origin) get a NEW ticket
on their own, so turning the flag on never floods the queues. Older sessions
get one when the user files it from the row menu (``file_ticket``) or through
an explicit, bounded ``backfill``.

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
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: in-process lock only
    fcntl = None

from ccc_server import core as _core

FLAG = "idea_tickets"
STATE_FILE_NAME = "idea-tickets.json"
QUEUES = {"idea": "IDEAS", "small_bug": "SMALL-BUGS"}
QUEUE_TYPES = {"IDEAS": "feature", "SMALL-BUGS": "bug"}
# Not a queue: moving a session here detaches it from its ticket.
OTHER = "OTHER"

JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_KEY_ENV = "JEV_API_KEY"
JEV_TIMEOUT_S = 8

_lock = threading.Lock()
_archive_lock = threading.Lock()
_inflight = set()
_queues_ready = set()
_state_cache = {"key": None, "data": None}
_flock_depth = threading.local()
# A claim older than this belongs to a filer that died mid-`wt take`.
CLAIM_STALE_S = 300

_FILED_RE = re.compile(r"^FILED:\s+(\S+)", re.M)
_EDITED_RE = re.compile(r"^EDITED:\s+(\S+)", re.M)
# Session-scoped env vars that would make `wt` record the server's own
# session as the ticket's submitter (and notify it on every change).
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


@contextmanager
def _file_lock():
    """Cross-process lock on the state file. The list build also runs in the
    archive-refresh subprocess, which sweeps the same pending sessions, so an
    in-process lock alone lets two processes overwrite each other's writes."""
    depth = getattr(_flock_depth, "n", 0)
    if fcntl is None or depth:
        _flock_depth.n = depth + 1
        try:
            yield
        finally:
            _flock_depth.n = depth
        return
    path = _state_path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        _flock_depth.n = 1
        try:
            yield
        finally:
            _flock_depth.n = 0
            fcntl.flock(fh, fcntl.LOCK_UN)


def _mutate(fn):
    """Apply ``fn(state)`` under both locks and persist. Returns fn's result."""
    with _file_lock(), _lock:
        _state_cache["key"] = None  # another process may have written
        data = json.loads(json.dumps(load_state()))
        result = fn(data)
        _save_state(data)
        return result


def _claim(sid, *, over_skip=False):
    """Atomically reserve ``sid`` for filing. False when another thread or
    process already filed or is filing it."""
    def _try(d):
        entry = d["sessions"].get(sid)
        if isinstance(entry, dict):
            if entry.get("ref"):
                return False
            if entry.get("filing") and time.time() - int(entry.get("at") or 0) < CLAIM_STALE_S:
                return False
            if entry.get("skip") and not over_skip:
                return False
        d["sessions"][sid] = {"filing": os.getpid(), "at": int(time.time())}
        return True
    return _mutate(_try)


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
            "explicitly_archived": bool(entry.get("explicitly_archived")),
            "moved_at": entry.get("moved_at", 0),
            # A review applies only to this session's existing transcript.
            # The UI invalidates it when newer activity arrives.
            "ready_to_close_reviewed_at": entry.get("ready_to_close_reviewed_at", 0),
        }
        h = ticket.get("handoff")
        if isinstance(h, dict) and h.get("state"):
            out[sid]["handoff"] = {k: h.get(k) for k in
                                   ("state", "auto", "at", "question", "nudges", "session",
                                    "waiting_on")}
    # UI spawns still waiting for their first reply: no ticket yet, but the
    # row belongs in Ideas now rather than in Other.
    for sid in (data.get("pending") or {}):
        if sid not in out:
            out[sid] = {"ref": "", "queue": "IDEAS", "sessions": 1,
                        "closed": False, "pending": True}
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


def _opening_text(sid, jsonl=None):
    text = _first_user_text(jsonl) if jsonl else ""
    if text:
        return text
    # Antigravity's transcript is outside Claude's project JSONL tree and
    # wraps user input in USER_REQUEST/metadata tags.
    try:
        transcript = _core._antigravity_transcript_path(sid)
        if transcript:
            with open(transcript, encoding="utf-8") as fh:
                for i, line in enumerate(fh):
                    if i >= 200:
                        break
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("type") == "USER_INPUT":
                        text = _core._antigravity_user_text(ev.get("content"))
                        if text:
                            return text
    except (AttributeError, OSError):
        pass
    return ""


# ── lifecycle ──────────────────────────────────────────────────────────────

def note_ui_spawn(session_id, via):
    """Spawn-time entry point: remember a session the user started from the
    UI, so its first reply can file a ticket. Any engine."""
    sid = str(session_id or "").strip()
    if not sid or str(via or "") != "ui" or not _enabled():
        return
    state = load_state()
    entry = state["sessions"].get(sid) or {}
    if sid in state["pending"] or entry.get("ref") or entry.get("filing") or entry.get("skip") == "other":
        return
    def _mark(d):
        existing = d["sessions"].get(sid) or {}
        if existing.get("ref") or existing.get("filing") or existing.get("skip") == "other":
            return
        # A fast first reply may have recorded an automatic skip before the
        # launch route supplied its explicit UI origin. Preserve manual Other.
        d["sessions"].pop(sid, None)
        d["pending"].setdefault(sid, {"at": int(time.time())})
    _mutate(_mark)


SWEEP_MAX_PER_CALL = 5


_AUTO_SKIP_PREFIX = "not a UI spawn"


def _auto_skip(entry):
    """True for a skip ``attach_or_file`` recorded on its own (no lineage
    yet), as opposed to a user's move to Other."""
    return (isinstance(entry, dict) and not entry.get("ref")
            and str(entry.get("skip") or "").startswith(_AUTO_SKIP_PREFIX))


def sweep_rows(rows):
    """List-build entry point for engines without a Stop hook (Codex etc.):
    queue ticket creation after a reply, or inherit a continuation's ticket
    as soon as its origin is known.

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
        if not sid or sid in _inflight:
            continue
        # Task-tool subagents ("agent-…") are helpers inside a session, not
        # sessions of their own; they don't join the ticket's log.
        if sid.startswith("agent-"):
            continue
        continuing = _ticketed(row.get("continued_from_session_id"))
        child = _ticketed(row.get("parent_session_id"))
        if sid in known:
            # An automatic skip was recorded before the session's lineage was
            # ticketed (the parent joined its idea later, or the parent link
            # was learned later); a user's move to Other is never undone.
            if not (_auto_skip(known.get(sid)) and (continuing or child)):
                continue
        elif not (sid in pending or continuing or child):
            continue
        # "Has replied": Claude rows carry a reply preview; Codex rows only
        # record that the last event was a turn result.
        if not (continuing or row.get("last_assistant_preview") or row.get("last_assistant_text")
                or row.get("last_event_type") == "result"):
            continue
        queued = on_turn_end(
            sid,
            continued_from=str(row.get("continued_from_session_id") or ""),
            parent=str(row.get("parent_session_id") or ""),
            text=str(row.get("first_message") or ""),
            retry_skip=sid in known,
        )
        if queued.get("queued"):
            started += 1
            if started >= SWEEP_MAX_PER_CALL:
                break
    _sweep_titles(rows, data)
    return started


def _row_title(row):
    """The session's own short title, or "" when it only has a raw opener."""
    name = str(row.get("display_name") or row.get("ai_title") or "").strip()
    if not name or name.startswith("\U0001f9f5"):
        return ""
    if not (row.get("auto_titled") or row.get("name_overridden") or row.get("ai_title")):
        return ""
    first = str(row.get("first_message") or "").strip()
    if first and first.startswith(name.rstrip(".").rstrip()):
        return ""
    return name[:80]


def _sweep_titles(rows, data):
    """Give a ticket its session's title once one exists: the opener's first
    line ("Can we fix this ...") is a poor handle. Only the session that
    started the ticket names it: a continuation or child is one step of a
    longer topic, and its title would rename the whole topic (CCC-263). A dict
    lookup per row; at most SWEEP_MAX_PER_CALL ``wt edit`` threads per call."""
    sessions, tickets = data["sessions"], data["tickets"]
    started = 0
    for row in rows or []:
        sid = str(row.get("session_id") or row.get("id") or "")
        entry = sessions.get(sid) if sid else None
        if not isinstance(entry, dict) or entry.get("kind") != "started":
            continue
        ref = entry.get("ref")
        ticket = tickets.get(ref) if ref else None
        if not ticket or ticket.get("closed") or ticket.get("title_src") == "session":
            continue
        title = _row_title(row)
        if not title or title == ticket.get("title") or ("title", ref) in _inflight:
            continue
        with _lock:
            _inflight.add(("title", ref))
        threading.Thread(target=_retitle, args=(ref, title), daemon=True).start()
        started += 1
        if started >= SWEEP_MAX_PER_CALL:
            break


def _retitle(ref, title):
    try:
        rc, _ = _wt(["edit", ref, "--title", title])
        if rc == 0:
            def _record(d):
                t = d["tickets"].get(ref)
                if t is not None:
                    t["title"], t["title_src"] = title, "session"
            _mutate(_record)
    except Exception as exc:
        _core._log_activity("idea", "IDEA_TICKET_ERR", f"retitle {ref} {exc}")
    finally:
        with _lock:
            _inflight.discard(("title", ref))

def on_turn_end(session_id, *, continued_from="", parent="", text="", retry_skip=False):
    """Stop-hook entry point. Returns at once; the work runs on a thread.
    ``continued_from`` / ``parent`` are lineage hints from a list row.
    ``retry_skip`` lets an automatic skip try again (its lineage got a ticket)."""
    sid = str(session_id or "").strip()
    if not sid or not _enabled():
        return {"queued": False, "reason": "disabled"}
    if sid.startswith("agent-"):
        return {"queued": False, "reason": "subagent"}
    known = load_state()["sessions"]
    if sid in known and not (retry_skip and _auto_skip(known.get(sid))):
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
        def _skip(d):
            # Never overwrite a ticket another process filed meanwhile.
            if sid not in d["sessions"] and sid not in d["pending"]:
                d["sessions"][sid] = {
                    "skip": f"not a UI spawn (spawned_via={via or 'unknown'})",
                    "at": int(time.time())}
        _mutate(_skip)
        return {"ok": True, "skipped": True}
    if sid in state["sessions"]:
        return {"ok": True, "known": True}

    text = _opening_text(sid, jsonl) or str(text_hint or "")
    if not _claim(sid):
        return {"ok": True, "known": True}
    label = (classify_fn or classify)(text)
    return _file_new(sid, QUEUES.get(label, "IDEAS"), text, cwd, label=label)


def _file_new(sid, queue, text, cwd, *, label=""):
    """File a fresh ticket for ``sid`` (already claimed) in ``queue``."""
    _ensure_queue(queue)
    rc, out = _wt([
        # `add`, not `take`: a claim makes WT rename the claiming session to
        # "IDEAS#N: <title>" on every claim and close. The queue's drain is
        # off, so the open ticket is never picked up by a worker.
        "add", "-q", queue,
        "--readiness", "needs-spec", "--type", QUEUE_TYPES[queue],
        "--title", _title_from(text),
        "--text", f"Started in CCC session {sid}.\n\n{text[:2000]}",
    ], cwd=cwd)
    m = _FILED_RE.search(out or "")
    if rc != 0 or not m:
        def _release(d):
            if (d["sessions"].get(sid) or {}).get("filing"):
                d["sessions"].pop(sid, None)
        _mutate(_release)
        _core._log_activity("idea", "IDEA_TICKET_FAIL", f"{sid} rc={rc}")
        return {"ok": False, "error": "wt take failed"}
    ref = m.group(1)

    def _record(d):
        d["tickets"][ref] = {"queue": queue, "sessions": [sid], "closed": False,
                             "title": _title_from(text)}
        d["sessions"][sid] = {"ref": ref, "kind": "started", "at": int(time.time())}
        d["pending"].pop(sid, None)
    _mutate(_record)
    _restamp_rows()
    _core._log_activity("idea", "IDEA_TICKET", f"{sid} -> {ref} ({label or queue})")
    return {"ok": True, "ref": ref, "queue": queue}


def file_ticket(sid, queue):
    """Row-menu entry point for a session with no ticket (started before the
    flag, or outside the UI): file one in the queue the user picked."""
    jsonl = None
    try:
        jsonl = _core._find_session_jsonl(sid)
    except Exception:
        jsonl = None
    _via, _parent, cwd = _spawn_meta(sid)
    text = _opening_text(sid, jsonl)
    if not _claim(sid, over_skip=True):
        ticket = ticket_for_session(sid)
        return ({"ok": True, "ref": ticket["ref"], "queue": ticket["queue"]} if ticket
                else {"ok": False, "error": "ticket is being filed"})
    return _file_new(sid, queue, text, cwd)


def backfill(session_ids):
    """One-off backfill for sessions that predate the flag. Files (or, for a
    continuation or child of an already-ticketed session, attaches) each one
    in order on a single thread, so pass them oldest first and lineage joins
    the origin's ticket instead of opening a new one."""
    data = load_state()
    todo = []
    for raw in session_ids or []:
        sid = str(raw or "").strip()
        entry = data["sessions"].get(sid)
        if (not sid or sid.startswith("agent-") or sid in todo
                or (isinstance(entry, dict) and entry.get("ref"))):
            continue
        todo.append(sid)
    if not todo or not _enabled():
        return {"ok": True, "queued": 0}

    def _mark(d):
        for sid in todo:
            if (d["sessions"].get(sid) or {}).get("skip"):
                d["sessions"].pop(sid, None)
            if sid not in d["sessions"]:
                d["pending"].setdefault(sid, {"at": int(time.time()), "backfill": True})
    _mutate(_mark)

    def _run():
        for sid in todo:
            with _lock:
                if sid in _inflight:
                    continue
                _inflight.add(sid)
            _attach_or_file_safe(sid)
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "queued": len(todo)}


_RECENT_BACKFILL_MARKER_NAME = "idea-tickets-recent-backfill-done"
_RECENT_BACKFILL_WINDOW_S = 2 * 24 * 3600
_RECENT_BACKFILL_MAX = 200


def _recent_backfill_marker_path():
    return Path(_core.COMMAND_CENTER_STATE_DIR) / _RECENT_BACKFILL_MARKER_NAME


def backfill_recent_once(rows):
    """First list build after idea_tickets turns on: ticket sessions active
    in the last 2 days, so upgrading doesn't leave recent work untracked.
    Reads only the rows the list already built for this call -- no extra
    scan -- and runs exactly once per machine (a marker file gates it)."""
    if not _enabled():
        return
    marker = _recent_backfill_marker_path()
    if marker.exists():
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(int(time.time())))
    except OSError:
        return
    cutoff = time.time() - _RECENT_BACKFILL_WINDOW_S
    dated = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("session_id") or row.get("id") or "").strip()
        if not sid or sid.startswith("agent-"):
            continue
        ts = row.get("last_interacted") or row.get("mtime") or row.get("modified") or 0
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            dated.append((ts, sid))
    dated.sort(key=lambda pair: pair[0])
    backfill([sid for _, sid in dated[:_RECENT_BACKFILL_MAX]])


def _restamp_rows():
    # The sidebar serves cached rows; restamp so the new ticket shows at once.
    try:
        _core._restamp_archive_serve_cache_after_mutation()
    except Exception:
        pass


def _attach(sid, ref, kind, origin):
    def _record(d):
        if (d["sessions"].get(sid) or {}).get("ref"):
            return  # another process got there first
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


def note_explicit_archive(session_ids, archived):
    """Keep the owner's put-away action separate from automatic completion."""
    if not _enabled():
        return
    def record(data):
        for sid in session_ids:
            entry = data["sessions"].get(sid)
            if isinstance(entry, dict) and entry.get("ref"):
                entry["explicitly_archived"] = bool(archived)
    _mutate(record)


def resume_after_owner_input(sid):
    """Accepted human input reopens completed work, including a closed ticket."""
    ticket = ticket_for_session(sid)
    archived = sid in _core._load_archived_conversations(sweep=False)
    if not archived and not (ticket and ticket.get("closed")):
        return
    changed, _, _, _ = _core._set_conversations_archived([sid], False, source="composer")
    if sid not in changed:
        on_archive(sid, False)
    _restamp_rows()


def on_archive(session_id, archived):
    """Archiving a ticket's latest session closes it; unarchiving reopens it.
    Earlier-session archive events stay in CCC's local lifecycle log."""
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
    # Group archive/unarchive calls arrive once per member and can run in
    # parallel. Serialize the external transition, then re-read state instead
    # of trusting the pre-thread snapshot (every unarchive saw closed=True).
    with _archive_transition_lock():
        _archive_worker_locked(sid, ticket, archived)


@contextmanager
def _archive_transition_lock():
    # Separate from the state lock: a slow wt command must not block filing
    # or state writes. The file lock also covers another dashboard process.
    with _archive_lock:
        lock_path = _state_path().with_suffix(".archive.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a") as fh:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fh, fcntl.LOCK_UN)


def _archive_worker_locked(sid, ticket, archived):
    _state_cache["key"] = None
    current = ticket_for_session(sid)
    if not current or current["ref"] != ticket["ref"]:
        return
    ref = current["ref"]
    latest = current["sessions"][-1] if current["sessions"] else sid
    try:
        # A newer action can overtake a queued worker; never apply the stale
        # action after its session has already changed lifecycle state again.
        load_archived = getattr(_core, "_load_archived_conversations", None)
        if callable(load_archived) and (sid in load_archived(sweep=False)) != archived:
            return
        if archived and sid == latest and not current["closed"]:
            rc, _ = _wt(["close", ref, "--force", "--no-code",
                         "--summary", f"Session {sid} archived in CCC."])
            if rc == 0:
                _mutate(lambda d: d["tickets"][ref].__setitem__("closed", True))
        elif not archived and current["closed"]:
            rc, _ = _wt(["reopen", ref, "--reason", f"Session {sid} unarchived in CCC."])
            if rc == 0:
                _mutate(lambda d: d["tickets"][ref].__setitem__("closed", False))
    except Exception as exc:
        _core._log_activity("idea", "IDEA_TICKET_ERR", f"archive {sid} {exc}")


def note_manual_move(session_id):
    """Keep an explicitly moved session inside the destination's time window."""
    moved_at = time.time()

    def record(data):
        entry = data["sessions"].get(session_id)
        if isinstance(entry, dict) and entry.get("ref"):
            entry["moved_at"] = moved_at

    _mutate(record)
    return moved_at


def move_ticket(session_id, queue):
    """Move a session's ticket to IDEAS or SMALL-BUGS, or to OTHER (detach it,
    see ``detach_ticket``). Synchronous: the row menu waits for the new ref so
    the sidebar regroups at once."""
    sid = str(session_id or "").strip()
    queue = str(queue or "").strip().upper()
    if queue == OTHER:
        return detach_ticket(sid)
    if queue not in QUEUE_TYPES:
        return {"ok": False, "error": "queue must be IDEAS, SMALL-BUGS or OTHER"}
    ticket = ticket_for_session(sid)
    if not ticket:
        return file_ticket(sid, queue) if sid else {"ok": False, "error": "no session"}
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


def detach_ticket(session_id):
    """Drag to the Other tab: the session is neither an idea nor a small bug.
    Close its CCC-owned ticket and mark every session on it skipped, so the
    sweep never re-files it and continuations stop inheriting it. Dragging it
    back to Ideas / Small bugs files a fresh ticket (``file_ticket``)."""
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "no session"}
    ticket = ticket_for_session(sid)
    if ticket and not ticket["closed"]:
        rc, _ = _wt(["close", ticket["ref"], "--force", "--no-code",
                     "--summary", f"Session {sid} moved to Other in CCC."])
        if rc != 0:
            return {"ok": False, "error": "wt close failed"}
    now = int(time.time())

    def _record(d):
        sids = set(ticket["sessions"] if ticket else []) | {sid}
        if ticket:
            d["tickets"].pop(ticket["ref"], None)
        for s in sids:
            d["sessions"][s] = {"skip": "other", "at": now}
            d["pending"].pop(s, None)
    _mutate(_record)
    return {"ok": True, "ref": "", "queue": OTHER}



# ── Autopilot and handoff ──────────────────────────────────────────────────
#
# A ticketed session that ends a turn is in one of three places: done (its
# footer says closure is met), waiting on the owner (a question, a decision
# or a user-owned action), or waiting on an agent (work remains that the
# agent itself owns). The owner only needs to see the middle case. Done
# tickets close and archive themselves; agent-owned tails are handed off:
# CCC tells the session it owns the ticket and watches it until the ticket
# closes, or blocks on a question with the regular "needs input" route.
#
# WT's reconciler never treats an ambient (non-WT-spawned) claimer as dead,
# so CCC is the watchdog for handed-off sessions: it nudges a stalled one a
# few times, then blocks the ticket on the owner.

AUTOPILOT_QUIET_S = 120
# Older sessions are left alone: turning the autopilot on must not act on a
# whole backlog in one burst.
AUTOPILOT_MAX_AGE_S = 2 * 86400
AUTOPILOT_MAX_PER_CALL = 3
HANDOFF_WATCH_EVERY_S = 60
HANDOFF_STALL_S = 20 * 60
HANDOFF_MAX_NUDGES = 3
HANDOFF_QUEUE = "SMALL-BUGS"
_ACTIVE_HANDOFF = ("running", "blocked", "waiting")

_NONE_STEP_RE = re.compile(
    r"^(?:none|nothing|no action(?: is)?(?: needed| required)?|n/?a|not applicable)?"
    r"\s*[.!]?\s*(?:[—–(:-].*)?$", re.I | re.S)
_OWNER_RE = re.compile(
    r"\bowner\s*[:=]\s*(agent|user|external|amir|human)\b"
    r"|^\s*\(?(agent|user|external|amir|human)\)?\s*[:\u2014\u2013-]", re.I)
_BUSY_STATES = ("working", "running", "thinking", "tool")
_watch = {"at": 0.0, "running": False}


def _is_none(text):
    return bool(_NONE_STEP_RE.match(str(text or "").strip()))


def _action_owner(action):
    """'agent' | 'user' | 'external' for a remaining action ('' unknown)."""
    if not isinstance(action, dict):
        return ""
    owner = str(action.get("owner") or "").strip().lower()
    if not owner:
        m = _OWNER_RE.search(str(action.get("text") or ""))
        owner = (m.group(1) or m.group(2)).lower() if m else ""
    return {"amir": "user", "human": "user"}.get(owner, owner)


def attention(session_state):
    """Classify a session's last footer: 'needs_you', 'done', 'handoff' or ''.

    '' means there is no valid footer to act on. Work that only waits on an
    external party (a scheduled release, a vendor) counts as done: routine
    jobs pick that up, so the owner's list shouldn't keep it.
    """
    ss = session_state if isinstance(session_state, dict) else {}
    if not _is_none(ss.get("next_step_user")):
        return "needs_you"
    comp = ss.get("completion")
    if not isinstance(comp, dict) or not comp.get("valid"):
        return ""
    if comp.get("ready_to_close"):
        return "done"
    try:
        decision = float((comp.get("decision_dependency") or {}).get("score") or 0)
    except (TypeError, ValueError):
        decision = 0.0
    if decision > 0:
        return "needs_you"
    if any(isinstance(c, dict) and c.get("blocking") for c in comp.get("caveats") or []):
        # A blocking caveat without an action or decision is a judgment call
        # the agent couldn't make on its own.
        if not comp.get("remaining_actions"):
            return "needs_you"
    owners = [_action_owner(a) for a in comp.get("remaining_actions") or []]
    if "user" in owners:
        return "needs_you"
    closure_met = bool((comp.get("scope") or {}).get("closure_met"))
    if owners and all(o == "external" for o in owners) and closure_met:
        return "done"
    if not owners and closure_met:
        return "done"
    return "handoff"


def _row_busy(row):
    if not isinstance(row, dict):
        return False
    for key in ("state", "codex_state"):
        if str(row.get(key) or "").lower() in _BUSY_STATES:
            return True
    if row.get("last_event_type") == "user":
        return True  # a turn was sent and hasn't answered yet
    if str(row.get("sidecar_status") or "").lower() in ("running", "queued", "starting"):
        return True
    return any(row.get(k) for k in ("sidecar_in_flight", "pending_spawn", "pending_tool",
                                    "needs_approval", "question_waiting", "stale_tool_call"))


def _row_mtime(row):
    for key in ("mtime", "modified", "last_interacted"):
        try:
            value = float(row.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value:
            return value
    return 0.0


def _autopilot_allowed():
    # The list also builds inside the archive-refresh subprocess; only the
    # serving process may inject into sessions or start the watchdog.
    import sys
    return "--archive-refresh-worker" not in sys.argv


def _ticket_views(rows, data):
    """ref -> {"lead": row, "rows": [member rows]} for ticketed rows."""
    sessions = data["sessions"]
    views = {}
    for row in rows or []:
        sid = str(row.get("session_id") or row.get("id") or "")
        entry = sessions.get(sid) if sid else None
        ref = entry.get("ref") if isinstance(entry, dict) else None
        if not ref:
            continue
        view = views.setdefault(ref, {"lead": None, "rows": []})
        view["rows"].append(row)
        if entry.get("kind") == "child":
            continue
        lead = view["lead"]
        if lead is None or _row_mtime(row) > _row_mtime(lead):
            view["lead"] = row
    return views


def autopilot_rows(rows, *, now=None):
    """List-build entry point: close+archive done tickets, hand off agent
    tails, and tick the handoff watchdog. Cheap dict work per row; wt calls
    and injects run on daemon threads, at most AUTOPILOT_MAX_PER_CALL."""
    if not _enabled() or not _autopilot_allowed():
        return 0
    now = time.time() if now is None else now
    data = load_state()
    views = _ticket_views(rows, data)
    started = 0
    for ref, view in views.items():
        ticket = data["tickets"].get(ref) or {}
        lead = view["lead"]
        if not lead or ticket.get("closed") or ("autopilot", ref) in _inflight:
            continue
        if (ticket.get("handoff") or {}).get("state") in _ACTIVE_HANDOFF:
            continue
        lead_sid = str(lead.get("session_id") or lead.get("id") or "")
        lead_mtime = _row_mtime(lead)
        if lead_mtime <= float(ticket.get("autopilot_mtime") or 0):
            continue  # already acted on this turn (or the owner undid it)
        if lead.get("archived") or lead.get("trashed") or lead.get("recently_unarchived"):
            continue
        if any(_row_busy(r) for r in view["rows"]):
            continue
        newest = max(_row_mtime(r) for r in view["rows"])
        if now - newest < AUTOPILOT_QUIET_S or now - lead_mtime > AUTOPILOT_MAX_AGE_S:
            continue
        verdict = attention(lead.get("session_state"))
        if verdict == "done":
            target = _autopilot_done
        elif verdict == "handoff":
            target = _autopilot_handoff
        else:
            continue
        with _lock:
            _inflight.add(("autopilot", ref))
        did = str((lead.get("session_state") or {}).get("did") or "").strip()
        threading.Thread(target=target, args=(ref, lead_sid, lead_mtime, did),
                         daemon=True).start()
        started += 1
        if started >= AUTOPILOT_MAX_PER_CALL:
            break
    _tick_watchdog(views, data, now)
    return started


def _mark_autopilot(ref, mtime, **extra):
    def _record(d):
        t = d["tickets"].get(ref)
        if t is not None:
            t["autopilot_mtime"] = max(float(t.get("autopilot_mtime") or 0), float(mtime))
            t.update(extra)
    _mutate(_record)


def _close_and_archive(ref, sid, summary, source):
    """Close the ticket with ``summary``, then archive its session group."""
    rc, _ = _wt(["close", ref, "--force", "--no-code", "--summary", summary[:1500]])
    if rc != 0:
        state = (_find(ref) or {}).get("status")
        if state != "closed":
            _core._log_activity("idea", "IDEA_TICKET_ERR", f"{source} close {ref} rc={rc}")
            return False
    _mutate(lambda d: d["tickets"].get(ref, {}).__setitem__("closed", True))
    _archive_group(sid, source)
    return True


def _archive_group(sid, source):
    try:
        _core._set_conversations_archived([sid], True, source=source)
    except Exception as exc:
        _core._log_activity("idea", "IDEA_TICKET_ERR", f"{source} archive {sid} {exc}")
    _restamp_rows()


def _autopilot_done(ref, sid, mtime, did):
    try:
        _mark_autopilot(ref, mtime)
        summary = (did or f"Session {sid} reported the work complete.") + \
            " (Closed by CCC autopilot: the session's footer met its closure criteria.)"
        if _close_and_archive(ref, sid, summary, "autopilot-done"):
            _core._log_activity("idea", "AUTOPILOT_DONE", f"{ref} {sid}")
    except Exception as exc:
        _core._log_activity("idea", "IDEA_TICKET_ERR", f"autopilot-done {ref} {exc}")
    finally:
        with _lock:
            _inflight.discard(("autopilot", ref))


def _autopilot_handoff(ref, sid, mtime, did):
    try:
        _mark_autopilot(ref, mtime)
        handoff(sid, auto=True)
    except Exception as exc:
        _core._log_activity("idea", "IDEA_TICKET_ERR", f"autopilot-handoff {ref} {exc}")
    finally:
        with _lock:
            _inflight.discard(("autopilot", ref))


def _handoff_prompt(ref, auto):
    why = ("CCC's autopilot handed this session off: your last turn ended with "
           "work you own still remaining." if auto else
           "The owner clicked Hand off: they are not watching this session.")
    return (
        f"[CCC handoff] {why} You now own WatchTower ticket {ref}. Drive the "
        "remaining work to a verified, delivered close on your own: finish your "
        "remaining actions, wait for deploys or CI with background waits (never "
        "a long foreground poll), and verify the result.\n\n"
        f"When it is done, close the ticket:\n"
        f"  wt close {ref} --summary \"<what shipped and how it was verified>\" "
        "--commit <SHA>   (or --no-code when nothing was committed)\n\n"
        "If you truly need the owner (a decision, access, or approval), ask once "
        "and end your turn:\n"
        f"  wt block {ref} --kind input --wait-on owner --question "
        "\"<one self-contained question>\"\n"
        "The answer will be sent back to this session. Work that only waits on a "
        "routine job (a scheduled release) does not need the owner: close the "
        "ticket and name that job in the summary. Work scheduled for a later "
        "date: file a follow-up ticket (or schedule it) and close this one.\n\n"
        "Do not take outward-facing or irreversible steps the owner has not "
        "already approved in this session (messages to clients or vendors, "
        "payments, deletions, public posts): block and ask instead."
    )


def _inject(sid, text, source):
    try:
        result = _core._inject_text_into_session(sid, text, mode="send", source=source)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    if isinstance(result, dict):
        return result if "ok" in result else {**result, "ok": True}
    return {"ok": bool(result)}


def _set_handoff(ref, **fields):
    def _record(d):
        t = d["tickets"].get(ref)
        if t is None:
            return None
        h = dict(t.get("handoff") or {})
        h.update(fields)
        t["handoff"] = h
        return h
    return _mutate(_record)


def handoff(session_id, *, auto=False):
    """Hand a session's work to CCC: the session owns its ticket (filed in
    SMALL-BUGS if it has none) and the watchdog follows it to a close."""
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "no session"}
    if not _enabled():
        return {"ok": False, "error": "idea_tickets is off"}
    ticket = ticket_for_session(sid)
    if not ticket:
        filed = file_ticket(sid, HANDOFF_QUEUE)
        if not filed.get("ok"):
            return filed
        ticket = ticket_for_session(sid)
        if not ticket:
            return {"ok": False, "error": "ticket not recorded"}
    ref = ticket["ref"]
    if ticket["closed"]:
        return {"ok": False, "error": f"{ref} is already closed"}
    current = load_state()["tickets"].get(ref, {}).get("handoff") or {}
    if current.get("state") in _ACTIVE_HANDOFF and current.get("session") == sid:
        return {"ok": True, "ref": ref, "already": True}
    sent = _inject(sid, _handoff_prompt(ref, auto), "handoff")
    if not sent.get("ok"):
        _core._log_activity("idea", "HANDOFF_FAIL", f"{ref} {sid} {sent.get('error')}")
        return {"ok": False, "error": sent.get("error") or "inject failed", "ref": ref}
    now = int(time.time())
    _set_handoff(ref, state="running", session=sid, auto=bool(auto), at=now,
                 nudges=0, nudged_at=0, question="")
    _wt(["comment", ref, ("Auto-handed off" if auto else "Handed off") +
         f" to session {sid}; CCC watches it until the ticket closes.", "--by", "system"])
    _core._log_activity("idea", "HANDOFF", f"{ref} {sid} auto={bool(auto)}")
    _restamp_rows()
    return {"ok": True, "ref": ref}


def takeback(session_id):
    """Stop watching: the owner manages the session again."""
    ticket = ticket_for_session(str(session_id or "").strip())
    if not ticket:
        return {"ok": False, "error": "no ticket"}
    ref = ticket["ref"]
    _set_handoff(ref, state="taken_back", taken_back_at=int(time.time()))
    _wt(["comment", ref, "Taken back by the owner; CCC stopped watching.", "--by", "system"])
    _restamp_rows()
    return {"ok": True, "ref": ref}


def answer(session_id, text):
    """Answer a blocked handoff: record it on the ticket and send it to the
    session (WT can't resume an unclaimed ticket's session itself)."""
    text = str(text or "").strip()
    ticket = ticket_for_session(str(session_id or "").strip())
    if not ticket or not text:
        return {"ok": False, "error": "no ticket" if not ticket else "empty answer"}
    ref = ticket["ref"]
    h = load_state()["tickets"].get(ref, {}).get("handoff") or {}
    target = h.get("session") or ticket["sessions"][-1]
    _wt(["answer", ref, text])
    sent = _inject(target, (
        f"[CCC handoff] The owner answered your question on {ref}:\n\n{text}\n\n"
        f"Continue driving {ref} to a verified close (wt close {ref} ... when done, "
        f"or wt block {ref} --kind input --wait-on owner --question ... if stuck again)."),
        "handoff-answer")
    if not sent.get("ok"):
        return {"ok": False, "error": sent.get("error") or "inject failed", "ref": ref}
    _set_handoff(ref, state="running", at=int(time.time()), nudges=0, nudged_at=0,
                 question="", session=target)
    _restamp_rows()
    return {"ok": True, "ref": ref}


def _find(ref):
    rc, out = _wt(["find", ref, "--json"], timeout=20)
    if rc != 0:
        return None
    try:
        data = json.loads(out[out.index("{"):]) if "{" in out else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _tick_watchdog(views, data, now):
    """Throttled: one background pass over handed-off tickets per minute."""
    active = {ref: t["handoff"] for ref, t in data["tickets"].items()
              if not t.get("closed")
              and (t.get("handoff") or {}).get("state") in _ACTIVE_HANDOFF}
    if not active or _watch["running"] or now - _watch["at"] < HANDOFF_WATCH_EVERY_S:
        return
    snap = {}
    for ref in active:
        view = views.get(ref) or {}
        h_sid = active[ref].get("session")
        row = next((r for r in view.get("rows") or []
                    if str(r.get("session_id") or r.get("id") or "") == h_sid), None)
        snap[ref] = {
            "busy": any(_row_busy(r) for r in view.get("rows") or []),
            "mtime": max([_row_mtime(r) for r in view.get("rows") or []] or [0]),
            "session_state": (row or view.get("lead") or {}).get("session_state"),
            "seen": row is not None,
        }
    _watch["at"], _watch["running"] = now, True
    threading.Thread(target=_watchdog_pass, args=(active, snap), daemon=True).start()


def _watchdog_pass(active, snap):
    try:
        for ref, h in active.items():
            try:
                _watch_one(ref, h, snap.get(ref) or {})
            except Exception as exc:
                _core._log_activity("idea", "IDEA_TICKET_ERR", f"watchdog {ref} {exc}")
    finally:
        _watch["running"] = False


def _watch_one(ref, h, info, *, now=None):
    now = time.time() if now is None else now
    sid = h.get("session") or ""
    item = _find(ref)
    if item is None:
        return
    if item.get("status") == "closed":
        _mutate(lambda d: d["tickets"].get(ref, {}).__setitem__("closed", True))
        _set_handoff(ref, state="closed", closed_at=int(now))
        if sid:
            _archive_group(sid, "handoff-closed")
        _core._log_activity("idea", "HANDOFF_CLOSED", f"{ref} {sid}")
        return
    if item.get("needs_input") or item.get("status") == "awaiting_answer":
        question = str(item.get("block_question") or "").strip()
        # A block that waits on another ticket, a worker or an outside party
        # is not the owner's to answer: show it as waiting, no Answer chip,
        # and don't nudge (the session parked on purpose).
        wait = item.get("wait") if isinstance(item.get("wait"), dict) else {}
        on = str(wait.get("on") or "owner")
        state = "blocked" if on == "owner" else "waiting"
        waiting_on = "" if on == "owner" else str(
            wait.get("ref") or wait.get("actor") or on)
        if (h.get("state"), h.get("question"), h.get("waiting_on") or "") != (
                state, question, waiting_on):
            _set_handoff(ref, state=state, question=question, waiting_on=waiting_on)
            _restamp_rows()
        return
    if h.get("state") in ("blocked", "waiting"):
        # Answered outside CCC (wt answer, the WT dashboard): back to running.
        _set_handoff(ref, state="running", question="", at=int(now), nudges=0)
        return
    if info.get("busy") or not info.get("seen"):
        return
    since = max(float(info.get("mtime") or 0), float(h.get("at") or 0),
                float(h.get("nudged_at") or 0))
    if now - since < HANDOFF_STALL_S:
        return
    # Idle long enough. If the session's own footer says done or asks the
    # owner, act on that instead of nudging blindly.
    ss = info.get("session_state") or {}
    verdict = attention(ss)
    if verdict == "done" and float(info.get("mtime") or 0) > float(h.get("at") or 0):
        did = str(ss.get("did") or "").strip()
        summary = (did or f"Session {sid} reported the work complete.") + \
            " (Closed by CCC after handoff: the session's footer met its closure criteria.)"
        if _close_and_archive(ref, sid, summary, "handoff-done"):
            _set_handoff(ref, state="closed", closed_at=int(now))
        return
    if verdict == "needs_you" and float(info.get("mtime") or 0) > float(h.get("at") or 0):
        question = str(ss.get("next_step_user") or "").strip() or \
            "The handed-off session is waiting on the owner; see its last reply."
        _block(ref, question)
        return
    nudges = int(h.get("nudges") or 0)
    if nudges >= HANDOFF_MAX_NUDGES:
        did = str(ss.get("did") or "").strip()
        _block(ref, f"The handed-off session stopped {nudges} times without closing {ref}."
               + (f" Its last update: {did}" if did else "")
               + " Answer with guidance, or take it back.")
        return
    sent = _inject(sid, (
        f"[CCC handoff] Reminder: you own {ref} and it is still open. Continue "
        "the remaining work now. Close it with wt close when verified, or "
        f"wt block {ref} --kind input --wait-on owner --question \"...\" if you "
        "need the owner."), "handoff-nudge")
    if sent.get("ok"):
        _set_handoff(ref, nudges=nudges + 1, nudged_at=int(now))
        _core._log_activity("idea", "HANDOFF_NUDGE", f"{ref} {sid} #{nudges + 1}")


def _block(ref, question):
    rc, _ = _wt(["block", ref, "--force", "--kind", "input", "--wait-on", "owner",
                 "--question", question[:1500]])
    if rc == 0:
        _set_handoff(ref, state="blocked", question=question[:1500])
        _restamp_rows()
        _core._log_activity("idea", "HANDOFF_BLOCKED", ref)
