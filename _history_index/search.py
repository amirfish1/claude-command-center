"""Full-text search over the indexed Claude Code conversations.

Two retrieval modes:
- Keyword (default): BM25 over FTS5 with auto OR-rewrite for bare multi-word queries.
- Hybrid: top-K BM25 ∪ top-K vector, fused via Reciprocal Rank Fusion. Requires
  sqlite-vec + Ollama; activate by passing semantic=True (search()) or via the
  --semantic CLI flag / `semantic: true` MCP arg.
"""
from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime
from typing import Any, Optional


# What counts as "user composed an FTS5 query, leave it alone": quoted phrases,
# explicit boolean keywords, parens, prefix-star. NOT '-', '+', '^', ':' on
# their own — those routinely show up in identifiers / filenames the user
# wants to search literally (e.g. `archive-filter-1d33`, `feat/foo-bar`,
# `user@example.com`).
_HAS_OPERATOR = re.compile(r'["()*]|\b(?:AND|OR|NOT|NEAR)\b', re.IGNORECASE)
_TOKENIZER = re.compile(r"[\w']+", re.UNICODE)


def rewrite_query(query: str) -> str:
    """Rewrite bare multi-word queries to OR-form so a single missing word
    doesn't zero out the result set. BM25 still ranks docs containing more
    of the terms higher, so this gives recall without sacrificing precision.

    Tokens are extracted (punctuation discarded), then each is quoted so
    FTS5 treats it as a literal phrase — this prevents stray '-' / ':' /
    '+' inside identifiers from being parsed as column-filter / negation
    operators.

    Queries that already use real FTS5 operators (quotes, OR, NEAR, prefix*,
    parens) are passed through unchanged — caller knows what they want.
    """
    q = query.strip()
    if not q or _HAS_OPERATOR.search(q):
        return q
    tokens = _TOKENIZER.findall(q)
    if not tokens:
        return q
    if len(tokens) == 1:
        # Single token: don't quote — preserves prefix-search behavior if the
        # user later types `*`, and reads cleaner in debug output.
        return tokens[0]
    return " OR ".join(f'"{t}"' for t in tokens)


