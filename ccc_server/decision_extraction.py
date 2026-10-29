# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Nightly decision extraction + report-only MEMORY.md staleness audit.

Problem: the owner rules on things mid-conversation ("we decided X", "go
with option B", "approved") and that ruling only ever lives inside one
session transcript. It never becomes a durable, queryable record, so a
later session (or the owner) has no way to ask "what did we decide about
X?" without re-reading transcripts by hand.

This module runs a background scan (a daemon thread the server starts --
no new launchd job, same pattern as ccc_server.decision_inbox) that:

1. Walks session transcripts incrementally, gated by (mtime, size) exactly
   like ccc_server.ship_graph's transcript sync -- a file already recorded
   with the same (mtime, size) is never re-opened.
2. Extracts explicit decisions from genuine user-authored text only (never
   tool_result echoes, never injected <system-reminder> blocks) using a
   tiered regex heuristic (strong / medium / weak phrase families), and
   stores each hit -- verbatim quote, session id, repo, date -- in a small
   SQLite table beside ship_graph.sqlite (``decisions.sqlite``).
3. Optionally (off by default, ``use_model_fallback``) escalates
   low-confidence heuristic hits to a cheap headless model call, capped by
   ``model_call_budget_per_run``. The hand-checked precision sample in
   MEMO-FIX-7's close summary showed the heuristic alone clears the 70%
   bar, so this path exists for future drift but is not exercised by
   default.
4. Exposes ``list_decisions()`` as the plain function a decisions UI
   (MEMO-FIX-5, not yet landed) can call, plus a read-only API surface.
5. ``audit_memory_staleness()`` is a *separate*, report-only pass: for each
   project's ``memory/MEMORY.md`` it flags entries whose linked memory file
   predates a same-project decision that shares distinctive terms with it.
   It never edits a memory file -- only ever returns a list of findings for
   a human to review.

Every server name is reached the same way as ccc_server.decision_inbox:
directly from disk / sqlite, no _core dependency, so tests exercise the
whole pipeline without server.py or a real transcript tree.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_tls = threading.local()
_run_lock = threading.Lock()
_running = {"since": None}

CONFIG_FILE_NAME = "decision-extraction.json"

DEFAULT_CONFIG = {
    "enabled": True,
    # How often the daemon thread wakes to check whether a nightly run is due.
    "interval_s": 3600,
    # "Nightly": the actual scan only runs once this long has passed since
    # the last completed run (persisted in the decisions.sqlite meta table).
    "run_every_s": 20 * 3600,
    # 0 = no cutoff (first run backfills every transcript found; subsequent
    # runs are cheap because (mtime, size) gates re-parsing). Set a positive
    # number of days to bound backfill cost on a host with a huge archive.
    "days": 0,
    # A single run only opens this many changed files, so a giant backfill
    # spreads across several nightly runs instead of blocking one for ages.
    "max_files_per_run": 800,
    # Escalate low-confidence heuristic hits to a cheap headless model.
    # Off by default -- see module docstring.
    "use_model_fallback": False,
    "model": "claude-haiku-4-5-20251001",
    "model_call_budget_per_run": 20,
    "model_confidence_floor": 0.5,
}


# ── paths / config ───────────────────────────────────────────────────────────

def _ccc_dir() -> Path:
    return Path.home() / ".claude" / "command-center"


def _get_db_path() -> Path:
    env = os.environ.get("CCC_DECISIONS_DB")
    if env:
        return Path(env)
    return _ccc_dir() / "decisions.sqlite"


def _get_projects_dir() -> Path:
    env = os.environ.get("CCC_PROJECTS_ROOT")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "projects"


def _get_codex_dir() -> Path:
    env = os.environ.get("CCC_CODEX_SESSIONS_ROOT")
    if env:
        return Path(env)
    return Path.home() / ".codex" / "sessions"


def config_path(path=None):
    if path:
        return Path(path)
    env = os.environ.get("CCC_DECISION_EXTRACTION_CONFIG")
    if env:
        return Path(env)
    return _ccc_dir() / CONFIG_FILE_NAME


def load_config(path=None):
    """Defaults overlaid with the user's JSON file. Unknown keys pass
    through (a forward-compatible file must not break an older server)."""
    cfg = dict(DEFAULT_CONFIG)
    p = config_path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        cfg.update(raw)
    return cfg


# ── sqlite ────────────────────────────────────────────────────────────────────

def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            val TEXT
        );

        CREATE TABLE IF NOT EXISTS scan_state (
            path TEXT PRIMARY KEY,
            mtime REAL,
            size INTEGER
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            repo TEXT,
            engine TEXT,
            ts REAL,
            date TEXT,
            quote TEXT,
            pattern TEXT,
            confidence REAL,
            source_path TEXT,
            project_dir TEXT,
            extracted_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_repo ON decisions(repo);
        CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(date);
        CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id);
        CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_dir);
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    if "source_line" not in cols:
        conn.execute("ALTER TABLE decisions ADD COLUMN source_line INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_source ON decisions(source_path)")
    conn.commit()


