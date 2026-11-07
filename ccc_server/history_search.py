# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 58150-58539).

Part of the server.py decomposition; see
the private plans repo (server-py-decomposition). Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import uuid

from ccc_server import core as _core

# Lazy-imported inside search_conversation_history: session_fts imports
# extract_history_terms etc. from this module at its own load time, so a
# top-level import here would be circular (whichever module loads first
# would force-load the other mid-init).

# ---------------------------------------------------------------------------
# Conversation history search — read-only window onto the separate `claude-index`
# tool. CCC drives the indexer in-process via the bundled _history_index
# package; the on-disk file lives at ~/.claude-index/index.db so a parallel
# standalone claude-index install can coexist on the same data.
# ---------------------------------------------------------------------------

# Vendored indexer (see _history_index/). Lazy-imported per-call where
# semantic search is requested so a fresh CCC install without
# sqlite-vec / Ollama still loads the rest of the server cleanly.
try:
    from _history_index import db as _hi_db
    from _history_index.manager import indexer as _hi_indexer
    _HI_AVAILABLE = True
except Exception:
    _HI_AVAILABLE = False
    _hi_db = None  # type: ignore
    _hi_indexer = None  # type: ignore

# _HISTORY_INDEX_PATH / _history_conn / _history_conn_lock live in server.py
# (tests patch them via the server module); reached through _core below.
# Used only by get_history_message + the /api/history/status and
# /api/history/setup admin endpoints now -- /api/search-history moved to
# session_fts, which self-freshens by (mtime, size) on every call.

# A single sqlite3.Connection cannot be used concurrently from multiple
# threads. check_same_thread=False only silences Python's guard — it does NOT
# serialise access, and overlapping .execute() on one shared handle raises
# SQLITE_MISUSE ("bad parameter or other API misuse"). The server runs behind
# ThreadingHTTPServer (a thread per request), so every *use* of the shared
# read-only connection is serialised through this lock. History search is
# user-triggered and low-frequency, so the serialisation cost is negligible and
# we keep the benefit of one cached connection (+ a single sqlite-vec load).
_history_query_lock = threading.Lock()

# What counts as "user composed an FTS5 query, leave it alone": quoted
# phrases, explicit boolean keywords, parens, prefix-star. NOT '-', '+',
# '^', ':' on their own — those routinely show up in identifiers /
# filenames the user wants to search literally (e.g. `archive-filter-1d33`,
# `feat/foo-bar`, `user@example.com`).
_HISTORY_FTS_OPERATOR_RE = re.compile(r'\b(?:AND|OR|NOT|NEAR)\b')
_HISTORY_FTS_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Question scaffolding and English function words.
_HISTORY_STOPWORDS = frozenset(
    "a about above after again all also am an and any are as at be because been "
    "before being below between both but by can could did do does doing done down "
    "during each few for from further had has have having he her here hers him his "
    "how i if in into is it its itself just know let like me more most my no nor "
    "not now of off on once only or other our out over own same she should so some "
    "such than that the their them then there these they this those through to too "
    "under until up us very was we were what when where which while who whom why "
    "will with would you your yours "
    "decide decided decision decisions discuss discussed talk talked work worked "
    "working session sessions status thing things stuff way ways find found "
    "remember recall earlier ago last previous previously please tell show "
    "didn doesn don wasn weren haven hasn hadn won wouldn shouldn couldn "
    "s t d m re ll ve".split()
)


def _is_explicit_history_fts_query(query: str) -> bool:
    q = (query or "").strip()
    if not q or "?" in q:
        return False
    if q.startswith('"') and q.endswith('"') and q.count('"') == 2:
        return True
    if _HISTORY_FTS_OPERATOR_RE.search(q):
        return True
    if re.search(r'\b\w+\*', q):
        return True
    return False


