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
            # A review applies only to this session's existing transcript.
            # The UI invalidates it when newer activity arrives.
            "ready_to_close_reviewed_at": entry.get("ready_to_close_reviewed_at", 0),
        }
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
        if not sid or sid in known or sid in _inflight:
            continue
        # Task-tool subagents ("agent-…") are helpers inside a session, not
        # sessions of their own; they don't join the ticket's log.
        if sid.startswith("agent-"):
            continue
        continuing = _ticketed(row.get("continued_from_session_id"))
        if not (sid in pending
                or continuing
                or _ticketed(row.get("parent_session_id"))):
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
    line ("Can we fix this ...") is a poor handle. A dict lookup per row; at
    most SWEEP_MAX_PER_CALL ``wt edit`` threads per call."""
    sessions, tickets = data["sessions"], data["tickets"]
    started = 0
    for row in rows or []:
        sid = str(row.get("session_id") or row.get("id") or "")
        entry = sessions.get(sid) if sid else None
        ref = entry.get("ref") if isinstance(entry, dict) else None
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
            rc, _ = _wt(["close", ref, "--force", "--no-code",
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