def _get_connection() -> sqlite3.Connection:
    if not hasattr(_tls, "conn") or _tls.conn is None:
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _tls.conn = sqlite3.connect(str(db_path), timeout=30.0)
        _init_db(_tls.conn)
    return _tls.conn


def _reset_connection_for_tests():
    """Test helper: drop the thread-local connection so a fresh env
    (new CCC_DECISIONS_DB) takes effect on the next _get_connection()."""
    if hasattr(_tls, "conn") and _tls.conn is not None:
        try:
            _tls.conn.close()
        except Exception:
            pass
        _tls.conn = None


# ── time helpers ──────────────────────────────────────────────────────────────

def _ts(s) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _date_of(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _repo_of(cwd: str) -> str:
    if not cwd:
        return ""
    name = Path(cwd.rstrip("/")).name
    return re.sub(r"-wt-.*$", "", name)


# ── heuristic decision matcher ────────────────────────────────────────────────
#
# Tiered by precision: "strong" phrases (explicit ruling language) match at
# any sentence length; "medium" and "weak" phrases only match short sentences,
# because a long sentence containing e.g. "approved" is far more likely to be
# incidental prose than an actual ruling. Patterns run against genuine
# user-authored text only (see _genuine_user_text / _codex_user_text) -- this
# alone removes most of the false-positive surface (assistant hedging,
# tool_result echoes, injected system text).

_STRONG = [
    ("decided", re.compile(r"\b(?:we|i)(?:'ve| have)?\s+decided\b|\bdecided to\b", re.I), 0.9),
    ("decision_marker", re.compile(r"\bdecision\s*(?:is|:)\s*\S", re.I), 0.85),
    ("ruling", re.compile(r"\bruling\s*:\s*\S", re.I), 0.9),
    ("final_call", re.compile(r"\bfinal (?:call|answer|decision)\s*(?:is|:)?\s*\S", re.I), 0.85),
]
_MEDIUM = [
    ("go_with", re.compile(r"\b(?:let'?s|we'?ll|we'?re|i'?ll|going to)\s+go with\b", re.I), 0.75),
    ("stick_with", re.compile(r"\bstick(?:ing)? with\b", re.I), 0.7),
    ("instead_of", re.compile(r"\binstead of\b.{0,80}\b(?:let'?s|we'?ll|use|go with|do)\b", re.I), 0.7),
    ("use_instead", re.compile(r"\b(?:use|do)\s+\S.{0,60}\binstead\b", re.I), 0.65),
]
_WEAK = [
    ("approved", re.compile(r"(?<![/-])\bapproved\b(?![/-])", re.I), 0.55),
    ("yes_go_ahead", re.compile(r"^(?:yes|yep|yeah|sure|ok|okay)[,.]?\s+(?:let'?s|go with|do that|use|proceed)\b", re.I), 0.55),
]

# "confirmed" was tried and dropped: on a 30-sample hand-check it was almost
# entirely technical confirmations ("confirmed X is nullable"), not rulings.

# A ruling is short. Anything past this is far more likely to be a large
# pasted/scraped blob (e.g. a Reddit post's JSON body) that happens to contain
# a trigger phrase somewhere inside it than an actual human-typed decision --
# this is what the strong tier's unbounded length let through pre-MEMO-FIX-7
# validation (a Reddit-post JSON dump containing "I decided to ..." matched
# even though the "sentence" _split_sentences produced was thousands of chars).
_STRONG_MAX_LEN = 500
_MEDIUM_MAX_LEN = 300
_WEAK_MAX_LEN = 200

# Pasted/scraped structured content (Reddit JSON dumps seen in reddit-writer
# transcripts: `{"id": "...", "sub": "r/...", "author": "...", ...}`) is not
# a human ruling even when a trigger phrase appears inside one field's text.
_JSON_FIELD_RE = re.compile(r'"[a-zA-Z_]+"\s*:\s*"')
_SUBREDDIT_RE = re.compile(r"\br/[A-Za-z]\w+\b")
# A JS-style object literal (unquoted or single-quoted keys, e.g. a logged
# `{ ruleCode: '3d', decision: { title: ... } }`) is source code pasted into
# a message, not prose -- "decision:" as a dict key is not a ruling.
_CODE_LITERAL_RE = re.compile(r"\w+\s*:\s*[\{'\"]")
# "decision: {" / "decision: [" on its own line (pretty-printed JSON/JS,
# split into a one-line "sentence" by the newline splitter below) is always
# a dict key, never prose -- no length-of-2 threshold needed for this one.
_DECISION_AS_CODE_KEY_RE = re.compile(r"\bdecision\s*:\s*[\{\[]", re.I)


def _looks_like_pasted_blob(s: str) -> bool:
    return (
        len(_JSON_FIELD_RE.findall(s)) >= 2
        or bool(_SUBREDDIT_RE.search(s))
        or len(_CODE_LITERAL_RE.findall(s)) >= 2
        or bool(_DECISION_AS_CODE_KEY_RE.search(s))
    )


# A sentence describing a decision or approval that was explicitly NOT made
# ("no user decision", "nor had I decided", "NOT approved the spec", "not
# approved for launch") is the opposite of a ruling -- caught on precision
# validation, where these outnumbered every other single false-positive
# cause across three independent hand-checked samples.
_UNDECIDED_RE = re.compile(r"\b(?:no|not|n't|nor)\b[^.!?]{0,20}\b(?:deci|approv|final)", re.I)

# A sentence describing a decision as a future contingency ("if a human
# decision is genuinely required", "needs a human decision") is describing
# a decision that has NOT happened -- a distinct, equally common
# false-positive class from the negated-decision one above (both point the
# same way: describing a decision's absence, not its outcome).
_PENDING_DECISION_RE = re.compile(
    r"\b(?:decision|approval)\b[^.!?]{0,25}\b(?:is|are)?\s*(?:needed|required|necessary"
    r"|(?:still\s+)?open|pending|unresolved|outstanding)\b"
    r"|\b(?:needs?|requires?|requiring|pending|open|unresolved|outstanding)\b[^.!?]{0,25}\bdecision\b"
    r"|\bwithout\s+a\b[^.!?]{0,15}\bdecision\b"
    # This user's own Becky/BYM escalation workflow logs a specific template
    # phrase, "Surface for <name>'s decision: ...", when a ticket is
    # escalated for a human ruling that has NOT happened yet.
    r"|\bsurface(?:d)? for\b[^.!?]{0,40}\bdecision\b",
    re.I,
)


def _looks_undecided(s: str) -> bool:
    return bool(_UNDECIDED_RE.search(s)) or bool(_PENDING_DECISION_RE.search(s))


_SYSTEM_BLOCK_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>|<command-name>.*?</command-args>",
    re.S,
)


