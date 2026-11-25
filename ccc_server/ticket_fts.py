"""Full-text index of this machine's WatchTower tickets for `ccc recall`.

Tickets often say what a session only implies: the customer problem in the
filing, and the outcome in the close summary. This indexes the local
WatchTower store (title, filing text, comments, progress notes, questions,
answers, close summary) and keeps, per ticket, the sessions that worked it so
a ticket hit can point at the session that ran it.

Cost rules (CLAUDE.md performance gates):
- A recall call first does one cheap aggregate query on the WatchTower store
  (`count(*)`, `max(updated_ts)`); when it matches the persisted signature,
  nothing else is read.
- On change, only `(number, updated_ts)` is listed; `item_json` is fetched
  and parsed only for tickets whose `updated_ts` moved.
- Syncs are throttled per process, since WatchTower writes about once a
  second.

GitHub-backed queues are not in the local store and are not indexed here.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

_SYNC_MIN_INTERVAL_S = 20.0
_BODY_CAP = 20000
_HISTORY_TEXT_FIELDS = {
    "comment": ("text",),
    "progress": ("text",),
    "block": ("question",),
    "answer": ("text",),
    "reopen": ("reason",),
    "defer": ("reason",),
    "verify": ("findings",),
    "wait": ("text",),
}
# Pasted screenshots are referenced by absolute path; the path is noise for search.
_IMAGE_PATH_RE = re.compile(r"(?:/[^\s/]+)+/[^\s/]+\.(?:png|jpe?g|gif|webp|heic)\b", re.I)

_lock = threading.Lock()
_last_sync = [0.0]
_schema_ready: set[str] = set()


def _get_db_path() -> Path:
    env = os.environ.get("CCC_TICKET_FTS_DB")
    if env:
        return Path(env)
    sfts = os.environ.get("CCC_SESSION_FTS_DB")  # keep beside a redirected session index
    if sfts:
        return Path(sfts).parent / "ticket_fts.sqlite"
    return Path.home() / ".claude" / "command-center" / "ticket_fts.sqlite"


def _get_wt_db_path() -> Path:
    env = os.environ.get("WATCHTOWER_DB")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "watchtower" / "queues.db"


def _connect() -> sqlite3.Connection:
    """A fresh connection per call: request threads never share one."""
    path = _get_db_path()
    key = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(key, timeout=30.0)
    conn.execute("PRAGMA synchronous=NORMAL")
    if key in _schema_ready:
        return conn
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS tdoc USING fts5(
            ref, title, body, tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS tmeta (
            number INTEGER PRIMARY KEY,
            rid INTEGER NOT NULL,
            ref TEXT NOT NULL,
            project TEXT NOT NULL,
            status TEXT NOT NULL,
            created TEXT NOT NULL,
            closed TEXT NOT NULL,
            updated_ts REAL NOT NULL,
            sessions TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS tmeta_rid ON tmeta (rid);
        CREATE TABLE IF NOT EXISTS tstate (k TEXT PRIMARY KEY, v TEXT NOT NULL);
    """)
    _schema_ready.add(key)
    return conn


def _clean(text) -> str:
    return _IMAGE_PATH_RE.sub(" ", str(text or "")).strip()


def ticket_doc(item: dict) -> dict:
    """The indexed fields and linked sessions of one WatchTower item."""
    parts: list[str] = []
    note = _clean(item.get("note"))
    text = _clean(item.get("text"))
    for t in (note, text if text != note else ""):
        if t:
            parts.append(t)
    resolution = item.get("resolution") if isinstance(item.get("resolution"), dict) else {}
    sessions: list[dict] = []
    seen: set[str] = set()
    order = {"closed": 0, "worked": 1, "touched": 2}

    def _add_session(sid, machine, role):
        sid = str(sid or "").strip()
        if not sid:
            return
        if sid in seen:
            for s in sessions:  # a session that claimed and then closed is the closer
                if s["session_id"] == sid and order[role] < order[s["role"]]:
                    s["role"] = role
            return
        seen.add(sid)
        sessions.append({"session_id": sid, "machine": str(machine or ""), "role": role})

    for h in item.get("history") or []:
        if not isinstance(h, dict):
            continue
        for field in _HISTORY_TEXT_FIELDS.get(str(h.get("event") or ""), ()):
            t = _clean(h.get(field))
            if t:
                parts.append(t)
        by = h.get("by") if isinstance(h.get("by"), dict) else {}
        if by.get("session_id"):
            role = "closed" if h.get("event") == "close" else (
                "worked" if h.get("event") in ("claim", "progress") else "touched")
            _add_session(by.get("session_id"), by.get("machine"), role)
    if resolution.get("summary"):
        parts.append(_clean(resolution.get("summary")))
    _add_session(resolution.get("session_id"), resolution.get("machine"), "closed")
    _add_session(item.get("claimed_session_id"), item.get("claimed_machine"), "worked")
    # The closing session is the one most worth opening: put it first.
    sessions.sort(key=lambda s: order.get(s["role"], 3))
    body = "\n".join(parts)[:_BODY_CAP]
    return {
        "ref": str(item.get("ref") or ""),
        "title": _clean(item.get("title")) or (note.splitlines()[0][:160] if note else ""),
        "body": body,
        "project": str(item.get("project") or ""),
        "status": str(item.get("status") or ""),
        "created": str(item.get("created_at") or ""),
        "closed": str(item.get("closed_at") or ""),
        "summary": _clean(resolution.get("summary"))[:400],
        "sessions": sessions[:8],
    }


