# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Recent-session content search across every agent harness.

Powers the sidebar conversation search (/api/search-recall-sessions) and the
Ask tab's recency channel (ccc_server/ask.py). Originally an in-process byte
scan of transcript files modified within the last N days -- itself a
replacement for an even older subprocess-per-keystroke design (see the
MEMO-FIX-15 history in git blame). MEMO-FIX-19 retired that scan in favor of
ccc_server.session_fts, which by then already indexed every harness this
module covered (Claude Code, Codex, Kimi Code, Gemini, Cursor) with real
BM25 + local-embeddings ranking -- running a second from-scratch scan over
the same files was redundant work with strictly worse ranking. This module
is now a thin day-window wrapper: it just clamps `days`/`limit` and hands off
to session_fts.search_sessions_enriched(), keeping the historical result
shape (session_id, cwd, ts_unix, snippet, _source='recall', ...) so callers
(the endpoint handler in server.py, ask.py) don't need to change.
"""

from __future__ import annotations

import time

from ccc_server import session_fts as _sfts

_DEFAULT_DAYS = 2.0
_MAX_DAYS = 30.0


def search_recent_sessions(query, days=_DEFAULT_DAYS, limit=20, cwd_like=None, match_ids=False):
    """Search recent sessions across all harnesses for session-level hits.

    `days` bounds the recency window (default 2 -- the "last day or two" case
    the sidebar search is for); results come back ranked by session_fts's
    BM25 + local-embeddings retrieval, not purely by recency.

    `match_ids` puts sessions whose id contains the query first, at any age.
    Sidebar only: a hex-looking word must not pull random sessions into Ask.
    """
    q = (query or "").strip()
    if not q:
        return {"results": []}
    try:
        days = float(days)
    except (TypeError, ValueError):
        days = _DEFAULT_DAYS
    days = max(0.25, min(days, _MAX_DAYS))
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 20

    since_ts = time.time() - days * 86400
    results = _sfts.search_sessions_enriched(
        q, limit=limit, cwd_like=cwd_like, since_ts=since_ts, source="recall",
    )
    if match_ids:
        id_hits = _sfts.session_id_hits(q, limit=min(limit, 20))
        seen = {hit["session_id"] for hit in id_hits}
        results = (id_hits + [r for r in results if r.get("session_id") not in seen])[:limit]
    return {"results": results}
