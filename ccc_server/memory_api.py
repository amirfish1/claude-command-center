# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""API-layer glue for ccc_server.ship_graph (is_shipped, search_sessions).

This module is the thin read-only surface that powers:

  - GET /api/memory/recall?q=&limit=              -> recall()
  - GET /api/memory/shipped?topic=                -> shipped()
  - GET /api/memory/file-history?path=&repo=       -> file_history()
  - GET /api/memory/decisions?topic=              -> decisions()
  - `ccc recall "<query>"` / `ccc shipped "<topic>"` / `ccc history <path>` /
    `ccc decisions "<topic>"` (./ccc CLI)

No new retrieval logic lives here. search_sessions() only returns
session_id, so recall() enriches each hit with title/repo/date/snippet read
from two tables that ship_graph.search_sessions() has already synced in the
same call (ship_graph's own session_meta table for repo/date, session_fts's
sdoc table for title/snippet) — two batched SQL queries total, no per-row
file reads, no subprocess.

file_history() is the one function here that does spawn a subprocess: a
single live `git log --follow` for the one path being asked about (not a
per-row loop over anything), because the cached `commits` table has no
path filter and can't follow renames. Session hits for the same path come
from session_meta.files (already synced), a single LIKE-filtered query.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import federation
from ccc_server import lineage as _lineage
from ccc_server import session_fts as _sfts
from ccc_server import ship_graph as _sg


def _session_meta_rows(conn: sqlite3.Connection, sids: list[str]) -> dict[str, dict]:
    placeholders = ",".join("?" for _ in sids)
    out: dict[str, dict] = {}
    try:
        cur = conn.execute(
            f"SELECT sid, repo, start_ts, end_ts FROM session_meta WHERE sid IN ({placeholders})",
            sids,
        )
    except sqlite3.OperationalError:
        return out
    for sid, repo, start_ts, end_ts in cur.fetchall():
        ts = start_ts or end_ts
        out[sid] = {
            "repo": repo or "",
            "date": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "ts": ts or 0.0,
            "last_ts": end_ts or start_ts or 0.0,
        }
    return out


def _lease_fields(sids: list[str]) -> dict[str, dict]:
    """{sid: {lease_owner, lease_handoff_at}} for the handful of result sids
    that have a handoff lease -- one directory listing, then a read only for
    sids that actually have a lease file (bounded by the result limit, never
    by session count). Lets a requesting node pick the owner copy and spot
    forks when the same session shows up on two machines (S4 dedupe)."""
    try:
        lease_dir = federation.leases_dir()
        names = {p.stem for p in lease_dir.glob("*.json")} if lease_dir.is_dir() else set()
    except OSError:
        return {}
    out: dict[str, dict] = {}
    for sid in sids:
        if sid not in names:
            continue
        lease = federation.read_lease(sid)
        if not lease:
            continue
        history = lease.get("history") or []
        handoff_at = history[-1].get("at") if history and isinstance(history[-1], dict) else None
        out[sid] = {"lease_owner": lease.get("owner_node"),
                    "lease_handoff_at": handoff_at or lease.get("acquired_at")}
    return out


def _sdoc_rows(sids: list[str]) -> dict[str, dict]:
    placeholders = ",".join("?" for _ in sids)
    out: dict[str, dict] = {}
    try:
        conn = _sfts._get_connection()
        cur = conn.execute(
            f"SELECT sid, title, prompts FROM sdoc WHERE sid IN ({placeholders})",
            sids,
        )
    except sqlite3.OperationalError:
        return out
    for sid, title, prompts in cur.fetchall():
        out[sid] = {
            "title": title or "",
            "snippet": " ".join((prompts or "").split())[:200],
        }
    return out


