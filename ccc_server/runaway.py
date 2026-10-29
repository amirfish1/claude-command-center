# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Runaway sessions: sessions that keep working long after anyone asked.

The brakes CCC already has (wake_guard, the inject circuit breaker, the idle
reaper) only cover wakes CCC itself starts. A "lone wolf" wakes itself: a
ScheduleWakeup / cron / /loop timer, a Claude or Codex goal continuation, or
WatchTower pinging a ticket it claimed. Each wake re-reads the whole context,
so a session that no longer has anything to do can burn a quota overnight.

No LLM judges the work. Two cheap facts decide, read from the transcript:

  * who drives it: every turn is tagged by its trigger (human, timer, goal,
    peer / WatchTower message, background task, ...). The streak is every
    turn since the last human one.
  * whether anything changes: a turn is productive when it edits a file,
    commits / pushes, closes a ticket or opens / merges a PR, or reports
    ScheduleWakeup(noop=false). ScheduleWakeup(noop=true) is the session
    itself saying nothing changed.

A session is flagged when it is still active, its autonomous streak is long
(MIN_AUTO_TURNS turns over MIN_AUTO_SPAN_S), it spent at least MIN_COST since
the last human turn, and either its last STAGNANT_TURNS autonomous turns were
all unproductive or its spend passed OVER_BUDGET. Real progress resets the
stagnation count, which is how long, productive sessions stay unflagged.

v1 only reports (a CCC card); it never blocks. "Let it run" records a lease
that hides the session until it expires; a human turn resets the streak.

Perf (CLAUDE.md gates): candidacy is stat-only (transcript mtime within
ACTIVE_WINDOW_S); only candidates are read, and only the bytes appended since
the last scan, with the parse state persisted to disk keyed by (inode, size).
A cold candidate is read from its last INITIAL_TAIL_BYTES at most. The API
answers from the last result and refreshes it on a background thread at most
every SCAN_TTL_S, so a request never waits on a scan. No subprocesses.

Engines: Claude (~/.claude/projects), Codex ($CODEX_HOME/sessions), Kimi
($KIMI_CODE_HOME/sessions/*/session_*/agents/main/wire.jsonl), and the
SQLite-backed Devin CLI (sessions.db) and Hermes (state.db, one per profile).
SQLite engines follow the same rules: a database whose file and WAL did not
change since the last scan is not queried; otherwise one query lists sessions
active within ACTIVE_WINDOW_S and only rows past each session's last-read id
are fetched, with JSON fields pulled by json_extract inside SQLite.
Stdlib-only.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from ccc_server import restart_recovery as _rr

PROJECTS_ROOT = _rr.PROJECTS_ROOT
CODEX_ROOT = _rr.CODEX_ROOT
STATE_DIR = _rr.STATE_DIR
SCAN_STATE_FILE = STATE_DIR / "runaway-scan.json"
LEASES_FILE = STATE_DIR / "runaway-leases.json"
KIMI_ROOT = Path(os.path.expanduser(os.environ.get("KIMI_CODE_HOME", "").strip() or "~/.kimi-code")) / "sessions"
DEVIN_SESSION_PREFIX = "devincli-"  # CCC's id for a raw Devin CLI session id

ACTIVE_WINDOW_S = 3 * 3600      # still running "into the sunset"
CODEX_DAYS_BACK = 14            # a long-lived rollout stays in its start-day dir
INITIAL_TAIL_BYTES = 16 * 1024 * 1024
INITIAL_TAIL_ROWS = 3000        # SQLite engines: rows read for a cold candidate
SCAN_TTL_S = 300.0  # the card polls every 5 min; a rescan costs seconds in the busy server
PRUNE_AFTER_S = 7 * 86400

MIN_AUTO_TURNS = 6
MIN_AUTO_SPAN_S = 2 * 3600
STAGNANT_TURNS = 5
# Cost-weighted tokens (fresh input + 1.25x cache write + 0.1x cache read +
# 5x output): roughly what a turn bills, unlike raw totals that cache reads
# dominate. The floor keeps cheap pings ("reply RECEIVED") from flagging.
MIN_COST = 1_000_000
OVER_BUDGET = 30_000_000

LEASE_DEFAULT_H = 24
DISMISS_H = 6

HUMAN = "human"
_PRODUCTIVE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch",
                     "edit", "write", "patch", "write_file"}  # + Devin, Hermes
