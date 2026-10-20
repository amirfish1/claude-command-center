# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Index of owner-question tags `[Q<n>:<slug>]` across agent transcripts.

Agents tag every question to the owner as `[Q33:gap-month]`; the FIRST
assistant message carrying a tag holds the full explanation and later
reminders repeat only the tag. This module finds that first message (plus
later mentions) so the UI can resolve a chip back to its explanation and list
questions that are still open.

A question is answered when the owner's reply mentions its tag, or an agent
writes `[Q<n>] answered`.

Cost model: no model calls. A transcript is only parsed when it changed
((mtime, size) differs from the persisted cache) and only when a cheap bytes
regex finds a tag in it; per-file results are cached on disk so the cold scan
of every transcript happens once.

Sources: Claude (~/.claude/projects), Codex (~/.codex/sessions rollouts), Kimi
(~/.kimi-code wire.jsonl), plus the SQLite stores of Hermes (state.db and
profile DBs) and Devin CLI (sessions.db). The DB stores are append-mostly and
huge, so they are read incrementally from a persisted row cursor and only
rows containing a tag marker are parsed.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_TAG_BYTES = re.compile(rb"\[Q\d+[:\]]")
_TAG = re.compile(r"\[Q(\d+):([a-z0-9-]+)\]")
_ANSWERED = re.compile(r"\[Q(\d+)\]\s*answered", re.IGNORECASE)
_BARE = re.compile(r"\[Q(\d+)(?::[a-z0-9-]+)?\]")
_TEXT_CAP = 12000
# The global agent instructions (and their examples like [Q33:gap-month]) get
# injected into transcripts as user context; never treat that as a real tag.
_INJECTED = "Owner question tags (all engines)"
_DB_CHUNK = 20000
_DEVIN_COLD_ROWS = 100000

_lock = threading.Lock()
_files: dict[str, dict] = {}  # path -> {"m": mtime, "s": size, "ev": [...]}
_loaded = False
_scanning = False
_ready = False  # a full index exists (loaded from disk or first scan done)
_last_scan = 0.0
_SCAN_MIN_GAP = 20.0


def _state_path() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "command-center" / "question-tags-cache.json"


def _projects_root() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def _load_cache() -> None:
    global _loaded, _ready
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(_state_path().read_text())
        if isinstance(data, dict) and data.get("v") == 2:
            _files.update(data.get("files") or {})
            _ready = bool(_files)
    except (OSError, ValueError):
        pass


def _save_cache() -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".qtags-")
        with os.fdopen(fd, "w") as f:
            json.dump({"v": 2, "files": _files}, f)
        os.replace(tmp, p)
    except OSError:
        pass


def _blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
            parts.append(b["text"])
    return "\n".join(parts)


def _iso(ts) -> str:
    """Normalise epoch seconds/ms or an ISO string to a sortable ISO string."""
    if isinstance(ts, (int, float)):
        if ts > 1e11:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        except (OverflowError, OSError, ValueError):
            return ""
    return ts or ""


def _event(role, text, uuid, ts, sid):
    """One tag event, or None when the text carries no tag / is injected rules."""
    if not text or not _BARE.search(text) or _INJECTED in text:
        return None
    return {"role": role, "uuid": uuid or "", "ts": _iso(ts), "sid": sid, "text": text[:_TEXT_CAP]}


def scan_file(path: str) -> list[dict]:
    """Tag events in one Claude transcript: assistant/user text that mentions a tag."""
    return scan_file_from(path, 0)[0]


def scan_file_from(path: str, offset: int) -> tuple[list[dict], int]:
    """Like scan_file but resumes at byte `offset`; returns (events, next offset).
    Only complete lines are consumed, so a half-written tail is re-read next time.
    Live transcripts are append-only and huge: re-reading them whole on every
    change is what made the index slow."""
    sid = Path(path).stem
    events = []
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            for raw in f:
                partial = not raw.endswith(b"\n")
                if not _TAG_BYTES.search(raw):
                    if partial:
                        break
                    offset += len(raw)
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    if partial:
                        break
                    offset += len(raw)
                    continue
                offset += len(raw)
                role = obj.get("type")
                if role not in ("assistant", "user") or obj.get("isSidechain"):
                    continue
                text = _blocks_text((obj.get("message") or {}).get("content"))
                e = _event(role, text, obj.get("uuid"), obj.get("timestamp"), obj.get("sessionId") or sid)
                if e:
                    events.append(e)
    except OSError:
        return [], offset
    return events, offset