def parse_since(since: Optional[str]) -> Optional[float]:
    """Parse '7d', '24h', '30m', '2w', or ISO date into a unix timestamp threshold."""
    if not since:
        return None
    s = since.strip().lower()
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
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _build_filters(
    cwd_like: Optional[str],
    project_like: Optional[str],
    since: Optional[str],
    role: Optional[str],
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if cwd_like:
        where.append("m.cwd LIKE ?")
        params.append(f"%{cwd_like}%")
    if project_like:
        where.append("m.project_dir LIKE ?")
        params.append(f"%{project_like}%")
    if role:
        where.append("m.type = ?")
        params.append(role)
    since_unix = parse_since(since)
    if since_unix is not None:
        where.append("m.ts_unix >= ?")
        params.append(since_unix)
    return where, params


def _title_row_hits(
    conn: sqlite3.Connection,
    fts_query: str,
    filter_where: list[str],
    filter_params: list[Any],
    cap: int = 50,
) -> list[sqlite3.Row]:
    """Rows that are the FIRST user message of their session (the display-title
    proxy) and match the FTS query.

    A session whose title is about the topic belongs on the first page even
    when hundreds of other sessions merely mention it in passing — the
    bare-content BM25 rank buries exactly those (CCC-615: a session titled
    'fix the twilio campaign' ranked #340 for query 'twilio').
    """
    where = ["messages_fts MATCH ?"] + filter_where
    params = [fts_query] + filter_params
    sql = f"""
        SELECT
            m.id, m.uuid, m.session_id, m.type, m.cwd, m.project_dir, m.git_branch,
            m.timestamp, m.ts_unix, m.slug, m.source_file, m.source_line,
            snippet(messages_fts, 0, '«', '»', '…', 12) AS snippet,
            bm25(messages_fts) AS score
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE {' AND '.join(where)}
          AND m.id IN (
              SELECT id FROM (
                  SELECT id, MIN(ts_unix) FROM messages
                  WHERE type = 'user' AND LTRIM(content) NOT LIKE '<%'
                  GROUP BY session_id
              )
          )
        ORDER BY score
        LIMIT ?
    """
    params.append(cap)
    return list(conn.execute(sql, params))


def _bm25_hits(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    raw: bool,
    cwd_like: Optional[str],
    project_like: Optional[str],
    since: Optional[str],
    role: Optional[str],
) -> list[sqlite3.Row]:
    fts_query = query if raw else rewrite_query(query)
    filter_where, filter_params = _build_filters(cwd_like, project_like, since, role)
    where = ["messages_fts MATCH ?"] + filter_where
    params = [fts_query] + filter_params
    sql = f"""
        SELECT
            m.id, m.uuid, m.session_id, m.type, m.cwd, m.project_dir, m.git_branch,
            m.timestamp, m.ts_unix, m.slug, m.source_file, m.source_line,
            snippet(messages_fts, 0, '«', '»', '…', 12) AS snippet,
            bm25(messages_fts) AS score
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE {' AND '.join(where)}
        ORDER BY score
        LIMIT ?
    """
    rows = list(conn.execute(sql, [*params, limit]))
    # Title boost: sessions whose first user message matches are ABOUT the
    # topic — promote them (ranked by score among themselves) above the
    # incidental content hits, preserving the content hits after.
    title_rows = _title_row_hits(conn, fts_query, filter_where, filter_params)
    if title_rows:
        title_ids = {r["id"] for r in title_rows}
        merged = {r["id"]: r for r in rows}
        for r in title_rows:
            merged[r["id"]] = r
        rows = sorted(
            merged.values(),
            key=lambda r: (0 if r["id"] in title_ids else 1, r["score"]),
        )[:limit]
    return rows


def _vec_hits(
    conn: sqlite3.Connection,
    query_embedding: bytes,
    limit: int,
    cwd_like: Optional[str],
    project_like: Optional[str],
    since: Optional[str],
    role: Optional[str],
    candidate_pool: int = 500,
) -> list[sqlite3.Row]:
    """Top-N nearest-neighbor messages by cosine, with metadata filters applied.

    candidate_pool controls how many vec hits are pulled before metadata filtering;
    set high enough that filters don't starve the result set.
    """
    where, params = _build_filters(cwd_like, project_like, since, role)
    where_sql = (" AND " + " AND ".join(where)) if where else ""
    sql = f"""
        WITH vec_hits AS (
            SELECT rowid AS id, distance
            FROM messages_vec
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
        )
        SELECT
            m.id, m.uuid, m.session_id, m.type, m.cwd, m.project_dir, m.git_branch,
            m.timestamp, m.ts_unix, m.slug, m.source_file, m.source_line,
            substr(m.content, 1, 240) AS snippet,
            v.distance AS score
        FROM vec_hits v
        JOIN messages m ON m.id = v.id
        WHERE 1=1{where_sql}
        ORDER BY v.distance
        LIMIT ?
    """
    return list(conn.execute(sql, [query_embedding, candidate_pool, *params, limit]))


def _rrf_fuse(
    bm25_rows: list[sqlite3.Row],
    vec_rows: list[sqlite3.Row],
    limit: int,
    k_const: int = 60,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion. Returns merged hits with combined score.

    Each result gets a `_source` tag: 'bm25', 'vec', or 'fused' (matched both
    paths). Callers use this to decide presentation — e.g. CCC shows a
    'semantic' badge for vec/fused hits versus a plain 'history' badge
    for bm25-only ones.
    """
    scores: dict[int, float] = {}
    payload: dict[int, dict[str, Any]] = {}
    bm25_ids: set[int] = set()
    vec_ids: set[int] = set()

    for rank, row in enumerate(bm25_rows, start=1):
        mid = row["id"]
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (k_const + rank)
        payload[mid] = dict(row)
        payload[mid]["bm25_rank"] = rank
        bm25_ids.add(mid)
    for rank, row in enumerate(vec_rows, start=1):
        mid = row["id"]
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (k_const + rank)
        # Prefer FTS snippet (with highlights) when both sources hit
        if mid not in payload:
            payload[mid] = dict(row)
        payload[mid]["vec_rank"] = rank
        vec_ids.add(mid)

    fused = []
    for mid, score in sorted(scores.items(), key=lambda kv: -kv[1])[:limit]:
        d = payload[mid]
        d["score"] = score
        if mid in bm25_ids and mid in vec_ids:
            d["_source"] = "fused"
        elif mid in vec_ids:
            d["_source"] = "vec"
        else:
            d["_source"] = "bm25"
        fused.append(d)
    return fused


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    cwd_like: Optional[str] = None,
    project_like: Optional[str] = None,
    since: Optional[str] = None,
    role: Optional[str] = None,
    raw: bool = False,
    semantic: bool = False,
    candidate_pool: int = 100,
):
    """Run a search and return rows ranked best-first.

    semantic=False: BM25 over FTS5 only (auto OR-rewrite unless raw=True).
    semantic=True:  hybrid retrieval — top-K BM25 ∪ top-K vec, fused via RRF.
                    Requires sqlite-vec extension loaded and embeddings backfilled.
    """
    def _bm25_only():
        rows = _bm25_hits(conn, query, limit, raw, cwd_like, project_like, since, role)
        # Tag for consistent shape — callers (e.g. CCC) decide rendering off `_source`.
        out = []
        for row in rows:
            d = dict(row)
            d["_source"] = "bm25"
            out.append(d)
        return out

    if not semantic:
        return _bm25_only()

    # Hybrid path: need embeddings + vec extension. Fall back gracefully if absent.
    from . import config as config_mod
    from . import db as db_mod
    from . import embed as embed_mod

    if not db_mod.has_vec_extension(conn):
        return _bm25_only()

    cfg = config_mod.load()
    try:
        q_emb = embed_mod.embed_batch(
            [embed_mod.truncate_for_embedding(query, cfg["max_embed_chars"])],
            model=cfg["embedding_model"],
            url=cfg["ollama_url"],
        )[0]
    except embed_mod.OllamaUnavailable:
        # Ollama down — degrade to keyword search rather than erroring.
        return _bm25_only()

    q_blob = embed_mod.to_blob(q_emb)

    bm25 = _bm25_hits(
        conn, query, candidate_pool, raw, cwd_like, project_like, since, role
    )
    vec = _vec_hits(
        conn, q_blob, candidate_pool, cwd_like, project_like, since, role
    )
    return _rrf_fuse(bm25, vec, limit)