_PROGRESS_RE = re.compile(
    r"\bgit\b[^\n|;&]{0,200}?\b(?:commit|push)\b"
    r"|\bwt\s+(?:close|accept)\b"
    r"|\bgh\s+pr\s+(?:create|merge)\b"
    r"|\*\*\* (?:Begin Patch|Update File|Add File)"
)
# Injected text that a service or another session wrote, not a person.
_AUTO_PREFIXES = (
    ("[WATCHTOWER]", "watchtower"),
    ("[watchtower]", "watchtower"),
    ("<task-notification", "task"),
    ("<cross-session-message", "peer"),
    ("Another Claude session sent", "peer"),
    ("[Cross-session", "system"),
    ("Announced from:", "peer"),
    ("<<autonomous-loop", "timer"),
    ("Stop hook feedback", "goal"),
)
_SENDER_RE = re.compile(r"\[sender: ([^\]]*?) \((\w+)\)")
_ANSWER_RE = re.compile(r"A human answered your blocked question on ticket ([A-Za-z0-9_-]+)")
_AUTHOR_RE = re.compile(r'"author":\s*\{"kind":\s*"(\w+)"')
_WT_PEER_RE = re.compile(r'from-name="(?:watchtower|wt[:-])')
_CLAUDE_ORIGINS = {"human": HUMAN, "scheduled": "timer", "peer": "peer",
                   "task_notification": "task", "system": "system"}

_LOCK = threading.Lock()
_MEMO = {"at": 0.0, "result": None, "running": False}


# ---------------------------------------------------------------- classify

def text_trigger(text):
    """Trigger of an injected prompt, from its text alone (default human)."""
    t = str(text or "").lstrip()
    if _ANSWER_RE.search(t[:400]):
        return "answer"
    m = _SENDER_RE.match(t)
    if m:
        if m.group(2) in ("human", "user", "owner"):
            return HUMAN
        name = m.group(1).lower()
        return "watchtower" if name.startswith(("watchtower", "wt:", "wt-")) else "peer"
    if t.startswith("[Message attribution"):
        m = _AUTHOR_RE.search(t[:2000])
        return HUMAN if m and m.group(1) in ("human", "user") else "peer"
    for prefix, kind in _AUTO_PREFIXES:
        if t.startswith(prefix):
            return kind
    return HUMAN


def claude_trigger(rec, text):
    if rec.get("wakeupSource"):
        return "timer"
    origin = rec.get("turnOrigin")
    if origin == "peer":
        # Automation unless the delivery is explicitly attributed to a human.
        attributed = text.lstrip().startswith(("[sender:", "[Message attribution"))
        trig = text_trigger(text)
        if trig == "answer" or (attributed and trig == HUMAN):
            return trig
        return "watchtower" if _WT_PEER_RE.search(text[:400]) else "peer"
    if origin in _CLAUDE_ORIGINS:
        return _CLAUDE_ORIGINS[origin]
    return text_trigger(text)


def _resolve_answer(st, trigger, text):
    """A relayed human answer is human the first time per ticket; WatchTower
    re-delivering the same answer again is automation."""
    if trigger != "answer":
        return trigger
    m = _ANSWER_RE.search(str(text or "")[:400])
    ref = m.group(1) if m else ""
    answered = st.setdefault("answered", [])
    if ref in answered:
        return "watchtower"
    answered.append(ref)
    del answered[:-50]
    return HUMAN


def _progress_text(value):
    if isinstance(value, dict):
        value = " ".join(str(v) for v in value.values() if isinstance(v, (str, int)))
    return bool(_PROGRESS_RE.search(str(value or "")))


# ------------------------------------------------------------ parse state

def _new_state():
    return {"offset": 0, "ino": 0, "partial": False, "sid": "", "cwd": "",
            "title": "", "last_ts": 0.0, "last_human": 0.0, "msg_id": "",
            "codex_total": None, "turn": None, "streak": _new_streak()}


def _new_streak():
    return {"turns": 0, "first": 0.0, "stagnant": 0, "productive": 0,
            "noop": 0, "cost": 0.0, "triggers": {}}


def _ts(value):
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _open_turn(st, trigger, ts):
    st["turn"] = {"trigger": trigger, "productive": False, "noop": False}
    if trigger == HUMAN:
        st["streak"] = _new_streak()
        st["last_human"] = ts or st["last_ts"]
    elif trigger is not None and not st["streak"]["first"]:
        st["streak"]["first"] = ts or st["last_ts"]


def _classify_open_turn(st, trigger, ts):
    """Late classification (Codex: the prompt follows task_started)."""
    turn = st["turn"]
    if turn is None:
        _open_turn(st, trigger, ts)
    elif turn["trigger"] is None:
        turn["trigger"] = trigger
        if trigger == HUMAN:
            st["streak"] = _new_streak()
            st["last_human"] = ts or st["last_ts"]
        elif not st["streak"]["first"]:
            st["streak"]["first"] = ts or st["last_ts"]
    elif trigger == HUMAN and turn["trigger"] != HUMAN:
        # A human steering mid-turn takes the wheel.
        turn["trigger"] = HUMAN
        st["streak"] = _new_streak()
        st["last_human"] = ts or st["last_ts"]


def _close_turn(st):
    turn, st["turn"] = st["turn"], None
    if not turn or not turn.get("active"):
        return  # a prompt that got no model work is not a turn
    trigger = turn["trigger"] or "unknown"
    st["last_trigger"] = trigger
    if trigger == HUMAN:
        return
    sk = st["streak"]
    sk["turns"] += 1
    sk["triggers"][trigger] = sk["triggers"].get(trigger, 0) + 1
    if turn["noop"]:
        sk["noop"] += 1
    if turn["productive"] and not turn["noop"]:
        sk["productive"] += 1
        sk["stagnant"] = 0
    else:
        sk["stagnant"] += 1