def extract_history_terms(query: str, max_terms: int = 8) -> list[str]:
    """Topic words of a natural query with stopwords removed and punctuation stripped."""
    tokens = _HISTORY_FTS_TOKEN_RE.findall(query or "")
    seen: set[str] = set()
    terms: list[str] = []
    for t in tokens:
        tl = t.lower()
        if tl in _HISTORY_STOPWORDS or tl in seen:
            continue
        seen.add(tl)
        terms.append(t)
    if not terms:
        for t in tokens:
            tl = t.lower()
            if tl not in seen:
                seen.add(tl)
                terms.append(t)
    return terms[:max_terms]


def _rewrite_history_query(q, mode="and"):
    """Rewrite bare queries to an FTS5 expression over topic terms.
    Tokens are quoted so embedded punctuation (e.g. '-' inside an identifier)
    can't be mis-parsed as an operator.
    """
    q = (q or "").strip()
    if not q or _is_explicit_history_fts_query(q):
        return q
    terms = extract_history_terms(q)
    if not terms:
        return q
    if len(terms) == 1:
        return f'"{terms[0]}"'
    joiner = " OR " if mode == "or" else " AND "
    return joiner.join(f'"{t}"' for t in terms)


# Patterns that crowd out useful preview text in FTS5 snippets. The cleaner
# below strips them before the snippet hits the UI.
#  - `[tool_use:NAME]` markers introduced by the indexer for assistant tool calls
#  - line-number prefixes from Read-tool / cat -n output: `1031\t...` and `1049- ...`
#  - markdown-table separator rows (`| --- | --- |`) that dominate changelog hits
_HISTORY_SNIPPET_TOOL_USE_RE = re.compile(r'\[tool_use:[^\]]+\]\s*')
_HISTORY_SNIPPET_LINENUM_RE = re.compile(r'\b\d{1,6}(?:\t|-(?=\s))')
_HISTORY_SNIPPET_TABLE_SEP_RE = re.compile(r'\|?\s*-{3,}(?:\s*\|\s*-{3,})+\s*\|?')
_HISTORY_SNIPPET_WS_RE = re.compile(r'[ \t]{2,}')


def _clean_history_snippet(snippet):
    """Strip noise from an FTS5-returned snippet so the preview shows real text
    instead of tool-call boilerplate or cat-n line numbers.

    `<mark>` highlight tags survive — none of the patterns we strip can contain
    them. If cleaning empties the snippet (rare: a result that was *only* noise),
    return the original so the UI still has something to show.
    """
    if not snippet:
        return snippet
    s = _HISTORY_SNIPPET_TOOL_USE_RE.sub('', snippet)
    s = _HISTORY_SNIPPET_LINENUM_RE.sub(' ', s)
    s = _HISTORY_SNIPPET_TABLE_SEP_RE.sub(' ', s)
    s = _HISTORY_SNIPPET_WS_RE.sub(' ', s)
    s = s.strip()
    return s if s else snippet


def _history_since_threshold(since):
    """Parse '7d', '24h', '30m', '2w' into a unix-timestamp threshold.
    Returns None for empty / 'all' / unparseable input — caller treats
    that as "no time filter".
    """
    if not since:
        return None
    s = since.strip().lower()
    if s in ("all", "any", "0"):
        return None
    now = time.time()
    try:
        if s.endswith("d"):
            return now - int(s[:-1]) * 86400
        if s.endswith("h"):
            return now - int(s[:-1]) * 3600
        if s.endswith("m"):
            return now - int(s[:-1]) * 60
        if s.endswith("w"):
            return now - int(s[:-1]) * 7 * 86400
    except ValueError:
        pass
    return None


def _since_to_ts(since):
    """Flexible `since` parser for search_conversation_history: accepts
    either a relative-window string ('7d', '24h', ...) from the sidebar, or
    an absolute unix timestamp (float/int, or a numeric string) from
    ask.py's `_ask_range_window`. The latter used to crash this function
    outright (`since.strip()` on a float) and get silently swallowed by
    ask.py's own try/except, so a bounded Ask-tab date range always searched
    zero history -- fixed as a side effect of unifying both callers onto one
    retrieval path."""
    if since is None:
        return None
    if isinstance(since, (int, float)):
        return float(since) if since > 0 else None
    s = str(since).strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        pass
    else:
        if v > 1_000_000:  # looks like an absolute unix timestamp already
            return v
    return _history_since_threshold(s)


