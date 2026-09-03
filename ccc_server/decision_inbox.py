"""Decision Inbox daemon + idle-session token governor (stdlib-only).

Problem this solves: stalled projects quietly become the owner's problem
again. A board row sits on "Blocked" for weeks, a WatchTower queue ages
without a worker, a session parks mid-task for hours -- and nobody does the
thinking until the owner stumbles on it. Separately, live sessions sometimes
burn tokens while stuck (same tool error over and over, "working" for an
hour with no file touched, context at 90% and never compacted) and nothing
flags it.

This module runs an hourly scan (a daemon thread the server starts; no
launchd job) that:

1. Collects *stalled candidates* from three sources:
   * a Strategy Board markdown table (rows marked Blocked, or Open with an
     ETA in the past) -- the board path lives in the user's config file,
     never in this repo;
   * WatchTower queues that are stuck with open tickets older than a
     threshold (one ``wt status --json`` per run, one ``wt ls`` per
     surfaced queue, never one per ticket);
   * CCC sessions that are live but idle for > N hours with an unfinished
     last task (pending tool, open goal, working state).
   For each candidate a cheap headless analyst (Sonnet by default) writes a
   *decision card*: up to three options with a cost each and exactly one
   recommended. The owner clicks one option and CCC spawns/injects the
   follow-through.

2. Runs the *token governor* over live sessions only (candidacy-gated: the
   live-id set intersected with the cached archive rows, tail bytes of the
   transcript only, no subprocess per row) and surfaces sessions that show
   repeated identical tool errors, no file edits for > 45 min while still
   working, or context >= 85% without compaction. Each finding gets a card
   with fixed pause / nudge / kill options -- no analyst call needed.

Anti-spam contract: at most ``max_cards_per_run`` new cards per run, and a
source id (``board:<slug>``, ``wt:<QUEUE>``, ``session:<sid>``,
``governor:<sid>:<kind>``) that already has an open card, or was decided or
dismissed within the last week, is skipped.

Every server name is reached through ``_core`` at call time, and every
entry point takes explicit inputs (rows, live ids, board text, wt output,
an analyst callable) so tests run the whole pipeline without server.py,
subprocesses, or a real transcript tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ccc_server import core as _core

CONFIG_FILE_NAME = "decision-inbox.json"
STATE_DIR_NAME = "decision-inbox"
CARDS_FILE_NAME = "cards.json"
RUNS_FILE_NAME = "runs.jsonl"

DEFAULT_CONFIG = {
    "enabled": True,
    # Scan cadence. The loop sleeps this long between runs.
    "interval_s": 3600,
    # Markdown file with a `| Category | Task | Status | ETA | Notes |` table.
    # Empty = source disabled. Personal paths belong in the config file.
    "strategy_board": "",
    # Anti-spam cap on NEW cards per run (analyst + governor together).
    "max_cards_per_run": 5,
    # A live session idle at least this long with an unfinished task = stalled.
    "idle_hours": 2.0,
    # A stuck WatchTower queue only surfaces once its oldest open ticket is
    # at least this old.
    "wt_age_days": 3.0,
    # Governor thresholds.
    "no_edit_minutes": 45,
    "context_pct": 85,
    "repeat_error_count": 3,
    # Analyst + follow-through models (headless `claude -p`).
    "model": "claude-sonnet-5",
    "follow_model": "claude-sonnet-5",
    # cwd for spawned follow-through sessions when the card has none.
    "spawn_cwd": "",
    # Skip a source id that was decided/dismissed within this window.
    "dedupe_days": 7.0,
    # Ignore board rows / sessions whose text matches (case-insensitive).
    "ignore_patterns": [],
}

# Hosts that count as "the owner asked to stop watching this".
CLOSED_STATUSES = ("decided", "dismissed")

_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_BOARD_BLOCKED_RE = re.compile(r"blocked", re.IGNORECASE)
_BOARD_OPEN_RE = re.compile(r"\bopen\b", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RELATIVE_ETA_GRACE = {
    # Relative words are anchored to the board file's mtime: the row was
    # written no later than the last save, so "tomorrow" older than two days
    # is past. Wider words get wider grace.
    "today": 1, "tonight": 1, "tomorrow": 2, "this week": 7, "next week": 14,
    "this month": 31,
}

_lock = threading.Lock()
_run_lock = threading.Lock()
_running = {"since": None, "thread": None}
_last_findings = {"governor": [], "at": None}


# ── paths / config / state ───────────────────────────────────────────────────

def _state_root():
    try:
        base = Path(_core.COMMAND_CENTER_STATE_DIR)
    except Exception:
        base = Path.home() / ".claude" / "command-center"
    return base


def config_path():
    return _state_root() / CONFIG_FILE_NAME


def state_dir():
    return _state_root() / STATE_DIR_NAME


def cards_path():
    return state_dir() / CARDS_FILE_NAME


def runs_path():
    return state_dir() / RUNS_FILE_NAME


def load_config(path=None):
    """Defaults overlaid with the user's JSON file. Unknown keys pass through
    (a forward-compatible file must not break an older server)."""
    cfg = dict(DEFAULT_CONFIG)
    p = Path(path) if path else config_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        cfg.update(raw)
    env_board = os.environ.get("CCC_DECISION_INBOX_BOARD")
    if env_board:
        cfg["strategy_board"] = env_board
    cfg["strategy_board"] = os.path.expanduser(str(cfg.get("strategy_board") or ""))
    cfg["spawn_cwd"] = os.path.expanduser(str(cfg.get("spawn_cwd") or ""))
    return cfg


def load_cards(path=None):
    p = Path(path) if path else cards_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def save_cards(cards, path=None):
    p = Path(path) if path else cards_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cards, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _append_run(record, path=None):
    p = Path(path) if path else runs_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def last_run(path=None):
    p = Path(path) if path else runs_path()
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            f.seek(max(0, size - 64_000))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            return rec
    return None


def _iso(ts):
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parse_iso(s):
    if not s:
        return None
    try:
        s = str(s).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _slug(text, n=48):
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:n] or "x"


def _ignored(text, cfg):
    pats = cfg.get("ignore_patterns") or []
    if not isinstance(pats, list):
        return False
    low = str(text or "").lower()
    return any(str(p).lower() in low for p in pats if str(p).strip())


# ── source 1: Strategy Board ─────────────────────────────────────────────────

def _split_table_row(line):
    line = line.strip()
    if not line.startswith("|"):
        return None
    cells = [c.strip() for c in line.strip("|").split("|")]
    return cells


def eta_is_past(eta, *, file_mtime, now):
    """(past?, reason). ISO dates compare directly; relative words anchor to
    the board's last save + a grace window; anything else is unknown."""
    text = str(eta or "").strip()
    if not text or text == "-":
        return False, ""
    m = _ISO_DATE_RE.search(text)
    if m:
        try:
            due = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                           23, 59, tzinfo=timezone.utc).timestamp()
        except ValueError:
            return False, ""
        if due < now:
            return True, f"ETA {m.group(0)} passed"
        return False, ""
    low = text.lower()
    for word, days in _RELATIVE_ETA_GRACE.items():
        if word in low:
            if file_mtime and (now - file_mtime) > days * 86400:
                age_d = int((now - file_mtime) // 86400)
                return True, f'ETA "{text}" written {age_d}d ago'
            return False, ""
    return False, ""


def parse_strategy_board(text, *, file_mtime, now, cfg=None):
    """Candidates from a markdown table: any row whose status says Blocked,
    or says Open with an ETA in the past. Header/separator rows and rows
    without a task cell are skipped; column order is taken from the header
    when it names Status/ETA, else positional (task=1, status=2, eta=3)."""
    cfg = cfg or {}
    out = []
    col = {"task": 1, "status": 2, "eta": 3, "notes": 4, "category": 0}
    header_seen = False
    for line in (text or "").splitlines():
        cells = _split_table_row(line)
        if not cells:
            continue
        if not header_seen:
            lowered = [c.lower() for c in cells]
            for i, c in enumerate(lowered):
                if "task" in c or "project" in c:
                    col["task"] = i
                elif "status" in c:
                    col["status"] = i
                elif "eta" in c:
                    col["eta"] = i
                elif "focus" in c or "next" in c or "note" in c:
                    col["notes"] = i
                elif "category" in c:
                    col["category"] = i
            header_seen = True
            continue
        if all(set(c) <= set(":- ") for c in cells):
            continue  # separator row
        get = lambda k: cells[col[k]] if col[k] < len(cells) else ""  # noqa: E731
        task = re.sub(r"\*\*", "", get("task")).strip()
        status = get("status")
        eta = get("eta")
        notes = get("notes")
        if not task:
            continue
        if _ignored(task + " " + notes, cfg):
            continue
        reason = ""
        if _BOARD_BLOCKED_RE.search(status):
            reason = "marked Blocked"
        elif _BOARD_OPEN_RE.search(status):
            past, why = eta_is_past(eta, file_mtime=file_mtime, now=now)
            if past:
                reason = why
        if not reason:
            continue
        out.append({
            "kind": "board",
            "source_id": "board:" + _slug(task),
            "title": task[:160],
            "category": get("category"),
            "status": status,
            "eta": eta,
            "notes": notes[:400],
            "reason": reason,
        })
    return out


def board_candidates(cfg, *, now):
    path = cfg.get("strategy_board") or ""
    if not path:
        return []
    p = Path(path)
    try:
        st = p.stat()
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return parse_strategy_board(text, file_mtime=st.st_mtime, now=now, cfg=cfg)


# ── source 2: WatchTower queues ──────────────────────────────────────────────

def _wt_bin():
    for cand in ("wt", "/opt/homebrew/bin/wt", "/usr/local/bin/wt",
                 str(Path.home() / ".local" / "bin" / "wt")):
        found = shutil.which(cand) if "/" not in cand else (cand if os.access(cand, os.X_OK) else None)
        if found:
            return found
    return None


def _wt_run(args, timeout=30):
    wt = _wt_bin()
    if not wt:
        return None
    try:
        proc = subprocess.run([wt] + list(args), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout or "null")
    except ValueError:
        return None


def wt_stuck_queues(status_rows, *, min_age_s):
    """Queues that are stuck with an open ticket at least ``min_age_s`` old,
    oldest first. Input is ``wt status --json`` (a list of dicts)."""
    out = []
    for row in status_rows or []:
        if not isinstance(row, dict):
            continue
        depth = row.get("depth") or 0
        age = row.get("oldest_open_age_s") or 0
        if not row.get("stuck") or depth <= 0 or age < min_age_s:
            continue
        out.append(row)
    out.sort(key=lambda r: -(r.get("oldest_open_age_s") or 0))
    return out


def wt_candidate(queue_row, tickets):
    q = str(queue_row.get("queue") or "?").upper()
    titles = []
    for t in tickets or []:
        if isinstance(t, dict):
            ref = t.get("ref") or t.get("id") or ""
            titles.append(f"{ref} {str(t.get('title') or '')[:120]}".strip())
        if len(titles) >= 8:
            break
    return {
        "kind": "wt",
        "source_id": "wt:" + q,
        "title": f"Queue {q}: {queue_row.get('depth')} open, oldest {queue_row.get('oldest_open_age') or '?'}",
        "queue": q,
        "depth": queue_row.get("depth"),
        "oldest_open_age": queue_row.get("oldest_open_age"),
        "since_progress": queue_row.get("since_progress"),
        "tickets": titles,
        "reason": f"stuck for {queue_row.get('since_progress') or '?'} with no worker",
    }


def wt_candidates(cfg, *, runner=None, limit=5):
    """One ``wt status`` per run, then one ``wt ls`` per surfaced queue
    (bounded by ``limit``). Never a call per ticket."""
    run = runner or _wt_run
    status = run(["status", "--json"])
    if not isinstance(status, list):
        return []
    min_age = float(cfg.get("wt_age_days") or 0) * 86400
    out = []
    for row in wt_stuck_queues(status, min_age_s=min_age)[:limit]:
        q = str(row.get("queue") or "")
        if not q or _ignored(q, cfg):
            continue
        tickets = run(["ls", "-q", q, "--status", "open", "--limit", "8", "--json"]) or []
        out.append(wt_candidate(row, tickets if isinstance(tickets, list) else []))
    return out


# ── source 3: idle sessions with unfinished work ─────────────────────────────

_WORKING_STATES = ("working", "in_progress", "in-progress", "running", "coding", "blocked", "stuck")
_DONE_GOAL = ("done", "complete", "completed", "shipped", "closed", "finished")


def session_is_unfinished(row):
    """A session whose last recorded task is not finished: a tool call is
    pending, a goal is open, or its own state marker says working."""
    if not isinstance(row, dict):
        return False
    if row.get("pending_tool"):
        return True
    goal = str(row.get("goal") or "").strip()
    goal_status = str(row.get("goal_status") or "").strip().lower()
    if goal and goal_status not in _DONE_GOAL:
        return True
    state = row.get("session_state")
    if isinstance(state, dict):
        state = state.get("state") or state.get("status") or ""
    if str(state or "").strip().lower() in _WORKING_STATES:
        return True
    return False


def _row_name(row):
    return str(row.get("display_name") or row.get("ai_title") or row.get("custom_title")
               or row.get("first_message") or row.get("session_id") or "")[:120]


def idle_session_candidates(rows, live_ids, *, now, cfg):
    idle_s = float(cfg.get("idle_hours") or 2) * 3600
    live = set(live_ids or ())
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not sid or sid not in live:
            continue
        mtime = row.get("mtime") or 0
        if not mtime or (now - mtime) < idle_s:
            continue
        if not session_is_unfinished(row):
            continue
        name = _row_name(row)
        if _ignored(name, cfg):
            continue
        idle_h = round((now - mtime) / 3600, 1)
        out.append({
            "kind": "session",
            "source_id": "session:" + str(sid),
            "session_id": sid,
            "title": name,
            "cwd": row.get("session_cwd") or row.get("folder_path") or "",
            "idle_hours": idle_h,
            "pending_tool": row.get("pending_tool"),
            "goal": str(row.get("goal") or "")[:300],
            "last_assistant_text": str(row.get("last_assistant_text") or "")[:600],
            "reason": f"live but idle {idle_h}h with an unfinished task",
        })
    out.sort(key=lambda c: -c["idle_hours"])
    return out


# ── token governor ───────────────────────────────────────────────────────────

def _read_tail(path, tail_bytes):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()  # drop the partial first line
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def analyze_transcript_tail(path, *, now, tail_bytes=400_000, window_s=45 * 60):
    """One pass over the tail of a Claude JSONL. Returns the signals the
    governor needs; never raises on a malformed line."""
    sig = {
        "last_activity_ts": 0.0, "last_edit_ts": 0.0, "had_edits": False,
        "tool_calls_window": 0, "edits_window": 0, "compact_seen": False,
        "error_counts": {}, "error_samples": {}, "assistant_turns": 0,
    }
    text = _read_tail(path, tail_bytes)
    if not text:
        return sig
    since = now - window_s
    for line in text.splitlines():
        if not line or '"type"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        ts = _parse_iso(ev.get("timestamp")) or 0.0
        if t == "system" and ev.get("subtype") == "compact_boundary":
            sig["compact_seen"] = True
            continue
        if t not in ("assistant", "user"):
            continue
        if ts:
            sig["last_activity_ts"] = max(sig["last_activity_ts"], ts)
        content = (ev.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if t == "assistant":
            sig["assistant_turns"] += 1
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if t == "assistant" and bt == "tool_use":
                name = str(block.get("name") or "")
                if ts >= since:
                    sig["tool_calls_window"] += 1
                if name in _EDIT_TOOLS:
                    sig["had_edits"] = True
                    sig["last_edit_ts"] = max(sig["last_edit_ts"], ts)
                    if ts >= since:
                        sig["edits_window"] += 1
            elif t == "user" and bt == "tool_result" and block.get("is_error"):
                rc = block.get("content")
                if isinstance(rc, list):
                    rc = " ".join(str(b.get("text", "")) for b in rc if isinstance(b, dict))
                sample = re.sub(r"\s+", " ", str(rc or ""))[:200]
                h = hashlib.sha1(sample.encode("utf-8", "replace")).hexdigest()[:10]
                sig["error_counts"][h] = sig["error_counts"].get(h, 0) + 1
                sig["error_samples"].setdefault(h, sample)
    return sig


def governor_findings(rows, live_ids, *, now, cfg, analyze=None):
    """Findings for live sessions only. ``analyze`` defaults to the tail
    parser and is injectable so tests never touch the filesystem."""
    analyze = analyze or (lambda p: analyze_transcript_tail(p, now=now,
                                                            window_s=float(cfg.get("no_edit_minutes") or 45) * 60))
    live = set(live_ids or ())
    ctx_threshold = float(cfg.get("context_pct") or 85)
    repeat_n = int(cfg.get("repeat_error_count") or 3)
    no_edit_s = float(cfg.get("no_edit_minutes") or 45) * 60
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not sid or sid not in live:
            continue
        name = _row_name(row)
        if _ignored(name, cfg):
            continue
        base = {"session_id": sid, "name": name,
                "cwd": row.get("session_cwd") or row.get("folder_path") or ""}
        # (c) context high, no compaction -- cheap, from the cached row.
        pct = float(row.get("live_context_percent") or 0)
        if not pct:
            limit = float(row.get("context_limit") or 0) or 200_000.0
            pct = 100.0 * float(row.get("latest_input_tokens") or 0) / limit
        if pct >= ctx_threshold:
            out.append(dict(base, kind="context_high", severity="warn",
                            detail=f"context at {int(pct)}% and not compacted",
                            value=int(pct)))
        path = row.get("jsonl_path")
        if not path:
            continue
        # Only sessions that touched their transcript recently get a tail
        # parse: an idle-for-days live process is the idle source's job.
        mtime = float(row.get("mtime") or 0)
        if not mtime or (now - mtime) > 6 * 3600:
            continue
        sig = analyze(path)
        for h, n in (sig.get("error_counts") or {}).items():
            if n >= repeat_n:
                out.append(dict(base, kind="repeated_errors", severity="warn",
                                detail=f"same tool error {n}x: {sig['error_samples'].get(h, '')[:140]}",
                                value=n))
                break
        working_now = sig.get("last_activity_ts", 0) >= now - 15 * 60
        if (working_now and sig.get("had_edits") and sig.get("tool_calls_window", 0) >= 5
                and sig.get("edits_window", 0) == 0
                and (now - (sig.get("last_edit_ts") or 0)) >= no_edit_s):
            mins = int((now - sig["last_edit_ts"]) // 60)
            out.append(dict(base, kind="no_edits", severity="info",
                            detail=f"working for {mins} min with no file changes ({sig['tool_calls_window']} tool calls)",
                            value=mins))
    return out


def governor_card(finding, *, now, run_id):
    sid = finding["session_id"]
    label = {
        "repeated_errors": "Repeated tool error",
        "no_edits": "Working with no file changes",
        "context_high": "Context nearly full",
    }.get(finding["kind"], finding["kind"])
    nudge = {
        "repeated_errors": "CCC governor: the same tool error has repeated several times. Stop retrying. "
                           "State the root cause in one paragraph, then either fix it a different way or ask for help.",
        "no_edits": "CCC governor: you have been working for a while without changing any file. "
                    "Summarize what you found so far, then either make the change or end the turn with a clear next step.",
        "context_high": "CCC governor: your context is nearly full. Write a short handoff summary of state and "
                        "next steps, then run /compact.",
    }[finding["kind"]]
    return {
        "id": "dc_" + uuid.uuid4().hex[:10],
        "source_id": f"governor:{sid}:{finding['kind']}",
        "kind": "governor",
        "title": f"{label}: {finding['name']}"[:160],
        "context": finding["detail"],
        "options": [
            {"label": "Nudge", "detail": "Steer a short reset instruction into the session.",
             "cost": "one turn", "recommended": True,
             "action": {"kind": "inject", "session_id": sid, "prompt": nudge}},
            {"label": "Pause", "detail": "Interrupt the current turn; the session stays resumable.",
             "cost": "free", "recommended": False,
             "action": {"kind": "pause", "session_id": sid}},
            {"label": "Kill", "detail": "Terminate the process. Transcript stays on disk.",
             "cost": "free, loses in-flight work", "recommended": False,
             "action": {"kind": "kill", "session_id": sid}},
        ],
        "status": "open",
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "run_id": run_id,
        "source": {k: finding.get(k) for k in ("session_id", "name", "kind", "detail", "value", "cwd")},
        "analyst": None,
    }


# ── analyst ──────────────────────────────────────────────────────────────────

def analyst_prompt(candidate):
    facts = {k: v for k, v in candidate.items() if k not in ("kind", "source_id") and v not in ("", None, [])}
    return (
        "You are a chief-of-staff analyst for a solo founder who runs many AI coding "
        "sessions from Claude Command Center (CCC). Something is stalled. Do the thinking "
        "so the owner only has to pick. Be concrete, short, and honest about cost.\n\n"
        f"Stalled item (kind={candidate.get('kind')}):\n{json.dumps(facts, indent=1, ensure_ascii=False)[:6000]}\n\n"
        "Write a decision card with exactly 2 or 3 options. Each option must be something "
        "that can start right now. Prefer options an autonomous session can execute; include "
        "at most one option that needs the owner personally. Exactly one option has "
        '"recommended": true.\n\n'
        "Output ONLY one JSON object, no prose, no code fences:\n"
        '{"title": "<= 90 chars, names the stall", '
        '"context": "2-3 sentences: what is stuck and why it matters", '
        '"options": [{"label": "<= 60 chars", "detail": "1-2 sentences", '
        '"cost": "e.g. 20 min Sonnet session / owner 5 min / $0", '
        '"recommended": true|false, '
        '"action": {"kind": "spawn"|"inject"|"human", '
        '"prompt": "for spawn/inject: the complete brief a fresh session needs to execute this option, '
        '2-6 sentences, include paths and names from the facts"}}]}\n'
        'Use "inject" only when the facts include a session_id (it steers that session); '
        '"human" when only the owner can do it (then prompt is empty). Valid JSON only: '
        "escape inner double quotes, no trailing commas."
    )


def parse_analyst_json(raw):
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("analyst did not return a JSON object")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("analyst response was not a JSON object")
    options = []
    for o in obj.get("options") or []:
        if not isinstance(o, dict) or not o.get("label"):
            continue
        action = o.get("action") if isinstance(o.get("action"), dict) else {}
        kind = str(action.get("kind") or "human").lower()
        if kind not in ("spawn", "inject", "human"):
            kind = "human"
        options.append({
            "label": str(o.get("label"))[:80],
            "detail": str(o.get("detail") or "")[:400],
            "cost": str(o.get("cost") or "")[:80],
            "recommended": bool(o.get("recommended")),
            "action": {"kind": kind, "prompt": str(action.get("prompt") or "")[:4000]},
        })
        if len(options) == 3:
            break
    if not options:
        raise ValueError("analyst returned no options")
    if sum(1 for o in options if o["recommended"]) != 1:
        for o in options:
            o["recommended"] = False
        options[0]["recommended"] = True
    title = str(obj.get("title") or "")[:120]
    if len(title) > 90:
        title = title[:90].rsplit(" ", 1)[0] + "…"
    return {"title": title, "context": str(obj.get("context") or "")[:800], "options": options}


def run_analyst(candidate, cfg, *, timeout=180):
    """Headless `claude -p` round trip. Returns the parsed card body; raises
    on CLI absence, timeout, non-zero exit, or unparseable JSON (one retry
    on the JSON case only, like the queue brief)."""
    claude_bin = _core._resolve_claude_bin()
    if not claude_bin.get("available"):
        raise RuntimeError(claude_bin.get("reason") or "Claude Code CLI not found")
    prompt = analyst_prompt(candidate)
    argv = [
        claude_bin["bin"], "-p", "--model", str(cfg.get("model") or DEFAULT_CONFIG["model"]),
        "--strict-mcp-config", '--mcp-config={"mcpServers":{}}',
        "--disallowedTools", "Bash,Write,Edit,MultiEdit,NotebookEdit,Task,WebFetch,WebSearch",
        prompt,
    ]
    try:
        cwd = str(_core._SCRATCH_DIR)
    except Exception:
        cwd = None
    last_err = None
    for attempt in (1, 2):
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip()[:300] or f"claude exited {proc.returncode}")
        try:
            return parse_analyst_json(proc.stdout)
        except ValueError as e:
            last_err = e
            if attempt == 2:
                raise
    raise last_err  # pragma: no cover


def fallback_card_body(candidate):
    """Used when no analyst is available (CLI missing, timeout). The owner
    still gets an actionable card instead of silence."""
    title = candidate.get("title") or candidate.get("source_id")
    brief = (
        f"Unblock this stalled item: {title}. Reason it surfaced: {candidate.get('reason')}. "
        f"Facts: {json.dumps({k: v for k, v in candidate.items() if k not in ('kind', 'source_id')}, ensure_ascii=False)[:1500]}. "
        "Investigate, do the work if it is within reach, and end with a one-paragraph status plus the single next decision."
    )
    options = [
        {"label": "Spawn a session to unblock it", "detail": "A fresh session investigates and does the work.",
         "cost": "one Sonnet session", "recommended": True, "action": {"kind": "spawn", "prompt": brief}},
        {"label": "Owner handles it", "detail": "Keep it on the board; no automation.",
         "cost": "owner time", "recommended": False, "action": {"kind": "human", "prompt": ""}},
    ]
    if candidate.get("session_id"):
        options.insert(1, {
            "label": "Nudge the existing session", "detail": "Steer the parked session to finish or hand off.",
            "cost": "one turn", "recommended": False,
            "action": {"kind": "inject", "prompt": "CCC decision inbox: you have been idle with an unfinished task. "
                                                  "Finish it, or write a handoff summary and stop."}})
    return {"title": str(title)[:90], "context": str(candidate.get("reason") or "")[:800], "options": options}


def analyst_card(candidate, body, *, now, run_id, analyst_meta):
    options = []
    for o in body["options"]:
        action = dict(o.get("action") or {})
        if candidate.get("session_id") and action.get("kind") == "inject":
            action["session_id"] = candidate["session_id"]
        elif action.get("kind") == "inject":
            action["kind"] = "spawn"  # nothing to steer into; run it fresh
        if candidate.get("cwd"):
            action.setdefault("cwd", candidate["cwd"])
        options.append(dict(o, action=action))
    return {
        "id": "dc_" + uuid.uuid4().hex[:10],
        "source_id": candidate["source_id"],
        "kind": candidate["kind"],
        "title": body["title"] or candidate.get("title") or candidate["source_id"],
        "context": body["context"],
        "options": options,
        "status": "open",
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "run_id": run_id,
        "source": {k: v for k, v in candidate.items() if k not in ("kind", "source_id")},
        "analyst": analyst_meta,
    }


# ── dedupe + run ─────────────────────────────────────────────────────────────

def blocked_source_ids(cards, *, now, dedupe_days):
    """Source ids that must not get a new card: open ones, and ones the
    owner decided/dismissed within the dedupe window."""
    window = float(dedupe_days or 0) * 86400
    blocked = set()
    for c in cards.values():
        if not isinstance(c, dict):
            continue
        sid = c.get("source_id")
        if not sid:
            continue
        if c.get("status") == "open":
            blocked.add(sid)
            continue
        ts = _parse_iso(c.get("updated_at")) or 0
        if c.get("status") in CLOSED_STATUSES and (now - ts) < window:
            blocked.add(sid)
    return blocked


def run_once(*, cfg=None, now=None, rows=None, live_ids=None, cards=None,
             analyst=None, wt_runner=None, board_text=None, board_mtime=None,
             persist=True):
    """One scan. Every input is injectable; defaults read the live server.
    Returns the run record (also appended to runs.jsonl when persisting)."""
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    run_id = "run_" + uuid.uuid4().hex[:8]
    started = time.time()
    cards = load_cards() if cards is None else cards
    if rows is None:
        rows = _server_rows()
    if live_ids is None:
        live_ids = _server_live_ids()
    max_new = int(cfg.get("max_cards_per_run") or 5)
    blocked = blocked_source_ids(cards, now=now, dedupe_days=cfg.get("dedupe_days"))
    record = {"run_id": run_id, "started_at": _iso(now), "sources": {}, "created": [],
              "skipped_dedupe": 0, "skipped_cap": 0, "errors": []}

    # Governor first: cheap, and a burning session outranks a stale board row.
    try:
        findings = governor_findings(rows, live_ids, now=now, cfg=cfg)
    except Exception as e:  # never let one source kill the run
        findings, _ = [], record["errors"].append(f"governor: {e}"[:200])
    with _lock:
        _last_findings["governor"] = findings
        _last_findings["at"] = _iso(now)
    record["sources"]["governor"] = len(findings)

    candidates = []
    if board_text is not None:
        candidates += parse_strategy_board(board_text, file_mtime=board_mtime or now, now=now, cfg=cfg)
    else:
        try:
            candidates += board_candidates(cfg, now=now)
        except Exception as e:
            record["errors"].append(f"board: {e}"[:200])
    record["sources"]["board"] = len(candidates)
    try:
        wt = wt_candidates(cfg, runner=wt_runner, limit=max_new)
    except Exception as e:
        wt, _ = [], record["errors"].append(f"wt: {e}"[:200])
    record["sources"]["wt"] = len(wt)
    candidates += wt
    idle = idle_session_candidates(rows, live_ids, now=now, cfg=cfg)
    record["sources"]["sessions"] = len(idle)
    candidates += idle

    new_cards = []

    def _admit(source_id):
        if source_id in blocked:
            record["skipped_dedupe"] += 1
            return False
        if len(new_cards) >= max_new:
            record["skipped_cap"] += 1
            return False
        blocked.add(source_id)
        return True

    for f in findings:
        source_id = f"governor:{f['session_id']}:{f['kind']}"
        if _admit(source_id):
            new_cards.append(governor_card(f, now=now, run_id=run_id))

    analyst_fn = analyst or (lambda c: run_analyst(c, cfg))
    for cand in candidates:
        if not _admit(cand["source_id"]):
            continue
        t0 = time.time()
        meta = {"model": cfg.get("model"), "duration_s": 0.0, "error": None}
        try:
            body = analyst_fn(cand)
        except Exception as e:
            meta["error"] = str(e)[:300]
            body = fallback_card_body(cand)
        meta["duration_s"] = round(time.time() - t0, 1)
        new_cards.append(analyst_card(cand, body, now=now, run_id=run_id, analyst_meta=meta))

    for c in new_cards:
        cards[c["id"]] = c
        record["created"].append({"id": c["id"], "source_id": c["source_id"], "title": c["title"]})
    record["duration_s"] = round(time.time() - started, 1)
    record["finished_at"] = _iso(time.time())
    if persist:
        save_cards(cards)
        _append_run(record)
    return record


# ── server glue (only these touch _core) ─────────────────────────────────────

def _server_rows():
    try:
        rows, _ = _core._archive_list_source_rows_cached({
            "include_prs": False, "resolve_pr_states": False,
            "resolve_effective": False, "resolve_worktree_dirty": False,
        })
        return list(rows or [])
    except Exception:
        return []


def _server_live_ids():
    try:
        return set(_core._discover_live_session_ids())
    except Exception:
        return set()


def start_background_run(cfg=None):
    """Kick one run on a daemon thread; refuse while one is in flight."""
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "error": "a scan is already running", "since": _running["since"]}

    def _go():
        try:
            run_once(cfg=cfg)
        except Exception:
            pass
        finally:
            _running["since"] = None
            _run_lock.release()

    _running["since"] = _iso(time.time())
    t = threading.Thread(target=_go, daemon=True, name="ccc-decision-inbox-run")
    _running["thread"] = t
    t.start()
    return {"ok": True, "started": True}


def decision_inbox_loop(initial_delay_s=120):
    """Daemon thread target: hourly scans, every failure = try next interval."""
    try:
        time.sleep(initial_delay_s)
    except Exception:
        return
    while True:
        cfg = load_config()
        if cfg.get("enabled", True):
            try:
                start_background_run(cfg)
            except Exception:
                pass
        try:
            time.sleep(max(300, int(cfg.get("interval_s") or 3600)))
        except Exception:
            return


def api_payload(*, cards=None, cfg=None):
    cfg = cfg or load_config()
    cards = load_cards() if cards is None else cards
    ordered = sorted(cards.values(), key=lambda c: (c.get("status") != "open", -(_parse_iso(c.get("created_at")) or 0)))
    with _lock:
        findings = list(_last_findings["governor"])
        findings_at = _last_findings["at"]
    return {
        "ok": True,
        "cards": ordered[:200],
        "open_count": sum(1 for c in ordered if c.get("status") == "open"),
        "governor": {"findings": findings, "at": findings_at},
        "last_run": last_run(),
        "running_since": _running["since"],
        "config": {
            "enabled": bool(cfg.get("enabled", True)),
            "interval_s": cfg.get("interval_s"),
            "strategy_board": bool(cfg.get("strategy_board")),
            "max_cards_per_run": cfg.get("max_cards_per_run"),
            "model": cfg.get("model"),
            "config_path": str(config_path()),
        },
    }


def _spawn_follow_through(prompt, *, cwd, name, cfg):
    cwd = cwd or cfg.get("spawn_cwd") or str(Path.home())
    result = _core.spawn_session(prompt, name=name, cwd=cwd, model=cfg.get("follow_model") or None)
    return result if isinstance(result, dict) else {"ok": bool(result)}


def _inject_follow_through(session_id, text):
    return _core._inject_text_into_session(session_id, text, mode="steer", source="decision_inbox")


def _session_pid(session_id):
    try:
        cwd = _core.find_session_cwd(session_id)
        status = _core.session_live_status(session_id, cwd) or {}
        if status.get("pid"):
            return int(status["pid"])
    except Exception:
        pass
    try:
        spawn = _core._find_live_spawn_entry_for_session(session_id)
        if spawn and spawn.get("pid"):
            return int(spawn["pid"])
    except Exception:
        pass
    return None


def perform_action(action, *, title="", cfg=None, spawn=None, inject=None,
                   pause=None, kill=None):
    """Execute an option's action. Callables are injectable for tests."""
    cfg = cfg or load_config()
    kind = str((action or {}).get("kind") or "human")
    sid = (action or {}).get("session_id")
    prompt = str((action or {}).get("prompt") or "")
    if kind == "human":
        return {"ok": True, "effect": "noted", "detail": "owner will handle it"}
    if kind == "spawn":
        if not prompt:
            return {"ok": False, "error": "option has no brief to spawn"}
        fn = spawn or (lambda p, **kw: _spawn_follow_through(p, cfg=cfg, **kw))
        res = fn(prompt, cwd=(action or {}).get("cwd") or "", name=("Decision: " + title)[:80])
        return dict(res, effect="spawned")
    if kind == "inject":
        if not sid or not prompt:
            return {"ok": False, "error": "inject needs session_id and text"}
        res = (inject or _inject_follow_through)(sid, prompt)
        return dict(res if isinstance(res, dict) else {"ok": bool(res)}, effect="injected")
    if kind == "pause":
        if not sid:
            return {"ok": False, "error": "pause needs session_id"}
        res = (pause or _core._interrupt_session)(sid)
        return dict(res if isinstance(res, dict) else {"ok": bool(res)}, effect="paused")
    if kind == "kill":
        if not sid:
            return {"ok": False, "error": "kill needs session_id"}
        if kill:
            return dict(kill(sid), effect="killed")
        pid = _session_pid(sid)
        if not pid:
            return {"ok": False, "error": "no live process for that session"}
        res = _core.system_process_kill([pid])
        ok = bool(res.get("killed")) if isinstance(res, dict) else bool(res)
        return {"ok": ok, "pid": pid, "effect": "killed", "result": res}
    return {"ok": False, "error": f"unknown action kind {kind!r}"}


def decide(card_id, option_index, *, cards=None, now=None, persist=True, **hooks):
    now = time.time() if now is None else now
    cards = load_cards() if cards is None else cards
    card = cards.get(str(card_id))
    if not card:
        return {"ok": False, "error": "unknown card"}
    if card.get("status") != "open":
        return {"ok": False, "error": f"card already {card.get('status')}"}
    try:
        idx = int(option_index)
        option = card["options"][idx]
    except (TypeError, ValueError, IndexError, KeyError):
        return {"ok": False, "error": "unknown option"}
    result = perform_action(option.get("action"), title=card.get("title", ""), **hooks)
    card["status"] = "decided" if result.get("ok") else "open"
    card["updated_at"] = _iso(now)
    card["decided"] = {"option": idx, "label": option.get("label"), "at": _iso(now), "result": result}
    if persist:
        save_cards(cards)
    return {"ok": bool(result.get("ok")), "card": card, "result": result}


def dismiss(card_id, *, cards=None, now=None, persist=True):
    now = time.time() if now is None else now
    cards = load_cards() if cards is None else cards
    card = cards.get(str(card_id))
    if not card:
        return {"ok": False, "error": "unknown card"}
    card["status"] = "dismissed"
    card["updated_at"] = _iso(now)
    if persist:
        save_cards(cards)
    return {"ok": True, "card": card}


def governor_act(session_id, action, *, reason="", **hooks):
    """One-click pause / nudge / kill from the governor strip (no card)."""
    action = str(action or "").lower()
    if action == "nudge":
        text = ("CCC governor: " + (reason or "you look stuck") +
                ". Stop, state the root cause in one paragraph, then fix it differently or hand off.")
        return perform_action({"kind": "inject", "session_id": session_id, "prompt": text}, **hooks)
    if action in ("pause", "kill"):
        return perform_action({"kind": action, "session_id": session_id}, **hooks)
    return {"ok": False, "error": "action must be nudge, pause, or kill"}