def _add_cost(st, weighted):
    turn = st["turn"]
    if turn is not None and turn["trigger"] == HUMAN:
        return
    st["streak"]["cost"] += weighted


def _mark_tool(st, name, payload):
    turn = st["turn"]
    if turn is None:
        return
    if name == "ScheduleWakeup" and isinstance(payload, dict):
        if payload.get("noop") is True:
            turn["noop"] = True
        elif payload.get("noop") is False:
            turn["productive"] = True
        return
    if name in _PRODUCTIVE_TOOLS or _progress_text(payload):
        turn["productive"] = True


# ------------------------------------------------------------------ Claude

def _claude_text(rec):
    return " ".join(str(b.get("text") or "") for b in _rr._blocks(rec) if b.get("type") == "text")


def _claude_prompt(st, rec, text, ts):
    _start_prompt(st, claude_trigger(rec, text), text, ts)


def _start_prompt(st, trigger, text, ts):
    """A new prompt closes the open turn and opens one for ``trigger``."""
    turn = st["turn"]
    if turn is not None and not turn.get("active"):
        st["turn"] = None  # the same prompt seen twice (dequeue + user record)
    elif turn is not None:
        _close_turn(st)
    trigger = _resolve_answer(st, trigger, text)
    if trigger == HUMAN and not st["title"] and not _ANSWER_RE.search(text[:400]):
        st["title"] = " ".join(text.split())[:120]
    _open_turn(st, trigger, ts)


def _repeat_trigger(st, trigger, text):
    """Kimi/Devin/Hermes carry no reliable sender on scripted prompts. The
    same long prompt arriving again is a script re-sending it, not a person."""
    if trigger != HUMAN:
        return trigger
    key = " ".join(str(text or "").split())[:400]
    if len(key) >= 40 and key == st.get("last_prompt_key"):
        return "repeat"
    st["last_prompt_key"] = key
    return trigger


def feed_claude(st, rec):
    if rec.get("isSidechain"):
        return
    kind = rec.get("type")
    ts = _ts(rec.get("timestamp"))
    if ts:
        st["last_ts"] = max(st["last_ts"], ts)
    if not st["cwd"] and rec.get("cwd"):
        st["cwd"] = str(rec["cwd"])
    if kind == "queue-operation":
        # A queued prompt can reach the model with only enqueue/dequeue
        # records and no user record of its own.
        queued = st.setdefault("queued", [])
        op = rec.get("operation")
        if op == "enqueue":
            queued.append(str(rec.get("content") or "")[:2000])
            del queued[:-20]
        elif op == "dequeue" and queued:
            _claude_prompt(st, rec, queued.pop(0), ts)
        elif op == "remove" and queued:
            content = str(rec.get("content") or "")[:2000]
            if content in queued:
                queued.remove(content)
        return
    if kind == "user":
        blocks = _rr._blocks(rec)
        if any(b.get("type") == "tool_result" for b in blocks):
            return
        # Peer and task deliveries are written as isMeta, but they are prompts.
        if (rec.get("isMeta") and not rec.get("turnOrigin")) or rec.get("isCompactSummary"):
            return
        if not rec.get("isMeta") and _rr._is_bookkeeping_user(rec):
            return
        text = _claude_text(rec)
        if text.strip():
            _claude_prompt(st, rec, text, ts)
        return
    if kind != "assistant":
        return
    msg = rec.get("message") or {}
    mid = str(msg.get("id") or "")
    if st["turn"] is None:
        if mid and mid == st.get("closed_mid"):
            return  # another content block of the message that ended the turn
        # Activity with no prompt: a Stop-hook / goal continuation.
        _open_turn(st, "goal", ts)
    st["turn"]["active"] = True
    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
    if usage and mid != st["msg_id"]:
        st["msg_id"] = mid
        _add_cost(st, (usage.get("input_tokens") or 0)
                  + 1.25 * (usage.get("cache_creation_input_tokens") or 0)
                  + 0.1 * (usage.get("cache_read_input_tokens") or 0)
                  + 5 * (usage.get("output_tokens") or 0))
    for block in _rr._blocks(rec):
        if block.get("type") == "tool_use":
            _mark_tool(st, str(block.get("name") or ""), block.get("input"))
    if msg.get("stop_reason") in ("end_turn", "stop_sequence"):
        st["closed_mid"] = mid
        _close_turn(st)


# ------------------------------------------------------------------- Codex

def _codex_user_text(payload):
    if payload.get("type") == "user_message":
        return str(payload.get("message") or "")
    for part in payload.get("content") or []:
        text = str(part.get("text") or "") if isinstance(part, dict) else ""
        if text.strip() and not text.lstrip().startswith(("<", "# AGENTS.md")):
            return text
    return ""


