# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Savings engine — the numbers behind ``GET /api/savings`` (lane L12).

Answers three money questions from the transcript record:

* ``api_value_usd``  — what the agent work in the window would have cost at
  API list prices (same rate table the throughput page uses).
* ``plan_cost_usd``  — what the user's subscription plan(s) accrued over the
  same window. Plans are configuration: the usage-DB ``subscription_plans``
  table when present, otherwise a friendly default ($200/mo "Claude Max")
  the user can edit through ``POST /api/savings/plan``.
* ``free_*``         — how much ran through the free model router for $0:
  tokens (``free_tokens``), their Sonnet-priced value (``free_saved_usd``)
  and how many CCC sessions were $0 runs (``free_runs``).

Data flow
---------
Transcripts under ``~/.claude/projects`` are parsed incrementally into a
tiny SQLite ledger (``usage/savings.sqlite3`` in the command-center state
dir). Per-file work is gated by ``(mtime, size)`` — unchanged transcripts
are never re-read — and the ledger itself is the persistent cache, so a
server restart pays no cold-parse. Resumed sessions replay earlier API
responses under new event ids but the same ``message.id`` (issue #60);
dedupe is done at query time by grouping on the message id, so a file may
be deleted or rewritten without losing events that only survive in a copy.

Claude-Code transcripts only: the savings story (Max plan, free router
through the Anthropic-shaped endpoint) is a Claude story.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default plan when the user has configured none: Claude Max at $200/mo.
# "editable" — POST /api/savings/plan writes a real row into
# subscription_plans, after which source becomes "configured".
DEFAULT_PLAN_MONTHLY_USD = 200.0
DEFAULT_PLAN_NAME = "Claude Max"
_PLAN_MAX_USD = 10000.0

# Free runs are valued at Sonnet list price — the reference "at API prices"
# rate from the product story.
_FREE_PRICING_MODEL = "claude-sonnet-4-6"
_SONNET_RATES = (3.00, 3.75, 0.30, 15.00)  # (input, cache_write, cache_read, output) $/MTok

_LEDGER_SCHEMA = 2
_LEDGER_FILE = ("usage", "savings.sqlite3")
_FREE_IDS_FILE = "savings-free-sessions.json"

_ROUTER_STATE_FILE = Path.home() / ".ccc" / "free-router.json"
_ROUTER_DEFAULT_PORT = 3017
_ROUTER_TIMEOUT_S = 1.5
_ROUTER_ANALYTICS_RANGES = {"today": "24h", "week": "7d", "month": "30d", "all": "90d"}

# How long a computed payload stays valid before the next request resyncs.
_PAYLOAD_TTL_S = 20.0
# A synchronous request spends at most this long catching the ledger up;
# anything left goes to a background thread.
_SYNC_SCAN_BUDGET_S = 2.0
_ANALYTICS_TTL_S = 120.0

# ---------------------------------------------------------------------------
# Dependency seams (production resolves via _core; tests pass fixtures)
# ---------------------------------------------------------------------------

# Tests may set these module attributes directly; None resolves the real
# location at call time so monkeypatch.setenv("HOME", tmp) works. Prefixed
# SAVINGS_ on purpose: _adopt_ccc_module copies every top-level name into
# server globals, so an unprefixed PROJECTS_ROOT/STATE_DIR here would
# clobber the real ones.
SAVINGS_PROJECTS_ROOT = None   # -> CCC_PROJECTS_ROOT / _core.PROJECTS_ROOT
SAVINGS_STATE_DIR = None       # -> _core.COMMAND_CENTER_STATE_DIR
SAVINGS_LEDGER_DB = None       # -> STATE_DIR / usage / savings.sqlite3
SAVINGS_PLANS_DB = None        # -> usage_db default (honours CCC_THROUGHPUT_DB)
SAVINGS_FREE_IDS_FILE = None   # -> STATE_DIR / savings-free-sessions.json
SAVINGS_ROUTER_STATE_FILE = None  # -> ~/.ccc/free-router.json


def _projects_root() -> Path:
    if SAVINGS_PROJECTS_ROOT is not None:
        return Path(SAVINGS_PROJECTS_ROOT)
    env = os.environ.get("CCC_PROJECTS_ROOT", "").strip()
    if env:
        return Path(env)
    root = getattr(_core, "PROJECTS_ROOT", None)
    return Path(root) if root else Path.home() / ".claude" / "projects"


def _state_dir() -> Path:
    if SAVINGS_STATE_DIR is not None:
        return Path(SAVINGS_STATE_DIR)
    d = getattr(_core, "COMMAND_CENTER_STATE_DIR", None)
    return Path(d) if d else Path.home() / ".claude" / "command-center"


def _ledger_path() -> Path:
    if SAVINGS_LEDGER_DB is not None:
        return Path(SAVINGS_LEDGER_DB)
    return _state_dir().joinpath(*_LEDGER_FILE)


def _plans_db_path() -> Path:
    if SAVINGS_PLANS_DB is not None:
        return Path(SAVINGS_PLANS_DB)
    from ccc_server.usage_db import cli as _udb_cli
    return Path(_udb_cli.default_db_path())


def _free_ids_path() -> Path:
    if SAVINGS_FREE_IDS_FILE is not None:
        return Path(SAVINGS_FREE_IDS_FILE)
    return _state_dir() / _FREE_IDS_FILE


def _router_state_path() -> Path:
    if SAVINGS_ROUTER_STATE_FILE is not None:
        return Path(SAVINGS_ROUTER_STATE_FILE)
    env = os.environ.get("CCC_FREE_ROUTER_STATE", "").strip()
    return Path(env) if env else _ROUTER_STATE_FILE


def _rates_for(model):
    """(in, cw, cr, out) $/MTok + whether the model was recognised."""
    fn = getattr(_core, "_rates_for_model_known", None)
    if not callable(fn):
        # Standalone context (tests, worker): resolve the rate table straight
        # from its owning module instead of through the adopted server name.
        try:
            from ccc_server import morning_launch as _ml
            fn = _ml._rates_for_model_known
        except Exception:
            fn = None
    if callable(fn):
        rates, known = fn(model or "")
        return tuple(rates), bool(known)
    return _SONNET_RATES, False


def _spawn_registry_entries():
    """Free-runtime candidates from the shared spawn registry."""
    for name in ("_disk_spawn_entries_cached", "_load_spawn_registry"):
        fn = getattr(_core, name, None)
        if callable(fn):
            try:
                entries = fn()
            except Exception:
                continue
            if isinstance(entries, list):
                return entries
    return []


def _pretty_model(model):
    fn = getattr(_core, "_stats_pretty_model", None)
    if callable(fn):
        try:
            return fn(model)
        except Exception:
            pass
    return model or "Unknown"


# ---------------------------------------------------------------------------
# Ledger (persistent (mtime,size)-gated per-file cache)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    mtime       REAL NOT NULL,
    size        INTEGER NOT NULL,
    scanned_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    dedupe_key  TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    session_id  TEXT,
    day         TEXT NOT NULL,
    model       TEXT,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    sidechain   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (file_path, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_events_dedupe ON events(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_events_file ON events(file_path);
-- First claim wins: which file's copy of a dedupe_key feeds the rollup.
-- Resumed sessions replay the same API response (same message.id, identical
-- usage) into a new file — the copy is stored but only the claimant counts.
CREATE TABLE IF NOT EXISTS claims (
    dedupe_key  TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL
);
-- Pre-aggregated CONTRIBUTING token sums, maintained at ingest so request
-- time never scans the event log. Keyed by (day, model, session_id) with ""
-- standing in for NULL so UNIQUE treats missing values as one bucket.
CREATE TABLE IF NOT EXISTS rollup (
    day         TEXT NOT NULL,
    model       TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    sidechain   INTEGER NOT NULL DEFAULT 0,
    n           INTEGER NOT NULL DEFAULT 0,
    UNIQUE (day, model, session_id)
);
CREATE INDEX IF NOT EXISTS idx_rollup_day ON rollup(day);
CREATE TABLE IF NOT EXISTS backlog (path TEXT PRIMARY KEY);
"""

_DROP_SQL = """
DROP TABLE IF EXISTS files;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS claims;
DROP TABLE IF EXISTS rollup;
DROP TABLE IF EXISTS backlog;
"""


def _connect_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > _LEDGER_SCHEMA:
        raise RuntimeError(f"savings ledger schema v{have} is newer than this code (v{_LEDGER_SCHEMA})")
    if have < _LEDGER_SCHEMA:
        # Ledger is a derived cache: rebuild from scratch on schema bump.
        conn.executescript(_DROP_SQL)
    conn.executescript(_SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version = {_LEDGER_SCHEMA}")
    conn.commit()
    return conn


def _parse_ts_local(ts_str):
    """ISO-8601 -> aware datetime in server-local time, or None."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        dt = datetime.fromisoformat(ts_str[:-1] + "+00:00" if ts_str.endswith("Z") else ts_str)
    except ValueError:
        return None
    return dt.astimezone()


def _safe_message(msg):
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, str):
        try:
            return json.loads(msg)
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def savings_scan_transcript(path):
    """One transcript -> (session_id, [(dedupe_key, day, model, in, cw, cr, out, sidechain)]).

    Dedupe keys are ``message.id`` where present — resumed sessions replay the
    same API responses under new event ids but keep message.id, so a global
    group-by removes the copies no matter which file they landed in. Events
    without a message id get a file-unique synthetic key and never dedupe.
    """
    session_id = None
    rows = []
    seen = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            seq = 0
            for line in fh:
                if "assistant" not in line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if ev.get("type") != "assistant":
                    continue
                msg = _safe_message(ev.get("message"))
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                mid = msg.get("id") if isinstance(msg.get("id"), str) else None
                if mid:
                    if mid in seen:
                        continue
                    seen.add(mid)
                local = _parse_ts_local(ev.get("timestamp"))
                if local is None:
                    continue
                if session_id is None and ev.get("sessionId"):
                    session_id = str(ev.get("sessionId"))
                seq += 1
                rows.append((
                    mid or f"noid:{path.name}:{seq}",
                    local.strftime("%Y-%m-%d"),
                    str(msg.get("model") or ""),
                    _int(usage.get("input_tokens")),
                    _int(usage.get("cache_creation_input_tokens")),
                    _int(usage.get("cache_read_input_tokens")),
                    _int(usage.get("output_tokens")),
                    1 if ev.get("isSidechain") else 0,
                ))
    except OSError:
        pass
    return session_id, rows


def _transcript_paths(root: Path):
    """Yield every ``*.jsonl`` transcript directly under each project dir."""
    try:
        project_dirs = sorted(
            (d for d in root.iterdir() if d.is_dir()), key=lambda p: p.name
        )
    except OSError:
        return
    for pd in project_dirs:
        try:
            files = sorted(
                (f for f in pd.iterdir() if f.is_file() and f.name.endswith(".jsonl")),
                key=lambda p: p.name,
            )
        except OSError:
            continue
        yield from files


_LEDGER_LOCK = threading.Lock()
_REFRESH_RUNNING = False


def _scan_incremental(conn, root: Path, deadline=None):
    """Catch the ledger up to the transcripts on disk.

    Returns ``{scanned, remaining, skipped}``. Per-file work happens only when
    ``(mtime, size)`` changed — the ledger row IS the persistent cache. Files
    are processed newest-first so today's sessions get priced before a large
    historical backlog; when ``deadline`` trips, unprocessed paths are queued
    in ``backlog`` for a background pass.
    """
    stats = {"scanned": 0, "remaining": 0, "skipped": 0}
    on_disk = set()
    candidates = []
    # One read of the ledger's file table instead of a query per transcript.
    known_map = {
        r["path"]: (r["mtime"], r["size"])
        for r in conn.execute("SELECT path, mtime, size FROM files")
    }
    queued_set = {r["path"] for r in conn.execute("SELECT path FROM backlog")}
    for path in _transcript_paths(root):
        on_disk.add(str(path))
        try:
            st = path.stat()
        except OSError:
            continue
        row = known_map.get(str(path))
        if row and row[0] == st.st_mtime and row[1] == st.st_size:
            stats["skipped"] += 1
            continue
        if str(path) in queued_set:
            continue  # already queued for the background pass
        candidates.append((st.st_mtime, str(path), st.st_size))

    # Queued backlog first (it was deferred by an earlier budget), then fresh
    # changes newest-first.
    work = []
    for p in queued_set:
        try:
            st = os.stat(p)
        except OSError:
            conn.execute("DELETE FROM backlog WHERE path=?", (p,))
            continue
        work.append((st.st_mtime, p, st.st_size))
    work += candidates
    work.sort(key=lambda t: t[0], reverse=True)

    # Forget transcripts that vanished (their claims transfer to surviving
    # copies elsewhere, so replayed events keep counting exactly once).
    known = set(known_map)
    for gone in known - on_disk:
        with conn:
            _unclaim_file(conn, gone)
            conn.execute("DELETE FROM files WHERE path=?", (gone,))

    for _mtime, path_str, _size in work:
        if deadline is not None and time.monotonic() >= deadline:
            stats["remaining"] += 1
            conn.execute("INSERT OR IGNORE INTO backlog (path) VALUES (?)", (path_str,))
            continue
        _ingest_file(conn, path_str)
        conn.execute("DELETE FROM backlog WHERE path=?", (path_str,))
        stats["scanned"] += 1
    conn.commit()
    return stats


def _credit(conn, day, model, sid, i, cw, cr, o, sc, sign):
    """Add (sign=1) or remove (sign=-1) one event's tokens from the rollup."""
    conn.execute(
        "INSERT INTO rollup (day, model, session_id, input_tokens,"
        " cache_write_tokens, cache_read_tokens, output_tokens, sidechain, n)"
        " VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(day, model, session_id) DO UPDATE SET"
        "   input_tokens = input_tokens + excluded.input_tokens,"
        "   cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens,"
        "   cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens,"
        "   output_tokens = output_tokens + excluded.output_tokens,"
        "   sidechain = MAX(sidechain, excluded.sidechain),"
        "   n = n + excluded.n",
        (day, model, sid, i * sign, cw * sign, cr * sign, o * sign, sc, sign),
    )


def _unclaim_file(conn, path_str: str):
    """Remove a file's events, claims and rollup credit.

    Replay copies carry the identical API response, so when the claimant file
    goes away the claim transfers to any surviving copy — the rollup keeps
    counting the event exactly once, just under the other file.
    """
    owned = conn.execute(
        "SELECT e.dedupe_key k, e.day, e.model, e.session_id,"
        " e.input_tokens i, e.cache_write_tokens cw, e.cache_read_tokens cr,"
        " e.output_tokens o, e.sidechain sc"
        " FROM events e JOIN claims c ON c.dedupe_key = e.dedupe_key"
        " WHERE c.file_path = ? AND e.file_path = ?",
        (path_str, path_str),
    ).fetchall()
    for ev in owned:
        _credit(conn, ev["day"], ev["model"] or "", ev["session_id"] or "",
                ev["i"], ev["cw"], ev["cr"], ev["o"], ev["sc"], -1)
    conn.execute("DELETE FROM claims WHERE file_path=?", (path_str,))
    conn.execute("DELETE FROM events WHERE file_path=?", (path_str,))
    for ev in owned:
        nxt = conn.execute(
            "SELECT file_path, day, model, session_id, input_tokens i,"
            " cache_write_tokens cw, cache_read_tokens cr, output_tokens o,"
            " sidechain sc FROM events WHERE dedupe_key=? LIMIT 1",
            (ev["k"],),
        ).fetchone()
        if nxt:
            conn.execute(
                "INSERT OR IGNORE INTO claims (dedupe_key, file_path) VALUES (?,?)",
                (ev["k"], nxt["file_path"]),
            )
            _credit(conn, nxt["day"], nxt["model"] or "", nxt["session_id"] or "",
                    nxt["i"], nxt["cw"], nxt["cr"], nxt["o"], nxt["sc"], 1)


def _ingest_file(conn, path_str: str):
    """Parse one transcript, replace its claim rows, and update the rollup."""
    try:
        st = os.stat(path_str)
    except OSError:
        return
    session_id, rows = savings_scan_transcript(Path(path_str))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        _unclaim_file(conn, path_str)
        conn.execute(
            "INSERT OR REPLACE INTO files (path, mtime, size, scanned_at) VALUES (?,?,?,?)",
            (path_str, st.st_mtime, st.st_size, now),
        )
        for key, day, model, i, cw, cr, o, sc in rows:
            conn.execute(
                "INSERT OR IGNORE INTO events (dedupe_key, file_path, session_id,"
                " day, model, input_tokens, cache_write_tokens,"
                " cache_read_tokens, output_tokens, sidechain)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, path_str, session_id, day, model, i, cw, cr, o, sc),
            )
            claimed = conn.execute(
                "INSERT OR IGNORE INTO claims (dedupe_key, file_path) VALUES (?,?)",
                (key, path_str),
            ).rowcount
            if claimed:
                _credit(conn, day, model or "", session_id or "", i, cw, cr, o, sc, 1)


def _rollup(conn, since_day: str, until_day=None):
    """Deduped per-(day, model, session) token rows for the window.

    Reads the ingest-maintained rollup table, so a request never scans the
    raw event log — the per-(day, model, session) working set is tiny.
    """
    sql = (
        "SELECT day, model, session_id,"
        " SUM(input_tokens) i, SUM(cache_write_tokens) cw,"
        " SUM(cache_read_tokens) cr, SUM(output_tokens) o, MAX(sidechain) sc"
        " FROM rollup WHERE day >= ? {until} GROUP BY day, model, session_id"
    )
    if until_day:
        rows = conn.execute(sql.format(until="AND day < ?"), (since_day, until_day)).fetchall()
    else:
        rows = conn.execute(sql.format(until=""), (since_day,)).fetchall()
    return rows


def _ledger_has_data(conn):
    try:
        return conn.execute("SELECT 1 FROM files LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


# ---------------------------------------------------------------------------
# Free sessions — CCC runs marked runtime:"free" (L04) + the router's own count
# ---------------------------------------------------------------------------

def _load_free_ids():
    path = _free_ids_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    ids = data.get("ids") if isinstance(data, dict) else None
    return {str(s) for s in ids if s} if isinstance(ids, list) else set()


def _save_free_ids(ids):
    path = _free_ids_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema_version": 1, "ids": sorted(ids)}))
        tmp.replace(path)
    except OSError:
        pass


def _collect_free_session_ids():
    """Union of registry entries marked runtime=free and every id seen before.

    The on-disk registry prunes dead PIDs, so anything ever observed is folded
    into a persisted set — a $0 run stays counted after its spawn row is gone.
    """
    found = set()
    for entry in _spawn_registry_entries():
        if not isinstance(entry, dict):
            continue
        runtime = str(entry.get("runtime") or "").strip().lower()
        if runtime != "free":
            continue
        for key in ("session_id", "resumed_sid"):
            sid = str(entry.get(key) or "").strip()
            if sid:
                found.add(sid)
    known = _load_free_ids()
    new = found - known
    if new:
        _save_free_ids(known | found)
    return known | found


_ANALYTICS_CACHE = {"ts": 0.0, "data": {}}
_ANALYTICS_LOCK = threading.Lock()


def _router_credentials():
    """(base_url, email, password) from ~/.ccc/free-router.json, or None."""
    try:
        data = json.loads(_router_state_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    admin = data.get("admin") if isinstance(data.get("admin"), dict) else data
    email = admin.get("email") or admin.get("admin_email")
    password = admin.get("password") or admin.get("admin_password")
    port = data.get("port") or _ROUTER_DEFAULT_PORT
    if not email or not password:
        return None
    return f"http://127.0.0.1:{int(port)}", str(email), str(password)


def _http_json(url, *, method="GET", payload=None, token=None, timeout=_ROUTER_TIMEOUT_S):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, f"http {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, str(e)


def _router_analytics(range_key, *, force=False):
    """{input_tokens, output_tokens, requests, est_savings_usd} or None.

    The router's hourly aggregate covers ALL traffic through it — including
    clients CCC never spawned — so it can only ever raise the free-token
    total, never lower it. Ranges map onto the router's rolling windows
    (24h/7d/30d/90d); "all" uses the widest window it offers.
    """
    now = time.time()
    with _ANALYTICS_LOCK:
        cached = _ANALYTICS_CACHE["data"].get(range_key)
        if not force and cached and now - _ANALYTICS_CACHE["ts"] < _ANALYTICS_TTL_S:
            return cached
    creds = _router_credentials()
    if creds is None:
        return None
    base, email, password = creds
    login, err = _http_json(f"{base}/api/auth/login", method="POST",
                            payload={"email": email, "password": password})
    if err or not isinstance(login, dict):
        return None
    token = login.get("token")
    if not token:
        return None
    window = _ROUTER_ANALYTICS_RANGES.get(range_key, "7d")
    summary, err = _http_json(f"{base}/api/analytics/summary?range={window}", token=token)
    if err or not isinstance(summary, dict):
        return None
    out = {
        "input_tokens": _int(summary.get("totalInputTokens")),
        "output_tokens": _int(summary.get("totalOutputTokens")),
        "requests": _int(summary.get("totalRequests")),
        "est_savings_usd": float(summary.get("estimatedCostSavings") or 0.0),
        "window": window,
    }
    with _ANALYTICS_LOCK:
        _ANALYTICS_CACHE["data"][range_key] = out
        _ANALYTICS_CACHE["ts"] = now
    return out


# ---------------------------------------------------------------------------
# Plan cost — subscription_plans (usage DB) or the default Max plan
# ---------------------------------------------------------------------------

def _load_plans():
    """[{name, engine, monthly_fee, active_from, active_to}] or [] when absent."""
    path = _plans_db_path()
    if not path.exists():
        return []
    try:
        from ccc_server.usage_db import schema as _udb_schema
        conn = _udb_schema.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT name, engine, monthly_fee, active_from, active_to "
                "FROM subscription_plans"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except (sqlite3.Error, OSError, RuntimeError):
        return []


def _parse_day(value):
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _accrued_fee(plans, start_day, end_dt):
    """Fees accrued over [start_day, end_dt); the final (partial) day prorates.

    Mirrors usage_db.fees.fee_for_range at second granularity: each plan's
    monthly fee divides by the days in its calendar month, and the window's
    share of each day counts (a full past day = its full daily slice).
    """
    import calendar
    total = 0.0
    end_day = end_dt.date() + timedelta(days=1)  # exclusive
    day = start_day
    while day < end_day:
        dim = calendar.monthrange(day.year, day.month)[1]
        day_start = datetime(day.year, day.month, day.day, tzinfo=end_dt.tzinfo)
        day_end = day_start + timedelta(days=1)
        covered_end = min(end_dt, day_end)
        share = max((covered_end - day_start).total_seconds(), 0.0) / 86400.0
        if share > 0:
            for p in plans:
                try:
                    fee = float(p.get("monthly_fee") or 0.0)
                except (TypeError, ValueError):
                    continue
                if fee <= 0:
                    continue
                a = _parse_day(p["active_from"]) if p.get("active_from") else None
                b = _parse_day(p["active_to"]) if p.get("active_to") else None
                if a and day < a:
                    continue
                if b and day >= b:
                    continue
                total += fee * share / dim
        day += timedelta(days=1)
    return total


def _plan_block(plans, today):
    """Describe the effective plan config for the UI."""
    if not plans:
        return {
            "name": DEFAULT_PLAN_NAME,
            "monthly_usd": DEFAULT_PLAN_MONTHLY_USD,
            "source": "default",
            "editable": True,
            "plans": [{"engine": "claude_code", "name": DEFAULT_PLAN_NAME,
                       "monthly_usd": DEFAULT_PLAN_MONTHLY_USD, "source": "default"}],
        }
    active = []
    monthly_total = 0.0
    for p in plans:
        a = _parse_day(p["active_from"]) if p.get("active_from") else None
        b = _parse_day(p["active_to"]) if p.get("active_to") else None
        if (a and today < a) or (b and today >= b):
            continue
        fee = float(p.get("monthly_fee") or 0.0)
        monthly_total += fee
        active.append({"engine": p.get("engine") or "", "name": p.get("name") or "plan",
                       "monthly_usd": round(fee, 2), "source": "configured"})
    return {
        "name": active[0]["name"] if len(active) == 1 else f"{len(active)} plans",
        "monthly_usd": round(monthly_total, 2),
        "source": "configured",
        "editable": True,
        "plans": active,
    }


_PANEL_PLAN_NOTE = "Set in the CCC savings panel"


def savings_set_plan(monthly_usd=None, name=None, reset=False):
    """Write (or clear) the user-editable plan row. Returns (plan_block, error).

    Only rows the panel itself wrote (marked by _PANEL_PLAN_NOTE) are
    replaced — plans added through the `throughput plans` CLI are the user's
    own configuration and stay untouched.
    """
    from ccc_server.usage_db import schema as _udb_schema
    path = _plans_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fee = None
    if not reset and monthly_usd is not None:
        try:
            fee = float(monthly_usd)
        except (TypeError, ValueError):
            return None, "monthly_usd must be a number"
        if not (0.0 <= fee <= _PLAN_MAX_USD):
            return None, f"monthly_usd must be between 0 and {_PLAN_MAX_USD:.0f}"
    try:
        conn = _udb_schema.connect(str(path))
        try:
            _udb_schema.migrate(conn)
            with conn:
                conn.execute(
                    "DELETE FROM subscription_plans WHERE engine='claude_code' AND note=?",
                    (_PANEL_PLAN_NOTE,),
                )
                if fee is not None:
                    plan_name = (str(name).strip()[:40] or "primary") if name else "primary"
                    conn.execute(
                        "INSERT INTO subscription_plans "
                        "(name, engine, monthly_fee, currency, active_from, active_to, note) "
                        "VALUES (?, 'claude_code', ?, 'USD', NULL, NULL, ?)",
                        (plan_name, fee, _PANEL_PLAN_NOTE),
                    )
        finally:
            conn.close()
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
        return None, f"could not save plan: {e}"
    _bust_payload_cache()
    return _plan_block(_load_plans(), datetime.now().astimezone().date()), None


# ---------------------------------------------------------------------------
# Range windows (local calendar, matching how the rest of the UI says "today")
# ---------------------------------------------------------------------------

_RANGES = {"today", "week", "month", "all"}


def _range_window(range_key, now=None):
    """-> (since_day:date, until_day:date, label). until is exclusive (=tomorrow)."""
    now = now or datetime.now().astimezone()
    today = now.date()
    if range_key == "today":
        return today, today + timedelta(days=1), "Today"
    if range_key == "week":
        return today - timedelta(days=today.weekday()), today + timedelta(days=1), "This week"
    if range_key == "month":
        return today.replace(day=1), today + timedelta(days=1), "This month"
    return datetime(1970, 1, 1).date(), today + timedelta(days=1), "All time"


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

_PAYLOAD_CACHE = {}
_PAYLOAD_LOCK = threading.Lock()


def _bust_payload_cache():
    with _PAYLOAD_LOCK:
        _PAYLOAD_CACHE.clear()


def _price(rates, i, cw, cr, o):
    return (i * rates[0] + cw * rates[1] + cr * rates[2] + o * rates[3]) / 1_000_000.0


def _refresh_ledger_sync(deadline_s=_SYNC_SCAN_BUDGET_S, root=None, ledger=None):
    """Sync incremental scan within a time budget; spawn a bg pass for the rest."""
    global _REFRESH_RUNNING
    root = Path(root) if root else _projects_root()
    ledger = Path(ledger) if ledger else _ledger_path()
    stats = {"scanned": 0, "remaining": 0, "skipped": 0}
    if root.is_dir():
        try:
            conn = _connect_ledger(ledger)
            try:
                deadline = time.monotonic() + deadline_s if deadline_s else None
                stats = _scan_incremental(conn, root, deadline)
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            pass
    if stats["remaining"]:
        with _LEDGER_LOCK:
            running = _REFRESH_RUNNING
            if not running:
                _REFRESH_RUNNING = True
        if not running:
            def _bg():
                global _REFRESH_RUNNING
                try:
                    conn2 = _connect_ledger(ledger)
                    try:
                        _scan_incremental(conn2, root, None)
                    finally:
                        conn2.close()
                except (sqlite3.Error, OSError):
                    pass
                finally:
                    _REFRESH_RUNNING = False
            threading.Thread(target=_bg, daemon=True, name="ccc-savings-scan").start()
    return stats


def savings_payload(range_key="today", *, refresh=False, projects_root=None,
                    ledger_db=None, free_ids=None, analytics_fn=None, now=None,
                    scan_budget_s=_SYNC_SCAN_BUDGET_S):
    """Build the ``/api/savings`` response. Returns (payload, http_status)."""
    range_key = str(range_key or "today").strip().lower()
    if range_key not in _RANGES:
        return ({"ok": False, "error": f"range must be one of {sorted(_RANGES)}"}, 400)

    now = now or datetime.now().astimezone()
    # Explicitly-injected data sources (tests, fixtures) skip the shared
    # TTL cache so one fixture's numbers can't leak into another's.
    injected = projects_root is not None or ledger_db is not None
    with _PAYLOAD_LOCK:
        cached = _PAYLOAD_CACHE.get(range_key)
    if cached and not refresh and not injected and (time.time() - cached["ts"] < _PAYLOAD_TTL_S):
        return dict(cached["payload"]), cached["status"]

    ledger = Path(ledger_db) if ledger_db else _ledger_path()
    stats = _refresh_ledger_sync(scan_budget_s, root=projects_root, ledger=ledger)
    free_set = set(free_ids) if free_ids is not None else _collect_free_session_ids()
    analytics = (analytics_fn or _router_analytics)(range_key)

    since_day, until_day, label = _range_window(range_key, now)

    api_value = 0.0
    estimated_value = 0.0   # value priced at the Sonnet fallback (model unknown)
    tokens = 0
    free_tokens_tracked = 0
    free_in_tracked = 0      # input-side tokens (incl. cache) for router compare
    free_out_tracked = 0
    free_saved_tracked = 0.0
    sessions = set()
    free_sessions = set()
    by_day = {}
    by_model = {}
    first_day = None
    warming = bool(stats.get("remaining"))

    try:
        conn = _connect_ledger(ledger)
        has_data = _ledger_has_data(conn)
        rows = _rollup(conn, str(since_day), str(until_day)) if has_data else []
        if range_key == "all":
            r = conn.execute("SELECT MIN(day) d FROM rollup").fetchone()
            first_day = r["d"] if r and r["d"] else None
        conn.close()
    except (sqlite3.Error, OSError):
        rows = []

    for r in rows:
        i, cw, cr, o = r["i"] or 0, r["cw"] or 0, r["cr"] or 0, r["o"] or 0
        total = i + cw + cr + o
        model = r["model"] or ""
        sid = r["session_id"] or ""
        is_free = bool(sid) and sid in free_set
        if is_free:
            cost = _price(_SONNET_RATES, i, cw, cr, o)
            free_sessions.add(sid)
            free_tokens_tracked += total
            free_in_tracked += i + cw + cr
            free_out_tracked += o
            free_saved_tracked += cost
        else:
            rates, known = _rates_for(model)
            cost = _price(rates, i, cw, cr, o)
            if not known:
                estimated_value += cost
        api_value += cost
        tokens += total
        if sid:
            sessions.add(sid)
        d = by_day.setdefault(r["day"], {"day": r["day"], "api_value_usd": 0.0,
                                         "free_saved_usd": 0.0, "tokens": 0})
        d["api_value_usd"] += cost
        if is_free:
            d["free_saved_usd"] += cost
        d["tokens"] += total
        if model:
            m = by_model.setdefault(model, {"model": model, "cost_usd": 0.0, "tokens": 0})
            m["cost_usd"] += cost
            m["tokens"] += total

    # Router-level free traffic can exceed what CCC sessions account for
    # (other tools pointed at the same router). Only ever adds, never hides.
    free_tokens = free_tokens_tracked
    free_saved = free_saved_tracked
    free_source = "sessions" if free_sessions else "none"
    router_connected = analytics is not None
    if analytics:
        routed_total = analytics["input_tokens"] + analytics["output_tokens"]
        if routed_total > free_tokens:
            extra_in = max(analytics["input_tokens"] - free_in_tracked, 0)
            extra_out = max(analytics["output_tokens"] - free_out_tracked, 0)
            free_saved += _price(_SONNET_RATES, extra_in, 0, 0, extra_out)
            free_tokens = routed_total
        if free_sessions:
            free_source = "sessions+router"
        elif routed_total:
            free_source = "router"

    # Plan cost accrues over the same local window; the default Max plan
    # applies only when the user configured nothing at all. For "all time"
    # the window starts at 1970, so accrue only from the first day that has
    # recorded work — an "all-time" plan cost of decades is nonsense.
    plans = _load_plans()
    effective_plans = plans if plans else [
        {"name": DEFAULT_PLAN_NAME, "engine": "claude_code",
         "monthly_fee": DEFAULT_PLAN_MONTHLY_USD, "active_from": None, "active_to": None}
    ]
    plan_start = since_day
    if range_key == "all":
        plan_start = _parse_day(first_day) if first_day else now.date()
    plan_cost = _accrued_fee(effective_plans, plan_start, now)
    plan_block = _plan_block(plans, now.date())

    roi = round(api_value / plan_cost, 1) if plan_cost > 0 and api_value > 0 else None
    payload = {
        "ok": True,
        "range": range_key,
        "label": label,
        "since": str(first_day) if range_key == "all" and first_day else str(since_day),
        "until": str(until_day),
        "api_value_usd": round(api_value, 2),
        "plan_cost_usd": round(plan_cost, 2),
        "roi_x": roi,
        "free_tokens": free_tokens,
        "free_saved_usd": round(free_saved, 2),
        "free_runs": len(free_sessions),
        "free_source": free_source,
        "router_connected": router_connected,
        "sessions": len(sessions),
        "tokens": tokens,
        "estimated_share_usd": round(estimated_value, 2),
        "partial": warming,
        "plan": plan_block,
        "by_day": [
            {"day": d["day"], "api_value_usd": round(d["api_value_usd"], 2),
             "free_saved_usd": round(d["free_saved_usd"], 2), "tokens": d["tokens"]}
            for d in (by_day[k] for k in sorted(by_day))
        ],
        "models": [
            {"model": m["model"], "label": _pretty_model(m["model"]),
             "cost_usd": round(m["cost_usd"], 2), "tokens": m["tokens"]}
            for m in sorted(by_model.values(), key=lambda x: x["cost_usd"], reverse=True)[:5]
        ],
        "engines": ["claude_code"],
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    status = 200
    with _PAYLOAD_LOCK:
        _PAYLOAD_CACHE[range_key] = {"ts": time.time(), "payload": payload, "status": status}
    return dict(payload), status


def handle_plan_post(data, *, payload_kwargs=None):
    """``POST /api/savings/plan`` — set/reset the editable plan. -> (payload, status)"""
    if not isinstance(data, dict):
        return {"ok": False, "error": "expected a JSON object"}, 400
    reset = bool(data.get("reset"))
    fee = data.get("monthly_usd", None)
    if not reset:
        if fee is None:
            return {"ok": False, "error": "monthly_usd required (or reset:true)"}, 400
        try:
            fee = float(fee)
        except (TypeError, ValueError):
            return {"ok": False, "error": "monthly_usd must be a number"}, 400
        if not (0.0 <= fee <= _PLAN_MAX_USD):
            return {"ok": False, "error": f"monthly_usd must be between 0 and {_PLAN_MAX_USD:.0f}"}, 400
    name = data.get("name")
    plan, err = savings_set_plan(monthly_usd=None if reset else fee, name=name, reset=reset)
    if err:
        return {"ok": False, "error": err}, 400
    payload, status = savings_payload(
        str(data.get("range") or "today"), refresh=True, **(payload_kwargs or {})
    )
    out = {"ok": True, "plan": plan, "savings": payload}
    return out, status


def _reset_for_tests():
    """Clear module-level caches so a test starts from a clean slate."""
    global _REFRESH_RUNNING
    with _PAYLOAD_LOCK:
        _PAYLOAD_CACHE.clear()
    with _ANALYTICS_LOCK:
        _ANALYTICS_CACHE["ts"] = 0.0
        _ANALYTICS_CACHE["data"] = {}
    _REFRESH_RUNNING = False
