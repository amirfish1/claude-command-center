# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""`ccc brief <session>` / GET /api/memory/brief/<session> (MEMO-FIX-20):
answers "where did this work stop and how do I resume?" for one session.

No new transcript scanning lives here. Everything is read from state two
other modules already sync in the background:
  - ccc_server.ship_graph's session_meta table (repo/cwd/dates/tickets/
    commits/files, from a full per-transcript parse) and tickets table
    (WatchTower status, synced off queues.db's own mtime).
  - ccc_server.session_fts's sdoc table (title, last-3-user-asks in
    `prompts`, last-3-assistant-replies in `report` -- also from a full
    per-transcript parse, so the "last reply" is accurate even when the
    session's true last topic sits deep inside a multi-MB transcript, well
    past any bounded head/tail window).
hooks/_reorient_shared.py supplies the injected-message filter (queue
notifications, peer-session pings) and the truncate() used to trim asks/
replies for display -- the same filter PostCompact's re-orientation block
uses, reused here so `ccc brief` and the post-compaction block never
disagree about what counts as a real user ask.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import federation

from ccc_server import lineage as _lineage
from ccc_server import session_fts as _sfts
from ccc_server import ship_graph as _sg

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
from _reorient_shared import INJECTED_PREFIXES, truncate as _truncate  # noqa: E402

LAST_REPLY_CHARS = 1500
ASK_CHARS = 150
# `brief()` is reachable from a paired peer (fleet.py's memory_brief route
# action) with data sourced from a transcript-derived session_meta row --
# tickets/commits/files counts aren't bounded upstream (a ref regex scan or
# a very active session can produce a long list). Capping here protects two
# things: the per-ref `tickets` table query in _ticket_info() and, more
# importantly, the per-commit `git branch -r --contains` subprocess in
# _commits_with_origin() -- with no cap, a crafted or just unusually large
# commits dict would run that subprocess (10 s timeout each) once per entry,
# on a request a peer can trigger. The multi-machine spec's own response
# caps (fleet.py's _MEMORY_MAX_ROWS etc.) only trim the LIST AFTER these
# calls already ran, so they don't bound the subprocess count on their own.
_BRIEF_LIST_CAP = 50
_LIKE_WILDCARD_RE = re.compile(r"([%_])")


def _escape_like(s: str) -> str:
    """Escape sqlite LIKE wildcards so a peer-supplied sid prefix is matched
    literally, not as a pattern (a stray '%' or '_' in `q` would otherwise
    widen the match far past the intended prefix)."""
    return _LIKE_WILDCARD_RE.sub(r"\\\1", s)


_SCRATCH_DIR_RE = re.compile(r"^/tmp/|/private/var/|/var/folders/", re.I)
# Claude Code's own per-project auto-memory dir (~/.claude/projects/<repo>/
# memory/*.md) -- bookkeeping the agent wrote about itself, not a work
# artifact worth surfacing as "what this session produced".
_AGENT_MEMORY_DIR_RE = re.compile(r"/\.claude/projects/[^/]+/memory/", re.I)

# Interactive resume syntax per engine. Only the two engines this ticket's
# transcript-parsing is validated against (Claude, Codex) plus Kimi (already
# documented for the usage-limit retrieval prompt, _USAGE_LIMIT_ENGINE_LOCATE
# in usage_limit.py) get an exact command; anything else falls back to
# pointing at `ccc sessions` rather than guessing a CLI flag that may not
# exist.
_ENGINE_RESUME = {
    "claude": lambda sid, cwd: f"cd {cwd or '<repo>'} && claude --resume {sid}",
    "codex": lambda sid, cwd: f"cd {cwd or '<repo>'} && codex resume {sid}",
    "kimi": lambda sid, cwd: f"cd {cwd or '<repo>'} && kimi --resume {sid}",
}


def _engine_of_path(path: str) -> str:
    p = path or ""
    if "/.codex/" in p:
        return "codex"
    if "/.claude/projects/" in p:
        return "claude"
    if "/.kimi-code/" in p:
        return "kimi"
    if "/.gemini/" in p:
        return "gemini"
    if "/.cursor/" in p:
        return "cursor"
    return "claude"


def _session_meta_row(conn, sid: str) -> dict:
    row = conn.execute(
        "SELECT repo, cwd, start_ts, end_ts, tickets, commits, files "
        "FROM session_meta WHERE sid = ?",
        (sid,),
    ).fetchone()
    if not row:
        return {}
    repo, cwd, start_ts, end_ts, tickets_json, commits_json, files_json = row

    def _load(raw, default):
        try:
            return json.loads(raw) if raw else default
        except (TypeError, ValueError):
            return default

    return {
        "repo": repo or "",
        "cwd": cwd or "",
        "start_ts": start_ts,
        "end_ts": end_ts,
        "tickets": _load(tickets_json, []),
        "commits": _load(commits_json, {}),
        "files": _load(files_json, []),
    }


def _transcript_path(conn, sid: str) -> str:
    row = conn.execute(
        "SELECT path FROM transcripts WHERE sid = ? ORDER BY mtime DESC LIMIT 1", (sid,)
    ).fetchone()
    return row[0] if row else ""