def feed_codex(st, rec):
    kind = rec.get("type")
    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
    ptype = payload.get("type")
    ts = _ts(rec.get("timestamp"))
    if ts:
        st["last_ts"] = max(st["last_ts"], ts)
    if kind == "session_meta":
        st["sid"] = st["sid"] or str(payload.get("id") or payload.get("session_id") or "")
        st["cwd"] = st["cwd"] or str(payload.get("cwd") or "")
        return
    if kind == "event_msg":
        if ptype == "task_started":
            if st["turn"] is not None:
                _close_turn(st)
            attribution = payload.get("turn_attribution") or {}
            _open_turn(st, "goal" if attribution.get("turn_trigger") == "goal" else None, ts)
        elif ptype in ("task_complete", "turn_aborted"):
            _close_turn(st)
        elif ptype == "user_message":
            _codex_prompt(st, _codex_user_text(payload), ts)
        elif ptype == "token_count":
            total = ((payload.get("info") or {}).get("total_token_usage") or {})
            if total:
                prev = st["codex_total"] or {}
                delta = {k: max(0, (total.get(k) or 0) - (prev.get(k) or 0))
                         for k in ("input_tokens", "cached_input_tokens",
                                   "cache_write_input_tokens", "output_tokens")}
                st["codex_total"] = {k: total.get(k) or 0 for k in delta}
                if prev:
                    cached = delta["cached_input_tokens"]
                    _add_cost(st, max(0, delta["input_tokens"] - cached) + 0.1 * cached
                              + 1.25 * delta["cache_write_input_tokens"]
                              + 5 * delta["output_tokens"])
        return
    if kind != "response_item":
        return
    if st["turn"] is not None and ptype in ("function_call", "custom_tool_call", "reasoning") or (
            st["turn"] is not None and ptype == "message" and payload.get("role") == "assistant"):
        st["turn"]["active"] = True
    if ptype == "message" and payload.get("role") == "user":
        _codex_prompt(st, _codex_user_text(payload), ts)
    elif ptype in ("function_call", "custom_tool_call"):
        raw = payload.get("arguments") if ptype == "function_call" else payload.get("input")
        _mark_tool(st, str(payload.get("name") or ""), raw)


def _codex_prompt(st, text, ts):
    if not text.strip():
        return
    trigger = _resolve_answer(st, text_trigger(text), text)
    if trigger == HUMAN and not st["title"] and not _ANSWER_RE.search(text[:400]):
        st["title"] = " ".join(text.split())[:120]
    _classify_open_turn(st, trigger, ts)


# -------------------------------------------------------------------- Kimi

_KIMI_ORIGINS = {"system_trigger": "system", "task": "task", "background_task": "task",
                 "skill_activation": "system", "injection": "system"}
_KIMI_STEP_CONTINUES = ("tool_use", "tool_calls")


def _kimi_text(value):
    if isinstance(value, str):
        return value
    return " ".join(str(b.get("text") or "") for b in value or [] if isinstance(b, dict))


def feed_kimi(st, rec):
    kind = rec.get("type")
    ts = float(rec.get("time") or 0) / 1000.0
    if ts:
        st["last_ts"] = max(st["last_ts"], ts)
    if kind == "profile.bind" and not st["cwd"]:
        st["cwd"] = str((rec.get("environmentDisclosure") or {}).get("cwd") or "")
    elif kind in ("turn.prompt", "turn.steer"):
        origin = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
        text = _kimi_text(rec.get("input"))
        if origin.get("kind", "user") == "user":
            trigger = text_trigger(text)
        elif origin.get("kind") == "system_trigger" and origin.get("name") == "goal":
            trigger = "goal"
        else:
            trigger = _KIMI_ORIGINS.get(origin.get("kind"), "system")
        if kind == "turn.prompt":
            if text.strip():
                _start_prompt(st, _repeat_trigger(st, trigger, text), text, ts)
        elif trigger == HUMAN and st["turn"] is not None:
            _classify_open_turn(st, _resolve_answer(st, trigger, text), ts)  # steering
    elif kind in ("turn.cancel", "turn.ended", "agent.turn.ended"):
        _close_turn(st)
    elif kind == "context.append_loop_event":
        event = rec.get("event") if isinstance(rec.get("event"), dict) else {}
        etype = event.get("type")
        if etype in ("step.begin", "tool.call", "step.end") and st["turn"] is None:
            if etype == "step.end":
                return  # the tail of a turn that already ended
            _open_turn(st, "goal", ts)  # model work with no prompt: a continuation
        if etype in ("step.begin", "tool.call"):
            st["turn"]["active"] = True
        if etype == "tool.call":
            _mark_tool(st, str(event.get("name") or ""), event.get("args"))
        elif etype == "step.end":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            _add_cost(st, (usage.get("inputOther") or 0)
                      + 1.25 * (usage.get("inputCacheCreation") or 0)
                      + 0.1 * (usage.get("inputCacheRead") or 0)
                      + 5 * (usage.get("output") or 0))
            if str(event.get("finishReason") or "end_turn") not in _KIMI_STEP_CONTINUES:
                _close_turn(st)


# ----------------------------------------------------------------- SQLite