def _split_sentences(text: str) -> list[str]:
    text = _SYSTEM_BLOCK_RE.sub(" ", text or "")
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def match_decision(sentence: str):
    """(pattern_name, confidence) for the first matching tier, or None."""
    s = (sentence or "").strip()
    if len(s) < 6:
        return None
    if _looks_like_pasted_blob(s) or _looks_undecided(s):
        return None
    if len(s) <= _STRONG_MAX_LEN:
        for name, rx, conf in _STRONG:
            if rx.search(s):
                return name, conf
    if len(s) <= _MEDIUM_MAX_LEN:
        for name, rx, conf in _MEDIUM:
            if rx.search(s):
                return name, conf
    if len(s) <= _WEAK_MAX_LEN:
        for name, rx, conf in _WEAK:
            if rx.search(s):
                return name, conf
    return None


def extract_decisions_from_text(text: str):
    """[(quote, pattern, confidence), ...] for one block of user text."""
    out = []
    for sentence in _split_sentences(text):
        hit = match_decision(sentence)
        if not hit:
            continue
        pattern, confidence = hit
        out.append((sentence[:280], pattern, confidence))
    return out


# ── transcript parsing (claude + codex) ──────────────────────────────────────

def _genuine_user_text(entry: dict) -> str | None:
    """Real, human-typed text only: never a tool_result echo, never a
    subagent/meta line, never an automation-injected prompt. Mirrors
    ship_graph._text_of's type filter, plus a promptSource check --
    "sdk"/"system"/"queued" entries are programmatic injections (e.g. a
    headless automation harness submitting a template-loaded prompt as the
    "user" turn), not something a person typed, and read like third-person
    narrative that trips the decision heuristics (observed on reddit-writer's
    scraped-post-analysis prompts during MEMO-FIX-7 precision validation)."""
    if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
        return None
    if entry.get("promptSource") not in (None, "typed"):
        return None
    msg = entry.get("message") or {}
    if msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
        return text.strip() or None
    return None