def scan_codex_file(path: str) -> list[dict]:
    """Codex rollout: response_item messages (event_msg duplicates are skipped)."""
    sid = Path(path).stem[-36:]
    events = []
    try:
        with open(path, "rb") as f:
            for raw in f:
                if not _TAG_BYTES.search(raw):
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                pl = obj.get("payload") or {}
                if obj.get("type") != "response_item" or pl.get("type") != "message":
                    continue
                role = pl.get("role")
                if role not in ("assistant", "user"):
                    continue
                parts = [b.get("text") for b in pl.get("content") or []
                         if isinstance(b, dict) and isinstance(b.get("text"), str)]
                e = _event(role, "\n".join(parts), pl.get("id"), obj.get("timestamp"), sid)
                if e:
                    events.append(e)
    except OSError:
        return []
    return events


def scan_kimi_file(path: str) -> list[dict]:
    """Kimi wire.jsonl: user context messages and assistant message events."""
    sid = Path(path).parents[2].name
    events = []
    try:
        with open(path, "rb") as f:
            for n, raw in enumerate(f):
                if not _TAG_BYTES.search(raw):
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                kind = obj.get("type")
                msg = obj.get("message") or {}
                if kind == "context.append_message":
                    role = msg.get("role")
                elif kind == "agent.message.appended":
                    msg = msg.get("message") or {}
                    role = msg.get("role")
                else:
                    continue
                if role not in ("assistant", "user"):
                    continue
                parts = [b.get("text") for b in msg.get("content") or []
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
                t = obj.get("time")
                e = _event(role, "\n".join(parts), msg.get("message_id") or f"{sid}:{n}", t, sid)
                if e:
                    events.append(e)
    except OSError:
        return []
    return events


def _db_rows(path: str, sql: str, args: tuple) -> list:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


def scan_hermes_db(path: str, cursor: int = 0) -> tuple[list[dict], int]:
    """New tagged messages (id > cursor) in a Hermes state.db -> (events, cursor)."""
    top = _db_rows(path, "SELECT COALESCE(MAX(id),0) FROM messages", ())
    if not top:
        return [], cursor
    hi = int(top[0][0])
    events = []
    for sid, role, content, ts, mid in _db_rows(
        path,
        "SELECT session_id, role, content, timestamp, id FROM messages "
        "WHERE id > ? AND id <= ? AND role IN ('user','assistant') AND content LIKE '%[Q%'",
        (cursor, hi),
    ):
        e = _event(role, content if isinstance(content, str) else "", f"h{mid}", ts, sid)
        if e:
            events.append(e)
    return events, hi


def scan_devin_db(path: str, cursor: int = 0) -> tuple[list[dict], int]:
    """New tagged messages in a Devin CLI sessions.db. The first scan only
    looks at the most recent _DEVIN_COLD_ROWS rows (the DB runs to GBs)."""
    top = _db_rows(path, "SELECT COALESCE(MAX(row_id),0) FROM message_nodes", ())
    if not top:
        return [], cursor
    hi = int(top[0][0])
    if not cursor:
        cursor = max(0, hi - _DEVIN_COLD_ROWS)
    events = []
    lo = cursor
    while lo < hi:
        up = min(hi, lo + _DB_CHUNK)
        for sid, raw, ts, rid in _db_rows(
            path,
            "SELECT session_id, chat_message, created_at, row_id FROM message_nodes "
            "WHERE row_id > ? AND row_id <= ? AND chat_message LIKE '%[Q%'",
            (lo, up),
        ):
            try:
                m = json.loads(raw)
            except (TypeError, ValueError):
                continue
            role = m.get("role")
            if role == "user" and (m.get("metadata") or {}).get("is_user_input") != 1:
                continue
            if role not in ("assistant", "user"):
                continue
            c = m.get("content")
            text = c if isinstance(c, str) else _blocks_text(c)
            e = _event(role, text, m.get("message_id") or f"d{rid}", ts, sid)
            if e:
                events.append(e)
        lo = up
    return events, hi


def _sources() -> list[tuple[str, str, str]]:
    """(cache key, kind, path) for every transcript store that exists."""
    home = Path(os.path.expanduser("~"))
    out = []

    def glob(root: Path, pattern: str, kind: str):
        try:
            for fp in root.glob(pattern):
                out.append((f"{kind}:{fp}", kind, str(fp)))
        except OSError:
            pass

    glob(_projects_root(), "*/*.jsonl", "claude")
    glob(home / ".codex" / "sessions", "*/*/*/rollout-*.jsonl", "codex")
    glob(home / ".kimi-code" / "sessions", "*/*/agents/*/wire.jsonl", "kimi")
    for fp in [home / ".hermes" / "state.db", *sorted((home / ".hermes" / "profiles").glob("*/state.db"))]:
        out.append((f"hermes:{fp}", "hermes", str(fp)))
    for sub in ("cli", "cli-next"):
        out.append((f"devin:{sub}", "devin", str(home / ".local" / "share" / "devin" / sub / "sessions.db")))
    return out


def _stat(kind: str, path: str):
    """(mtime, size); SQLite stores fold in the -wal file, where writes land first."""
    st = os.stat(path)
    if kind in ("hermes", "devin"):
        try:
            w = os.stat(path + "-wal")
            return max(st.st_mtime, w.st_mtime), st.st_size + w.st_size
        except OSError:
            pass
    return st.st_mtime, st.st_size


def refresh(max_age: float = _SCAN_MIN_GAP, force: bool = False) -> bool:
    """Re-scan changed transcripts. Returns False if a scan is already running
    or ran within `max_age` seconds."""
    global _scanning, _last_scan, _ready
    with _lock:
        _load_cache()
        if _scanning or (not force and time.time() - _last_scan < max_age):
            return False
        _scanning = True
    try:
        seen = set()
        dirty = False
        for key, kind, path in _sources():
            seen.add(key)
            try:
                m, sz = _stat(kind, path)
            except OSError:
                continue
            rec = _files.get(key)
            if rec and rec["m"] == m and rec["s"] == sz:
                continue
            if kind in ("hermes", "devin"):
                scan = scan_hermes_db if kind == "hermes" else scan_devin_db
                ev, cur = scan(path, (rec or {}).get("cur", 0))
                _files[key] = {"m": m, "s": sz, "cur": cur, "ev": ((rec or {}).get("ev") or []) + ev}
            elif kind == "claude":
                # Resume from the saved offset when the file only grew; caches
                # written before offsets existed (no "o") rescan from the top.
                grew = bool(rec) and "o" in rec and sz >= rec["s"]
                ev, off = scan_file_from(path, rec["o"] if grew else 0)
                _files[key] = {"m": m, "s": sz, "o": off, "ev": (rec["ev"] if grew else []) + ev}
            else:
                scan = {"codex": scan_codex_file, "kimi": scan_kimi_file}[kind]
                _files[key] = {"m": m, "s": sz, "ev": scan(path)}
            dirty = True
        for key in [k for k in _files if k not in seen]:
            del _files[key]
            dirty = True
        if dirty:
            _save_cache()
        return True
    finally:
        with _lock:
            _scanning = False
            _ready = True
            _last_scan = time.time()


def build_index(files: dict | None = None) -> dict:
    """Pure: fold per-file events into {"questions": [...]} ordered by n."""
    events = []
    for rec in (files if files is not None else _files).values():
        events.extend(rec.get("ev") or [])
    events.sort(key=lambda e: e.get("ts") or "")
    qs: dict[int, dict] = {}
    for e in events:
        text = e["text"]
        if e["role"] == "assistant":
            for m in _TAG.finditer(text):
                n = int(m.group(1))
                q = qs.get(n)
                if q is None:
                    qs[n] = q = {
                        "n": n, "tag": m.group(0), "slug": m.group(2),
                        "first": {"sid": e["sid"], "ts": e["ts"], "uuid": e["uuid"], "text": text},
                        "mentions": [], "answered": False, "answered_by": "",
                    }
                elif e["uuid"] != q["first"]["uuid"] and len(q["mentions"]) < 50:
                    q["mentions"].append({"sid": e["sid"], "ts": e["ts"], "uuid": e["uuid"]})
            for m in _ANSWERED.finditer(text):
                q = qs.get(int(m.group(1)))
                if q and not q["answered"]:
                    q["answered"], q["answered_by"] = True, "agent"
        else:
            for m in _BARE.finditer(text):
                q = qs.get(int(m.group(1)))
                if q and not q["answered"] and e["ts"] >= q["first"]["ts"]:
                    q["answered"], q["answered_by"] = True, "owner"
    return {"questions": [qs[n] for n in sorted(qs)]}


def payload() -> dict:
    """API payload. Never scans inside the request: a stale index kicks a
    background refresh and is served as-is. `scanning` is true only until the
    first full index exists (cold start); `refreshing` means a rescan is in
    flight, so a tag the UI cannot resolve yet may appear shortly."""
    with _lock:
        _load_cache()
        stale = time.time() - _last_scan >= _SCAN_MIN_GAP and not _scanning
        scanning_now = _scanning
    if stale:
        threading.Thread(target=refresh, daemon=True, name="qtags-scan").start()
        scanning_now = True
    out = build_index()
    out["ok"] = True
    out["scanning"] = not _ready
    out["refreshing"] = scanning_now
    return out