def _connect_ro(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
    con.execute("PRAGMA query_only = 1")
    return con


def _db_sig(db):
    sig = []
    for suffix in ("", "-wal"):
        try:
            stat = os.stat(f"{db}{suffix}")
            sig.append([stat.st_mtime_ns, stat.st_size])
        except OSError:
            sig.append(None)
    return sig


def _db_scan(engine, db, states, now, query_sessions, feed_session):
    """Shared SQLite candidacy: skip unchanged DBs, read only new rows."""
    db = str(db)
    prefix = f"{engine}:{db}#"
    meta_key = f"{engine}-db:{db}"
    meta = states.get(meta_key) if isinstance(states.get(meta_key), dict) else {}
    sig = _db_sig(db)
    if sig[0] is not None and meta.get("sig") != sig:
        try:
            con = _connect_ro(db)
        except sqlite3.Error:
            con = None
        if con is not None:
            try:
                for sid, info in query_sessions(con, now - ACTIVE_WINDOW_S):
                    key = prefix + sid
                    st = states.get(key)
                    if not isinstance(st, dict) or "streak" not in st:
                        st = _new_state()
                    st["sid"] = st["sid"] or info.get("sid", sid)
                    st["cwd"] = st["cwd"] or str(info.get("cwd") or "")
                    feed_session(con, sid, st, info)
                    states[key] = st
                meta = {"sig": sig, "seen": now}
            except sqlite3.Error:
                pass
            finally:
                con.close()
    meta["seen"] = now
    states[meta_key] = meta
    out = []
    for key, st in states.items():
        if key.startswith(prefix) and isinstance(st, dict) and now - st["last_ts"] <= ACTIVE_WINDOW_S:
            st["seen"] = now
            out.append((engine, db, st))
    return out


def _rows_since(con, sql_new, sql_tail, sid, last_id, st):
    if last_id:
        return con.execute(sql_new, (sid, last_id)).fetchall()
    rows = con.execute(sql_tail, (sid, INITIAL_TAIL_ROWS + 1)).fetchall()
    if len(rows) > INITIAL_TAIL_ROWS:
        st["partial"] = True
        rows = rows[:INITIAL_TAIL_ROWS]
    return rows[::-1]


# ------------------------------------------------------------------- Devin

# Devin rows can be many MB (inline images, tool dumps). octet_length() reads
# only the record header, so oversized rows are skipped without loading them;
# their role and usage go unseen, which can only make a flag less likely.
# octet_length() needs SQLite 3.43; older builds parse every row.
DEVIN_ROW_CAP = 1024 * 1024
_DEVIN_MSG = ("CASE WHEN octet_length(chat_message) <= %d THEN chat_message END" % DEVIN_ROW_CAP
              if sqlite3.sqlite_version_info >= (3, 43) else "chat_message")
_DEVIN_COLS = f"""node_id, created_at,
    json_extract(m, '$.role'),
    substr(json_extract(m, '$.content'), 1, 2000),
    json_extract(m, '$.tool_calls'),
    json_extract(m, '$.metadata.is_user_input'),
    json_extract(m, '$.metadata.finish_reason'),
    json_extract(m, '$.metadata.metrics.input_tokens'),
    json_extract(m, '$.metadata.metrics.cache_read_tokens'),
    json_extract(m, '$.metadata.metrics.cache_creation_tokens'),
    json_extract(m, '$.metadata.metrics.output_tokens')
    FROM (SELECT node_id, created_at, {_DEVIN_MSG} AS m FROM message_nodes
          WHERE session_id = ?"""
_DEVIN_NEW = f"SELECT {_DEVIN_COLS} AND node_id > ?) ORDER BY node_id"
_DEVIN_TAIL = f"SELECT {_DEVIN_COLS} ORDER BY node_id DESC LIMIT ?) ORDER BY node_id DESC"


def _devin_sessions(con, lower):
    for sid, cwd, title in con.execute(
            "SELECT id, working_directory, title FROM sessions WHERE last_activity_at >= ?",
            (int(lower),)):
        yield str(sid), {"sid": DEVIN_SESSION_PREFIX + str(sid), "cwd": cwd, "title": title}


def _devin_feed_session(con, sid, st, info):
    rows = _rows_since(con, _DEVIN_NEW, _DEVIN_TAIL, sid, st.get("last_id") or 0, st)
    for row in rows:
        st["last_id"] = row[0]
        feed_devin(st, row)
    if not st["title"] and info.get("title"):
        st["title"] = str(info["title"])[:120]


def feed_devin(st, row):
    (_nid, created, role, content, tool_calls, user_input, finish,
     fresh, cache_read, cache_write, output) = row
    ts = float(created or 0)
    if ts:
        st["last_ts"] = max(st["last_ts"], ts)
    text = str(content or "")
    if role == "user":
        if user_input:
            _start_prompt(st, _repeat_trigger(st, text_trigger(text), text), text, ts)
        elif text.strip().lower() == "continue":
            _start_prompt(st, "goal", text, ts)
        # anything else unattributed is compaction bookkeeping, not a prompt
        return
    if role != "assistant":
        return
    if st["turn"] is None:
        _open_turn(st, "goal", ts)
    st["turn"]["active"] = True
    _add_cost(st, (fresh or 0) + 1.25 * (cache_write or 0) + 0.1 * (cache_read or 0) + 5 * (output or 0))
    try:
        calls = json.loads(tool_calls) if tool_calls else []
    except ValueError:
        calls = []
    for call in calls if isinstance(calls, list) else []:
        if isinstance(call, dict):
            _mark_tool(st, str(call.get("name") or ""), call.get("arguments"))
    if finish == "stop":
        _close_turn(st)


# ------------------------------------------------------------------ Hermes

_HERMES_COLS = """id, role, substr(content, 1, 2000), tool_calls, finish_reason, timestamp,
    display_kind FROM messages WHERE session_id = ?"""
_HERMES_NEW = f"SELECT {_HERMES_COLS} AND id > ? ORDER BY id"
_HERMES_TAIL = f"SELECT {_HERMES_COLS} ORDER BY id DESC LIMIT ?"


def _hermes_sessions(con, lower):
    for row in con.execute(
            "SELECT id, cwd, title, source, input_tokens, output_tokens, cache_read_tokens,"
            " cache_write_tokens FROM sessions"
            " WHERE COALESCE(last_activity_at, started_at) >= ?", (lower,)):
        yield str(row[0]), {"cwd": row[1], "title": row[2], "source": row[3], "totals": row[4:8]}


def _hermes_feed_session(con, sid, st, info):
    st["source"] = info.get("source") or ""
    rows = _rows_since(con, _HERMES_NEW, _HERMES_TAIL, sid, st.get("last_id") or 0, st)
    for row in rows:
        st["last_id"] = row[0]
        feed_hermes(st, row)
    if not st["title"] and info.get("title"):
        st["title"] = str(info["title"])[:120]
    # Hermes keeps usage per session, not per message: bill the growth since
    # the last scan to whoever drove the latest turn.
    totals = [int(v or 0) for v in info.get("totals") or (0, 0, 0, 0)]
    prev = st.get("hermes_total")
    st["hermes_total"] = totals
    if prev:
        d_in, d_out, d_read, d_write = (max(0, a - b) for a, b in zip(totals, prev))
        driver = (st["turn"] or {}).get("trigger") or st.get("last_trigger")
        if driver != HUMAN:
            st["streak"]["cost"] += d_in + 1.25 * d_write + 0.1 * d_read + 5 * d_out


def feed_hermes(st, row):
    _mid, role, content, tool_calls, finish, ts, display_kind = row
    ts = float(ts or 0)
    if ts:
        st["last_ts"] = max(st["last_ts"], ts)
    text = str(content or "")
    if role == "user":
        if display_kind == "internal_notification":
            trigger = "task"
        elif st.get("source") == "cron":
            trigger = "timer"
        else:
            trigger = text_trigger(text)
        if display_kind == "steer":
            if st["turn"] is not None:
                _classify_open_turn(st, trigger, ts)
            return
        if text.strip():
            _start_prompt(st, _repeat_trigger(st, trigger, text), text, ts)
        return
    if role != "assistant":
        return
    if st["turn"] is None:
        _open_turn(st, "goal", ts)
    st["turn"]["active"] = True
    try:
        calls = json.loads(tool_calls) if tool_calls else []
    except ValueError:
        calls = []
    for call in calls if isinstance(calls, list) else []:
        fn = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn, dict):
            args = fn.get("arguments")
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                pass
            _mark_tool(st, str(fn.get("name") or ""), args)
    if finish and finish != "tool_calls":
        _close_turn(st)


