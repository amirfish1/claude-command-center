# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Lineage-aware memory (MEMO-FIX-lineage).

CCC already stores three distinct "this session relates to that session"
relations, in three different places:

  - spawn parent/child ("spawned-by"): `session-graph.json` edges, the
    dashboard/worker's live view of `POST /api/sessions/spawn`.
  - report-to ("who does this session report back to"): rebindable route ids
    in `report_routes.py`.
  - continuation ancestor/successor ("Continue in a new session", F2,
    usage-limit auto-resume): the first user turn of the successor's
    transcript names its ancestor as "Origin session id: <sid>", indexed as
    `ship_graph.session_meta.continuation_origin` (see ship_graph.py).

This module composes the three into the two things a "where are we" answer
or a recall/search result actually needs: `chain_summary()` (parent +
latest-successor for one session, for `ccc brief`) and `collapse_chain_hits()`
(fold a ranked hit list's lineage-linked rows into their newest member, for
`ccc recall` / sidebar search).

Every lookup here is either an indexed sqlite query (continuation_origin) or
one small JSON file read (session-graph.json, report-routes.json) -- never a
transcript scan or a subprocess -- so this stays perf-gate-compliant even
called on every brief()/recall(). Stdlib + ship_graph + report_routes only,
no `_core` coupling, so it is independently unit-testable.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

from ccc_server import test_isolation_active
from ccc_server import report_routes as _routes
from ccc_server import ship_graph as _sg

# Cycle-guard for every chain walk below: a spawn/continuation graph is never
# expected to nest this deep, so hitting the cap means a cycle (or a
# corrupted edge list), not a legitimately long chain.
MAX_CHAIN_DEPTH = 25


def _session_graph_path():
    if test_isolation_active():
        return os.path.join(tempfile.gettempdir(), f"ccc-test-session-graph-{os.getpid()}.json")
    base = os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )
    return os.path.join(base, "session-graph.json")


# session-graph.json grows for as long as the dashboard has run (one entry
# per spawn ever made -- hundreds of KB on a machine with months of history).
# collapse_chain_hits() can run once per sidebar keystroke, so this caches
# the parsed edges by (path, mtime, size): re-parsed only when a spawn
# actually changes the file, never on every search request.
_spawn_edges_cache = {"path": None, "mtime": None, "size": None, "parent_of": {}, "children_of": {}}