def _codex_user_text(entry: dict) -> str | None:
    if entry.get("type") != "response_item":
        return None
    p = entry.get("payload") or {}
    if p.get("type") != "message" or p.get("role") != "user":
        return None
    content = p.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") in ("input_text", "text")]
        text = "\n".join(p2 for p2 in parts if p2)
        return text.strip() or None
    return None


def extract_decisions_from_file(path: str, engine: str):
    """Every decision hit in one transcript file; malformed lines are skipped.

    Read errors propagate so the background scan can retain its previous
    extraction and retry this file instead of caching a failed read.
    """
    sid = Path(path).stem
    cwd = ""
    out = []
    seen = set()
    with open(path, "rb") as f:
        for line_number, raw in enumerate(f, 1):
            try:
                d = json.loads(raw)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            if engine == "claude":
                cwd = cwd or d.get("cwd") or ""
                text = _genuine_user_text(d)
                ts = _ts(d.get("timestamp"))
            else:  # codex
                payload = d.get("payload") or {}
                if d.get("type") == "session_meta":
                    sid = payload.get("id") or payload.get("session_id") or sid
                    cwd = payload.get("cwd") or cwd
                    continue
                if d.get("type") == "turn_context" and not cwd:
                    cwd = payload.get("cwd") or cwd
                text = _codex_user_text(d)
                ts = _ts(d.get("timestamp"))
            if not text:
                continue
            for quote, pattern, confidence in extract_decisions_from_text(text):
                key = quote.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "id": "dec_" + uuid.uuid4().hex[:12],
                    "session_id": sid or Path(path).stem,
                    "repo": _repo_of(cwd),
                    "engine": engine,
                    "ts": ts or 0.0,
                    "date": _date_of(ts) if ts else "",
                    "quote": quote,
                    "pattern": pattern,
                    "confidence": confidence,
                    "source_path": path,
                    "source_line": line_number,
                    "project_dir": Path(path).parent.name if engine == "claude" else "",
                })
    return out


# ── candidate discovery (mtime, size gated) ──────────────────────────────────