def _open_history_index():
    """Return a cached read-only sqlite3.Connection to the claude-index
    store, or None if the index file doesn't exist yet (user never ran
    the indexer).

    `mode=ro` enforces read-only at the URI level so even a stray
    INSERT here would raise instead of silently mutating the file.
    `check_same_thread=False` lets worker threads share this one cached
    handle, but it does NOT make concurrent use safe — every actual query
    must hold `_history_query_lock` (see its definition above).
    """
    if _core._history_conn is not None:
        return _core._history_conn
    with _core._history_conn_lock:
        if _core._history_conn is not None:
            return _core._history_conn
        if not _core._HISTORY_INDEX_PATH.is_file():
            return None
        uri = f"file:{_core._HISTORY_INDEX_PATH}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        except sqlite3.OperationalError:
            return None
        conn.row_factory = sqlite3.Row
        # Best-effort load of sqlite-vec on this read-only handle so semantic
        # search can use it. Silent no-op if the extension isn't installed —
        # the read path then degrades to BM25 only, which is the correct
        # behavior for users without semantic set up.
        if _hi_db is not None:
            try:
                _hi_db._try_load_vec(conn)
            except Exception:
                pass
        _core._history_conn = conn
        return conn


def _history_drop_conn():
    """Close and forget the cached read connection so the next search
    reopens against a freshly created index file (used after re-ingest)."""
    with _core._history_conn_lock:
        if _core._history_conn is not None:
            # Hold the query lock too so we never close the handle out
            # from under an in-flight search on another worker thread.
            with _history_query_lock:
                try:
                    _core._history_conn.close()
                except Exception:
                    pass
                _core._history_conn = None


def search_conversation_history(query, limit=20, cwd_like=None, since=None, semantic=False):
    """Search conversation history across every indexed harness.

    MEMO-FIX-19: delegates to ccc_server.session_fts (BM25 + optional local-
    Ollama semantic fusion), the same index behind `ccc recall` and the
    sidebar's /api/search-recall-sessions. Retired the separate claude-index
    lexical/vendored-semantic path this used to run — that db only ever
    covered Claude Code + Codex, while session_fts also covers Kimi Code,
    Gemini CLI and Cursor.

    `since` accepts either a relative-window string ('7d', '24h', ...) or an
    absolute unix timestamp (ask.py sends the latter).

    Returns {results: [...]}; the historical {error: ...} shape is preserved
    only for a query FTS5 itself rejects (bad operator syntax) since the
    "index not found" case no longer applies -- session_fts builds itself.
    """
    from ccc_server import session_fts as _sfts

    q = (query or "").strip()
    if not q:
        return {"results": []}
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20

    since_ts = _since_to_ts(since)
    source = "semantic" if semantic and _sfts._ollama_available() else "bm25"
    try:
        results = _sfts.search_sessions_enriched(
            q, limit=limit, cwd_like=cwd_like, since_ts=since_ts, source=source,
        )
    except sqlite3.OperationalError as e:
        return {"error": f"search failed: {e}", "results": []}

    for r in results:
        if r.get("snippet"):
            r["snippet"] = _clean_history_snippet(r["snippet"])
    return {"results": results}


def get_history_message(uuid):
    """Fetch a single message by uuid from the conversation index, or
    None if not found. Used by the click-through panel."""
    if not uuid:
        return None
    conn = _open_history_index()
    if conn is None:
        return None
    try:
        with _history_query_lock:
            row = conn.execute(
                "SELECT uuid, session_id, type, role, cwd, project_dir, git_branch, "
                "timestamp, ts_unix, model, source_file, source_line, content "
                "FROM messages WHERE uuid = ?",
                (uuid,),
            ).fetchone()
    except (sqlite3.OperationalError, sqlite3.ProgrammingError):
        return None
    return dict(row) if row else None