def _source_signature(src: sqlite3.Connection) -> str:
    n, mx = src.execute("SELECT count(*), max(updated_ts) FROM items").fetchone()
    return f"{n}:{mx}"


def sync(force: bool = False) -> bool:
    """Bring the index up to date with the WatchTower store. True if it ran."""
    now = time.time()
    if not force and now - _last_sync[0] < _SYNC_MIN_INTERVAL_S:
        return False
    if not _lock.acquire(blocking=False):
        return False
    try:
        _last_sync[0] = now
        wt = _get_wt_db_path()
        if not wt.exists():
            return False
        src = sqlite3.connect(wt.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        try:
            sig = _source_signature(src)
            conn = _connect()
            try:
                return _apply(src, conn, sig, force)
            finally:
                conn.close()
        finally:
            src.close()
    except sqlite3.Error:
        return False
    finally:
        _lock.release()


def _apply(src: sqlite3.Connection, conn: sqlite3.Connection, sig: str, force: bool) -> bool:
    row = conn.execute("SELECT v FROM tstate WHERE k='sig'").fetchone()
    if row and row[0] == sig and not force:
        return True
    have = dict(conn.execute("SELECT number, updated_ts FROM tmeta"))
    current = dict(src.execute("SELECT number, coalesce(updated_ts, 0) FROM items"))
    changed = [n for n, ts in current.items() if have.get(n) != ts]
    gone = [n for n in have if n not in current]
    with conn:
        for n in gone:
            _delete(conn, n)
        for start in range(0, len(changed), 500):
            chunk = changed[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for number, raw, ts in src.execute(
                    f"SELECT number, item_json, coalesce(updated_ts, 0) FROM items "
                    f"WHERE number IN ({marks})", chunk):
                try:
                    item = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                _upsert(conn, number, ts, ticket_doc(item))
        conn.execute("INSERT OR REPLACE INTO tstate (k, v) VALUES ('sig', ?)", (sig,))
    return True


def _delete(conn: sqlite3.Connection, number: int) -> None:
    row = conn.execute("SELECT rid FROM tmeta WHERE number=?", (number,)).fetchone()
    if row:
        conn.execute("DELETE FROM tdoc WHERE rowid=?", (row[0],))
    conn.execute("DELETE FROM tmeta WHERE number=?", (number,))


def _upsert(conn: sqlite3.Connection, number: int, ts: float, doc: dict) -> None:
    _delete(conn, number)
    cur = conn.execute("INSERT INTO tdoc (ref, title, body) VALUES (?, ?, ?)",
                       (doc["ref"], doc["title"], doc["body"]))
    conn.execute(
        "INSERT INTO tmeta (number, rid, ref, project, status, created, closed, updated_ts, sessions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (number, cur.lastrowid, doc["ref"], doc["project"], doc["status"], doc["created"],
         doc["closed"], ts, json.dumps({"sessions": doc["sessions"], "summary": doc["summary"]})))


def _built() -> bool:
    try:
        conn = _connect()
        try:
            return conn.execute("SELECT 1 FROM tstate WHERE k='sig'").fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _match_expr(query: str) -> str:
    from ccc_server import session_fts as _sfts
    return _sfts._match_expr(query)


def search(query: str, limit: int = 5) -> list[dict]:
    """Best-matching tickets for `query`, each with the sessions that worked it."""
    query = (query or "").strip()
    if not query:
        return []
    if not _built():
        # The first build parses every ticket (~8 s for ~5k): never in a request.
        threading.Thread(target=sync, kwargs={"force": True}, daemon=True,
                         name="ticket-fts-build").start()
        return []
    sync()
    match = _match_expr(query)
    if not match:
        return []
    try:
        conn = _connect()
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT m.ref, t.title, m.project, m.status, m.created, m.closed, m.sessions, "
            "snippet(tdoc, 2, '', '', '…', 18) "
            "FROM tdoc t JOIN tmeta m ON m.rid = t.rowid "
            "WHERE tdoc MATCH ? ORDER BY bm25(tdoc, 10.0, 4.0, 1.0) LIMIT ?",
            (match, max(1, min(int(limit), 20)))).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out = []
    for ref, title, project, status, created, closed, extra, snip in rows:
        try:
            extra = json.loads(extra)
        except (TypeError, ValueError):
            extra = {}
        out.append({
            "ref": ref, "title": title, "project": project, "status": status,
            "date": (closed or created)[:10],
            "snippet": " ".join(str(snip or "").split())[:300],
            "summary": extra.get("summary", ""),
            "sessions": extra.get("sessions") or [],
        })
    return out