def _default_devin_dbs():
    try:
        from ccc_server import devin
        return [str(p) for p in devin._devin_cli_db_paths()]
    except Exception:
        home = Path.home() / ".local" / "share" / "devin"
        return [str(home / "cli" / "sessions.db"), str(home / "cli-next" / "sessions.db")]


def _default_hermes_dbs():
    try:
        from ccc_server import hermes
        return [str(p) for p in hermes._hermes_db_paths()]
    except Exception:
        root = Path.home() / ".hermes"
        return [str(root / "state.db")] + sorted(str(p) for p in root.glob("profiles/*/state.db"))


# -------------------------------------------------------------------- scan

def _read_new(path, st, size, ino):
    """Feed the bytes appended since the last scan; returns records."""
    if st["ino"] != ino or size < st["offset"]:
        fresh = _new_state()
        st.clear()
        st.update(fresh)
        st["ino"] = ino
    start = st["offset"]
    if start == 0 and size > INITIAL_TAIL_BYTES:
        start = size - INITIAL_TAIL_BYTES
        st["partial"] = True
    if start >= size:
        return []
    with open(path, "rb") as fh:
        fh.seek(start)
        raw = fh.read(size - start)
    if start and start != st["offset"]:
        cut = raw.find(b"\n")
        raw = raw[cut + 1:] if cut >= 0 else b""
        start = size - len(raw)
    end = raw.rfind(b"\n")
    if end < 0:
        st["offset"] = start
        return []
    st["offset"] = start + end + 1
    return list(_rr._records(raw[:end + 1]))


