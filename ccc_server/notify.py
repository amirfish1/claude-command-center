# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Notifications: intake, scheduling and fan-out to connected dashboards.

POST /api/notify records a notification and publishes it on the /api/events
SSE hub (topic "notify.request") so every open dashboard sees it live.
static/notify.js owns presentation: the macOS-app native bridge, a Web
Notification while the page is hidden, an in-app toast otherwise.

Scheduled items — the 6pm daily digest and savings milestones — are emitted by
scheduler_loop(), started once from server main(). They also fire an osascript
banner on macOS so they still land when no browser is open.

Session-state diffs from _dashboard_session_watch_tick feed
observe_session_states(), which turns working->idle/waiting/ended transitions
into "finished" / "needs you" notifications with a cost line from the usage
DB. All lookups are lazy and every failure degrades to a shorter line.

State lives in two small JSON files under the command-center state dir:
notify-state.json (digest/milestone bookkeeping) and notify-log.json (bounded
ring of recent items for the /api/notify/pending + /api/notify/history views).

Stdlib-only; no side effects at import.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from ccc_server import core as _core
from ccc_server import test_isolation_active
from ccc_server.paths import COMMAND_CENTER_STATE_DIR, _SYS_OSASCRIPT


KINDS = frozenset(
    ("info", "success", "task", "needs_input", "milestone", "digest", "error")
)

if test_isolation_active():
    _STATE_DIR = Path(tempfile.gettempdir()) / "ccc-test-notify"
else:
    _STATE_DIR = COMMAND_CENTER_STATE_DIR
STATE_FILE = _STATE_DIR / "notify-state.json"
LOG_FILE = _STATE_DIR / "notify-log.json"

LOG_LIMIT = 300
_TITLE_LIMIT = 200
_BODY_LIMIT = 1000
_RATE_WINDOW_S = 300.0
_RATE_MAX = 30
_DEDUPE_S = 60.0
_DIGEST_HOUR = 18  # 6pm local

# A working stretch shorter than this earns no "finished" ping — it filters
# out quick interactive turns so only real tasks notify.
_MIN_WORK_S = 10.0
_TASK_COOLDOWN_S = 120.0
_NEEDS_INPUT_COOLDOWN_S = 90.0

# Ladders, in USD (API-priced value of agent work / free-model savings) and
# tokens. Each rung fires once ever, recorded in notify-state.json.
_VALUE_MILESTONES = (10.0, 100.0, 1000.0, 10000.0)
_FREE_SAVED_MILESTONES = (5.0, 25.0, 100.0)
_FREE_TOKEN_MILESTONES = (1_000_000_000,)

_lock = threading.Lock()
_recent_keys = {}   # dedupe: (kind, title, body) -> (last emit epoch, item id)
_rate_bucket = []   # epochs of recent emits, for the per-window cap
_work_started = {}  # sid -> monotonic epoch the session entered "working"
_last_kind_emit = {}  # (sid, kind) -> epoch, per-session cooldowns


# ── persistence ──────────────────────────────────────────────────────────


def _read_json(path, default):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json_atomic(path, obj):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        pass


def _load_state():
    state = _read_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("milestones", [])
    return state


def _save_state(state):
    _write_json_atomic(STATE_FILE, state)


def _append_log(item):
    with _lock:
        items = _read_json(LOG_FILE, [])
        items.append(item)
        items = items[-LOG_LIMIT:]
        _write_json_atomic(LOG_FILE, items)


def history(limit=50):
    """Most recent items first, capped at `limit`."""
    try:
        limit = max(1, min(int(limit), LOG_LIMIT))
    except (TypeError, ValueError):
        limit = 50
    items = _read_json(LOG_FILE, [])
    return list(reversed(items[-limit:]))


def pending(since_id=None):
    """Log entries after `since_id` (the client's last seen id).

    Unknown or missing cursors fall back to a short window so a stale cursor
    can never replay hundreds of old items.
    """
    items = _read_json(LOG_FILE, [])
    since_id = str(since_id or "").strip()
    if since_id:
        for i, item in enumerate(items):
            if isinstance(item, dict) and item.get("id") == since_id:
                return {"items": items[i + 1:], "latest": items[-1].get("id") if items else None}
    cutoff = time.time() - 600  # unknown/absent cursor: last 10 minutes only
    fresh = [it for it in items
             if isinstance(it, dict) and float(it.get("ts") or 0) >= cutoff]
    return {"items": fresh, "latest": items[-1].get("id") if items else None}