def _sdoc_row(sid: str) -> dict:
    try:
        fconn = _sfts._get_connection()
        _sfts._sync_index(fconn, force=False)
        row = fconn.execute(
            "SELECT title, prompts, report FROM sdoc WHERE sid = ?", (sid,)
        ).fetchone()
    except Exception:
        return {"title": "", "prompts": "", "report": ""}
    if not row:
        return {"title": "", "prompts": "", "report": ""}
    title, prompts, report = row
    return {"title": title or "", "prompts": prompts or "", "report": report or ""}


def _last_user_asks(prompts_text: str, limit: int = 3) -> list[str]:
    """Last `limit` real user asks, newest last -- same "is this an injected
    message, not something the user typed" filter PostCompact's
    re-orientation block uses (INJECTED_PREFIXES)."""
    parts = [p.strip() for p in (prompts_text or "").split("\n---\n") if p.strip()]
    out = []
    for p in reversed(parts):
        one_line = re.sub(r"\s+", " ", p).strip()
        if not one_line or one_line.startswith(INJECTED_PREFIXES):
            continue
        out.append(_truncate(one_line, ASK_CHARS))
        if len(out) >= limit:
            break
    out.reverse()
    return out


def _last_reply(report_text: str) -> str:
    """The single most recent assistant message (report holds the last
    three, joined by the same "\\n---\\n" separator _finish() uses),
    trimmed to LAST_REPLY_CHARS keeping the END -- the conclusion, not the
    opening -- since that is what answers "where did this stop"."""
    parts = [p.strip() for p in (report_text or "").split("\n---\n") if p.strip()]
    if not parts:
        return ""
    last = parts[-1]
    if len(last) <= LAST_REPLY_CHARS:
        return last
    return "…" + last[-(LAST_REPLY_CHARS - 1):]


def _ticket_info(conn, refs: list[str]) -> list[dict]:
    """Ticket refs come from a bare regex scan of transcript text (any
    'WORD-N' token), so a session that just talks about e.g. an internal doc's
    own 'ADS-1, ADS-2, ...' numbering scheme produces refs that were never a
    real WatchTower ticket -- not a queue whose status lookup is failing.
    Drop refs whose project prefix doesn't match any project WatchTower has
    ever synced, instead of showing them with an unhelpful '[?]' status."""
    out = []
    known_projects: set[str] | None = None
    for ref in refs:
        row = conn.execute("SELECT status, title FROM tickets WHERE ref = ?", (ref,)).fetchone()
        if not row:
            if known_projects is None:
                known_projects = {
                    r[0] for r in conn.execute("SELECT DISTINCT project FROM tickets").fetchall()
                }
            if ref.split("-")[0] not in known_projects:
                continue
        out.append({
            "ref": ref,
            "status": row[0] if row and row[0] else "",
            "title": row[1] if row and row[1] else "",
        })
    return out