def _kimi_wires(kimi_root):
    for workdir in _scandir(kimi_root):
        for session in _scandir(workdir.path):
            if session.name.startswith("session_"):
                yield os.path.join(session.path, "agents", "main", "wire.jsonl")


def _scandir(path):
    try:
        with os.scandir(path) as it:
            return [e for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _candidates(now, projects_root, codex_root, kimi_root=None):
    lower = now - ACTIVE_WINDOW_S
    for entry in _rr._claude_files(projects_root):
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_mtime >= lower:
            yield "claude", entry.path, stat
    for wire in _kimi_wires(kimi_root) if kimi_root is not None else ():
        try:
            stat = os.stat(wire)
        except OSError:
            continue
        if stat.st_mtime >= lower:
            yield "kimi", wire, stat
    if codex_root is None:
        return
    seen = set()
    for days in range(CODEX_DAYS_BACK + 1):
        t = now - days * 86400
        for stamp in {time.strftime("%Y/%m/%d", time.localtime(t)),
                      time.strftime("%Y/%m/%d", time.gmtime(t))}:
            if stamp in seen:
                continue
            seen.add(stamp)
            for entry in _rr._jsonl_entries(os.path.join(codex_root, stamp)):
                if not entry.name.startswith("rollout-"):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                if stat.st_mtime >= lower:
                    yield "codex", entry.path, stat


def _codex_head_title(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024 * 1024)  # Codex heads carry long instructions; flagged rows only
    except OSError:
        return ""
    for rec in _rr._records(head):
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        if rec.get("type") == "response_item" and payload.get("type") == "message" \
                and payload.get("role") == "user":
            text = _codex_user_text(payload)
            if text.strip():
                return " ".join(text.split())[:120]
    return ""


def _session_id(engine, path, st):
    if engine == "claude":
        return Path(path).stem
    if engine == "kimi":
        return Path(path).parents[2].name  # .../session_<uuid>/agents/main/wire.jsonl
    if st["sid"]:
        return st["sid"]
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path)
    return m.group(1) if m else ""


def evaluate(st, now):
    """(flagged, reasons) for one session's parse state."""
    sk = st["streak"]
    span = (st["last_ts"] - sk["first"]) if sk["first"] else 0.0
    reasons = []
    if now - st["last_ts"] > ACTIVE_WINDOW_S:
        return False, reasons
    if sk["turns"] < MIN_AUTO_TURNS or span < MIN_AUTO_SPAN_S or sk["cost"] < MIN_COST:
        return False, reasons
    if sk["stagnant"] >= STAGNANT_TURNS:
        reasons.append("stagnant")
    if sk["cost"] >= OVER_BUDGET:
        reasons.append("over_budget")
    return bool(reasons), reasons


def _entry(engine, path, st, reasons):
    sk = st["streak"]
    return {
        "session_id": _session_id(engine, path, st),
        "engine": engine,
        "path": path,
        "cwd": st["cwd"],
        "title": st["title"],
        "auto_turns": sk["turns"],
        "auto_since": sk["first"],
        "auto_hours": round(max(0.0, st["last_ts"] - sk["first"]) / 3600, 1) if sk["first"] else 0.0,
        "stagnant_turns": sk["stagnant"],
        "productive_turns": sk["productive"],
        "noop_turns": sk["noop"],
        "cost_tokens": int(sk["cost"]),
        "triggers": dict(sk["triggers"]),
        "last_human_at": st["last_human"] or None,
        "last_activity": st["last_ts"],
        "partial": st["partial"],
        "reasons": reasons,
    }


def _read_json(path, default):
    return _rr._read_json(path, default)


def _write_json(path, data):
    _rr._write_json(path, data)


_FEEDERS = {"claude": feed_claude, "codex": feed_codex, "kimi": feed_kimi}