# ── delivery ─────────────────────────────────────────────────────────────


def _publish(item):
    """Fan out on the dashboard SSE hub; a no-op when the hub is absent."""
    try:
        _core._dashboard_events.publish(
            "notify.request",
            entity={"type": "notify", "id": str(item.get("id") or "")},
            patch=dict(item),
        )
        return True
    except Exception:
        return False


def _osascript_notify(title, body):
    """macOS banner via osascript — the only path that reaches the user with
    zero browsers open. Best effort everywhere; never raises."""
    if sys.platform != "darwin" or not _SYS_OSASCRIPT:
        return False

    def _q(text):
        return '"' + str(text or "").replace("\\", "\\\\").replace('"', '\\"') + '"'

    try:
        subprocess.run(
            [_SYS_OSASCRIPT, "-e",
             f"display notification {_q(body)[:260]} with title {_q(title)[:120]} "
             f'subtitle "Command Center"'],
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _emit(item, publish=None, os_notify=False):
    """Log + publish + optional OS banner. Returns the item.

    A kind whose pop-up is not approved (ccc_server/popups.py) is held:
    nothing is logged, published or bannered."""
    from ccc_server import popups
    if not popups.notify_allowed(item.get("kind")):
        item["held"] = True
        return item
    _append_log(item)
    (publish or _publish)(item)
    if os_notify:
        try:
            _osascript_notify(item.get("title"), item.get("body"))
        except Exception:
            pass
    return item


def _rate_ok(now):
    with _lock:
        _rate_bucket[:] = [t for t in _rate_bucket if now - t < _RATE_WINDOW_S]
        if len(_rate_bucket) >= _RATE_MAX:
            return False
        _rate_bucket.append(now)
        return True


def _dedupe_hit(item, now):
    """Return the existing item's id when this (kind, title, body) already
    fired inside the dedupe window; otherwise record it and return None."""
    key = (item.get("kind"), item.get("title"), item.get("body"))
    with _lock:
        last = _recent_keys.get(key)
        if last is not None and now - last[0] < _DEDUPE_S:
            return last[1]
        _recent_keys[key] = (now, item.get("id"))
        if len(_recent_keys) > 512:
            cutoff = now - _DEDUPE_S
            for k in [k for k, v in _recent_keys.items() if v[0] < cutoff]:
                _recent_keys.pop(k, None)
        return None


# ── intake (POST /api/notify) ────────────────────────────────────────────


def validate(data):
    """Normalize a POST body into an item, or return an error string."""
    if not isinstance(data, dict):
        return None, "expected object"
    title = " ".join(str(data.get("title") or "").split())[:_TITLE_LIMIT].strip()
    if not title:
        return None, "title is required"
    body = " ".join(str(data.get("body") or "").split())[:_BODY_LIMIT].strip()
    kind = str(data.get("kind") or "info").strip().lower()
    if kind not in KINDS:
        return None, f"unknown kind {kind!r}"
    url = str(data.get("url") or "").strip()[:500]
    if url and not (url.startswith("/") or url.startswith("https://") or url.startswith("http://")):
        return None, "url must be a site-relative or http(s) link"
    session_id = str(data.get("session_id") or "").strip()[:120]
    item = {
        "id": "ntf_" + uuid.uuid4().hex[:16],
        "title": title,
        "body": body,
        "kind": kind,
        "url": url,
        "session_id": session_id,
        "ts": time.time(),
    }
    return item, None


def post(data, publish=None, now=None):
    """Validate, rate-limit, dedupe, then emit. Returns the response dict."""
    now = time.time() if now is None else float(now)
    item, err = validate(data)
    if err:
        return {"ok": False, "error": err}, 400
    if not _rate_ok(now):
        return {"ok": False, "error": "too many notifications; try again shortly"}, 429
    dup_id = _dedupe_hit(item, now)
    if dup_id:
        return {"ok": True, "deduped": True, "id": dup_id}, 200
    _emit(item, publish=publish, os_notify=bool(data.get("os")))
    if item.get("held"):
        return {"ok": True, "id": item["id"], "held": True}, 200
    return {"ok": True, "id": item["id"]}, 200


# ── session-completion observation ────────────────────────────────────────


def _session_identity(sid):
    """{name, engine, repo_path} via the cached census map; {} on any miss."""
    try:
        ident = (_core._census_identity_map() or {}).get(sid) or {}
        return ident if isinstance(ident, dict) else {}
    except Exception:
        return {}


def _session_is_free_runtime(sid, ident=None):
    """True when the spawn record marks this a free-router ($0) session.

    L04 stamps "runtime":"free" on spawn; the unified-key env it injects is
    the same signal read straight off the registry entry, so either record
    shape resolves it.
    """
    ident = ident or {}
    if ident.get("runtime") == "free":
        return True
    try:
        for entry in _core._load_spawn_registry() or ():
            if not isinstance(entry, dict):
                continue
            if sid not in (entry.get("session_id"), entry.get("resumed_sid")):
                continue
            if entry.get("runtime") == "free":
                return True
            env = entry.get("env") or {}
            base = str(env.get("ANTHROPIC_BASE_URL") or "")
            if base.startswith("http://127.0.0.1:") or base.startswith("http://localhost:"):
                return True
            return False
    except Exception:
        pass
    return False


def _usage_db_path():
    env = os.environ.get("CCC_THROUGHPUT_DB", "").strip()
    if env:
        return os.path.expanduser(env)
    return str(Path.home() / ".claude" / "command-center" / "usage" / "throughput.sqlite3")


def _connect_usage_db_ro():
    path = _usage_db_path()
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _session_cost(sid):
    """(list_cost_usd, token buckets) for the session from the usage DB.

    cost_usd is the API-list-price value of the work; buckets let free-model
    (unpriced) runs still be estimated at Sonnet rates. None when untracked.
    """
    conn = _connect_usage_db_ro()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT s.input_tokens, s.cache_read_input_tokens, "
            "s.cache_creation_input_tokens, s.output_tokens, s.total_tokens, "
            "c.cost_usd "
            "FROM sessions s JOIN session_costs c ON c.session_id = s.id "
            "WHERE s.source_session_id = ? "
            "ORDER BY s.last_activity_at DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if not row:
            return None
        return {
            "cost_usd": row["cost_usd"],
            "input_tokens": row["input_tokens"] or 0,
            "cache_read_tokens": row["cache_read_input_tokens"] or 0,
            "cache_creation_tokens": row["cache_creation_input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "total_tokens": row["total_tokens"] or 0,
        }
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Claude Sonnet 4.5 list rates (usage_db/rates.json): the "API prices" the
# free-model savings line is measured against.
_SONNET_RATES = {"input": 3.0, "cache_read": 0.3, "cache_write": 3.75, "output": 15.0}


def _sonnet_estimate_usd(buckets):
    try:
        return (
            float(buckets.get("input_tokens") or 0) * _SONNET_RATES["input"]
            + float(buckets.get("cache_read_tokens") or 0) * _SONNET_RATES["cache_read"]
            + float(buckets.get("cache_creation_tokens") or 0) * _SONNET_RATES["cache_write"]
            + float(buckets.get("output_tokens") or 0) * _SONNET_RATES["output"]
        ) / 1_000_000.0
    except (TypeError, ValueError):
        return 0.0


def _fmt_duration(seconds):
    try:
        s = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, rem = divmod(s, 60)
        return f"{m}m {rem}s" if rem >= 15 else f"{m}m"
    h, rem = divmod(s, 3600)
    return f"{h}h {rem // 60}m"


def _fmt_usd(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    if v < 0.01:
        return "<$0.01"
    if v < 100:
        return f"${v:.2f}"
    return f"${v:,.0f}"


def _display_name(sid, ident):
    name = str((ident or {}).get("name") or "").strip()
    if name:
        return name[:60]
    if sid:
        return "agent " + str(sid)[:8]
    return "your agent"


def _cost_line(sid, free_runtime):
    """The 'Cost $0, saved $X' tail for a finished run. Never raises; empty
    string when nothing is known."""
    if free_runtime:
        saved = None
        cost = _session_cost(sid)
        if cost:
            saved = cost.get("cost_usd")
            if saved is None:
                saved = _sonnet_estimate_usd(cost)
        if saved and saved > 0.005:
            return f" Cost $0, saved about {_fmt_usd(saved)} at API prices."
        return " Cost $0."
    cost = _session_cost(sid)
    if cost and cost.get("cost_usd"):
        return f" About {_fmt_usd(cost['cost_usd'])} of work."
    return ""


def _cooldown_ok(sid, kind, now, window_s):
    key = (sid, kind)
    last = _last_kind_emit.get(key)
    if last is not None and now - last < window_s:
        return False
    _last_kind_emit[key] = now
    if len(_last_kind_emit) > 4096:
        cutoff = now - 3600
        for k in [k for k, v in _last_kind_emit.items() if v < cutoff]:
            _last_kind_emit.pop(k, None)
    return True


def observe_session_states(previous, current, publish=None, now=None):
    """Turn session-state diffs into notifications. Called once per
    _dashboard_session_watch_tick (≈1s, only while a dashboard is connected).
    `previous`/`current` are {sid: {state, question_waiting, needs_approval}}.
    Baseline call (previous=None) seeds durations without notifying."""
    now_mono = time.monotonic()
    now_epoch = time.time() if now is None else float(now)
    emitted = []
    current = current or {}
    prev = previous or {}

    def _state_of(entry):
        return str((entry or {}).get("state") or "")

    for sid, cur in current.items():
        before = prev.get(sid)
        cur_state = _state_of(cur)
        prev_state = _state_of(before)
        if cur_state == "working":
            if prev_state != "working" or sid not in _work_started:
                _work_started[sid] = now_mono
            continue
        # Only notify on leaving a real working stretch (or on waiting flags
        # raised while already in "waiting").
        work_s = now_mono - _work_started.get(sid, now_mono)
        _work_started.pop(sid, None)
        if before is None:
            continue
        needs_now = bool((cur or {}).get("question_waiting") or (cur or {}).get("needs_approval"))
        was_busy = prev_state == "working"
        entered_waiting = (
            (was_busy and cur_state == "waiting")
            or (cur_state == "waiting" and needs_now
                and not (before or {}).get("question_waiting")
                and not (before or {}).get("needs_approval"))
        )
        if entered_waiting:
            if not _cooldown_ok(sid, "needs_input", now_epoch, _NEEDS_INPUT_COOLDOWN_S):
                continue
            ident = _session_identity(sid)
            name = _display_name(sid, ident)
            item = {
                "id": "ntf_" + uuid.uuid4().hex[:16],
                "kind": "needs_input",
                "title": f"{name} needs you",
                "body": "It's waiting for your input.",
                "url": f"/?session={sid}",
                "session_id": sid,
                "ts": now_epoch,
            }
            if _dedupe_hit(item, now_epoch):
                continue
            emitted.append(_emit(item, publish=publish))
            continue
        if was_busy and cur_state in ("idle", "ended"):
            if work_s < _MIN_WORK_S:
                continue
            if not _cooldown_ok(sid, "task", now_epoch, _TASK_COOLDOWN_S):
                continue
            ident = _session_identity(sid)
            name = _display_name(sid, ident)
            free = _session_is_free_runtime(sid, ident)
            dur = _fmt_duration(work_s)
            body = f"Done in {dur}." if dur else "Done."
            body += _cost_line(sid, free)
            item = {
                "id": "ntf_" + uuid.uuid4().hex[:16],
                "kind": "task",
                "title": f"{name} finished",
                "body": body.strip(),
                "url": f"/?session={sid}",
                "session_id": sid,
                "ts": now_epoch,
            }
            if _dedupe_hit(item, now_epoch):
                continue
            emitted.append(_emit(item, publish=publish))

    for sid in prev:
        if sid in current:
            continue
        if _state_of(prev.get(sid)) != "working":
            _work_started.pop(sid, None)
            continue
        work_s = now_mono - _work_started.get(sid, now_mono)
        _work_started.pop(sid, None)
        if work_s < _MIN_WORK_S:
            continue
        if not _cooldown_ok(sid, "task", now_epoch, _TASK_COOLDOWN_S):
            continue
        ident = _session_identity(sid)
        name = _display_name(sid, ident)
        dur = _fmt_duration(work_s)
        item = {
            "id": "ntf_" + uuid.uuid4().hex[:16],
            "kind": "task",
            "title": f"{name} wrapped up",
            "body": (f"Done in {dur}." if dur else "The session ended."),
            "url": f"/?session={sid}",
            "session_id": sid,
            "ts": now_epoch,
        }
        if _dedupe_hit(item, now_epoch):
            continue
        emitted.append(_emit(item, publish=publish))
    return emitted


# ── scheduled items: daily digest + savings milestones ───────────────────


def _savings_snapshot(base_url, range_name):
    """L12's /api/savings, self-called on loopback. None until that lane (or a
    compatible endpoint) exists — the digest still ships without it."""
    if not base_url:
        return None
    try:
        req = urllib.request.Request(
            f"{base_url}/api/savings?range={range_name}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


_TOTALS_MEMO = {"ts": 0.0, "value": None}
_TOTALS_TTL_S = 900.0  # totals only grow; a 15-min memo keeps the O(events)
# scan out of the per-minute scheduler tick while staying fresh enough for
# milestone detection and the 6pm digest.


def _usage_db_totals(now=None, fresh=False):
    """{all_time_usd, today_usd, sessions_today} from the usage DB — the
    dependency-free savings source. Memoized for _TOTALS_TTL_S."""
    epoch = time.time() if now is None else float(now)
    cached = _TOTALS_MEMO["value"]
    if not fresh and cached is not None and epoch - _TOTALS_MEMO["ts"] < _TOTALS_TTL_S:
        return dict(cached)
    value = _usage_db_totals_uncached(epoch)
    if value is not None:
        _TOTALS_MEMO["ts"] = epoch
        _TOTALS_MEMO["value"] = dict(value)
    return value


def _usage_db_totals_uncached(now=None):
    """(all-time list cost, today's local-date list cost, sessions today)
    straight from the usage DB."""
    conn = _connect_usage_db_ro()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT ts, cost_usd FROM event_costs WHERE ts IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    today = datetime.fromtimestamp(now or time.time()).date()
    all_time = 0.0
    today_total = 0.0
    for row in rows:
        v = row["cost_usd"]
        if v is None:
            continue
        all_time += v
        try:
            day = datetime.fromisoformat(
                str(row["ts"]).replace("Z", "+00:00")
            ).astimezone().date()
        except ValueError:
            continue
        if day == today:
            today_total += v
    try:
        srows = conn.execute(
            "SELECT started_at FROM sessions WHERE started_at IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        srows = []
    sessions_today = 0
    for row in srows:
        try:
            day = datetime.fromisoformat(
                str(row["started_at"]).replace("Z", "+00:00")
            ).astimezone().date()
        except ValueError:
            continue
        if day == today:
            sessions_today += 1
    try:
        conn.close()
    except Exception:
        pass
    return {"all_time_usd": all_time, "today_usd": today_total,
            "sessions_today": sessions_today}


def _compose_digest(totals, savings, now):
    today_usd = (totals or {}).get("today_usd") or 0.0
    sessions_today = (totals or {}).get("sessions_today") or 0
    free_saved = float((savings or {}).get("free_saved_usd") or 0)
    free_runs = int((savings or {}).get("free_runs") or 0)
    if today_usd <= 0 and sessions_today <= 0:
        body = "Open Command Center to see what your agents are up to tonight."
    else:
        parts = []
        if today_usd > 0:
            parts.append(f"about {_fmt_usd(today_usd)} of work")
        if sessions_today > 0:
            parts.append(f"{sessions_today} session" + ("" if sessions_today == 1 else "s"))
        body = "Today your agents did " + " across ".join(parts) + "."
        if free_saved > 0 and free_runs > 0:
            body += f" {_fmt_usd(free_saved)} of it ran on free models. Cost: $0."
    return {
        "id": "ntf_" + uuid.uuid4().hex[:16],
        "kind": "digest",
        "title": "Your daily agent report",
        "body": body,
        "url": "/",
        "session_id": "",
        "ts": float(now),
    }


def _milestone_items(totals, savings, state, now):
    out = []
    all_time = max(
        float((totals or {}).get("all_time_usd") or 0),
        float((savings or {}).get("api_value_usd") or 0),
    )
    hit = set(state.get("milestones") or [])
    for rung in _VALUE_MILESTONES:
        key = f"value-{int(rung)}"
        if all_time >= rung and key not in hit:
            out.append({
                "id": "ntf_" + uuid.uuid4().hex[:16],
                "kind": "milestone",
                "title": f"{_fmt_usd(rung)} of agent work",
                "body": f"Your AI dev team has now done {_fmt_usd(rung)} of work "
                        "at API prices. Not bad for a local dashboard.",
                "url": "/",
                "session_id": "",
                "ts": float(now),
                "_milestone_key": key,
            })
    saved = float((savings or {}).get("free_saved_usd") or 0)
    for rung in _FREE_SAVED_MILESTONES:
        key = f"free-saved-{int(rung)}"
        if saved >= rung and key not in hit:
            out.append({
                "id": "ntf_" + uuid.uuid4().hex[:16],
                "kind": "milestone",
                "title": f"{_fmt_usd(rung)} saved on free models",
                "body": f"Free-model runs have saved {_fmt_usd(rung)} so far.",
                "url": "/",
                "session_id": "",
                "ts": float(now),
                "_milestone_key": key,
            })
    tokens = float((savings or {}).get("free_tokens") or 0)
    for rung in _FREE_TOKEN_MILESTONES:
        key = f"free-tokens-{int(rung)}"
        if tokens >= rung and key not in hit:
            out.append({
                "id": "ntf_" + uuid.uuid4().hex[:16],
                "kind": "milestone",
                "title": "1 billion tokens on free models",
                "body": "A billion tokens of agent work, all at $0.",
                "url": "/",
                "session_id": "",
                "ts": float(now),
                "_milestone_key": key,
            })
    return out


def emit_due(now=None, publish=None, os_notify=True, base_url=None):
    """Emit whichever scheduled notifications are due right now and record
    them in notify-state.json. Called by scheduler_loop (the only marker of
    'sent'), so the daily digest fires exactly once a day across every CCC
    instance sharing this state dir."""
    now = time.time() if now is None else float(now)
    emitted = []
    with _lock:
        state = _load_state()
        today = datetime.fromtimestamp(now).date().isoformat()
        hour = datetime.fromtimestamp(now).hour
        digest_due = hour >= _DIGEST_HOUR and state.get("last_digest_date") != today
        # Milestone ladders only grow, so re-checking them every quarter hour
        # loses nothing and keeps the all-events scan + the loopback savings
        # call out of the per-minute tick.
        milestone_due = now - float(state.get("last_milestone_check") or 0) >= 900
        totals = None
        savings_today = None
        savings_all = None
        if digest_due:
            totals = _usage_db_totals(now)
            savings_today = _savings_snapshot(base_url, "today")
        if milestone_due:
            state["last_milestone_check"] = now
            if totals is None:
                totals = _usage_db_totals(now)
            if totals or base_url:
                savings_all = _savings_snapshot(base_url, "all")
        due = []
        if digest_due:
            due.append(_compose_digest(totals, savings_today, now))
            state["last_digest_date"] = today
        if milestone_due:
            for item in _milestone_items(totals, savings_all, state, now):
                key = item.pop("_milestone_key", None)
                if key:
                    state["milestones"].append(key)
                due.append(item)
        if due or milestone_due:
            _save_state(state)
    for item in due:
        _emit(item, publish=publish, os_notify=os_notify)
        emitted.append(item)
    return emitted


def scheduler_loop(base_url=None, publish=None, os_notify=True, interval_s=60.0):
    """Daemon thread: tick once a minute, emit whatever is due. Errors inside
    a tick must never kill the loop."""
    while True:
        try:
            emit_due(publish=publish, os_notify=os_notify, base_url=base_url)
        except Exception:
            pass
        time.sleep(interval_s)


def reset_for_tests():
    """Clear in-memory rate/dedupe/cooldown state between tests."""
    with _lock:
        _recent_keys.clear()
        _rate_bucket.clear()
        _work_started.clear()
        _last_kind_emit.clear()