def _commit_on_origin(repo_root: str, sha: str) -> bool | None:
    """None when the answer can't be determined (no local clone, git
    failure) -- distinct from False (confirmed local-only)."""
    if not repo_root or not sha:
        return None
    try:
        res = subprocess.run(
            ["git", "-C", repo_root, "branch", "-r", "--contains", sha],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return any(line.strip().startswith("origin/") for line in res.stdout.splitlines())


def _commits_with_origin(repo_root: str, commits: dict) -> list[dict]:
    # Capped BEFORE the loop, not after building `out` -- each entry below
    # runs its own `git branch --contains` subprocess (10 s timeout), so an
    # uncapped commits dict is a per-request subprocess-count multiplier,
    # not just an oversize list.
    items = list(commits.items())[:_BRIEF_LIST_CAP]
    out = [
        {
            "sha": sha,
            "subject": info.get("subject", "") if isinstance(info, dict) else "",
            "branch": info.get("branch", "") if isinstance(info, dict) else "",
            "on_origin": _commit_on_origin(repo_root, sha),
        }
        for sha, info in items
    ]
    out.sort(key=lambda c: c["sha"])
    return out


def _artifacts_outside_repos(files: list[str], roots: dict) -> list[str]:
    """Files this session wrote that don't live inside any known repo --
    e.g. a scratch report under ~/dev/scratch/. Excludes throwaway tmp
    paths, which are never meant to be an artifact worth surfacing."""
    root_paths = [r.rstrip("/") for r in roots.values()]
    out = []
    for f in files:
        if any(f == r or f.startswith(r + "/") for r in root_paths):
            continue
        if _SCRATCH_DIR_RE.search(f or "") or _AGENT_MEMORY_DIR_RE.search(f or ""):
            continue
        out.append(f)
    return out


def _ref_owner_node(ref: str) -> str | None:
    """This session ref's owning node_id if it's a federation global ref
    (`<node-uuid>:<native-sid>`), else None -- including for a bare/local
    sid, which carries no node prefix at all."""
    if not ref:
        return None
    node, _native = federation.parse_session_ref(ref)
    return node


def _node_tags(refs: list[str]) -> dict[str, str]:
    """{ref: owner_node_id} for every ref in `refs` that names a peer other
    than this node -- local refs (the common single-machine case) are left
    out entirely, so a caller can tell "this is local" from "absent"."""
    here = federation.node_id()
    tags = {}
    for ref in refs:
        node = _ref_owner_node(ref)
        if node and node != here:
            tags[ref] = node
    return tags


def _resume_command(engine: str, sid: str, cwd: str) -> str:
    fn = _ENGINE_RESUME.get(engine)
    if fn:
        return fn(sid, cwd)
    return f"ccc sessions  # find {sid[:8]} and reattach from the dashboard ({engine})"


def resolve_session(query: str) -> dict:
    """{'session_id': str | None, 'alternates': [str, ...]}. `query` is
    tried, in order, as: an exact session id, an unambiguous id-prefix
    (LIKE match, no query words), then a recall-style search — the same
    ranking `ccc recall` uses — picking the top hit and returning the next
    few as alternates."""
    q = (query or "").strip()
    if not q:
        return {"session_id": None, "alternates": []}

    conn = _sg._get_connection()
    _sg._sync_all(conn, force=False)

    row = conn.execute("SELECT sid FROM session_meta WHERE sid = ?", (q,)).fetchone()
    if row:
        return {"session_id": row[0], "alternates": []}

    # >=8 chars (not 4): the spec's own contract for this arg is "sid prefix
    # (>=8 chars)" (multi-machine spec section 4.1) -- a peer-reachable
    # short prefix matches far too broadly. Wildcards are escaped so a
    # literal '%'/'_' in `q` can't widen the match into a pattern scan.
    if " " not in q and len(q) >= 8:
        rows = conn.execute(
            "SELECT sid FROM session_meta WHERE sid LIKE ? ESCAPE '\\' ORDER BY start_ts DESC",
            (_escape_like(q) + "%",),
        ).fetchall()
        if rows:
            sids = [r[0] for r in rows]
            return {"session_id": sids[0], "alternates": sids[1:6]}

    hits = _sg.search_sessions(q, limit=6)
    sids = [h["session_id"] for h in hits if h.get("session_id")]
    if not sids:
        return {"session_id": None, "alternates": []}
    return {"session_id": sids[0], "alternates": sids[1:6]}


def brief(query: str) -> dict:
    """GET /api/memory/brief/<session> -- see module docstring for sourcing.

    `found: False` (no session_id) means `query` matched nothing at all,
    not even a recall hit -- distinct from a resolved session that simply
    has thin data (e.g. no commits made)."""
    resolved = resolve_session(query)
    sid = resolved["session_id"]
    if not sid:
        return {"query": query, "session_id": None, "alternates": [], "found": False}

    conn = _sg._get_connection()
    meta = _session_meta_row(conn, sid)
    path = _transcript_path(conn, sid)
    engine = _engine_of_path(path)
    sdoc = _sdoc_row(sid)

    roots = _sg.discover_repo_roots()
    repo_root = roots.get(meta.get("repo", ""), "")
    lineage = _lineage.chain_summary(conn, sid)
    children = _lineage.spawn_children_of(sid)
    links = _lineage.continuation_links(conn, sid)
    nodes = _node_tags([lineage["parent"], lineage["latest"], *lineage["continuation_ancestors"], *children])

    def _date(ts):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""

    return {
        "query": query,
        "session_id": sid,
        "alternates": resolved["alternates"],
        "found": True,
        "title": sdoc.get("title") or "",
        "repo": meta.get("repo", ""),
        "cwd": meta.get("cwd", ""),
        "engine": engine,
        "start_date": _date(meta.get("start_ts")),
        "end_date": _date(meta.get("end_ts")),
        "tickets": _ticket_info(conn, (meta.get("tickets") or [])[:_BRIEF_LIST_CAP]),
        "last_user_asks": _last_user_asks(sdoc.get("prompts", "")),
        "last_assistant_reply": _last_reply(sdoc.get("report", "")),
        "files_touched": (meta.get("files") or [])[:_BRIEF_LIST_CAP],
        "commits": _commits_with_origin(repo_root, meta.get("commits") or {}),
        "artifacts_outside_repos": _artifacts_outside_repos(
            (meta.get("files") or [])[:_BRIEF_LIST_CAP], roots),
        "resume_command": _resume_command(engine, sid, meta.get("cwd", "")),
        "indexing": _sfts.is_indexing() or _sg.is_indexing(),
        "parent": lineage["parent"],
        "latest": lineage["latest"],
        "continuation_ancestors": lineage["continuation_ancestors"],
        "children": children,
        # Direct continuation neighbours (multi-machine S6). A predecessor
        # not indexed here carries `dangling: True`; only a scope=all brief
        # (memory_fanout.brief_all) asks peers to resolve it.
        "predecessor": links["predecessor"],
        "successors": links["successors"],
        # {ref: owner_node_id} for any of the refs above that name a peer
        # node (multi-machine spec S6) -- empty on a single-machine setup.
        "nodes": nodes,
    }