def scan(now=None, projects_root=None, codex_root=None, state_file=None, debug=False,
         kimi_root=None, devin_dbs=None, hermes_dbs=None):
    """Incrementally re-read active transcripts; return flagged entries."""
    now = time.time() if now is None else now
    projects_root = PROJECTS_ROOT if projects_root is None else projects_root
    codex_root = CODEX_ROOT if codex_root is None else codex_root
    kimi_root = KIMI_ROOT if kimi_root is None else kimi_root
    devin_dbs = _default_devin_dbs() if devin_dbs is None else devin_dbs
    hermes_dbs = _default_hermes_dbs() if hermes_dbs is None else hermes_dbs
    state_file = SCAN_STATE_FILE if state_file is None else state_file
    states = _read_json(state_file, {})
    if not isinstance(states, dict):
        states = {}
    flagged, watch = [], []
    seen = []
    for engine, path, stat in _candidates(now, projects_root, codex_root, kimi_root):
        st = states.get(path)
        if not isinstance(st, dict) or "streak" not in st:
            st = _new_state()
        st["engine"] = engine
        if st.get("size") != stat.st_size or st.get("ino") != stat.st_ino:
            try:
                records = _read_new(path, st, stat.st_size, stat.st_ino)
            except OSError:
                continue
            feed = _FEEDERS[engine]
            for rec in records:
                feed(st, rec)
            st["size"] = stat.st_size
        st["seen"] = now
        states[path] = st
        seen.append((engine, path, st))
    for db in devin_dbs:
        seen += _db_scan("devin", db, states, now, _devin_sessions, _devin_feed_session)
    for db in hermes_dbs:
        seen += _db_scan("hermes", db, states, now, _hermes_sessions, _hermes_feed_session)
    for engine, path, st in seen:
        hit, reasons = evaluate(st, now)
        if hit:
            if not st["title"] and engine in ("claude", "codex"):
                st["title"] = (_rr._title(path, []) if engine == "claude"
                               else _codex_head_title(path))
            flagged.append(_entry(engine, path, st, reasons))
        elif debug and st["streak"]["turns"] >= 3:
            watch.append(_entry(engine, path, st, []))
    states = {p: s for p, s in states.items()
              if isinstance(s, dict) and now - (s.get("seen") or 0) < PRUNE_AFTER_S}
    _write_json(state_file, states)
    flagged.sort(key=lambda e: -e["cost_tokens"])
    watch.sort(key=lambda e: -e["cost_tokens"])
    return flagged, watch


# ------------------------------------------------------------------ leases

def _leases(now, leases_file=None):
    data = _read_json(leases_file or LEASES_FILE, {})
    if not isinstance(data, dict):
        return {}
    return {sid: v for sid, v in data.items()
            if isinstance(v, dict) and (v.get("until") or 0) > now}


def set_lease(session_id, hours, kind="lease", now=None, leases_file=None):
    now = time.time() if now is None else now
    leases_file = leases_file or LEASES_FILE
    data = _leases(now, leases_file)
    data[session_id] = {"until": now + float(hours) * 3600, "kind": kind, "at": now}
    _write_json(leases_file, data)
    _MEMO["at"] = 0.0
    return data[session_id]


def _refresh_memo():
    try:
        flagged, watch = scan()
        _MEMO.update(at=time.time(), result=(flagged, watch))
    finally:
        with _LOCK:
            _MEMO["running"] = False


def state(now=None, debug=False, leases_file=None, wait=False, **kwargs):
    """Flagged sessions minus leased / dismissed ones.

    The default (dashboard) call never scans on the request thread: it
    answers from the last result and, when that is older than SCAN_TTL_S,
    starts one background refresh. Inside the busy server a warm rescan
    takes seconds of GIL time, so it must not block a request. Explicit
    roots (tests), debug and wait=True scan synchronously."""
    now = time.time() if now is None else now
    if debug or kwargs or wait:
        flagged, watch = scan(now=now, debug=debug, **kwargs)
    else:
        with _LOCK:
            stale = _MEMO["result"] is None or now - _MEMO["at"] >= SCAN_TTL_S
            start = stale and not _MEMO.get("running")
            if start:
                _MEMO["running"] = True
        if start:
            threading.Thread(target=_refresh_memo, name="ccc-runaway-scan", daemon=True).start()
        flagged, watch = _MEMO["result"] or ([], [])
    leases = _leases(now, leases_file)
    out = {"sessions": [e for e in flagged if e["session_id"] not in leases],
           "leased": sorted(sid for sid in leases),
           "thresholds": {"min_auto_turns": MIN_AUTO_TURNS, "min_auto_span_h": MIN_AUTO_SPAN_S / 3600,
                          "stagnant_turns": STAGNANT_TURNS, "min_cost_tokens": MIN_COST,
                          "over_budget_tokens": OVER_BUDGET, "active_window_h": ACTIVE_WINDOW_S / 3600}}
    if debug:
        out["watch"] = watch
    return out


# --------------------------------------------------------------------- API

def handle_api_get(handler, parsed=None):
    debug = False
    if parsed is not None:
        import urllib.parse
        debug = urllib.parse.parse_qs(parsed.query).get("debug", ["0"])[0] in ("1", "true")
    out = state(debug=debug)
    handler.send_json({"ok": True, "pending": _MEMO["result"] is None and not debug, **out})


def handle_api_post(handler, path):
    if path not in ("/api/runaway/lease", "/api/runaway/dismiss"):
        return False
    try:
        length = int(handler.headers.get("Content-Length", "0") or 0)
        payload = json.loads(handler.rfile.read(length) if 0 < length <= 65536 else b"{}")
    except (ValueError, OSError):
        payload = None
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    if not _rr._sid_ok(sid):
        handler.send_json({"ok": False, "error": 'Expected {"session_id"}.'}, 400)
        return True
    if path == "/api/runaway/lease":
        try:
            hours = min(168.0, max(1.0, float(payload.get("hours") or LEASE_DEFAULT_H)))
        except (TypeError, ValueError):
            hours = float(LEASE_DEFAULT_H)
        handler.send_json({"ok": True, "lease": set_lease(sid, hours, "lease")})
    else:
        handler.send_json({"ok": True, "lease": set_lease(sid, DISMISS_H, "dismiss")})
    return True