def _load_spawn_edges(path=None):
    """(parent_of, children_of) dicts read straight from session-graph.json.

    Deliberately not `session_graph.py`'s `_SessionGraph` class: that module
    is coupled to the live server's `_core` proxy and owns write/merge
    semantics this module never needs -- lineage.py only ever reads.
    """
    path = path or _session_graph_path()
    try:
        st = os.stat(path)
    except OSError:
        return {}, {}
    cache = _spawn_edges_cache
    if cache["path"] == path and cache["mtime"] == st.st_mtime and cache["size"] == st.st_size:
        return cache["parent_of"], cache["children_of"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    parent_of: dict[str, str] = {}
    children_of: dict[str, list[str]] = {}
    for e in data.get("edges") or []:
        if not isinstance(e, dict):
            continue
        parent = str(e.get("parent") or "").strip()
        child = str(e.get("child") or "").strip()
        if not parent or not child or parent == child:
            continue
        parent_of[child] = parent
        children_of.setdefault(parent, []).append(child)
    cache.update(path=path, mtime=st.st_mtime, size=st.st_size,
                 parent_of=parent_of, children_of=children_of)
    return parent_of, children_of


def spawn_parent_of(sid: str, path=None) -> str:
    """The session that spawned `sid` via `POST /api/sessions/spawn`, or ""."""
    parent_of, _ = _load_spawn_edges(path)
    return parent_of.get(sid, "")


def spawn_children_of(sid: str, path=None) -> list[str]:
    """Sessions `sid` spawned, in session-graph.json edge order."""
    _, children_of = _load_spawn_edges(path)
    return list(children_of.get(sid, []))


def report_to_of(sid: str, path=None) -> str:
    """Current (possibly rebound) report-to address for a spawned session,
    or "" if `sid` has no report-to route."""
    routes = _routes.list_routes(child_session_id=sid, path=path)
    if not routes:
        return ""
    return routes[-1].get("report_to") or ""


def continuation_ancestors_of(conn: sqlite3.Connection, sid: str) -> list[str]:
    """Walk `continuation_origin` backward from `sid`, nearest-first, to the
    oldest ancestor. Empty if `sid` never continued from an earlier session."""
    out = []
    seen = {sid}
    current = sid
    for _ in range(MAX_CHAIN_DEPTH):
        origin = _sg.continuation_origin_of(conn, current)
        if not origin or origin in seen:
            break
        out.append(origin)
        seen.add(origin)
        current = origin
    return out


def latest_successor(conn: sqlite3.Connection, sid: str) -> str:
    """Walk `continuation_origin` forward (whoever named `sid` as their
    origin, then whoever named THAT session, ...) to the newest member of the
    chain. Returns `sid` itself if nothing ever continued from it."""
    seen = {sid}
    current = sid
    for _ in range(MAX_CHAIN_DEPTH):
        children = _sg.continuation_children_of(conn, current)
        nxt = next((c for c in children if c not in seen), "")
        if not nxt:
            return current
        seen.add(nxt)
        current = nxt
    return current


def continuation_chain_members(conn: sqlite3.Connection, sid: str) -> list[str]:
    """Every sid in `sid`'s continuation chain, oldest first, `sid` itself
    included. For a "where are we" answer that needs the whole chain's
    `ccc brief` output, not just the parent/latest endpoints `chain_summary`
    reports."""
    ancestors = continuation_ancestors_of(conn, sid)
    root = ancestors[-1] if ancestors else sid
    members = [root]
    seen = {root}
    current = root
    for _ in range(MAX_CHAIN_DEPTH):
        children = _sg.continuation_children_of(conn, current)
        nxt = next((c for c in children if c not in seen), "")
        if not nxt:
            break
        members.append(nxt)
        seen.add(nxt)
        current = nxt
    return members


def orchestrator_parent(sid: str, session_graph_path=None, report_routes_path=None) -> str:
    """The dispatcher that kicked off `sid`'s chain: `sid`'s own spawn parent,
    falling back to its report-to address, if either exists. Callers that
    want the parent of a whole *continuation* chain should resolve `sid` to
    `continuation_ancestors_of(...)[-1]` (or `sid` itself if empty) first --
    see `chain_summary()`, which does exactly that."""
    parent = spawn_parent_of(sid, path=session_graph_path)
    if parent:
        return parent
    return report_to_of(sid, path=report_routes_path)


def chain_summary(conn: sqlite3.Connection, sid: str, session_graph_path=None, report_routes_path=None) -> dict:
    """One-shot lineage summary for `sid`, the whole of what `ccc brief` /
    session_brief.brief() surfaces as 'parent: X, latest: Y':

      - "parent": the orchestrator that kicked off `sid`'s continuation chain
        -- resolved from the chain's oldest member, since later continuations
        are usually auto-resumed (no fresh spawn/report-to of their own) while
        the first session in the chain is the one an orchestrator actually
        dispatched.
      - "latest": the newest successor in `sid`'s continuation chain, or ""
        if `sid` is already the newest (nothing to point at).
      - "continuation_ancestors": `sid`'s continuation ancestors, nearest-first.
    """
    ancestors = continuation_ancestors_of(conn, sid)
    root = ancestors[-1] if ancestors else sid
    latest = latest_successor(conn, sid)
    return {
        "parent": orchestrator_parent(
            root, session_graph_path=session_graph_path, report_routes_path=report_routes_path
        ),
        "latest": latest if latest != sid else "",
        "continuation_ancestors": ancestors,
    }


def continuation_links(conn: sqlite3.Connection, sid: str) -> dict:
    """`sid`'s direct continuation neighbours, one hop each way (multi-machine
    S6): {"predecessor": {...} | None, "successors": [{...}, ...]}.

    The predecessor is the session `sid` names as its origin. When that sid
    isn't indexed on this node (the session moved between machines), it is
    kept with `dangling: True` -- never dropped and never guessed -- so a
    scope=all brief can resolve it lazily on a peer. Two indexed lookups,
    no walk, no network."""
    origin = _sg.continuation_origin_of(conn, sid)
    predecessor = None
    if origin:
        known = conn.execute("SELECT 1 FROM session_meta WHERE sid = ?", (origin,)).fetchone()
        predecessor = {"session_id": origin, "ref": origin}
        if not known:
            predecessor["dangling"] = True
    successors = [{"session_id": c, "ref": c}
                  for c in _sg.continuation_children_of(conn, sid) if c != sid][:MAX_CHAIN_DEPTH]
    return {"predecessor": predecessor, "successors": successors}


def collapse_chain_hits(
    hits: list[dict],
    conn: sqlite3.Connection,
    session_graph_path=None,
    sid_key: str = "session_id",
    ts_key: str = "ts_unix",
) -> list[dict]:
    """Fold hits that are lineage-linked *to another hit already in this same
    list* into their newest member, tagging the survivor with
    `chain_collapsed` (count folded) and `chain_collapsed_sids`.

    Only collapses along edges that stay inside `hits` -- two sibling
    sessions spawned by the same orchestrator do NOT collapse into each other
    unless that orchestrator is itself one of the hits, so this can never
    hide a result behind an ancestor the caller never asked about. Preserves
    the input's rank order; the survivor is emitted at the best-ranked
    member's position.
    """
    if len(hits) <= 1:
        return list(hits)
    sids = [h.get(sid_key) for h in hits if h.get(sid_key)]
    hit_sid_set = set(sids)
    if len(hit_sid_set) <= 1:
        return list(hits)

    parent_of, _ = _load_spawn_edges(session_graph_path)

    cont_origin: dict[str, str] = {}
    placeholders = ",".join("?" for _ in sids)
    try:
        cur = conn.execute(
            f"SELECT sid, continuation_origin FROM session_meta WHERE sid IN ({placeholders})",
            sids,
        )
        cont_origin = {row_sid: origin for row_sid, origin in cur.fetchall() if origin}
    except sqlite3.OperationalError:
        pass

    def _family_root(sid: str) -> str:
        current = sid
        seen = set()
        for _ in range(MAX_CHAIN_DEPTH):
            if current in seen:
                break
            seen.add(current)
            nxt = cont_origin.get(current) or parent_of.get(current) or ""
            if not nxt or nxt not in hit_sid_set:
                break
            current = nxt
        return current

    def _continuation_root(sid: str) -> str:
        current = sid
        seen = set()
        for _ in range(MAX_CHAIN_DEPTH):
            if current in seen:
                break
            seen.add(current)
            nxt = cont_origin.get(current) or ""
            if not nxt or nxt not in hit_sid_set:
                break
            current = nxt
        return current

    groups: dict[str, list[dict]] = {}
    for h in hits:
        sid = h.get(sid_key)
        root = _family_root(sid) if sid else id(h)
        groups.setdefault(root, []).append(h)

    # The survivor is the newest member of the ROOT's own continuation chain
    # (the orchestrator's latest successor), never a spawned child: a child
    # that is merely newer would otherwise stand in for the master and its
    # title/snippet would hide the session the query actually matched.
    def _representative(root, members):
        spine = [m for m in members if _continuation_root(m.get(sid_key)) == root]
        return max(spine or members, key=lambda m: m.get(ts_key) or 0)

    out = []
    emitted_roots = set()
    for h in hits:
        sid = h.get(sid_key)
        root = _family_root(sid) if sid else id(h)
        if root in emitted_roots:
            continue
        emitted_roots.add(root)
        members = groups[root]
        if len(members) == 1:
            out.append(h)
            continue
        newest = _representative(root, members)
        rep = dict(newest)
        rep["chain_collapsed"] = len(members) - 1
        rep["chain_collapsed_sids"] = [
            m.get(sid_key) for m in members if m.get(sid_key) != newest.get(sid_key)
        ]
        out.append(rep)
    return out