def _candidate_files(cfg=None):
    cfg = cfg or {}
    days = float(cfg.get("days") or 0)
    cutoff = (time.time() - days * 86400) if days > 0 else 0.0
    out = []
    p_dir = _get_projects_dir()
    if p_dir.exists():
        for p in p_dir.glob("*/*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 0 and st.st_mtime >= cutoff:
                out.append(("claude", str(p), st.st_mtime, st.st_size))
    c_dir = _get_codex_dir()
    if c_dir.exists():
        for p in c_dir.rglob("*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 0 and st.st_mtime >= cutoff:
                out.append(("codex", str(p), st.st_mtime, st.st_size))
    return out


# ── scan run ──────────────────────────────────────────────────────────────────

def run_once(*, cfg=None, now=None, persist=True, files=None, model_classifier=None):
    """One incremental scan. Every input is injectable so tests never touch
    the real filesystem or spawn a subprocess. Returns a run record."""
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    conn = _get_connection()
    started = time.time()
    run_id = "dex_" + uuid.uuid4().hex[:8]

    have = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT path, mtime, size FROM scan_state")}
    candidates = files if files is not None else _candidate_files(cfg)
    todo = [(eng, p, mt, sz) for (eng, p, mt, sz) in candidates if have.get(p) != (mt, sz)]
    max_files = int(cfg.get("max_files_per_run") or DEFAULT_CONFIG["max_files_per_run"])
    truncated = len(todo) > max_files
    todo = todo[:max_files]

    new_decisions = []
    scan_rows = []
    errors = []
    for eng, path, mt, sz in todo:
        try:
            found = extract_decisions_from_file(path, engine=eng)
        except Exception as e:  # never let one bad file kill the run
            errors.append(f"{path}: {e}"[:200])
            continue
        new_decisions.extend(found)
        scan_rows.append((path, mt, sz))

    model_checked = 0
    if cfg.get("use_model_fallback") and model_classifier is not None:
        floor = float(cfg.get("model_confidence_floor") or DEFAULT_CONFIG["model_confidence_floor"])
        budget = int(cfg.get("model_call_budget_per_run") or 0)
        kept = []
        for d in new_decisions:
            if budget > 0 and d["confidence"] < 0.7:
                try:
                    verdict = bool(model_classifier(d["quote"]))
                    model_checked += 1
                    budget -= 1
                    d["confidence"] = max(d["confidence"], 0.85) if verdict else 0.2
                    d["model_checked"] = True
                except Exception as e:
                    errors.append(f"model_classifier: {e}"[:200])
            if d["confidence"] >= floor:
                kept.append(d)
        new_decisions = kept

    inserted = 0
    if persist:
        with conn:
            # A changed transcript replaces its previous extraction. Otherwise
            # every append duplicates old quotes and edits leave stale rulings.
            conn.executemany("DELETE FROM decisions WHERE source_path = ?",
                             [(p,) for p, _, _ in scan_rows])
            for d in new_decisions:
                conn.execute(
                    "INSERT OR REPLACE INTO decisions "
                    "(id, session_id, repo, engine, ts, date, quote, pattern, confidence, "
                    "source_path, project_dir, extracted_at, source_line) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d["id"], d["session_id"], d["repo"], d["engine"], d["ts"], d["date"],
                     d["quote"], d["pattern"], d["confidence"], d["source_path"],
                     d["project_dir"], now, d.get("source_line")),
                )
                inserted += 1
            conn.executemany("INSERT OR REPLACE INTO scan_state VALUES (?,?,?)", scan_rows)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_run_at', ?)", (repr(now),))

    return {
        "run_id": run_id,
        "candidates": len(candidates),
        "scanned": len(todo),
        "truncated": truncated,
        "new_decisions": inserted,
        "model_checked": model_checked,
        "errors": errors,
        "duration_s": round(time.time() - started, 3),
        "at": _iso(now),
    }


def last_run_at():
    conn = _get_connection()
    row = conn.execute("SELECT val FROM meta WHERE key = 'last_run_at'").fetchone()
    if not row:
        return None
    try:
        return _iso(float(row[0]))
    except (TypeError, ValueError):
        return None


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
    threading.Thread(target=_go, daemon=True, name="ccc-decision-extraction-run").start()
    return {"ok": True, "started": True}


def decision_extraction_loop(initial_delay_s=180):
    """Daemon thread target: wakes every ``interval_s`` and only actually
    scans once ``run_every_s`` has elapsed since the last completed run
    (persisted in decisions.sqlite) -- "nightly" without a second daemon or
    a launchd job. Every failure just waits for the next wake."""
    try:
        time.sleep(initial_delay_s)
    except Exception:
        return
    while True:
        cfg = load_config()
        if cfg.get("enabled", True):
            try:
                conn = _get_connection()
                row = conn.execute("SELECT val FROM meta WHERE key = 'last_run_at'").fetchone()
                last = float(row[0]) if row else 0.0
                due_s = float(cfg.get("run_every_s") or DEFAULT_CONFIG["run_every_s"])
                if time.time() - last >= due_s:
                    start_background_run(cfg)
            except Exception:
                pass
        try:
            time.sleep(max(300, int(cfg.get("interval_s") or DEFAULT_CONFIG["interval_s"])))
        except Exception:
            return


# ── query surface: the function a decisions UI (MEMO-FIX-5) can call ────────

_QUERY_STOPWORDS = frozenset("""
    a an and are as at be been but by did do does for from had has have how i
    in is it its me my of on or our that the their this to was we were what
    when where which who why will with you your about decide decided decision
    decisions remember please tell use using
""".split())


def _read_decisions(where: str, params: list, order: str, limit: int) -> list[dict]:
    """Read existing records only: never create/migrate a DB or start a scan.

    Older stores remain usable before the background writer adds source_line.
    A missing/locked store leaves callers free to use the session fallback.
    """
    try:
        conn = sqlite3.connect(_get_db_path().resolve().as_uri() + "?mode=ro",
                               uri=True, timeout=0.1)
        try:
            conn.row_factory = sqlite3.Row
            cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
            line = "source_line" if "source_line" in cols else "NULL AS source_line"
            rows = conn.execute(
                "SELECT id, session_id, repo, engine, ts, date, quote, pattern, "
                f"confidence, source_path, {line} FROM decisions WHERE {where} "
                f"ORDER BY {order} LIMIT ?", [*params, max(1, min(int(limit), 50))],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []


def search_decisions(topic: str, *, repo: str | None = None, limit: int = 10) -> list[dict]:
    """Keyword matches in stored user quotes, ranked by coverage then recency.

    This is evidence retrieval, not a verdict that a ruling is still current.
    Search the full store before LIMIT so older decisions remain discoverable.
    """
    terms = list(dict.fromkeys(w for w in re.findall(r"[^\W_]+", (topic or "")[:512].lower())
                              if len(w) > 1 and w not in _QUERY_STOPWORDS))[:12]
    if not terms:
        return []
    score = " + ".join("(instr(lower(quote), ?) > 0)" for _ in terms)
    where = f"({score}) > 0"
    params = list(terms)
    if repo:
        where += " AND repo = ?"
        params.append(repo)
    return _read_decisions(where, params + terms,
                           f"({score}) DESC, ts DESC, confidence DESC, id", limit)


def lookup_decision_sessions(ids: list[str]) -> dict[str, dict]:
    """Resolve Ask citations by indexed session id, without opening transcripts."""
    ids = list(dict.fromkeys(ids))[:12]
    if not ids:
        return {}
    # One row per requested session; a chatty session must not crowd out others.
    placeholders = ",".join("?" for _ in ids)
    rows = _read_decisions(
        f"id IN (SELECT id FROM (SELECT id, row_number() OVER "
        f"(PARTITION BY session_id ORDER BY ts DESC, id) AS n FROM decisions "
        f"WHERE session_id IN ({placeholders})) WHERE n = 1)", ids, "ts DESC, id", len(ids))
    return {r["session_id"]: {"session_id": r["session_id"], "repo": r["repo"],
            "title": "Decision: " + r["quote"], "snippet": r["quote"],
            "harness": r["engine"], "ts_unix": r["ts"]} for r in rows}


def list_decisions(*, repo=None, since_ts=None, limit=100):
    """Recent decisions, newest first. This is the plain function contract
    a future decisions interface (MEMO-FIX-5) plugs into; it does no
    scanning itself -- callers get whatever the last completed run found."""
    conn = _get_connection()
    q = ("SELECT id, session_id, repo, engine, ts, date, quote, pattern, "
         "confidence, source_path FROM decisions")
    clauses, params = [], []
    if repo:
        clauses.append("repo = ?")
        params.append(repo)
    if since_ts:
        clauses.append("ts >= ?")
        params.append(float(since_ts))
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY ts DESC LIMIT ?"
    params.append(max(1, min(int(limit or 100), 500)))
    cols = ["id", "session_id", "repo", "engine", "ts", "date", "quote", "pattern",
            "confidence", "source_path"]
    return [dict(zip(cols, row)) for row in conn.execute(q, params).fetchall()]


def decisions_api_payload(*, repo=None, limit=50):
    return {
        "ok": True,
        "decisions": list_decisions(repo=repo, limit=limit),
        "last_run_at": last_run_at(),
        "running_since": _running["since"],
    }


# ── report-only MEMORY.md staleness audit ────────────────────────────────────
#
# Correlation is exact, not fuzzy: a claude-engine decision's source_path
# lives under ~/.claude/projects/<project_dir>/<sid>.jsonl, and that same
# <project_dir> is exactly where the auto-memory system keeps
# memory/MEMORY.md and its linked files for that project. No repo-name
# guessing -- the project_dir column captures this at scan time.

_MEMORY_STOPWORDS = frozenset({
    "about", "after", "again", "always", "before", "being", "between",
    "could", "every", "never", "other", "shall", "since", "still", "their",
    "there", "these", "thing", "think", "those", "under", "until", "where",
    "which", "while", "would", "should", "session", "sessions",
})

_MEMORY_LINE_RE = re.compile(r"^-\s*\[(.*?)\]\((.*?)\)\s*[—\-–]?\s*(.*)$")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _keywords(text: str, min_len: int = 5) -> set[str]:
    words = re.findall(r"[A-Za-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= min_len and w not in _MEMORY_STOPWORDS}


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text or "")
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def _memory_modified_ts(memory_file: Path, frontmatter: dict) -> float:
    modified = frontmatter.get("modified")
    if modified:
        ts = _ts(modified)
        if ts:
            return ts
    try:
        return memory_file.stat().st_mtime
    except OSError:
        return 0.0


def _decisions_by_project():
    conn = _get_connection()
    rows = conn.execute(
        "SELECT id, session_id, repo, ts, date, quote, project_dir FROM decisions "
        "WHERE engine = 'claude' AND project_dir != '' ORDER BY ts DESC"
    ).fetchall()
    by_project: dict[str, list[dict]] = {}
    for dec_id, sid, repo, ts, date, quote, project_dir in rows:
        by_project.setdefault(project_dir, []).append({
            "id": dec_id, "session_id": sid, "repo": repo, "ts": ts,
            "date": date, "quote": quote,
        })
    return by_project


def audit_memory_staleness(*, decisions_by_project=None, min_shared_terms=2, projects_dir=None):
    """Report-only findings: a memory entry whose linked file predates a
    same-project decision it shares distinctive terms with. Never writes to
    any memory file -- callers decide whether/how to act on the report."""
    decisions_by_project = decisions_by_project if decisions_by_project is not None else _decisions_by_project()
    base = Path(projects_dir) if projects_dir else _get_projects_dir()
    findings = []

    for project_dir, decisions in decisions_by_project.items():
        mem_dir = base / project_dir / "memory"
        index = mem_dir / "MEMORY.md"
        if not index.is_file():
            continue
        try:
            index_text = index.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line in index_text.splitlines():
            m = _MEMORY_LINE_RE.match(line.strip())
            if not m:
                continue
            title, fname, hook = m.groups()
            mem_path = mem_dir / fname
            if not mem_path.is_file():
                continue
            try:
                body = mem_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            frontmatter = _parse_frontmatter(body)
            modified_ts = _memory_modified_ts(mem_path, frontmatter)
            mem_terms = (_keywords(hook) | _keywords(frontmatter.get("description", ""))
                         | _keywords(body[:1200]))
            if not mem_terms:
                continue
            for d in decisions:
                if not d["ts"] or d["ts"] <= modified_ts:
                    continue
                shared = mem_terms & _keywords(d["quote"])
                if len(shared) >= min_shared_terms:
                    findings.append({
                        "memory_file": str(mem_path),
                        "memory_title": title,
                        "project_dir": project_dir,
                        "decision_id": d["id"],
                        "session_id": d["session_id"],
                        "decision_date": d["date"],
                        "quote": d["quote"],
                        "shared_terms": sorted(shared),
                        "reason": "newer decision shares terms with this memory entry; review for staleness",
                    })
    findings.sort(key=lambda f: f["decision_date"], reverse=True)
    return findings


def audit_api_payload():
    return {"ok": True, "findings": audit_memory_staleness()}