def recall(query: str, limit: int = 20, scope: str = "local") -> dict:
    """GET /api/memory/recall — ranked sessions for `query`, each enriched
    with title/repo/date/snippet from already-synced index state.

    Neither search_sessions() nor its underlying index syncs block this call
    for more than a small budget: a cold index warms/catches up on a
    background thread (see session_fts.warm_start / ship_graph.warm_start),
    and a request that lands mid-warm just gets whatever is indexed so far
    plus `indexing: true` rather than waiting tens of seconds.

    `scope="all"` (multi-machine S4) also asks every paired peer and merges
    their rows in (memory_fanout.recall_all); the default stays local so a
    caller resuming by bare `session_id` never gets a peer's session.
    """
    if scope == "all":
        from ccc_server import memory_fanout as _fanout
        return _fanout.recall_all(query, limit, lambda q, n: recall(q, limit=n))
    # MEMO-FIX-lineage: over-fetch so collapsing lineage-linked hits into
    # their newest member still leaves `limit` rows on the table, rather than
    # quietly returning fewer than asked for.
    hits = _sg.search_sessions(query, limit=max(limit * 2, limit + 10))
    sids = [h["session_id"] for h in hits if h.get("session_id")]
    session_meta = _session_meta_rows(_sg._get_connection(), sids) if sids else {}
    sdoc = _sdoc_rows(sids) if sids else {}
    # Long sessions are also indexed as sections (session_fts.ssec): point at
    # the best-matching one -- its turn range and a hit-centred snippet --
    # since the session-level snippet is just the opening prompt.
    sections = _sfts.section_matches(query, sids) if sids else {}
    results = []
    for sid in sids:
        sm = session_meta.get(sid, {})
        sd = sdoc.get(sid, {})
        row = {
            "session_id": sid,
            "title": sd.get("title", ""),
            "repo": sm.get("repo", ""),
            "date": sm.get("date", ""),
            "snippet": sd.get("snippet", ""),
            "_ts": sm.get("ts", 0.0),
            "last_activity_ts": sm.get("last_ts", 0.0),
        }
        if sid in sections:
            row["match"] = sections[sid]
        results.append(row)
    conn = _sg._get_connection()
    results = _lineage.collapse_chain_hits(results, conn, ts_key="_ts")[:limit]
    leases = _lease_fields([r["session_id"] for r in results])
    for row in results:
        row.pop("_ts", None)
        row.update(leases.get(row["session_id"], {}))
    indexing = _sfts.is_indexing() or _sg.is_indexing()
    return {"query": query, "results": results, "indexing": indexing}


def shipped(topic: str, scope: str = "local") -> dict:
    """GET /api/memory/shipped — is_shipped() contract as-is, with the
    topic echoed back for the CLI/UI to display. `scope="all"` merges every
    reachable peer's verdict (memory_fanout.shipped_all)."""
    if scope == "all":
        from ccc_server import memory_fanout as _fanout
        return _fanout.shipped_all(topic, lambda t: shipped(t))
    result = _sg.is_shipped(topic)
    result["topic"] = topic
    return result


def _resolve_repo_for_path(path: str, repo_hint: str = "") -> tuple[str, str, str]:
    """(repo_name, repo_root, rel_path) for a file path.

    `path` may be absolute or repo-relative. `repo_hint` — a repo name from
    ship_graph.discover_repo_roots(), or that repo's own root path —
    disambiguates a relative path when more than one known repo could
    contain it; without a hint, a relative path is matched against each
    known repo's working tree.
    """
    roots = _sg.discover_repo_roots()
    p = (path or "").strip()

    if repo_hint:
        hint = repo_hint.strip()
        root = roots.get(hint)
        name = hint
        if not root:
            for rname, rpath in roots.items():
                if rpath.rstrip("/") == hint.rstrip("/"):
                    root, name = rpath, rname
                    break
        if root:
            root_s = root.rstrip("/")
            rel = p[len(root_s):].lstrip("/") if p.startswith(root_s) else p.lstrip("/")
            return name, root_s, rel

    if p.startswith("/"):
        best = None
        for name, root in roots.items():
            root_s = root.rstrip("/")
            if p == root_s or p.startswith(root_s + "/"):
                if best is None or len(root_s) > len(best[1]):
                    best = (name, root_s)
        if best:
            name, root_s = best
            return name, root_s, p[len(root_s):].lstrip("/")
        return "", "", p

    for name, root in roots.items():
        if (Path(root) / p).exists():
            return name, root.rstrip("/"), p
    return "", "", p


