"""Extracted from server.py (originally lines 58150-58539).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import os
import re
import sqlite3
import threading
import time
import uuid

from ccc_server import core as _core

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
    from _history_index import search as _hi_search
    from _history_index.manager import indexer as _hi_indexer
    _HI_AVAILABLE = True
except Exception:
    _HI_AVAILABLE = False
    _hi_db = None  # type: ignore
    _hi_search = None  # type: ignore
    _hi_indexer = None  # type: ignore

# _HISTORY_INDEX_PATH / _history_conn / _history_conn_lock live in server.py
# (tests patch them via the server module); reached through _core below.

# Self-freshening search: each /api/search-history request kicks a throttled
# background incremental ingest so search content tracks live transcripts
# without any manual re-index. The gap keeps search-as-you-type from
# stampeding the indexer; the first search after server start always ingests.
try:
    _HISTORY_AUTO_INGEST_GAP_SEC = max(
        30.0,
        float(os.environ.get("CCC_HISTORY_AUTO_INGEST_SEC", "120")),
    )
except ValueError:
    _HISTORY_AUTO_INGEST_GAP_SEC = 120.0

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
_HISTORY_FTS_OPERATOR_RE = re.compile(r'["()*]|\b(?:AND|OR|NOT|NEAR)\b', re.IGNORECASE)
_HISTORY_FTS_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)


def _rewrite_history_query(q):
    """Bare multi-word queries → OR-form so a single missing word doesn't
    zero out FTS5's implicit-AND. Tokens are quoted so embedded punctuation
    (e.g. '-' inside an identifier) can't be mis-parsed as an operator.

    Mirrors rewrite_query() in claude-index — kept inline so CCC has no
    runtime dependency on that package.
    """
    q = (q or "").strip()
    if not q or _HISTORY_FTS_OPERATOR_RE.search(q):
        return q
    tokens = _HISTORY_FTS_TOKEN_RE.findall(q)
    if not tokens:
        return q
    if len(tokens) == 1:
        return tokens[0]
    return " OR ".join(f'"{t}"' for t in tokens)


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


def _rerank_history_results(results, query, conn):
    """Stable-rerank so exact-phrase matches rank above all-words (AND)
    matches, which rank above any-word (OR) / semantic-only matches.

    A bare multi-word query is OR-rewritten for recall (see
    _rewrite_history_query), and the semantic path adds pure-vector hits with
    no literal word at all. Both let a result that contains only one query
    word — or none — outrank one containing the whole phrase, which reads as
    "random results". This re-tiers the already-fetched top-N by literal
    presence in the message body, preserving the within-tier BM25/RRF order.

    No-op for single-word queries and for queries where the user supplied
    their own FTS operators or quotes — those already mean what was typed.
    """
    if not results:
        return results
    q = (query or "").strip()
    if not q or _HISTORY_FTS_OPERATOR_RE.search(q):
        return results
    tokens = [t.lower() for t in _HISTORY_FTS_TOKEN_RE.findall(q)]
    if len(tokens) < 2:
        return results
    phrase = " ".join(tokens)
    # Snippets are a 12-token window — too short to test for ALL words. Pull
    # the full body for the candidate uuids in one query and tier on that.
    uuids = [r.get("uuid") for r in results if r.get("uuid")]
    content_by_uuid = {}
    if uuids:
        placeholders = ",".join("?" for _ in uuids)
        try:
            with _history_query_lock:
                for row in conn.execute(
                    f"SELECT uuid, content FROM messages WHERE uuid IN ({placeholders})",
                    uuids,
                ):
                    content_by_uuid[row["uuid"]] = (row["content"] or "").lower()
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            return results

    def _tier(r):
        c = content_by_uuid.get(r.get("uuid"), "")
        if not c:
            return 2
        if phrase in c:
            return 0
        if all(t in c for t in tokens):
            return 1
        return 2

    return sorted(results, key=_tier)


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
    """Search the indexed conversation history.

    semantic=False (default): BM25 over FTS5 with auto OR-rewrite. Results
        get _source='bm25'.
    semantic=True: hybrid retrieval via the vendored _history_index.search —
        top-K BM25 ∪ top-K vec, fused via Reciprocal Rank Fusion. Each result
        is tagged _source ∈ {'bm25', 'vec', 'fused'} so the UI can
        differentiate ('history' vs 'semantic history' badge). Falls back
        to BM25 transparently if sqlite-vec or Ollama is unavailable.

    Returns a dict {results: [...]} on success, or
    {error: str, results: []} when the index is missing or a query is
    rejected by FTS5 (malformed operator usage, etc.).
    """
    conn = _open_history_index()
    if conn is None:
        return {
            "error": (
                "Conversation index not found at ~/.claude-index/index.db. "
                "Click the History toggle to build it."
            ),
            "results": [],
        }
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20

    # Semantic path — delegate to the vendored search, which handles RRF +
    # _source tagging + graceful BM25 fallback when vec/Ollama is missing.
    if semantic and _hi_search is not None:
        try:
            with _history_query_lock:
                rows = _hi_search.search(
                    conn,
                    query,
                    limit=limit,
                    cwd_like=cwd_like,
                    since=since,
                    semantic=True,
                )
        except (sqlite3.OperationalError, sqlite3.ProgrammingError) as e:
            return {"error": f"search failed: {e}", "results": []}
        results = []
        for row in rows:
            d = dict(row) if not isinstance(row, dict) else row
            # The vendored BM25 wraps highlights in «» (claude-index CLI
            # convention); CCC's UI expects <mark>. Translate.
            sn = d.get("snippet") or ""
            sn = sn.replace("«", "<mark>").replace("»", "</mark>")
            if sn:
                sn = _clean_history_snippet(sn)
            d["snippet"] = sn
            results.append(d)
        return {"results": _rerank_history_results(results, query, conn)}

    # Lexical path — CCC's existing BM25 SQL with the snippet cleaner.
    fts_query = _rewrite_history_query(query)
    if not fts_query:
        return {"results": []}
    where = ["messages_fts MATCH ?"]
    params = [fts_query]
    if cwd_like:
        where.append("m.cwd LIKE ?")
        params.append(f"%{cwd_like}%")
    threshold = _history_since_threshold(since)
    if threshold is not None:
        where.append("m.ts_unix >= ?")
        params.append(threshold)
    sql = f"""
        SELECT m.uuid, m.session_id, m.type, m.cwd, m.git_branch,
               m.timestamp, m.ts_unix,
               snippet(messages_fts, 0, '<mark>', '</mark>', '…', 12) AS snippet,
               bm25(messages_fts) AS score
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE {' AND '.join(where)}
        ORDER BY score
        LIMIT ?
    """
    params.append(limit)
    try:
        with _history_query_lock:
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        # FTS5 rejects malformed queries (unbalanced quotes, dangling
        # operators, etc.) with OperationalError. Surface as an empty
        # result + error string so the UI can show "syntax error" rather
        # than a 500.
        return {"error": f"search failed: {e}", "results": []}
    except sqlite3.ProgrammingError as e:
        # Connection closed under us (e.g. a concurrent index reset between
        # fetching the handle and acquiring the query lock). Degrade, don't 500.
        return {"error": f"search failed: {e}", "results": []}
    results = [dict(r) for r in rows]
    # Title boost (CCC-615): sessions whose first NON-synthetic user message
    # (the display-title proxy) matches are ABOUT the topic — promote them
    # above incidental content hits, same as the vendored semantic search.
    if _hi_search is not None:
        try:
            title_where, title_params = [], []
            if cwd_like:
                title_where.append("m.cwd LIKE ?")
                title_params.append(f"%{cwd_like}%")
            if threshold is not None:
                title_where.append("m.ts_unix >= ?")
                title_params.append(threshold)
            with _history_query_lock:
                title_rows = _hi_search._title_row_hits(
                    conn, fts_query, title_where, title_params)
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            title_rows = []
        if title_rows:
            title_by_uuid = {}
            for r in title_rows:
                d = dict(r)
                sn = (d.get("snippet") or "").replace("«", "<mark>").replace("»", "</mark>")
                if sn:
                    sn = _clean_history_snippet(sn)
                d["snippet"] = sn
                d["_source"] = "bm25"
                title_by_uuid[d["uuid"]] = d
            results = [r for r in results if r.get("uuid") not in title_by_uuid]
            results = (list(title_by_uuid.values()) + results)[:limit]
    for r in results:
        if r.get("snippet"):
            r["snippet"] = _clean_history_snippet(r["snippet"])
        r["_source"] = "bm25"
    return {"results": _rerank_history_results(results, query, conn)}


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