def _git_file_commits(repo_root: str, rel_path: str, limit: int) -> list[dict]:
    """Live `git log --follow` for one path — the cached `commits` table has
    no path filter and doesn't track renames, so this is a targeted subprocess
    call, not a per-row scan."""
    if not repo_root or not rel_path:
        return []
    try:
        res = subprocess.run(
            ["git", "-C", repo_root, "log", "--follow", f"-{max(1, limit)}",
             "--format=%h\x1f%ct\x1f%s", "--", rel_path],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if res.returncode != 0:
        return []
    out = []
    for line in res.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        short_hash, ct, subject = parts[0], parts[1], parts[2]
        ts = float(ct) if ct else 0.0
        out.append({
            "kind": "commit",
            "hash": short_hash,
            "ts": ts,
            "date": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "why": subject,
        })
    return out


def _sessions_touching_file(conn: sqlite3.Connection, repo_root: str, rel_path: str,
                             limit: int) -> list[dict]:
    """Sessions whose session_meta.files (already synced) names this path —
    one LIKE-filtered SQL query, then an in-Python exact-match filter over
    just the matched rows (bounded by the LIKE, not by session count)."""
    basename = Path(rel_path).name if rel_path else ""
    if not basename:
        return []
    try:
        cur = conn.execute(
            "SELECT sid, start_ts, end_ts, files FROM session_meta WHERE files LIKE ?",
            (f"%{basename}%",),
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        return []
    abs_candidate = str(Path(repo_root) / rel_path) if repo_root else ""
    out = []
    for sid, start_ts, end_ts, files_json in rows:
        try:
            files = json.loads(files_json) if files_json else []
        except (TypeError, ValueError):
            files = []
        if not any(f == abs_candidate or f == rel_path or f.endswith("/" + rel_path) for f in files):
            continue
        out.append({"sid": sid, "ts": start_ts or end_ts or 0.0})
    out.sort(key=lambda h: h["ts"], reverse=True)
    return out[:limit]


def _sessions_with_file_op(conn: sqlite3.Connection, abs_path: str, limit: int) -> list[dict]:
    """Sessions that wrote or read exactly `abs_path` (ship_graph's
    session_files, indexed by path: one lookup, repo or not)."""
    if not abs_path:
        return []
    try:
        rows = conn.execute(
            """SELECT sf.sid, sf.op, sm.start_ts, sm.end_ts
               FROM session_files sf LEFT JOIN session_meta sm ON sm.sid = sf.sid
               WHERE sf.path = ?""",
            (abs_path,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = [{"sid": sid, "op": op, "ts": start_ts or end_ts or 0.0} for sid, op, start_ts, end_ts in rows]
    out.sort(key=lambda h: h["ts"], reverse=True)
    return out[:limit]


def file_history(path: str, repo: str = "", limit: int = 20, scope: str = "local") -> dict:
    """GET /api/memory/file-history — sessions and commits that touched
    `path`, newest first, each with a one-line why (commit subject / session
    title). `scope="all"` adds peers' history for the same repo identity."""
    if scope == "all":
        from ccc_server import memory_fanout as _fanout
        return _fanout.file_history_all(
            path, repo, limit,
            lambda p, repo="", limit=20: file_history(p, repo=repo, limit=limit),
            lambda p, r: _resolve_repo_for_path(os.path.expanduser((p or "").strip()), r))
    p = os.path.expanduser((path or "").strip())
    if not p:
        return {"path": "", "repo": "", "history": []}

    repo_name, repo_root, rel_path = _resolve_repo_for_path(p, repo)
    entries = _git_file_commits(repo_root, rel_path, limit) if repo_root and rel_path else []

    conn = _sg._get_connection()
    _sg._sync_all(conn, force=False)
    if os.path.isabs(p):
        abs_path = os.path.normpath(p)
    elif repo_root and rel_path:
        abs_path = os.path.normpath(os.path.join(repo_root, rel_path))
    else:
        abs_path = ""
    sess_hits = _sessions_with_file_op(conn, abs_path, limit)
    # session_meta.files (writes only, basename-LIKE) still covers anything
    # session_files hasn't re-indexed yet, and repo-relative suffix matches.
    seen = {h["sid"] for h in sess_hits}
    if rel_path:
        for h in _sessions_touching_file(conn, repo_root, rel_path, limit):
            if h["sid"] not in seen:
                seen.add(h["sid"])
                sess_hits.append({**h, "op": "wrote"})
    sids = [h["sid"] for h in sess_hits]
    sdoc = _sdoc_rows(sids) if sids else {}
    for h in sess_hits:
        sd = sdoc.get(h["sid"], {})
        entries.append({
            "kind": "session",
            "session_id": h["sid"],
            "op": h.get("op", "wrote"),
            "ts": h["ts"],
            "date": time.strftime("%Y-%m-%d", time.localtime(h["ts"])) if h["ts"] else "",
            "why": sd.get("title") or "(untitled session)",
        })

    entries.sort(key=lambda e: e.get("ts") or 0, reverse=True)
    return {"path": p, "repo": repo_name, "history": entries[:limit]}


# Words that mark a snippet as recording a DECISION rather than just
# mentioning a topic — "we added X" isn't a decision, "we went with X
# instead of Y" is. This weaker fallback is used only when the nightly
# extraction has no matching user quotes.
_DECISION_MARKERS = (
    "decided", "decision", "we'll use", "we will use", "going with",
    "went with", "instead of", "rather than", "chose", "chosen",
    "settled on", "agreed to", "opted for", "final call", "let's go with",
)


def _looks_like_decision(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _DECISION_MARKERS)


def _decision_hits(topic: str, limit: int) -> list[dict]:
    """Heuristic decision-shaped filter over search_sessions() hits."""
    hits = _sg.search_sessions(topic, limit=max(limit * 3, 20))
    sids = [h["session_id"] for h in hits if h.get("session_id")]
    sdoc = _sdoc_rows(sids) if sids else {}
    out = []
    for sid in sids:
        sd = sdoc.get(sid, {})
        snippet = sd.get("snippet", "")
        if not _looks_like_decision(snippet):
            continue
        out.append({"session_id": sid, "title": sd.get("title", ""), "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def decisions(topic: str, limit: int = 10, repo: str | None = None) -> dict:
    """Stored user decision quotes first; labelled session snippets as fallback.

    The request never runs extraction. Quotes remain candidate evidence, not
    approved or necessarily current policy. All original result fields stay
    available to older CLI/UI consumers.
    """
    from ccc_server import decision_extraction as _dex

    t = (topic or "").strip()
    if not t:
        return {"topic": "", "results": []}
    limit = max(1, min(int(limit), 50))
    repo = (repo or "").strip() or None
    extracted = _dex.search_decisions(t, repo=repo, limit=limit)
    if extracted:
        return {"topic": t, "results": [{
            "session_id": d["session_id"],
            "title": "Decision: " + d["quote"][:120],
            "repo": d["repo"], "date": d["date"], "snippet": d["quote"],
            "decision_id": d["id"], "provenance": "extracted_user_quote",
            "status": "candidate", "confidence": d["confidence"],
            "source": {"path": d["source_path"], "line": d["source_line"],
                       "timestamp": d["ts"], "engine": d["engine"]},
        } for d in extracted]}

    hits = _decision_hits(t, 50 if repo else limit)
    sids = [h["session_id"] for h in hits]
    session_meta = _session_meta_rows(_sg._get_connection(), sids) if sids else {}
    results = []
    for h in hits:
        sm = session_meta.get(h["session_id"], {})
        if repo and sm.get("repo") != repo:
            continue
        results.append({
            "session_id": h["session_id"],
            "title": h["title"],
            "repo": sm.get("repo", ""),
            "date": sm.get("date", ""),
            "snippet": h["snippet"],
            "provenance": "session_snippet", "status": "unverified",
        })
    return {"topic": t, "results": results[:limit]}
