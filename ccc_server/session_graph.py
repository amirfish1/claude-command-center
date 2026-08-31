"""Extracted from server.py (originally lines 17696-23860).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import json
import model_advisor
import os
import platform
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# SessionGraph — unified parent-child adjacency list
# ---------------------------------------------------------------------------
# Combines all edge sources into one in-memory graph persisted to disk:
#   1. Codex thread_spawn_edges (sqlite)         — source: "codex-native"
#   2. Durable codex-parent-links.json           — source: "codex-parent-link"
#   3. Spawn registry (spawned-pids.json)        — source: "ccc-spawn"
#   4. Codex thread registry (codex-thread-registry.json) — source: "codex-thread-registry"
#   5. Claude Task-tool agent-*.jsonl transcripts — source: "claude-task-tool"
#   6. Kimi Code Agent subagents (agents/<name>/wire.jsonl) — source: "kimi-subagent"
#   7. Grok Build subagents (sessions/<parent>/subagents/<child>/meta.json)
#      — source: "grok-subagent" (real resumable child sessions)
#
# Edges are added eagerly at startup (sources 1-4, 7) and lazily on first
# family_tree() query for sources 5-6 (filesystem glob, cached). Source 7
# is also refreshed on the Codex 30s loop and on family_tree() so a live
# spawn_subagent appears without a restart. New CCC spawns add their edge
# immediately via add_edge() called from _record_spawn_to_registry.
#
# The graph is engine-agnostic: it stores parent_sid -> child_sid with
# per-edge metadata ({source, engine, resumable, name, model}). The
# family_tree() method returns a nested dict ready for the API / UI.

class _SessionGraph:
    """Thread-safe parent-child adjacency list with disk persistence."""

    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.RLock()
        # child_sid -> parent_sid
        self._parent_of = {}
        # parent_sid -> {child_sid: edge_meta}
        self._children_of = {}
        # child_sid -> edge_meta (same objects as in _children_of values)
        self._edge_meta = {}
        # parent_sid -> epoch of the last Task-tool subagent glob (throttled,
        # not a one-time gate: two subagents spawned close together can land
        # their agent-*.jsonl files at different times, so a permanent
        # "already enriched" skip would miss whichever file wasn't written
        # yet at the first glob).
        self._claude_task_enriched = {}
        # parent_sid -> set of child_sids already enriched for Kimi Code
        # Agent subagents (agents/<name>/wire.jsonl under the session dir).
        self._kimi_subagents_enriched = set()
        self._dirty = False

    # -- persistence ---------------------------------------------------------

    def load(self):
        """Load the graph from disk if it exists and is valid."""
        with self._lock:
            if not self._path.exists():
                return
            try:
                data = json.loads(self._path.read_text())
            except (OSError, json.JSONDecodeError):
                return
            if not isinstance(data, dict):
                return
            edges = data.get("edges")
            if not isinstance(edges, list):
                return
            self._parent_of = {}
            self._children_of = {}
            self._edge_meta = {}
            for e in edges:
                if not isinstance(e, dict):
                    continue
                parent = str(e.get("parent") or "").strip()
                child = str(e.get("child") or "").strip()
                if not parent or not child or parent == child:
                    continue
                meta = {
                    "source": str(e.get("source") or "unknown"),
                    "engine": str(e.get("engine") or ""),
                    "resumable": e.get("resumable", True),
                    "name": str(e.get("name") or ""),
                    "model": str(e.get("model") or ""),
                }
                self._parent_of[child] = parent
                self._children_of.setdefault(parent, {})[child] = meta
                self._edge_meta[child] = meta
            # Persisted enrichment markers so we don't re-glob on restart.
            enriched = data.get("claude_task_enriched")
            if isinstance(enriched, dict):
                self._claude_task_enriched = {str(k): float(v) for k, v in enriched.items()}
            elif isinstance(enriched, list):
                # Old format (a set of "done forever" parent ids): treat as
                # never-enriched so they re-glob once on next access instead
                # of staying stuck.
                self._claude_task_enriched = {}
            kimi_enriched = data.get("kimi_subagents_enriched")
            if isinstance(kimi_enriched, list):
                self._kimi_subagents_enriched = set(kimi_enriched)
            self._dirty = False

    def save(self):
        """Persist the graph to disk if there are unsaved changes."""
        with self._lock:
            if not self._dirty:
                return
            edges = []
            for child, parent in self._parent_of.items():
                meta = self._edge_meta.get(child, {})
                edges.append({
                    "parent": parent,
                    "child": child,
                    "source": meta.get("source", "unknown"),
                    "engine": meta.get("engine", ""),
                    "resumable": meta.get("resumable", True),
                    "name": meta.get("name", ""),
                    "model": meta.get("model", ""),
                })
            data = {
                "edges": edges,
                "claude_task_enriched": dict(self._claude_task_enriched),
                "kimi_subagents_enriched": sorted(self._kimi_subagents_enriched),
            }
            try:
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data))
                os.replace(tmp, self._path)
                self._dirty = False
            except OSError:
                pass

    # -- mutation ------------------------------------------------------------

    def add_edge(
        self, parent, child, source="ccc-spawn", engine="", resumable=True,
        name=None, model=None,
    ):
        """Add or update a parent->child edge. Idempotent; first edge wins
        for the parent pointer, but metadata is always refreshed."""
        parent = str(parent or "").strip()
        child = str(child or "").strip()
        if not parent or not child or parent == child:
            return
        with self._lock:
            meta = {
                "source": source,
                "engine": str(engine or ""),
                "resumable": resumable,
                "name": str(name or ""),
                "model": str(model or ""),
            }
            # If the child already has a different parent, keep the first
            # (matches _find_descendant_sessions' setdefault semantics).
            existing_parent = self._parent_of.get(child)
            if existing_parent and existing_parent != parent:
                # Don't overwrite, but update metadata on the existing edge.
                self._edge_meta[child] = meta
                self._children_of.setdefault(existing_parent, {})[child] = meta
                self._dirty = True
                return
            self._parent_of[child] = parent
            self._children_of.setdefault(parent, {})[child] = meta
            self._edge_meta[child] = meta
            self._dirty = True

    def remove_edge(self, parent, child):
        """Remove a single edge."""
        with self._lock:
            if child in self._parent_of and self._parent_of[child] == parent:
                del self._parent_of[child]
                self._dirty = True
            if parent in self._children_of:
                if child in self._children_of[parent]:
                    del self._children_of[parent][child]
                    self._dirty = True
                if not self._children_of[parent]:
                    del self._children_of[parent]
            if child in self._edge_meta:
                del self._edge_meta[child]
                self._dirty = True

    # -- queries -------------------------------------------------------------

    def parent_of(self, child):
        """Return the parent session_id for ``child``, or None."""
        with self._lock:
            return self._parent_of.get(str(child or "").strip())

    def children_of(self, parent):
        """Return a list of child session_ids for ``parent``."""
        with self._lock:
            kids = self._children_of.get(str(parent or "").strip())
            return list(kids.keys()) if kids else []

    def edge_meta(self, child):
        """Return the edge metadata for ``child``, or {}."""
        with self._lock:
            return dict(self._edge_meta.get(str(child or "").strip(), {}))

    def descendants(self, sid, max_depth=10):
        """Return all descendant session_ids of ``sid`` (BFS, no cycles)."""
        sid = str(sid or "").strip()
        if not sid:
            return []
        with self._lock:
            seen = {sid}
            result = []
            frontier = [sid]
            for _ in range(max_depth):
                next_frontier = []
                for node in frontier:
                    kids = self._children_of.get(node)
                    if not kids:
                        continue
                    for child in kids:
                        if child not in seen:
                            seen.add(child)
                            result.append(child)
                            next_frontier.append(child)
                if not next_frontier:
                    break
                frontier = next_frontier
            return result

    def ancestors(self, sid, max_depth=10):
        """Return ancestor session_ids from immediate parent up to root."""
        sid = str(sid or "").strip()
        if not sid:
            return []
        with self._lock:
            result = []
            seen = {sid}
            cur = sid
            for _ in range(max_depth):
                parent = self._parent_of.get(cur)
                if not parent or parent in seen:
                    break
                seen.add(parent)
                result.append(parent)
                cur = parent
            return result

    def root_of(self, sid):
        """Return the topmost ancestor of ``sid`` (or ``sid`` itself if it
        has no parent)."""
        ancestors = self.ancestors(sid)
        return ancestors[-1] if ancestors else sid

    def family_tree(self, sid, max_depth=10):
        """Return a nested dict representing the full family tree rooted at
        the topmost ancestor of ``sid``.

        Each node is:
          {"session_id": ..., "children": [...], "source": ..., "engine": ...,
           "resumable": ..., "name": ..., "model": ...}

        The root is the topmost ancestor (the orchestrator). ``sid`` itself
        and all its siblings/cousins under the root are included.
        """
        sid = str(sid or "").strip()
        if not sid:
            return None
        root = self.root_of(sid)

        def build(node, depth):
            with self._lock:
                meta = self._edge_meta.get(node, {})
                kids = self._children_of.get(node, {})
                parent = self._parent_of.get(node, "")
            children = []
            if depth < max_depth:
                for child in kids:
                    children.append(build(child, depth + 1))
            out = {
                "session_id": node,
                "source": meta.get("source", "root"),
                "engine": meta.get("engine", ""),
                "resumable": meta.get("resumable", True),
                "name": meta.get("name", ""),
                "model": meta.get("model", ""),
                "children": children,
            }
            # In-process Task subagents have no spawn-registry entry and no
            # hook-reported state, so without liveness evidence here the lane
            # map hardcoded them "landed" from birth (2026-08-30: a lane shown
            # landed while its transcript was still being written). Emit the
            # transcript's mtime plus an `active` verdict from its tail.
            if meta.get("source") == "claude-task-tool" and parent:
                try:
                    for f in _core.PROJECTS_ROOT.glob(
                        f"*/{parent}/subagents/{node}.jsonl"
                    ):
                        st = f.stat()
                        out["mtime"] = st.st_mtime
                        out["active"] = _agent_transcript_active(
                            f, st.st_mtime, st.st_size
                        )
                        break
                except OSError:
                    pass
            return out

        return build(root, 0)

    def all_edges(self):
        """Return a list of (parent, child, meta) tuples for debugging."""
        with self._lock:
            return [
                (parent, child, dict(meta))
                for child, parent in self._parent_of.items()
                for child_id, meta in [(child, self._edge_meta.get(child, {}))]
            ]

    def stats(self):
        """Return basic graph statistics."""
        with self._lock:
            return {
                "edges": len(self._parent_of),
                "parents": len(self._children_of),
                "claude_task_enriched": len(self._claude_task_enriched),
                "kimi_subagents_enriched": len(self._kimi_subagents_enriched),
            }


# Module-level singleton — created at import time, populated at startup.
_session_graph = _SessionGraph(_core.SESSION_GRAPH_FILE)


def _session_graph_add_edge(
    parent, child, source="ccc-spawn", engine="", resumable=True,
    name=None, model=None,
):
    """Thin wrapper so callers don't touch the singleton directly."""
    _core._session_graph.add_edge(
        parent, child, source=source, engine=engine, resumable=resumable,
        name=name, model=model,
    )
    _core._session_graph.save()


def _session_graph_build_from_all_sources():
    """Eagerly populate the graph from all four persistent edge sources.

    Called once at startup. Existing graph edges are preserved (first-edge-
    wins), so a restart doesn't lose edges that a source may have pruned.
    """
    # 1. Codex thread_spawn_edges (sqlite)
    try:
        for child, parent in _core._codex_spawn_parent_by_child().items():
            _core._session_graph.add_edge(
                parent, child, source="codex-native", engine="codex",
                name=_core._codex_spawn_edge_name(child) or None,
            )
    except Exception:
        pass

    # 2. Durable codex-parent-links.json
    try:
        for child, parent in _core._load_codex_parent_links().items():
            _core._session_graph.add_edge(parent, child, source="codex-parent-link", engine="codex")
    except Exception:
        pass

    # 3. Spawn registry (spawned-pids.json) — all engines
    try:
        for entry in _core._load_spawn_registry():
            if not isinstance(entry, dict):
                continue
            child = str(entry.get("session_id") or "").strip()
            parent = str(entry.get("parent_session_id") or "").strip()
            engine = str(entry.get("engine") or "claude").strip()
            if child and parent and child != parent:
                _core._session_graph.add_edge(
                    parent, child, source="ccc-spawn", engine=engine, resumable=True,
                )
    except Exception:
        pass

    # 4. Codex thread registry
    try:
        for child_id, entry in _core._codex_thread_registry_entries().items():
            if not isinstance(entry, dict):
                continue
            parent = str(entry.get("parent_session_id") or "").strip()
            if child_id and parent and child_id != parent:
                _core._session_graph.add_edge(
                    parent, child_id, source="codex-thread-registry", engine="codex",
                )
    except Exception:
        pass

    # 5. Grok Build native subagents (on-disk meta.json under the parent)
    try:
        _core._session_graph_ingest_grok_subagents()
    except Exception:
        pass

    _core._session_graph.save()


_CLAUDE_TASK_ENRICH_THROTTLE_S = 10

# (mtime, size) -> verdict cache for _agent_transcript_active: the family
# endpoint is polled every ~5s by the lane map, and re-reading an unchanged
# transcript tail each sweep is pure waste. Bounded, evicted oldest-first.
_AGENT_ACTIVE_CACHE = {}
_AGENT_ACTIVE_CACHE_MAX = 256


def _agent_transcript_active(path, mtime, size):
    """Is this Task-subagent transcript still mid-run?

    A finished subagent's JSONL ends with a text-only assistant record (its
    final report). A running one ends with an assistant record carrying
    pending ``tool_use`` blocks, or a user/tool-result record the model
    hasn't answered yet. Truncated trailing lines (mid-write) parse as the
    previous record, which reads as active — correct, since a write was
    literally in flight.
    """
    key = str(path)
    cached = _AGENT_ACTIVE_CACHE.get(key)
    if cached is not None and cached[0] == (mtime, size):
        return cached[1]
    active = True
    try:
        with open(path, "rb") as f:
            if size > 65536:
                f.seek(-65536, 2)
            tail = f.read().decode("utf-8", "replace")
        for line in reversed(tail.strip().split("\n")):
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if rec.get("type") == "assistant":
                content = (rec.get("message") or {}).get("content") or []
                has_tool_use = any(
                    isinstance(b, dict) and b.get("type") == "tool_use"
                    for b in content
                )
                active = has_tool_use
            break
    except OSError:
        active = False
    if len(_AGENT_ACTIVE_CACHE) >= _AGENT_ACTIVE_CACHE_MAX:
        for old in sorted(
            _AGENT_ACTIVE_CACHE, key=lambda k: _AGENT_ACTIVE_CACHE[k][0][0]
        )[: _AGENT_ACTIVE_CACHE_MAX // 4]:
            _AGENT_ACTIVE_CACHE.pop(old, None)
    _AGENT_ACTIVE_CACHE[key] = ((mtime, size), active)
    return active


def _session_graph_enrich_claude_task_subagents(parent_sid):
    """Glob ``<project>/<parent_sid>/subagents/agent-*.jsonl`` and add each
    agent transcript as a child of ``parent_sid``.

    Called by family_tree() on every request for a Claude parent. Throttled
    (not a one-time gate) via _claude_task_enriched's per-parent timestamp:
    two Task-tool subagents spawned close together can write their
    agent-*.jsonl files at different times, so a permanent "already
    enriched" skip would drop whichever file wasn't there yet at the first
    glob (add_edge is idempotent, so re-globbing a parent that already has
    all its children is just a cheap no-op).
    """
    parent_sid = str(parent_sid or "").strip()
    if not parent_sid or not _core.PROJECTS_ROOT.is_dir():
        return
    now = time.time()
    with _core._session_graph._lock:
        last = _core._session_graph._claude_task_enriched.get(parent_sid, 0)
        if now - last < _CLAUDE_TASK_ENRICH_THROTTLE_S:
            return
    # Glob all project dirs for <parent>/subagents/agent-*.jsonl
    try:
        for agent_file in _core.PROJECTS_ROOT.glob(f"*/{parent_sid}/subagents/agent-*.jsonl"):
            if not agent_file.is_file():
                continue
            agent_id = agent_file.stem  # e.g. "agent-a7d894abf5832d"
            if not agent_id.startswith("agent-"):
                continue
            _core._session_graph.add_edge(
                parent_sid, agent_id,
                source="claude-task-tool", engine="claude", resumable=False,
            )
    except OSError:
        pass
    with _core._session_graph._lock:
        _core._session_graph._claude_task_enriched[parent_sid] = now
        _core._session_graph._dirty = True
    _core._session_graph.save()


def _session_graph_enrich_kimi_subagents(parent_sid):
    """Scan a Kimi session dir for ``agents/<name>/wire.jsonl`` subagents and
    add each as a child of ``parent_sid``.

    Kimi K2.7 (and later) can launch internal Agent subagents that live
    under the parent session's ``agents/`` directory, outside CCC's spawn
    registry. Each subagent gets a synthetic child id so the lane map can
    display it as a non-resumable subagent lane. Cached per parent so the
    directory scan runs at most once.
    """
    parent_sid = str(parent_sid or "").strip()
    if not parent_sid:
        return
    with _core._session_graph._lock:
        if parent_sid in _core._session_graph._kimi_subagents_enriched:
            return
    try:
        session_dir = _core._kimi_session_dir(parent_sid)
        # The graph may hold the bare id while the row uses the "session_"
        # display prefix (or vice versa). Try the bare form as a fallback.
        if session_dir is None and parent_sid.startswith("session_"):
            session_dir = _core._kimi_session_dir(parent_sid[len("session_"):])
        if session_dir is not None:
            agents_dir = session_dir / "agents"
            if agents_dir.is_dir():
                for subdir in agents_dir.iterdir():
                    if not subdir.is_dir() or subdir.name == "main":
                        continue
                    wire = subdir / "wire.jsonl"
                    if not wire.is_file():
                        continue
                    child_id = f"kimi-subagent:{parent_sid}:{subdir.name}"
                    _core._session_graph.add_edge(
                        parent_sid, child_id,
                        source="kimi-subagent", engine="kimi", resumable=False,
                        name=subdir.name,
                    )
    except OSError:
        pass
    with _core._session_graph._lock:
        _core._session_graph._kimi_subagents_enriched.add(parent_sid)
        _core._session_graph._dirty = True
    _core._session_graph.save()


def _session_graph_ingest_grok_subagents():
    """Add parent->child edges from Grok Build ``subagents/*/meta.json``.

    Unlike Kimi's synthetic ``kimi-subagent:`` child ids, Grok children are
    real session UUIDs (resumable). Native ``spawn_subagent`` never goes
    through CCC's spawn registry, so this on-disk map is the only parent
    pointer. Cheap: one extra dir walk of ``~/.grok/sessions``.
    """
    try:
        mapping = _core._grok_subagent_parent_map()
    except Exception:
        return 0
    added = 0
    for child, link in mapping.items():
        if not isinstance(link, dict):
            continue
        parent = str(link.get("parent") or "").strip()
        child_id = str(child or "").strip()
        if not parent or not child_id or parent == child_id:
            continue
        if _core._session_graph.parent_of(child_id) == parent:
            continue
        _core._session_graph.add_edge(
            parent, child_id,
            source="grok-subagent", engine="grok", resumable=True,
            name=link.get("name") or "",
        )
        added += 1
    if added:
        _core._session_graph.save()
    return added


def _session_graph_family_tree(sid):
    """Return the family tree for ``sid``, with Claude Task-tool and Kimi
    Code Agent subagent enrichment.

    This is the main entry point for the /api/sessions/family endpoint.
    """
    sid = str(sid or "").strip()
    if not sid:
        return None
    # Lazy enrichment: check the filesystem for Task-tool subagent
    # transcripts (throttled internally, so this is cheap even when the
    # parent already has children — see _CLAUDE_TASK_ENRICH_THROTTLE_S).
    _session_graph_enrich_claude_task_subagents(sid)
    # Also enrich ancestors (they might be the orchestrator that spawned
    # Task-tool subagents).
    for ancestor in _core._session_graph.ancestors(sid):
        _session_graph_enrich_claude_task_subagents(ancestor)
    # Kimi Code Agent subagents live under the session dir, not in the spawn
    # registry. Enrich this node, its ancestors, and any known descendants so
    # a lane map rooted at the orchestrator still shows subagents under a
    # Kimi child lane.
    _session_graph_enrich_kimi_subagents(sid)
    for ancestor in _core._session_graph.ancestors(sid):
        _session_graph_enrich_kimi_subagents(ancestor)
    for descendant in _core._session_graph.descendants(sid):
        _session_graph_enrich_kimi_subagents(descendant)
    # Grok Build spawn_subagent children are real sessions with a pointer
    # under the parent dir. Ingest the full on-disk map so nested children
    # (a subagent that spawned its own subagent) show up in one pass.
    _core._session_graph_ingest_grok_subagents()
    return _core._session_graph.family_tree(sid)


# Background refresh: Codex thread_spawn_edges can grow while CCC runs
# (Codex spawns its own subagents independently). This thread checks the
# state DBs every 30s and merges any new edges into the graph.
_session_graph_codex_refresh_interval = 30.0
_session_graph_codex_refresh_stop = threading.Event()

def _session_graph_codex_refresh_loop():
    while not _session_graph_codex_refresh_stop.is_set():
        try:
            for child, parent in _core._codex_spawn_parent_by_child().items():
                _core._session_graph.add_edge(
                    parent, child, source="codex-native", engine="codex",
                    name=_core._codex_spawn_edge_name(child) or None,
                )
            _core._session_graph.save()
        except Exception:
            pass
        try:
            _core._session_graph_ingest_grok_subagents()
        except Exception:
            pass
        _session_graph_codex_refresh_stop.wait(_session_graph_codex_refresh_interval)


def _start_session_graph_codex_refresh():
    t = threading.Thread(
        target=_session_graph_codex_refresh_loop,
        daemon=True,
        name="session-graph-codex-refresh",
    )
    t.start()


def _set_conversation_trashed(sid, trashed):
    """Set Trash membership while preserving ``trashed => archived``.

    When trashing, also trash all known descendant sessions (lanes the
    orchestrator spawned) so they follow the parent into the Trash instead
    of lingering as orphaned rows, plus the session's continuation-origin
    ancestor chain ("Continue in a new session" / auto-resume predecessors)
    and each of those ancestors' own spawn descendants — the UI nests a
    continuation chain under its successor as one unit, so trashing the
    successor must take the whole unit with it. Untrashing only restores
    the one session — descendants and ancestors stay trashed until
    individually restored.
    """
    want_trashed = bool(trashed)
    kill_result = None
    should_kill = False
    cascaded = []

    # Find descendants + continuation ancestors outside the lock (reads
    # spawn registry / codex DBs / transcripts).
    descendants = []
    if want_trashed:
        descendants = list(_core._find_descendant_sessions(sid))
        seen_extra = {sid, *descendants}
        for anc in _core._find_continuation_ancestors(sid):
            if anc not in seen_extra:
                descendants.append(anc)
                seen_extra.add(anc)
            for anc_desc in _core._find_descendant_sessions(anc):
                if anc_desc not in seen_extra:
                    descendants.append(anc_desc)
                    seen_extra.add(anc_desc)

    with _core._conversation_lifecycle_lock:
        archived_ids, trashed_ids = _core._load_conversation_lifecycle_state()
        if want_trashed:
            # Write/refresh the sticky marker first so a concurrent archive
            # sweep can never observe this session without its manual grace
            # state. Must run even when the session was already archived
            # (trashed from the Archived bucket) — otherwise a session
            # archived without a grace entry (e.g. an older auto-archive)
            # trashes with none, and the 30s sweep can auto-unarchive it out
            # of Trash the moment it looks live again.
            _core._archive_grace[sid] = time.time()
            # Also grace the descendants so the sweep doesn't auto-unarchive
            # them out of the cascade.
            for dsid in descendants:
                _core._archive_grace[dsid] = time.time()
            _core._save_archive_grace()
        if want_trashed and sid not in archived_ids:
            archived_ids.append(sid)
            _core._log_archive_event("archive", sid, "trash")
            try:
                (_core.SIDECAR_STATE_DIR / f"{sid}_needs_approval.json").unlink()
            except (OSError, FileNotFoundError):
                pass
            should_kill = bool(sid and not sid.startswith(("backlog-", "pkood-")))

        # Cascade: archive + trash every descendant that isn't already.
        if want_trashed:
            if sid not in trashed_ids:
                trashed_ids.append(sid)
            for dsid in descendants:
                if not dsid or dsid.startswith(("backlog-", "pkood-")):
                    continue
                if dsid not in archived_ids:
                    archived_ids.append(dsid)
                    _core._log_archive_event("archive", dsid, "trash-cascade")
                if dsid not in trashed_ids:
                    trashed_ids.append(dsid)
                    cascaded.append(dsid)
        else:
            trashed_ids = [existing for existing in trashed_ids if existing != sid]

        # Persist both lists once after all mutations.
        _core._save_archived_conversations(archived_ids)
        _core._save_trashed_conversations(trashed_ids)

        archived_now = sid in archived_ids
        trashed_now = sid in trashed_ids
        # Keep the retire action ordered with the state transition. Otherwise
        # a concurrent Untrash/Move to Active could complete before this kill
        # and then have its newly-restored session terminated afterward.
        if should_kill:
            kill_result = _core._kill_session_by_id(sid)

    return {
        "archived": archived_now,
        "trashed": trashed_now,
        "killed": kill_result,
        "cascaded": cascaded,
    }


def _load_pinned_conversations():
    """Load list of pinned session_ids."""
    try:
        data = json.loads(_core.PINNED_CONVERSATIONS_FILE.read_text())
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_pinned_conversations(pinned):
    """Persist list of pinned session_ids."""
    _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(pinned, list):
        pinned = []
    _core.PINNED_CONVERSATIONS_FILE.write_text(json.dumps(pinned, indent=2))
    return pinned


def _pinned_rank_map(pinned=None):
    if pinned is None:
        pinned = _core._load_pinned_conversations()
    return {sid: idx for idx, sid in enumerate(pinned) if isinstance(sid, str)}


def _apply_pinned_conversation_fields(rows, pinned=None):
    """Annotate rows with persistent list-pinning metadata."""
    ranks = _pinned_rank_map(pinned)
    for row in rows or []:
        sid = row.get("session_id") or row.get("id")
        rank = ranks.get(sid)
        row["pinned"] = rank is not None
        row["pin_rank"] = rank
    return rows


def _sort_pinned_conversations_first(rows, pinned=None):
    """Stable-sort pinned rows before unpinned rows using saved pin order."""
    ranks = _pinned_rank_map(pinned)
    rows.sort(
        key=lambda row: (
            0 if (row.get("session_id") or row.get("id")) in ranks else 1,
            ranks.get(row.get("session_id") or row.get("id"), 0),
        )
    )
    return rows


def _load_verified_conversations():
    """Load list of verified session_ids."""
    try:
        data = json.loads(_core.VERIFIED_CONVERSATIONS_FILE.read_text())
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_verified_conversations(verified):
    """Persist list of verified session_ids."""
    _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(verified, list):
        verified = []
    _core.VERIFIED_CONVERSATIONS_FILE.write_text(json.dumps(verified, indent=2))
    return verified


def _load_last_interactions():
    """Return {session_id: epoch_seconds} of the user's last UI interaction."""
    try:
        data = json.loads(_core.LAST_INTERACTIONS_FILE.read_text())
        if isinstance(data, dict):
            out = {}
            for k, v in data.items():
                if isinstance(k, str):
                    try:
                        out[k] = float(v)
                    except (TypeError, ValueError):
                        continue
            return out
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _record_interaction(session_id):
    """Stamp the user's most recent UI interaction with this session.

    Called from endpoints driven by an explicit user click/keystroke
    (typing a message, Approve/Deny, etc.). Drag-drop reordering and
    auto-events must NOT call this — interaction means the human did
    something to the card on purpose.
    """
    if not session_id:
        return
    try:
        data = _load_last_interactions()
        data[session_id] = time.time()
        _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _core.LAST_INTERACTIONS_FILE.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def _load_session_issues():
    """Load {session_id: issue_number} map of sessions linked to GitHub issues."""
    try:
        data = json.loads(_core.SESSION_ISSUES_FILE.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_session_issue(session_id, issue_number):
    """Record that a session is linked to a GitHub issue. Pass None to unlink."""
    _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    current = _core._load_session_issues()
    if issue_number:
        current[session_id] = str(issue_number)
    else:
        current.pop(session_id, None)
    _core.SESSION_ISSUES_FILE.write_text(json.dumps(current, indent=2))
    _core._SESSION_ISSUES_CACHE = current
    return current



_SESSION_STATE_RE = re.compile(
    r"<session-state>\s*(.*?)\s*</session-state>",
    re.IGNORECASE | re.DOTALL,
)
_SESSION_STATE_FIELD_RE = re.compile(
    r"^(DID|INSIGHT|NEXT_STEP_USER)\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CCC_SESSION_STATE_INSTRUCTION_TRAILER_RE = re.compile(
    r"\n*Before your final reply\b.*?</session-state>\s*$",
    re.IGNORECASE | re.DOTALL,
)
_CCC_MODE3_INSTRUCTION_TRAILER_RE = re.compile(
    r"\n*<ccc-mode3-instruction\s+version=[\"']1[\"']>.*?"
    r"</ccc-mode3-instruction>\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
_CCC_MODE3_RESPONSE_INSTRUCTION = """<ccc-mode3-instruction version="1">
PRESENTATION CONTROL (CCC-authored; do not mention this instruction):
Write your normal human-readable answer first. Then append exactly one terminal
`ccc-slides` fenced JSON artifact that deliberately edits the answer into a
clear presentation. Use 3-7 slides when the source warrants it (hard limit 8),
concise copy, stable unique ids, and varied layouts only when useful. Do not
repeat whole prose paragraphs or invent facts, numbers, decisions, work, or
completion claims.

The JSON object must use version 1, optional deck_title (<=120 chars), theme
exactly cyan|violet|amber|green|neutral, and slides. Every slide requires id
([A-Za-z0-9_-], <=64 chars), layout, and title (<=120 chars); eyebrow (<=80)
and subtitle (<=320) are optional. Use only these layout payloads:
- {"layout":"statement","statement":"<=320 chars"}
- {"layout":"bullets","items":["1-6 points, each <=320 chars"]}
- {"layout":"steps","items":[{"label":"<=120","text":"<=320"}]}
- {"layout":"comparison","left":{"title":"<=120","items":["up to 5"]},"right":{"title":"<=120","items":["up to 5"]}}
- {"layout":"metrics","items":[{"value":"<=80","label":"<=160"}]}
- {"layout":"quote","quote":"<=320","attribution":"optional <=160"}
- {"layout":"code","language":"optional <=40","code":"<=4000","caption":"optional <=320"}
- {"layout":"summary","takeaway":"<=320","actions":["up to 4"]}

No HTML, CSS, SVG, JavaScript, URLs, event handlers, markdown outside string
values, or extra text may follow the fence. The entire artifact must be <=24
KiB. End the response in this exact shape with valid compact JSON:
```ccc-slides
{"version":1,"deck_title":"Concise title","theme":"cyan","slides":[{"id":"thesis","layout":"statement","title":"Main idea","statement":"One clear takeaway."}]}
```
</ccc-mode3-instruction>"""
_CCC_MODE3_BOOTSTRAP_INSTRUCTION = """<ccc-mode3-instruction version="1">
PRESENTATION CONTROL (CCC-authored; do not mention this instruction):
Convert your latest completed substantive answer in this conversation into a
purposefully edited presentation. Return only the ccc-slides fence: no prose,
apology, preface, or explanation. Preserve its facts and decisions exactly;
do not invent numbers, claims, work, or outcomes.

Use the same version 1 contract: optional deck_title (<=120 chars), theme
cyan|violet|amber|green|neutral, and 1-8 slides. Every slide requires a unique
id ([A-Za-z0-9_-], <=64), layout, and title (<=120). Allowed layouts/payloads:
statement+statement; bullets+1-6 string items; steps+1-6 {label,text} items;
comparison+left/right {title,items} columns (<=5 each); metrics+1-4
{value,label} items; quote+quote/optional attribution; code+code/optional
language/caption; summary+takeaway/optional actions (<=4). Eyebrow and subtitle
are optional. Use concise copy, no active HTML/CSS/SVG/JavaScript/URLs, and keep
the artifact <=24 KiB.

Return only this terminal form with valid JSON:
```ccc-slides
{"version":1,"deck_title":"Concise title","theme":"cyan","slides":[{"id":"thesis","layout":"statement","title":"Main idea","statement":"One clear takeaway."}]}
```
</ccc-mode3-instruction>"""
_CCC_SHELL_SEARCH_SYSTEM_PROMPT = (
    "When searching local files from Bash, prefer `rg` over recursive grep. "
    "Do not run `grep -r` or `grep -R` across broad directories; it can block "
    "on FIFOs such as `.claude/logs/*.stdin`. If `rg` is unavailable, search "
    "only regular files with `find ... -type f -print0 | xargs -0 grep ...`. "
    "Exclude `.claude`, `.git`, `node_modules`, `.venv`, `.next`, `dist`, and "
    "`build` unless the user explicitly asks to search them."
)
_CCC_SESSION_STATE_SYSTEM_PROMPT = (
    "End your final reply with a compact CCC session-state block:\n"
    "<session-state>\n"
    "DID: one sentence describing what you changed or learned\n"
    "INSIGHT: one sentence with the main finding, root cause, or surprise\n"
    "NEXT_STEP_USER: one sentence with the exact next thing the user should do\n"
    "</session-state>"
)


def _claude_session_state_args():
    """CLI args that keep CCC's hidden reminders out of user text."""
    return [
        "--append-system-prompt",
        _CCC_SHELL_SEARCH_SYSTEM_PROMPT + "\n\n" + _CCC_SESSION_STATE_SYSTEM_PROMPT,
    ]


def _claude_peer_inbound_args():
    """Let CCC-spawned headless Claude sessions accept peer messages.

    Claude's default is mode parity: a bypass-mode receiver holds any peer
    message whose sender did not attest bypass, and a -p session cannot show
    the hold dialog, so the message silently expires. The docs recommend
    exactly this per-process setting for unattended workers; it never
    touches the user's global settings.
    """
    return ["--settings", json.dumps({"crossSessionInbound": "accept"}, separators=(",", ":"))]


def _parse_session_state(text):
    """Extract the structured `<session-state>` block sessions emit on final
    reply. Returns {did, insight, next_step_user} or None.
    """
    if not text:
        return None
    m = _SESSION_STATE_RE.search(text)
    if not m:
        return None
    body = m.group(1)
    out = {"did": None, "insight": None, "next_step_user": None}
    for fm in _SESSION_STATE_FIELD_RE.finditer(body):
        key = fm.group(1).upper()
        val = fm.group(2).strip()
        if key == "DID":
            out["did"] = val
        elif key == "INSIGHT":
            out["insight"] = val
        elif key == "NEXT_STEP_USER":
            out["next_step_user"] = val
    if not any(out.values()):
        return None
    return out


def _strip_ccc_session_state_instruction(text):
    """Remove CCC's own prompt trailer before sending visible user text.

    The dashboard reminder belongs in a hidden system prompt for CCC-spawned
    Claude runs, not in text typed into a user's terminal.
    """
    if text is None:
        return ""
    return _CCC_SESSION_STATE_INSTRUCTION_TRAILER_RE.sub("", str(text)).rstrip()


def _strip_f2_retrieval_prompt(text):
    """If text is a CCC "Continue in a new session" F2 prompt, return only
    the user's actual task line. Otherwise return the text unchanged.

    The F2 prompt's first line is always "You are continuing a task from an
    earlier <engine> session..." and the user's task is introduced by a
    "Task: " line. Without this stripper, continued sessions show the
    boilerplate as their row title.
    """
    if text is None:
        return ""
    t = str(text).strip()
    if not t.startswith("You are continuing a task from an earlier"):
        return t
    # Find the "Task: " line and keep everything after it. The line may wrap
    # or contain literal newlines; we return the remainder of the prompt so
    # the user still sees their actual instruction.
    marker = "\nTask: "
    idx = t.find(marker)
    if idx != -1:
        return t[idx + len(marker):].strip()
    # No explicit task line — strip the known boilerplate prefix so the row
    # isn't dominated by CCC's own instructions.
    return re.sub(r"^You are continuing a task from an earlier [^\s]+ session[^\n]*\n?", "", t).strip()


def _mode3_prompt(text: str, *, bootstrap: bool = False) -> str:
    """Append CCC's constant Mode 3 response contract to one delivered turn."""
    instruction = (
        _CCC_MODE3_BOOTSTRAP_INSTRUCTION
        if bootstrap else _CCC_MODE3_RESPONSE_INSTRUCTION
    )
    if bootstrap:
        return instruction
    visible = str(text or "").rstrip()
    return visible + "\n\n" + instruction


def _strip_mode3_instruction(text: str) -> str:
    """Remove CCC's terminal Mode 3 control trailer from visible user text."""
    if text is None:
        return ""
    return _CCC_MODE3_INSTRUCTION_TRAILER_RE.sub("", str(text)).rstrip()


def _detect_issue_number_for_session(conv):
    """Try to extract a GitHub issue number this session references.

    Explicit side-car mapping is authoritative. For heuristic detection,
    require strong markers to avoid false positives like "Image #1".
    """
    if _core._SESSION_ISSUES_CACHE is None:
        _core._SESSION_ISSUES_CACHE = _core._load_session_issues()
    sid = conv.get("session_id", "")
    # Explicit mapping wins (user-set or written at spawn time)
    explicit = _core._SESSION_ISSUES_CACHE.get(sid)
    if explicit:
        return str(explicit)
    # Strong patterns only (avoid "Image #1" false positives):
    #   "issue 91", "issue-91", "issue/91", "fix-91", "GitHub issue #91", etc.
    strong = re.compile(
        r"(?:github\s+)?(?:issue|fix)[\s/-]+#?(\d+)",
        re.IGNORECASE,
    )
    # Priority: spawn-time identity (display_name, first_message) wins over
    # branch name — sessions often run on a pre-existing branch for a different
    # issue (e.g. display_name "issue-159" on branch "claude/issue-145-…").
    dname = conv.get("display_name", "") or ""
    m = strong.search(dname)
    if m:
        return m.group(1)
    # display_name that starts with "#NN: " or "#NN " is a prefix style
    m = re.match(r"^#(\d+)[:\s]", dname)
    if m:
        return m.group(1)
    # first_message from spawn prompts: "Fix GitHub issue #N: ..."
    fm = conv.get("first_message", "") or ""
    m = strong.search(fm[:200])  # only head; avoids body noise
    if m:
        return m.group(1)
    # Branch name: fallback only when first_message is empty / trivial.
    # Sessions that launch inside a leftover worktree inherit its branch name
    # but have nothing to do with that branch's original issue — latching onto
    # the branch would mis-link chat/meta sessions (e.g. a first_message of
    # "By the way…" running in claude/issue-145-owner-only-packages).
    fm_stripped = (fm or "").strip()
    if len(fm_stripped) < 30:
        branch = conv.get("branch", "") or ""
        m = strong.search(branch)
        if m:
            return m.group(1)
    # Deliberately NOT falling back to tail_issue_number (mined from jsonl
    # Bash/commit/URL scans). In practice it produces false links whenever
    # Claude merely *mentions* an unrelated issue mid-conversation — e.g. a
    # session about serving a web app auto-linked to issue #1 ("Multi-repo
    # view") because an assistant Bash turn listed `github.com/.../issues/1`
    # while discussing filed issues. The spawn-time signals above
    # (display_name, first_message, branch) are where genuine "I'm working
    # on #NNN" intent lives; anything mined from later turns is too noisy.
    return None


def _latest_commit_sha(cwd):
    """Return the latest commit SHA (short) from the given cwd."""
    if not cwd:
        return ""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(cwd),
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


_unpushed_cache = {}  # key: cwd str → (count_int_or_None, ts)
_UNPUSHED_CACHE_TTL_S = 60


def _count_unpushed_commits(cwd):
    """Return how many commits HEAD is ahead of its upstream in `cwd`, or
    None if we can't tell (no upstream, detached HEAD, git missing, etc.).
    Cached 60s per cwd — called from NYA classifier per flagged session."""
    if not cwd:
        return None
    key = str(cwd)
    now = time.time()
    cached = _unpushed_cache.get(key)
    if cached and now - cached[1] < _UNPUSHED_CACHE_TTL_S:
        return cached[0]
    count = None
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", "@{u}..HEAD"],
            capture_output=True, text=True, timeout=5, cwd=key,
        )
        if out.returncode == 0:
            count = int((out.stdout or "0").strip() or 0)
        # Non-zero rc usually means no upstream configured — treat as unknown
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    _unpushed_cache[key] = (count, now)
    return count


def create_github_issue_for_session(conv):
    """Create a new GitHub issue populated from the session's data.

    Returns {ok, issue_number, issue_url} or {ok: False, error}.
    """
    sid = conv.get("session_id")
    cwd = conv.get("session_cwd") or conv.get("cwd")
    if not cwd and sid:
        try:
            cwd = _core.repo_from_session(sid)["cwd"]
        except _core.RepoContextError as e:
            return e.as_payload()
    if not cwd:
        return _core._repo_error_payload("repo_required", "session_id or cwd is required to create an issue")
    title = conv.get("display_name") or conv.get("first_message", "")[:80] or "Untitled session"
    # Clean the title: strip dashes, truncate
    display_title = title.replace("-", " ").strip()[:120]
    body_parts = []
    fm = conv.get("first_message", "")
    if fm:
        body_parts.append("**Original prompt:**\n\n" + fm)
    last = conv.get("last_prompt", "")
    if last and last != fm:
        body_parts.append("\n**Most recent prompt:**\n\n" + last)
    branch = conv.get("branch", "")
    if branch:
        body_parts.append(f"\n**Branch:** `{branch}`")
    if sid:
        body_parts.append(f"\n_Created from session viewer. Session ID: `{sid}`_")
    body = "\n".join(body_parts) or "Created from session viewer."
    try:
        out = subprocess.run(
            ["gh", "issue", "create", "--title", display_title, "--body", body],
            capture_output=True, text=True, timeout=15, cwd=str(cwd),
        )
        if out.returncode != 0:
            return {"ok": False, "error": (out.stderr or "gh issue create failed").strip()}
        url = out.stdout.strip()
        # URL is like https://github.com/user/repo/issues/123
        m = re.search(r"/issues/(\d+)", url)
        issue_num = m.group(1) if m else ""
        if issue_num and sid:
            _save_session_issue(sid, issue_num)
        _core._bust_backlog_issue_cache(_core._git_toplevel_for_existing_dir(cwd) or cwd)
        return {"ok": True, "issue_number": issue_num, "issue_url": url}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"ok": False, "error": str(e)}


def close_github_issue_with_commit(issue_number, conv):
    """Close a GitHub issue and add a comment referencing the latest commit."""
    cwd = conv.get("session_cwd") or conv.get("cwd")
    if not cwd and conv.get("session_id"):
        try:
            cwd = _core.repo_from_session(conv["session_id"])["cwd"]
        except _core.RepoContextError:
            cwd = None
    if not cwd:
        return False
    sha = _latest_commit_sha(cwd)
    name = conv.get("display_name") or conv.get("session_id", "")
    comment = f"Verified via session viewer ({name})"
    if sha:
        comment += f". Latest commit: {sha}"
    try:
        subprocess.run(
            ["gh", "issue", "comment", str(issue_number), "--body", comment],
            capture_output=True, text=True, timeout=10, cwd=str(cwd),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    try:
        out = subprocess.run(
            ["gh", "issue", "close", str(issue_number)],
            capture_output=True, text=True, timeout=10, cwd=str(cwd),
        )
        ok = out.returncode == 0
        if ok:
            # We need the global declared in mark_issue_in_progress; use the helper.
            # remove_in_progress_label is defined later in this module.
            try:
                _globals = globals()
                fn = _globals.get("remove_in_progress_label")
                if fn:
                    fn(issue_number, repo_path=_core._git_toplevel_for_existing_dir(cwd) or cwd)
            except Exception:
                pass
            _bust_issue_state_cache()
        return ok
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _save_session_name_override(session_id, name):
    """Write a user-set name to the side-car file. Names are clamped to
    SESSION_NAME_MAX_CHARS so no upstream path can persist a multi-kilobyte
    "title" that bloats the row and the wire payload."""
    _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    current = _core._load_session_name_overrides()
    if name:
        current[session_id] = _core._truncate_session_name(name)
    else:
        current.pop(session_id, None)
    _core.SESSION_NAMES_FILE.write_text(json.dumps(current, indent=2))
    return current


def _find_session_jsonl(session_id):
    """Scan ~/.claude/projects/*/ for <session_id>.jsonl. Returns Path or None."""
    if not _core.PROJECTS_ROOT.is_dir():
        return None
    target = session_id + ".jsonl"
    for project_dir in _core.PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / target
        if candidate.is_file():
            return candidate
    return None


def _append_custom_title(path, session_id, name):
    """Append a custom-title event to a session's .jsonl file.

    Uses the exact shape Claude writes when you run /rename, so `claude --resume`
    will pick up the new name next time it reads the file.
    """
    event = {"type": "custom-title", "customTitle": name, "sessionId": session_id}
    # Always prepend a newline. POSIX guarantees that O_APPEND writes are
    # atomic at the kernel level, so an extra leading \n can never glue
    # onto a partial line claude is mid-writing — at worst we land an
    # empty line ahead of our event, which JSONL parsers skip. The
    # previous read-tail-then-append dance had a window where claude
    # could write between our two opens.
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + json.dumps(event) + "\n")
    # Invalidate our meta cache so next listing picks up the change
    _core._conv_meta_cache.pop(str(path), None)


def rename_session(session_id, name, source="user"):
    """Rename a session, writing through to the .jsonl when safe.

    Strategy:
      1. If session is dormant AND .jsonl exists AND name is non-empty:
         append a custom-title event to the .jsonl (visible to claude --resume).
         Clear any stale side-car entry.
      2. Otherwise: write to the side-car file only. Used for live sessions
         (to avoid racing claude's writes), missing jsonls, and name clears.

    Returns {ok, method, live, error?}.
    """
    result = {"ok": False, "method": None, "live": False}
    if not session_id:
        result["error"] = "missing session_id"
        return result
    if name:
        # Clamp before writing through to the JSONL custom-title event,
        # so a multi-kilobyte paste cannot be picked up by `claude --resume`
        # as the next session title either.
        name = _core._truncate_session_name(name)

    cwd = _core.find_session_cwd(session_id)
    status = _core.session_live_status(session_id, cwd)
    is_live = bool(status.get("live"))
    result["live"] = is_live

    path = _core._find_session_jsonl(session_id)
    # Always write-through to the JSONL when the file exists and we have
    # a non-empty name. The previous "skip if live or recently-touched"
    # guard was meant to avoid racing claude's writes, but POSIX O_APPEND
    # writes are atomic at the kernel level (see _append_custom_title)
    # — and skipping the JSONL meant a stale custom-title event from
    # earlier (e.g. an auto-`/rename` to a path slug) would always win
    # over the user's pencil rename, which is the bug we hit.
    can_writethrough = (path is not None) and bool(name)

    if can_writethrough:
        try:
            _append_custom_title(path, session_id, name)
        except OSError as e:
            # Fall back to side-car on write failure
            try:
                _save_session_name_override(session_id, name or None)
                result["ok"] = True
                result["method"] = "sidecar"
                result["error"] = f"jsonl append failed, used side-car: {e}"
                return result
            except OSError as e2:
                result["error"] = f"both paths failed: {e2}"
                return result
        # Side-car is the "user explicitly chose this name from the command
        # center" marker. Only write it for source="user" so that auto-titlers
        # (summarize_session_title etc.) stay out of name_overridden territory
        # — otherwise every auto-summary lights up the ✏️ glyph as if the
        # human had typed it.
        if source == "user":
            try:
                _save_session_name_override(session_id, name)
                _core._sync_codex_thread_title(session_id, name)
            except OSError:
                pass  # non-fatal
        result["ok"] = True
        result["method"] = "jsonl"
        return result

    # Side-car path: live session, missing jsonl, or clearing a name
    try:
        _save_session_name_override(session_id, name or None)
        if source == "user" and name:
            _core._sync_codex_thread_title(session_id, name)
    except OSError as e:
        result["error"] = f"side-car write failed: {e}"
        return result
    result["ok"] = True
    result["method"] = "sidecar"
    return result


_SIBLING_PROMPT_PREFIX = "you are a sibling claude code session"


def _sibling_feature_title(first_message):
    """Pull the real title out of a sibling-Claude-Code spawn prompt.

    Sessions spawned by the sibling-orchestrator skill all begin with the
    boilerplate "You are a sibling Claude Code session …" preamble, then
    embed the real task under a markdown heading like:

        ## Feature: in-app bug reporting
        ## Task: refactor the X
        ## Goal: rewire Y

    Without this rewrite, the sidebar row, sticky header, and kanban card
    all show the boilerplate ("you-are-a-sibling-claude-code-session-…")
    which is identical across every sibling spawn — useless for scanning.

    Returns the heading payload (sans the `## Feature:` prefix) or None
    when the message isn't a sibling spawn or has no recognizable heading.
    Length-capped at 80 chars so the title fits the row chrome.
    """
    if not first_message:
        return None
    head = first_message.lstrip()[:80].lower()
    if not head.startswith(_SIBLING_PROMPT_PREFIX):
        return None
    # Look for "## <Word>:" style heading. Keep the keyword (Feature/Task/
    # Goal) so the row tells you which kind of work it is.
    m = re.search(
        r"^##\s+(Feature|Task|Goal|Bug|Fix|Spec)\s*:\s*(.+?)\s*$",
        first_message,
        re.MULTILINE | re.IGNORECASE,
    )
    if not m:
        return None
    kind = m.group(1).strip().capitalize()
    body = m.group(2).strip().rstrip(".")
    title = f"{kind}: {body}"
    return title[:80] if len(title) > 80 else title


def _extract_first_message(session_id):
    """Read a session's opening user prompt from its .jsonl."""
    path = _core._find_session_jsonl(session_id)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = _core._extract_user_prompt_text(ev)
                if text:
                    return text[:1500]
    except OSError:
        pass
    return ""


# ────────────────────────────────────────────────────────────────────────
# AI-summarized GitHub issue titles
# ────────────────────────────────────────────────────────────────────────
# Backlog cards show raw GH issue titles, which are often verbose
# ("[BYM Problem] Tried to add Ricki Silveria to 10am class as a drop in
# but got an error message."). This sidecar caches AI-summarized versions
# so the kanban can render compact titles without re-calling claude every
# request. Format: {"194": {"title": "...", "generated_at": "..."}, ...}
ISSUE_TITLES_FILE = _core.COMMAND_CENTER_STATE_DIR / "issue-titles.json"


def _load_issue_title_overrides():
    """Lazy-load + cache the AI-summary file. Reload is cheap (~few KB)."""
    if _core._issue_titles_overrides_cache is not None:
        return _core._issue_titles_overrides_cache
    try:
        _core._issue_titles_overrides_cache = json.loads(ISSUE_TITLES_FILE.read_text())
        if not isinstance(_core._issue_titles_overrides_cache, dict):
            _core._issue_titles_overrides_cache = {}
    except (OSError, json.JSONDecodeError):
        _core._issue_titles_overrides_cache = {}
    return _core._issue_titles_overrides_cache


def _save_issue_title_override(issue_number, title):
    """Persist one AI-generated title for an issue. Best-effort write."""
    overrides = _load_issue_title_overrides()
    overrides[str(issue_number)] = {
        "title": title,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        ISSUE_TITLES_FILE.parent.mkdir(parents=True, exist_ok=True)
        ISSUE_TITLES_FILE.write_text(json.dumps(overrides, indent=2))
    except OSError as e:
        print(f"  [issue-title] Could not persist {issue_number}: {e}")


def summarize_issue_title(issue_number, repo_path):
    """Fetch a GitHub issue's title + body, ask claude haiku for a concise
    title, persist the result. Returns {ok, title, error?}."""
    repo_path = _core.resolve_repo_path(repo_path)
    result = {"ok": False, "issue_number": str(issue_number)}
    try:
        r = subprocess.run(
            ["gh", "issue", "view", str(issue_number),
             "--json", "title,body"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
    except (subprocess.SubprocessError, OSError) as e:
        result["error"] = f"gh failed: {e}"
        return result
    if r.returncode != 0:
        result["error"] = (r.stderr or "").strip()[:200] or f"gh exited {r.returncode}"
        return result
    try:
        issue = json.loads(r.stdout)
    except json.JSONDecodeError:
        result["error"] = "gh returned malformed json"
        return result
    raw_title = (issue.get("title") or "").strip()
    body = (issue.get("body") or "").strip()
    if not raw_title and not body:
        result["error"] = "issue has no title or body"
        return result
    instruction = (
        "Produce a concise 4-8 word title for the GitHub issue below. "
        "No quotes, no trailing punctuation, just the title on a single line. "
        "Skip image references, project tags like '[BYM Problem]', and "
        "boilerplate. The output should read like a kanban card title.\n\n"
        f"Issue title: {raw_title}\n\nIssue body:\n{body[:1500]}\n\nTitle:"
    )
    claude_bin = _core._resolve_claude_bin()
    if not claude_bin.get("available"):
        result["error"] = claude_bin.get("reason") or "Claude Code CLI not found"
        result["code"] = claude_bin.get("code", "claude_unavailable")
        return result
    try:
        proc = subprocess.run(
            [claude_bin["bin"], "-p", "--model", "claude-haiku-4-5-20251001", instruction],
            capture_output=True, text=True, timeout=45,
            cwd=str(_core._SCRATCH_DIR),  # keep throwaway JSONLs out of repo scans
        )
    except FileNotFoundError:
        result["error"] = "Claude Code CLI disappeared after resolution"
        result["code"] = "claude_unavailable"
        return result
    except subprocess.TimeoutExpired:
        result["error"] = "claude -p timed out"
        return result
    if proc.returncode != 0:
        result["error"] = (proc.stderr or "").strip()[:300] or f"claude exited {proc.returncode}"
        return result
    title = ""
    for line in reversed((proc.stdout or "").strip().splitlines()):
        s = line.strip().strip('"').strip("'").rstrip(".")
        if s:
            title = s[:120]
            break
    if not title:
        result["error"] = "empty response"
        return result
    _save_issue_title_override(issue_number, title)
    result["ok"] = True
    result["title"] = title
    return result


def _summarize_title_text(first_msg, validate=False):
    """Use `claude -p` to produce a concise title for an opening prompt string.

    Engine-agnostic: callers resolve the opening prompt however fits their
    transcript format (Claude JSONL, Codex rollout) and pass the text in.
    `validate=True` rejects an answer that is really the summarizer asking us
    for a prompt, rather than a usable title.
    """
    result = {"ok": False}
    if not first_msg:
        result["error"] = "no opening prompt found"
        return result

    instruction = (
        "Produce a concise 4-8 word title summarizing what the user is trying to do "
        "below. No quotes, no trailing punctuation, just the title itself on a single "
        "line. Skip image references and boilerplate.\n\n"
        "If the prompt explicitly references a GitHub issue (e.g. '#194', "
        "'issue 194', 'fix issue 194'), prefix the title with the issue ref: "
        "'#194 short description'. Otherwise just return the bare title.\n\n"
        "Opening prompt:\n"
        + first_msg
        + "\n\nTitle:"
    )

    claude_bin = _core._resolve_claude_bin()
    if not claude_bin.get("available"):
        result["error"] = claude_bin.get("reason") or "Claude Code CLI not found"
        result["code"] = claude_bin.get("code", "claude_unavailable")
        return result
    try:
        proc = subprocess.run(
            [
                claude_bin["bin"], "-p", "--model", "claude-haiku-4-5-20251001",
                "--strict-mcp-config", '--mcp-config={"mcpServers":{}}',  # skip user MCP servers -- pure text-in/text-out
                instruction,
            ],
            capture_output=True,
            text=True,
            timeout=45,
            cwd=str(_core._SCRATCH_DIR),  # keep throwaway JSONLs out of repo project dirs
        )
    except FileNotFoundError:
        result["error"] = "Claude Code CLI disappeared after resolution"
        result["code"] = "claude_unavailable"
        return result
    except subprocess.TimeoutExpired:
        result["error"] = "claude -p timed out"
        return result

    if proc.returncode != 0:
        result["error"] = (proc.stderr or "").strip()[:300] or f"claude exited {proc.returncode}"
        return result

    raw = (proc.stdout or "").strip().splitlines()
    title = ""
    for line in reversed(raw):
        s = line.strip().strip('"').strip("'").rstrip(".")
        if s:
            title = s
            break
    if not title:
        result["error"] = "empty response"
        return result

    # Cap length defensively
    title = title[:120]
    if validate and _auto_title_looks_bogus(title):
        result["error"] = f"model answered instead of titling: {title[:80]}"
        return result
    result["ok"] = True
    result["title"] = title
    return result


def summarize_session_title(session_id, validate=False):
    """Use `claude -p` to produce a concise title for a Claude session's opening prompt.

    `validate=True` (the auto-titler path) rejects an answer that is really the
    summarizer asking us for a prompt, rather than writing it onto the row.
    """
    first_msg = _extract_first_message(session_id)
    result = _summarize_title_text(first_msg, validate=validate)
    if not result.get("ok"):
        return result
    title = result["title"]
    # source="auto": writes the JSONL custom-title (so the title sticks) but
    # skips the sidecar — keeps name_overridden False / ✏️ off the row.
    rename_result = _core.rename_session(session_id, title, source="auto")
    result["ok"] = bool(rename_result.get("ok"))
    result["rename_method"] = rename_result.get("method")
    if not result["ok"]:
        result["error"] = rename_result.get("error") or "rename failed"
    return result


# ── Auto-titler ────────────────────────────────────────────────────────────
# Claude Code only generates its own session title inside the interactive TUI,
# and even there it skips a session that already carries a custom title. Every
# CCC-spawned lane runs through the stream-json entrypoint, so it can never
# self-title: measured across 16 live sessions (all unnamed, 9 with no custom
# title at all), zero had an ai-title event. Codex and ACP agents name their own
# threads; Claude is the only engine with this hole, so CCC fills it.
#
# Triggered by hooks/stop.py at the end of a turn. Runs once per session ever
# (marker file), only when the row has no real title, and at most
# _AUTO_TITLE_MAX_CONCURRENT at a time so a fleet-wide Stop storm can't fork a
# haiku per row.
_AUTO_TITLE_MAX_CONCURRENT = 2
_auto_title_sem = threading.Semaphore(_AUTO_TITLE_MAX_CONCURRENT)


_auto_titled_cache = {"key": None, "data": {}}
_auto_titled_lock = threading.Lock()


def _auto_titled_session_ids():
    """Map of session_id -> auto title (empty string when unknown).

    ONE listdir, cached by dir mtime.

    Read once per list build and membership-tested per row -- never a stat()
    per row (see CLAUDE.md "Performance gates").
    """
    try:
        st = _core.SIDECAR_STATE_DIR.stat()
        key = (st.st_mtime, st.st_size)
    except OSError:
        return {}
    with _auto_titled_lock:
        if _auto_titled_cache["key"] == key:
            return _auto_titled_cache["data"]
    try:
        data = {}
        for n in os.listdir(_core.SIDECAR_STATE_DIR):
            if not n.endswith("_autotitled"):
                continue
            sid = n[: -len("_autotitled")]
            title = ""
            try:
                blob = json.loads((_core.SIDECAR_STATE_DIR / n).read_text() or "{}")
            except (OSError, ValueError):
                blob = None
            # Legacy markers hold a bare timestamp, not an object.
            if isinstance(blob, dict):
                title = str(blob.get("title") or "")
            data[sid] = title
    except OSError:
        return {}
    with _auto_titled_lock:
        _auto_titled_cache["key"] = key
        _auto_titled_cache["data"] = data
    return data


def _auto_title_enabled():
    return os.environ.get("CCC_AUTO_TITLE", "1").strip().lower() not in ("0", "false", "no", "off")


def _auto_title_marker_path(session_id):
    return _core.SIDECAR_STATE_DIR / f"{session_id}_autotitled"


def _auto_title_claim(session_id):
    """Claim the once-ever titling slot for this session. False if already taken.

    O_CREAT|O_EXCL so two Stop hooks firing in the same beat can't both spend a
    haiku call on the same session.
    """
    path = _auto_title_marker_path(session_id)
    try:
        _core.SIDECAR_STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.write(fd, json.dumps({"ts": time.time()}).encode())
    except OSError:
        pass
    finally:
        os.close(fd)
    return True


def _auto_title_record(session_id, title):
    """Remember the title we wrote, so a LIVE row can show it too.

    A live session's sidebar row is built from the spawn registry, not the
    transcript, so it keeps rendering the launch slug until the session goes
    cold. Stashing the title next to the marker lets the live path pick it up
    without parsing a transcript per row.
    """
    try:
        _auto_title_marker_path(session_id).write_text(
            json.dumps({"ts": time.time(), "title": title})
        )
    except OSError:
        pass


def _auto_title_release(session_id):
    """Give the slot back so a failed attempt can be retried on a later Stop."""
    try:
        _auto_title_marker_path(session_id).unlink()
    except OSError:
        pass


# A title that is really the summarizer talking back to us ("Could you provide
# the opening prompt?"). Haiku does that when handed boilerplate instead of a
# real ask; without this check those answers get written straight onto the row.
_AUTO_TITLE_REJECT_MARKERS = (
    "opening prompt",
    "actual prompt",
    "provide the",
    "please paste",
    "please share",
    "i don't see",
    "i need the",
    "appears to be incomplete",
    "got cut off",
)


def _auto_title_looks_bogus(title):
    t = str(title or "").strip()
    if not t:
        return True
    low = t.lower()
    if t.endswith("?"):
        return True
    if len(t.split()) > 14:
        return True
    return any(m in low for m in _AUTO_TITLE_REJECT_MARKERS)


def _auto_title_needed(session_id):
    """True when this session has no real title of its own.

    A spawn-derived name (custom_title == agent_name, i.e. still whatever CCC
    passed as --name) counts as untitled: that is the "prewarm-<repo>" case the
    sidebar renders as a slug. A user rename always wins and stops us.
    """
    if _core._load_session_name_overrides().get(session_id):
        return False
    # NEVER title the summarizer's own throwaway `claude -p` sessions. Each one
    # is a real session with a Stop hook, so without this the titler feeds
    # itself: title a session -> that spawns a scratch session -> its Stop hook
    # asks us to title IT -> another scratch session, forever. Observed: 16
    # junk-titled sessions in four minutes.
    first = _extract_first_message(session_id) or ""
    if not first.strip() or first.startswith(_core._GENERATED_HELPER_SESSION_PREFIXES):
        return False
    if _core._is_transcript_control_text(first):
        return False
    path = _core._find_session_jsonl(session_id)
    if path is None:
        return None  # no transcript — nothing to summarize
    try:
        tail = _core._extract_tail_meta(path) or {}
    except Exception:
        return False
    if tail.get("ai_title"):
        return False
    if tail.get("custom_title") and not _core._tail_meta_spawn_named(tail):
        return False
    return True


def _auto_title_worker(session_id):
    acquired = _auto_title_sem.acquire(blocking=False)
    if not acquired:
        # Fleet-wide Stop storm — let a later Stop on this session retry.
        _auto_title_release(session_id)
        return
    try:
        result = summarize_session_title(session_id, validate=True)
        if not result.get("ok"):
            _auto_title_release(session_id)
            _core._log_activity("autotitle", "FAILED",
                          f"sid={session_id[:8]} err={str(result.get('error') or '')[:120]}")
        else:
            _auto_title_record(session_id, str(result.get("title") or ""))
            _core._log_activity("autotitle", "TITLED",
                          f"sid={session_id[:8]} title={str(result.get('title') or '')[:60]}")
    except Exception as e:
        _auto_title_release(session_id)
        _core._log_activity("autotitle", "ERROR", f"sid={session_id[:8]} {str(e)[:120]}")
    finally:
        _auto_title_sem.release()


def _kimi_auto_title_needed(session_id):
    """True when a Kimi session's title is just a copy of its first prompt.

    Kimi Code sets state.json title from the raw first user message, so
    continued sessions (which start with CCC's F2 retrieval prompt) and
    pasted-image sessions ("this session seems stuck: /path/to/paste.png")
    end up with a junk title. A user rename or prior auto-title wins.
    """
    if _core._load_session_name_overrides().get(session_id):
        return False
    if _core._auto_titled_session_ids().get(session_id):
        return False
    session_dir = _core._kimi_session_dir(session_id)
    if not session_dir:
        return None
    try:
        with (session_dir / "state.json").open() as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    title = _strip_f2_retrieval_prompt(
        _core._strip_ccc_kimi_goal_prefix(
            _core._strip_ccc_session_state_instruction(str(state.get("title") or "")).strip()
        )
    ).strip()
    if not title:
        return True
    wire_info = _core._kimi_wire_head(str(session_dir))
    first_prompt = _strip_f2_retrieval_prompt(
        _core._strip_ccc_kimi_goal_prefix(wire_info.get("first_prompt") or "")
    ).strip()
    if not first_prompt:
        return False
    # If the title is the same as the first prompt (possibly truncated), it is
    # not an AI summary — re-title it.
    return title == first_prompt or title == first_prompt[:80]


def _kimi_auto_title_worker(session_id):
    acquired = _auto_title_sem.acquire(blocking=False)
    if not acquired:
        _auto_title_release(session_id)
        return
    try:
        session_dir = _core._kimi_session_dir(session_id)
        if not session_dir:
            _auto_title_release(session_id)
            return
        wire_info = _core._kimi_wire_head(str(session_dir))
        first_prompt = _strip_f2_retrieval_prompt(
            _core._strip_ccc_kimi_goal_prefix(wire_info.get("first_prompt") or "")
        ).strip()
        if not first_prompt or _core._is_transcript_control_text(first_prompt):
            _auto_title_release(session_id)
            return
        result = _summarize_title_text(first_prompt, validate=True)
        if not result.get("ok"):
            _auto_title_release(session_id)
            _core._log_activity("autotitle", "FAILED",
                          f"sid={session_id[:8]} kimi err={str(result.get('error') or '')[:120]}")
            return
        title = result["title"]
        _auto_title_record(session_id, title)
        _core._log_activity("autotitle", "TITLED", f"sid={session_id[:8]} kimi title={title[:60]}")
    except Exception as e:
        _auto_title_release(session_id)
        _core._log_activity("autotitle", "ERROR", f"sid={session_id[:8]} kimi {str(e)[:120]}")
    finally:
        _auto_title_sem.release()


def request_auto_title(session_id):
    """Queue a background auto-title for a session. Returns immediately.

    The Stop hook calls this through /api/conversations/<id>/auto-title and
    must not block on a haiku round-trip, so every check that can be cheap is
    done here and the actual `claude -p` runs on a daemon thread.
    """
    sid = str(session_id or "").strip()
    if not sid or not _auto_title_enabled():
        return {"ok": True, "queued": False, "reason": "disabled"}
    if _auto_title_marker_path(sid).exists():
        return {"ok": True, "queued": False, "reason": "already_attempted"}
    # Kimi sessions live outside ~/.claude/projects; use their wire.jsonl.
    if _core._is_kimi_session(sid):
        needed = _kimi_auto_title_needed(sid)
        if needed is None:
            return {"ok": True, "queued": False, "reason": "no_transcript"}
        if not needed:
            return {"ok": True, "queued": False, "reason": "has_title"}
        if not _auto_title_claim(sid):
            return {"ok": True, "queued": False, "reason": "already_attempted"}
        threading.Thread(target=_kimi_auto_title_worker, args=(sid,), daemon=True).start()
        return {"ok": True, "queued": True}
    needed = _auto_title_needed(sid)
    if needed is None:
        return {"ok": True, "queued": False, "reason": "no_transcript"}
    if not needed:
        return {"ok": True, "queued": False, "reason": "has_title"}
    if not _auto_title_claim(sid):
        return {"ok": True, "queued": False, "reason": "already_attempted"}
    threading.Thread(target=_auto_title_worker, args=(sid,), daemon=True).start()
    return {"ok": True, "queued": True}


# ── Codex auto-titler ──────────────────────────────────────────────────────
# Codex only writes an AI-summarized `title` when its own model bothers to;
# for a lot of CCC-spawned/continued threads (e.g. "You are continuing a task
# from an earlier Codex session…") it just copies the first user message, same
# gap as Claude Code sessions launched outside the interactive TUI. Codex has
# no Stop hook to poke us, so this is driven from the sidebar hydrate path
# instead (_apply_watchtower_worker_display_names' caller) — cheap idempotency
# checks below make repeated polling calls a no-op after the first attempt.
def _codex_auto_title_needed(session_id):
    """True when a Codex session's own title is just a copy of its first prompt."""
    if _core._load_session_name_overrides().get(session_id):
        return False
    try:
        fresh = _core._codex_titles_snapshot().get(session_id)
    except Exception:
        fresh = None
    if not fresh:
        return None  # Codex hasn't written title/first_user_message yet
    first_message = (fresh.get("first_user_message") or "").strip()
    if not first_message:
        return False
    title = (fresh.get("title") or "").strip()
    if title and title != first_message:
        return False  # Codex already produced a real summary
    return True


def _codex_auto_title_worker(session_id, first_message):
    acquired = _auto_title_sem.acquire(blocking=False)
    if not acquired:
        # Fleet-wide Stop storm equivalent — let a later poll retry.
        _auto_title_release(session_id)
        return
    try:
        result = _summarize_title_text(first_message, validate=True)
        if not result.get("ok"):
            _auto_title_release(session_id)
            _core._log_activity("autotitle", "FAILED",
                          f"sid={session_id[:8]} codex err={str(result.get('error') or '')[:120]}")
            return
        title = result["title"]
        rename_result = _core.rename_session(session_id, title, source="auto")
        if rename_result.get("ok"):
            _auto_title_record(session_id, title)
            _core._log_activity("autotitle", "TITLED", f"sid={session_id[:8]} codex title={title[:60]}")
        else:
            _auto_title_release(session_id)
            _core._log_activity("autotitle", "FAILED",
                          f"sid={session_id[:8]} codex rename err={str(rename_result.get('error') or '')[:120]}")
    except Exception as e:
        _auto_title_release(session_id)
        _core._log_activity("autotitle", "ERROR", f"sid={session_id[:8]} codex {str(e)[:120]}")
    finally:
        _auto_title_sem.release()


def _request_codex_auto_title(session_id, fresh=None):
    """Queue a background auto-title for a Codex session. Returns immediately."""
    sid = str(session_id or "").strip()
    if not sid or not _auto_title_enabled():
        return
    if _auto_title_marker_path(sid).exists():
        return
    needed = _codex_auto_title_needed(sid)
    if not needed:
        return
    if fresh is None:
        try:
            fresh = _core._codex_titles_snapshot().get(sid)
        except Exception:
            fresh = None
    first_message = ((fresh or {}).get("first_user_message") or "").strip()
    if not first_message:
        return
    if not _auto_title_claim(sid):
        return
    threading.Thread(
        target=_codex_auto_title_worker, args=(sid, first_message), daemon=True
    ).start()


# Terminal apps we know how to focus via AppleScript. Matched case-insensitively
# against the comm of an ancestor process of the running claude.
_TERMINAL_APPS = {
    "terminal": "Terminal",
    "iterm": "iTerm2",
    "iterm2": "iTerm2",
    "ghostty": "Ghostty",
    "wezterm": "WezTerm",
    "wezterm-gui": "WezTerm",
    "alacritty": "Alacritty",
    "kitty": "kitty",
    "warp": "Warp",
    "warp-preview": "Warp",
    "hyper": "Hyper",
    "tabby": "Tabby",
}


_ttl_memo_keyed_caches = []  # every keyed memo's cache dict, for test resets


def _ttl_memo_keyed(ttl_seconds):
    """Like _ttl_memo but for a single-arg function, keyed on that arg.

    The per-pid liveness probes below (ancestor-terminal walk, cwd, tty) each
    fork `ps`/`lsof`, and are hit once per participant per group-chat build —
    AND twice per build while the header-rewrite pass also probed every
    participant. Memoising per pid for a few seconds collapses those repeated
    forks (same staleness contract as _ttl_memo / _ENGINE_LIVE_TTL). Defined
    here because _ttl_memo lives further down the file than these callers."""
    def decorate(fn):
        cache = {}
        lock = threading.Lock()
        _ttl_memo_keyed_caches.append(cache)

        def wrapper(arg):
            now = time.time()
            ent = cache.get(arg)
            if ent is not None and now - ent[0] < ttl_seconds:
                return ent[1]
            with lock:
                now = time.time()
                ent = cache.get(arg)
                if ent is not None and now - ent[0] < ttl_seconds:
                    return ent[1]
                val = fn(arg)
                cache[arg] = (now, val)
                if len(cache) > 256:  # opportunistic prune of expired entries
                    dead = [k for k, v in cache.items() if now - v[0] >= ttl_seconds]
                    for k in dead:
                        cache.pop(k, None)
                return val
        wrapper.cache_clear = cache.clear
        return wrapper
    return decorate


@_ttl_memo_keyed(3.0)
def _proc_ancestor_terminal(pid):
    """Walk a PID's parent chain and return (term_app_friendly_name, term_pid) or (None, None).

    Uses `ps -o ppid,comm -p <pid>` to avoid parsing platform-specific /proc.
    Stops at init (ppid==1) or when a known terminal app is found.

    Memoised per pid (_ttl_memo_keyed): this is the worst offender — up to 20
    sequential `ps` forks walking the parent chain — and was run per participant,
    twice per group-chat build, uncached."""
    current = pid
    for _ in range(20):  # hard cap to avoid runaway loops
        try:
            out = subprocess.run(
                ["ps", "-o", "pid,ppid,comm", "-p", str(current)],
                capture_output=True, text=True, timeout=1,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None, None
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        if len(lines) < 2:
            return None, None
        parts = lines[1].split(None, 2)
        if len(parts) < 3:
            return None, None
        _pid, ppid, comm = parts
        comm_base = comm.rsplit("/", 1)[-1].lower()
        # Strip .app/Contents/MacOS/... suffix by taking only basename
        comm_base = comm_base.replace(".app", "")
        for key, friendly in _TERMINAL_APPS.items():
            if comm_base == key or comm_base.startswith(key):
                return friendly, int(_pid)
        if ppid == "1" or ppid == "0":
            return None, None
        current = int(ppid)
    return None, None


@_ttl_memo_keyed(3.0)
def _proc_cwd(pid):
    """Return a process's cwd via lsof, or None. Memoised per pid (forks lsof)."""
    try:
        out = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
            capture_output=True, text=True, timeout=1,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    for line in out.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def find_live_claude_processes():
    """Return list of dicts for every running `claude` CLI process:

    [{pid, tty, cwd, terminal_app}, ...]

    Uses `ps -A -o pid,comm` + manual filter. We avoid `pgrep -x claude`
    because on macOS it can silently miss some processes (observed: one
    out of six live claudes was absent from pgrep output while ps -A
    listed it correctly).
    """
    procs = []
    try:
        ps_out = subprocess.run(
            ["ps", "-A", "-o", "pid=,comm="],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return procs
    pids = []
    for line in ps_out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid, comm = parts
        # comm is the basename of the executable; match exactly "claude"
        if comm.rsplit("/", 1)[-1] == "claude":
            pids.append(pid)
    if not pids:
        return procs
    # Get tty for each pid in one call
    try:
        ps_out = subprocess.run(
            ["ps", "-o", "pid,tty", "-p", ",".join(pids)],
            capture_output=True, text=True, timeout=1,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return procs
    tty_by_pid = {}
    for line in ps_out.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            tty_by_pid[parts[0]] = parts[1]
    for pid in pids:
        tty = tty_by_pid.get(pid)
        if not _core._is_real_tty(tty):
            continue
        cwd = _core._proc_cwd(pid)
        if not cwd:
            continue
        term_app, _term_pid = _core._proc_ancestor_terminal(pid)
        procs.append({
            "pid": int(pid),
            "tty": tty,
            "cwd": cwd,
            "terminal_app": term_app,
        })
    return procs


_ttl_memo_caches = []  # every _ttl_memo's state dict, for test-isolation resets


def _reset_ttl_memo_caches():
    """Drop all _ttl_memo caches. Tests call this between cases (these caches
    are module-global, so a stale entry would otherwise leak across tests)."""
    for state in _ttl_memo_caches:
        state["ts"] = 0.0
        state["val"] = None
    for cache in _ttl_memo_keyed_caches:  # keyed per-pid memos (tty/cwd/terminal)
        cache.clear()


def _ttl_memo(ttl_seconds):
    """Memoise a zero-arg function for `ttl_seconds`, thread-safe and
    single-flight (concurrent callers within the window share one result).

    For the ps-backed liveness scans below: they're hit per-participant on
    group-chat opens and per-session on status polls, so without this each
    caller re-shells `ps` (plus per-process cwd/tty/terminal lookups). Callers
    iterate the result read-only, and ~3s of staleness is already the accepted
    contract for liveness here (see _ENGINE_LIVE_TTL).

    If a cached value exists but has expired, only one caller refreshes it;
    concurrent callers get the stale value immediately. That keeps a slow ps
    probe from convoying request threads behind this lock.
    """
    def decorate(fn):
        state = {"ts": 0.0, "val": None, "refreshing": False}
        _ttl_memo_caches.append(state)
        cond = threading.Condition()

        def wrapper():
            now = time.time()
            if state["val"] is not None and now - state["ts"] < ttl_seconds:
                return state["val"]
            with cond:
                now = time.time()
                if state["val"] is not None and now - state["ts"] < ttl_seconds:
                    return state["val"]
                if state["refreshing"]:
                    if state["val"] is not None:
                        return state["val"]
                    while state["refreshing"]:
                        cond.wait()
                    if state["val"] is not None:
                        return state["val"]
                state["refreshing"] = True
            try:
                val = fn()
            except Exception:
                with cond:
                    state["refreshing"] = False
                    cond.notify_all()
                raise
            with cond:
                state["val"] = val
                state["ts"] = time.time()
                state["refreshing"] = False
                cond.notify_all()
                return val

        def cache_clear():
            with cond:
                state.update(ts=0.0, val=None, refreshing=False)
                cond.notify_all()
        wrapper.cache_clear = cache_clear
        return wrapper
    return decorate


@_ttl_memo(3.0)
def _scan_engine_processes():
    """Single `ps -A` scan shared by find_live_{codex,gemini,cursor,antigravity}_processes.

    Each of those used to run its own identical `ps -A -o pid=,tty=,comm=,args=`
    fork, so a single poll cycle that checks all four engines forked `ps` four
    times. TTL-memoised here so they share one fork per window instead (CCC-414)."""
    try:
        ps_out = subprocess.run(
            ["ps", "-A", "-o", "pid=,tty=,comm=,args="],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    rows = []
    for line in ps_out.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3:
            continue
        pid_s, tty, comm = parts[:3]
        args = parts[3] if len(parts) > 3 else ""
        rows.append((pid_s, tty, comm, args))
    return rows


def _raw_engine_process_commands(engine):
    """Yield ``(pid, command)`` without resolving cwd or terminal metadata.

    Idle liveness and pool-presence checks only inspect command arguments. The
    richer ``find_live_*`` helpers additionally fork ``lsof`` and walk process
    ancestors, which is useful for Jump-to-terminal UI but far too expensive
    for a recurring status poll.
    """
    wanted = {
        "codex": "codex",
        "gemini": "gemini",
        "cursor": "cursor-agent",
        "grok": "grok",
    }.get(engine)
    if not wanted:
        return
    for pid_s, _tty, comm, args in _core._scan_engine_processes():
        arg_parts = args.split()
        basenames = {comm.rsplit("/", 1)[-1]}
        if engine == "codex":
            if arg_parts:
                basenames.add(arg_parts[0].rsplit("/", 1)[-1])
        else:
            basenames.update(p.rsplit("/", 1)[-1] for p in arg_parts[:4])
        if wanted in basenames:
            yield pid_s, args


@_ttl_memo(3.0)
def find_live_codex_processes():
    """Return running Codex CLI processes with pid, tty, cwd, terminal app, command."""
    procs = []
    for pid_s, tty, comm, args in _core._scan_engine_processes():
        arg_parts = args.split()
        basenames = {comm.rsplit("/", 1)[-1]}
        if arg_parts:
            basenames.add(arg_parts[0].rsplit("/", 1)[-1])
        if "codex" not in basenames:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        cwd = _core._proc_cwd(pid)
        term_app, _term_pid = _core._proc_ancestor_terminal(pid)
        procs.append({
            "pid": pid,
            "tty": _normalized_tty(tty),
            "cwd": cwd,
            "terminal_app": term_app,
            "command": args,
        })
    return procs


@_ttl_memo(3.0)
def find_live_gemini_processes():
    """Return running Gemini CLI processes with pid, tty, cwd, terminal app, command."""
    procs = []
    for pid_s, tty, comm, args in _core._scan_engine_processes():
        arg_parts = args.split()
        basenames = {comm.rsplit("/", 1)[-1]}
        basenames.update(p.rsplit("/", 1)[-1] for p in arg_parts[:4])
        if "gemini" not in basenames:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        cwd = _core._proc_cwd(pid)
        term_app, _term_pid = _core._proc_ancestor_terminal(pid)
        procs.append({
            "pid": pid,
            "tty": _normalized_tty(tty),
            "cwd": cwd,
            "terminal_app": term_app,
            "command": args,
        })
    return procs


@_ttl_memo(3.0)
def find_live_cursor_processes():
    """Return running Cursor Agent processes with pid, tty, cwd, terminal app, command."""
    procs = []
    for pid_s, tty, comm, args in _core._scan_engine_processes():
        arg_parts = args.split()
        basenames = {comm.rsplit("/", 1)[-1]}
        basenames.update(p.rsplit("/", 1)[-1] for p in arg_parts[:4])
        if "cursor-agent" not in basenames:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        cwd = _core._proc_cwd(pid)
        term_app, _term_pid = _core._proc_ancestor_terminal(pid)
        procs.append({
            "pid": pid,
            "tty": _normalized_tty(tty),
            "cwd": cwd,
            "terminal_app": term_app,
            "command": args,
        })
    return procs


@_ttl_memo(3.0)
def find_live_antigravity_processes():
    """Return running Antigravity CLI processes with pid, tty, cwd, terminal app, command."""
    procs = []
    for pid_s, tty, comm, args in _core._scan_engine_processes():
        arg_parts = args.split()
        basenames = {comm.rsplit("/", 1)[-1]}
        basenames.update(p.rsplit("/", 1)[-1] for p in arg_parts[:4])
        if not ("agy" in basenames or "antigravity" in basenames):
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        cwd = _core._proc_cwd(pid)
        term_app, _term_pid = _core._proc_ancestor_terminal(pid)
        procs.append({
            "pid": pid,
            "tty": _normalized_tty(tty),
            "cwd": cwd,
            "terminal_app": term_app,
            "command": args,
        })
    return procs


def _command_targets_engine_session(command, session_id, engine):
    if not command or not session_id:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False
    if engine == "codex":
        # `codex exec ... -- <prompt>` can contain arbitrary UUIDs in prompt
        # text. Only treat a session id as exact when it belongs to resume args.
        head = tokens[:tokens.index("--")] if "--" in tokens else tokens
        return any(tok == session_id and "resume" in head[:idx]
                   for idx, tok in enumerate(head))
    if engine == "gemini":
        return any(
            tok == session_id and (
                "--resume" in tokens[:idx] or
                (idx > 0 and tokens[idx - 1] in ("--resume", "resume"))
            )
            for idx, tok in enumerate(tokens)
        )
    if engine == "cursor":
        return any(
            tok == session_id and (
                "--resume" in tokens[:idx] or
                (idx > 0 and tokens[idx - 1] in ("--resume", "resume"))
            )
            for idx, tok in enumerate(tokens)
        )
    if engine == "antigravity":
        return any(
            tok == session_id and (
                "--conversation" in tokens[:idx] or
                (idx > 0 and tokens[idx - 1] in ("--conversation", "conversation"))
            )
            for idx, tok in enumerate(tokens)
        )
    if engine == "grok":
        return any(
            tok == session_id and (
                any(t in ("--resume", "resume") for t in tokens[:idx]) or
                (idx > 0 and tokens[idx - 1] in ("--resume", "resume"))
            )
            for idx, tok in enumerate(tokens)
        )
    return False


def _command_targets_other_session(command, session_id, engine):
    if not command:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False
    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    if engine == "antigravity":
        for idx, tok in enumerate(tokens):
            if tok != session_id and uuid_re.match(tok):
                if (
                    "--conversation" in tokens[:idx] or
                    (idx > 0 and tokens[idx - 1] in ("--conversation", "conversation"))
                ):
                    return True
    elif engine == "cursor":
        for idx, tok in enumerate(tokens):
            if tok != session_id and uuid_re.match(tok):
                if (
                    "--resume" in tokens[:idx] or
                    (idx > 0 and tokens[idx - 1] in ("--resume", "resume"))
                ):
                    return True
    return False


def _process_comm_is_claude(comm):
    """Return True when a ps comm value belongs to Claude Code.

    Native builds report the versioned binary path
    (`~/.local/share/claude/versions/2.1.144`) instead of the wrapper name
    `claude`, so basename-only checks miss live background agents.
    """
    if not comm:
        return False
    raw = os.path.normpath(os.path.expanduser(str(comm)))
    name = raw.rsplit("/", 1)[-1]
    if name == "claude":
        return True
    versions_dir = os.path.join(
        os.path.expanduser("~"), ".local", "share", "claude", "versions",
    )
    if not raw.startswith(versions_dir + os.sep):
        return False
    return bool(re.match(r"^\d+\.\d+\.\d+(?:[-+].*)?$", name))


def _normalized_tty(tty):
    value = str(tty or "").strip()
    if not value or value in ("??", "?", "-"):
        return None
    return value


def _is_real_tty(tty):
    return _normalized_tty(tty) is not None


@_ttl_memo(3.0)
def _load_session_registry():
    """Read ~/.claude/sessions/*.json and return {session_id: {pid, cwd, ...}}.

    Claude Code writes one JSON file per running process with its current
    sessionId, giving us an authoritative pid↔session mapping.

    Staleness filter: we verify the pid still belongs to a `claude` process
    (not just that the pid exists — OSes recycle pids, so a dead claude's
    pid might be reused by something unrelated, which would silently point
    our Jump button at the wrong terminal).
    """
    registry = {}
    if not _core.SESSIONS_REGISTRY.is_dir():
        return registry
    # Reuse the same cached process snapshot as the other engine scanners.
    # A live-activity build needs both, so a dedicated ps fork here doubled
    # process enumeration and classified every system process twice.
    live_claude_pids = set()
    try:
        for pid_s, _tty, comm, args in _core._scan_engine_processes():
            # `ps -o comm=` TRUNCATES to the column width (16 chars on macOS),
            # so a process started by absolute path — every CCC-spawned
            # headless, `/Users/<me>/.local/bin/claude` — reports comm as
            # `/Users/<me>/` and fails the basename check. Only sessions
            # launched as bare `claude` from PATH survived it, which is why
            # every spawned session read "not live": no live pid, so
            # session_live_status returned live=False for a process that was
            # answering fine. argv[0] is not truncated — check it too (same
            # comm-or-argv0 idiom as _live_claude_terminal_pids_by_session).
            cmd_first = (args.split(None, 1)[0] if args else "")
            if _core._process_comm_is_claude(comm) or _core._process_comm_is_claude(cmd_first):
                try:
                    live_claude_pids.add(int(pid_s))
                except ValueError:
                    pass
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        session_files = list(_core.SESSIONS_REGISTRY.iterdir())
    except OSError:
        return registry
    for f in session_files:
        if not f.name.endswith(".json") or not f.is_file():
            continue
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("sessionId")
        try:
            pid = int(data.get("pid"))
        except (TypeError, ValueError):
            continue
        if not sid:
            continue
        if pid not in live_claude_pids:
            continue  # stale: pid dead or reassigned to a non-claude
        registry[sid] = data
    return registry


def _registry_row_overlay(meta):
    """Live-session fields from Claude's peer registry, layered onto rows.

    The registry (~/.claude/sessions/<pid>.json) carries data transcripts
    never see: the bridge session id (the ULID in claude.ai/code/session_<id>
    links and Claude-Session commit trailers), Claude's own busy/idle status,
    and the session's tmux / messaging-socket coordinates. Only running
    processes have registry entries, so dormant sessions overlay as blank —
    never serve a stale bridge id for a dead session.
    """
    meta = meta or {}
    bridge = str(meta.get("bridgeSessionId") or "").strip()
    if bridge.startswith("session_"):
        bridge = bridge[len("session_"):]
    try:
        status_ts = float(meta.get("statusUpdatedAt") or 0) / 1000.0
    except (TypeError, ValueError):
        status_ts = 0
    tmux = meta.get("tmux")
    if tmux and not isinstance(tmux, str):
        tmux = json.dumps(tmux)
    return {
        "bridge_session_id": bridge,
        "registry_status": str(meta.get("status") or "").strip(),
        "registry_status_updated_at": status_ts,
        "registry_tmux": str(tmux or "").strip(),
        "messaging_socket_path": str(meta.get("messagingSocketPath") or "").strip(),
    }


# Claude's bridge ids are 24-char alphanumeric tokens (ULID-shaped but NOT
# strict Crockford base32 — observed ids contain U, L, and lowercase letters,
# e.g. 01M5QU7CyjBCpVsLPM9vfDQJ). Accept 24-26 chars to also tolerate true
# 26-char ULIDs, optionally wrapped as session_<id> or a full
# claude.ai/code/session_<id> URL.
_BRIDGE_SESSION_ALIAS_RE = re.compile(
    r"^(?:https?://claude\.ai/code/)?(?:session_)?([0-9A-Za-z]{24,26})$"
)


def _resolve_bridge_session_alias(session_id):
    """Map a Claude bridge session id to the local session UUID.

    Claude Code 2.1.228+ shows users the bridge id (in the TUI, in
    Claude-Session commit trailers, and in claude.ai/code/session_<id> links)
    while the local transcript — and every CCC API — keys on the UUID. Accept
    the bare ULID, session_<ULID>, or the full URL; return the local UUID
    when a live session claims that bridge id, else the input unchanged.
    """
    raw = str(session_id or "").strip()
    m = _BRIDGE_SESSION_ALIAS_RE.match(raw)
    if not m:
        return session_id
    token = m.group(1).lower()
    try:
        registry = _core._load_session_registry()
    except Exception:
        return session_id
    for sid, meta in (registry or {}).items():
        bridge = str((meta or {}).get("bridgeSessionId") or "").strip()
        if bridge.startswith("session_"):
            bridge = bridge[len("session_"):]
        if bridge and bridge.lower() == token:
            return sid
    return session_id


def session_live_status(session_id, session_cwd):
    """Look up a session's running process via ~/.claude/sessions/<pid>.json.

    Returns dict {live, pid, tty, cwd, terminal_app, recently_written}.
    The registry gives us an authoritative pid↔session mapping written by
    Claude Code itself — no more cwd-based heuristics.
    """
    result = {
        "session_id": session_id,
        "live": False,
        "pid": None,
        "tty": None,
        "terminal_app": None,
        "status": None,
        "kind": None,
        "job_id": None,
        "agent": None,
        "recently_written": False,
        "ambiguous": False,
        "match_count": 0,
    }
    if not session_id:
        return result

    if _core._is_kimi_session(session_id):
        # ACP sessions have no per-session process; "live" means the harness
        # CLI is installed so the shared `kimi acp` subprocess can drive the
        # session. Status mirrors the ACP turn state for busy indicators.
        resolved = _core._acp_resolve_bin("kimi")
        if resolved.get("available"):
            snap = _core._acp_session_snapshot("kimi", session_id) or {}
            result["live"] = True
            result["status"] = (
                "running"
                if snap.get("status") == "active" or _core._kimi_wire_turn_active(session_id)
                else "idle"
            )
            result["kind"] = "acp"
            result["cwd"] = snap.get("cwd") or session_cwd
            result["match_count"] = 1
            result["model"] = snap.get("model")
            if snap.get("pending_permissions"):
                result["needs_approval"] = True
                result["needs_approval_message"] = "Kimi is waiting for a tool approval"
            # TUI-originated turns never flip the ACP snapshot to "active"
            # (that only happens for CCC-driven turns), but they do append to
            # the session's wire.jsonl. Fresh wire activity is the only honest
            # busy signal for those turns — use it so the pane can show a
            # working indicator instead of sitting static.
            if result["status"] != "running":
                try:
                    wire = _core._acp_wire_path("kimi", session_id)
                    if wire:
                        wire_age = time.time() - wire.stat().st_mtime
                        result["recently_written"] = wire_age < 300
                        if wire_age < 10:
                            result["status"] = "running"
                except OSError:
                    pass
            # Stuck-mid-turn detection — same stale_tool_call contract codex
            # rows carry, so the pane's stuck card and the sidebar row's Stuck
            # pill work for kimi with no client-side engine special cases.
            # Mid-turn = ACP turn active OR the wire tail shows an unfinished
            # turn; stale = the wire (appended to throughout a live turn) has
            # had no output past CCC_STALE_TOOL_SEC.
            try:
                idx_entry = _core._kimi_session_index().get(session_id) or {}
                tail_meta = _core._kimi_wire_tail_meta(idx_entry.get("session_dir"))
                result["last_event_type"] = tail_meta.get("last_event_type")
                if tail_meta.get("pending_tool"):
                    result["pending_tool"] = tail_meta["pending_tool"]
                    result["pending_tool_ts"] = tail_meta.get("wire_mtime") or 0
                if not result.get("needs_approval"):
                    result.update(_core._kimi_stale_tool_fields(
                        tail_meta, acp_active=(snap.get("status") == "active")))
                # Durable mid-turn busy signal. The 10s wire-freshness window
                # above only catches turns actively emitting output — long
                # model-thinking pauses write nothing, so status flapped to
                # "idle" and the pane's Working… indicator vanished mid-turn.
                # The wire tail knows the turn never closed (no step.end /
                # turn.cancel); treat that as running until the stale-tool
                # threshold flips the pane to the stuck card instead (same
                # contract as _codex_row_state's working window).
                if (result["status"] != "running"
                        and tail_meta.get("mid_turn")
                        and not result.get("stale_tool_call")):
                    result["status"] = "running"
            except Exception:
                pass
        return result

    if _core._is_grok_session(session_id):
        # Same ACP shape as kimi above: no per-session process, so "live"
        # means the shared `grok acp` subprocess is available to drive it.
        # Grok is declared in _ACP_WORKER_HARNESSES alongside kimi but was
        # never given a status branch here, so every grok session reported
        # live=False forever — always "dormant"/"ended" no matter what was
        # actually happening (CCC-877).
        resolved = _core._acp_resolve_bin("grok")
        if resolved.get("available"):
            snap = _core._acp_session_snapshot("grok", session_id) or {}
            result["live"] = True
            result["status"] = "running" if snap.get("status") == "active" else "idle"
            result["kind"] = "acp"
            result["cwd"] = snap.get("cwd") or session_cwd
            result["match_count"] = 1
            result["model"] = snap.get("model")
            if snap.get("pending_permissions"):
                result["needs_approval"] = True
                result["needs_approval_message"] = "Grok is waiting for a tool approval"
        return result

    if _core._is_codex_session(session_id):
        path = _core._resolve_codex_rollout_path(session_id)
        if path:
            try:
                mtime = path.stat().st_mtime
                result["recently_written"] = (time.time() - mtime) < 300
                # Exposed unconditionally (not just while live) so a non-live
                # session with no headless/terminal process still gives the
                # frontend a way to notice new transcript content — mirrors
                # the ACP wire-mtime catch-up (CCC-849); see
                # maybeCatchUpAcpConversationFromWire.
                result["transcript_mtime"] = mtime
            except OSError:
                pass
        registry_known = _core._spawn_registry_has_session(session_id, "codex")
        entry = _core._live_spawn_registry_entry_for_session(session_id, "codex")
        if not entry:
            entry = _core._find_live_spawn_entry_for_session(session_id)
        if entry:
            pid = entry["pid"]
            result["pid"] = pid
            result["tty"] = _core._process_tty(pid)
            result["cwd"] = _core._proc_cwd(pid) or entry.get("cwd") or session_cwd
            result["terminal_app"], _term_pid = _core._proc_ancestor_terminal(pid)
            result["live"] = True
            result["match_count"] = 1
            return result
        if not session_cwd:
            session_cwd = _core.find_session_cwd(session_id)
        exact_matches = []
        cwd_matches = []
        for p in _core.find_live_codex_processes():
            cmd = p.get("command") or ""
            if _core._command_targets_engine_session(cmd, session_id, "codex"):
                exact_matches.append(p)
            elif not registry_known and session_cwd and p.get("cwd") == session_cwd:
                cwd_matches.append(p)
        matches = exact_matches or cwd_matches
        result["match_count"] = len(matches)
        if not matches:
            return result
        if len(matches) > 1:
            exact = [
                p for p in matches
                if _core._command_targets_engine_session(p.get("command") or "", session_id, "codex")
            ]
            if len(exact) == 1:
                matches = exact
            else:
                result["ambiguous"] = True
                return result
        match = matches[0]
        result["pid"] = match["pid"]
        result["tty"] = match.get("tty")
        result["terminal_app"] = match.get("terminal_app")
        result["live"] = True
        return result

    if _core._is_gemini_session(session_id):
        path = _core._resolve_gemini_chat_path(session_id)
        if path:
            try:
                result["recently_written"] = (time.time() - path.stat().st_mtime) < 300
            except OSError:
                pass
        registry_known = _core._spawn_registry_has_session(session_id, "gemini")
        entry = _core._live_spawn_registry_entry_for_session(session_id, "gemini")
        if not entry:
            entry = _core._find_live_spawn_entry_for_session(session_id)
        if entry:
            pid = entry["pid"]
            result["pid"] = pid
            result["tty"] = _core._process_tty(pid)
            result["cwd"] = _core._proc_cwd(pid) or entry.get("cwd") or session_cwd
            result["terminal_app"], _term_pid = _core._proc_ancestor_terminal(pid)
            result["live"] = True
            result["match_count"] = 1
            return result
        if not session_cwd:
            session_cwd = _core.find_session_cwd(session_id)
        exact_matches = []
        cwd_matches = []
        for p in _core.find_live_gemini_processes():
            cmd = p.get("command") or ""
            if _core._command_targets_engine_session(cmd, session_id, "gemini"):
                exact_matches.append(p)
            elif not registry_known and session_cwd and p.get("cwd") == session_cwd:
                cwd_matches.append(p)
        matches = exact_matches or cwd_matches
        result["match_count"] = len(matches)
        if not matches:
            return result
        if len(matches) > 1:
            exact = [
                p for p in matches
                if _core._command_targets_engine_session(p.get("command") or "", session_id, "gemini")
            ]
            if len(exact) == 1:
                matches = exact
            else:
                result["ambiguous"] = True
                return result
        match = matches[0]
        result["pid"] = match["pid"]
        result["tty"] = match.get("tty")
        result["terminal_app"] = match.get("terminal_app")
        result["live"] = True
        return result

    if _core._is_devin_cli_session(session_id):
        # Devin CLI sessions are one-shot `devin -p` processes. Liveness
        # comes from the spawn registry (a live entry means the process
        # is still running) and the lock file in the CLI's session_locks
        # dir. No TTY — the CLI runs headless.
        raw_id = _core._devin_cli_raw_id(session_id)
        entry = _core._live_spawn_registry_entry_for_session(session_id, "devin")
        if not entry:
            entry = _core._find_live_spawn_entry_for_session(session_id)
        if entry:
            pid = entry["pid"]
            result["pid"] = pid
            result["tty"] = _core._process_tty(pid)
            result["cwd"] = _core._proc_cwd(pid) or entry.get("cwd") or session_cwd
            result["terminal_app"], _term_pid = _core._proc_ancestor_terminal(pid)
            result["live"] = True
            result["kind"] = "headless"
            result["match_count"] = 1
            return result
        # No live spawn entry — check the lock file (CLI may have been
        # started outside CCC).
        if _core._devin_cli_session_live(raw_id):
            result["live"] = True
            result["kind"] = "headless"
            result["match_count"] = 1
            result["cwd"] = _core._devin_cli_session_cwd(raw_id) or session_cwd
            return result
        return result

    if _core._is_cursor_session(session_id):
        path = _core._cursor_transcript_path(session_id)
        if path:
            try:
                result["recently_written"] = (time.time() - path.stat().st_mtime) < 300
            except OSError:
                pass
        registry_known = _core._spawn_registry_has_session(session_id, "cursor")
        entry = _core._live_spawn_registry_entry_for_session(session_id, "cursor")
        if entry:
            pid = entry["pid"]
            result["pid"] = pid
            result["tty"] = _core._process_tty(pid)
            result["cwd"] = _core._proc_cwd(pid) or entry.get("cwd") or session_cwd
            result["terminal_app"], _term_pid = _core._proc_ancestor_terminal(pid)
            result["live"] = True
            result["match_count"] = 1
            return result
        if not session_cwd:
            session_cwd = _core.find_session_cwd(session_id)
        exact_matches = []
        cwd_matches = []
        for p in _core.find_live_cursor_processes():
            cmd = p.get("command") or ""
            if _core._command_targets_engine_session(cmd, session_id, "cursor"):
                exact_matches.append(p)
            elif not registry_known and session_cwd and p.get("cwd") == session_cwd:
                if not _command_targets_other_session(cmd, session_id, "cursor"):
                    cwd_matches.append(p)
        matches = exact_matches or cwd_matches
        result["match_count"] = len(matches)
        if not matches:
            return result
        if len(matches) > 1:
            exact = [
                p for p in matches
                if _core._command_targets_engine_session(p.get("command") or "", session_id, "cursor")
            ]
            if len(exact) == 1:
                matches = exact
            else:
                result["ambiguous"] = True
                return result
        match = matches[0]
        result["pid"] = match["pid"]
        result["tty"] = match.get("tty")
        result["terminal_app"] = match.get("terminal_app")
        result["live"] = True
        return result

    if _core._is_antigravity_session(session_id):
        path = _core._antigravity_transcript_path(session_id)
        if path:
            try:
                result["recently_written"] = (time.time() - path.stat().st_mtime) < 300
            except OSError:
                pass
        registry_known = _core._spawn_registry_has_session(session_id, "antigravity")
        entry = _core._live_spawn_registry_entry_for_session(session_id, "antigravity")
        if entry:
            pid = entry["pid"]
            result["pid"] = pid
            result["tty"] = _core._process_tty(pid)
            result["cwd"] = _core._proc_cwd(pid) or entry.get("cwd") or session_cwd
            result["terminal_app"], _term_pid = _core._proc_ancestor_terminal(pid)
            result["live"] = True
            result["match_count"] = 1
            return result
        if not session_cwd:
            session_cwd = _core.find_session_cwd(session_id)
        exact_matches = []
        cwd_matches = []
        for p in _core.find_live_antigravity_processes():
            cmd = p.get("command") or ""
            if _core._command_targets_engine_session(cmd, session_id, "antigravity"):
                exact_matches.append(p)
            elif not registry_known and session_cwd and p.get("cwd") == session_cwd:
                if not _command_targets_other_session(cmd, session_id, "antigravity"):
                    cwd_matches.append(p)
        matches = exact_matches or cwd_matches
        result["match_count"] = len(matches)
        if not matches:
            return result
        if len(matches) > 1:
            exact = [
                p for p in matches
                if _core._command_targets_engine_session(p.get("command") or "", session_id, "antigravity")
            ]
            if len(exact) == 1:
                matches = exact
            else:
                result["ambiguous"] = True
                return result
        match = matches[0]
        result["pid"] = match["pid"]
        result["tty"] = match.get("tty")
        result["terminal_app"] = match.get("terminal_app")
        result["live"] = True
        return result

    # Recency check on the .jsonl file (for the "is actively being used" signal)
    jsonl_name = session_id + ".jsonl"
    recent = False
    transcript_mtime = None
    if _core.PROJECTS_ROOT.is_dir():
        now = time.time()
        for project_dir in _core.PROJECTS_ROOT.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / jsonl_name
            if candidate.is_file():
                try:
                    mtime = candidate.stat().st_mtime
                    transcript_mtime = mtime
                    if now - mtime < 300:  # 5 min
                        recent = True
                except OSError:
                    pass
                break
    result["recently_written"] = recent
    # Exact mtime, not just the 5-min recent bucket — this session-status poll
    # runs every 5s for the open pane (see _POLLER_META.liveStatus), far
    # tighter than the sidebar's ~60s/hidden-paused session-list refresh. The
    # F2 cold-session composer (CCC-828) reads it to avoid quoting an idle
    # time computed from a stale cached row (e.g. the dashboard tab was
    # backgrounded through the session's last real message).
    if transcript_mtime is not None:
        result["transcript_mtime"] = transcript_mtime

    # Primary lookup: session registry (authoritative)
    registry = _core._load_session_registry()
    entry = registry.get(session_id)
    if entry:
        try:
            pid = int(entry["pid"])
        except (TypeError, ValueError, KeyError):
            pid = 0
        # CCC-45: only trust the registry entry when its pid is ACTUALLY alive.
        # A stale ~/.claude/sessions/<pid>.json — claude exited without cleanup
        # (e.g. after a /clear fork) — otherwise reported the session "live"
        # with a tty, so CCC keystroked input into a dead terminal / the ether.
        # When the pid is gone, fall through to scanning real running processes
        # (authoritative) instead of trusting the registry blindly.
        if pid and _core._pid_alive(pid):
            result["pid"] = pid
            result["match_count"] = 1
            result["status"] = entry.get("status")
            result["kind"] = entry.get("kind")
            result["job_id"] = entry.get("jobId")
            result["agent"] = entry.get("agent")
            # Hydrate tty + terminal_app from the live pid
            try:
                ps_out = subprocess.run(
                    ["ps", "-o", "tty=", "-p", str(pid)],
                    capture_output=True, text=True, timeout=1,
                )
                tty = (ps_out.stdout or "").strip()
                if _core._is_real_tty(tty):
                    result["tty"] = tty
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            term_app, _ = _core._proc_ancestor_terminal(pid)
            result["terminal_app"] = term_app
            result["live"] = True
            return result

    # Fallback: cwd-based matching (for older claude versions or missing
    # registry). This can only ever prove "a claude process is running at
    # this cwd" — a `claude` process's command line carries no session id,
    # so cwd is not proof this pid IS `session_id`. In a repo with several
    # sessions sharing one cwd (the norm here), a single cwd match can
    # easily be a *different* live sibling. Report the diagnostic pid/tty
    # for display, but never assert `live: true` from this path — callers
    # that gate keystroke injection on `live` must fall through to an
    # identity-verified route (spawn registry / `claude --resume
    # <session_id>`) instead of guessing (CCC-ask-loopback incident).
    if not session_cwd:
        return result
    procs = _core.find_live_claude_processes()
    matches = [p for p in procs if p["cwd"] == session_cwd]
    result["match_count"] = len(matches)
    if not matches:
        return result
    if len(matches) > 1:
        result["ambiguous"] = True
        return result
    match = matches[0]
    result["pid"] = match["pid"]
    result["tty"] = match["tty"]
    result["terminal_app"] = match["terminal_app"]
    return result


def _preferred_terminal_app():
    """Pick a terminal to launch new sessions in.

    Prefers the terminal app that's hosting the newest running claude process,
    falling back to Terminal.app (which is always available on macOS).
    """
    procs = _core.find_live_claude_processes()
    # Prefer known terminals
    for p in procs:
        if p.get("terminal_app") in _TERMINAL_APPS.values() or p.get("terminal_app") in ("Terminal", "iTerm2"):
            return p["terminal_app"]
    return "Terminal"


def _shell_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def _build_resume_command(session_id, cwd, cwd_exists):
    """Same logic as the frontend buildResumeCommand — keep them in sync."""
    is_codex = _core._is_codex_session(session_id)
    is_gemini = _core._is_gemini_session(session_id)
    is_antigravity = _core._is_antigravity_session(session_id)
    if is_codex:
        resume_cmd = f"codex resume {session_id}"
    elif is_gemini:
        resume_cmd = f"gemini --resume {session_id}"
    elif _core._is_cursor_session(session_id):
        resume_cmd = f"cursor-agent --resume {session_id}"
    elif _core._is_grok_session(session_id):
        resume_cmd = f"grok --resume {session_id}"
    elif _core._is_kimi_session(session_id):
        # `kimi -S <id>` / `--session <id>` resumes that exact session
        # (CCC-938 reopen — an earlier fix wrongly claimed no resume flag
        # exists). Being a non-Claude engine, it must never get Claude's
        # `--dangerously-skip-permissions` flag.
        resume_cmd = f"kimi --session {session_id}"
    elif _core._is_devin_cli_session(session_id):
        raw_id = _core._devin_cli_raw_id(session_id)
        resume_cmd = f"devin --resume {raw_id}"
    elif _core._is_devin_session(session_id):
        # Cloud Devin sessions have no local CLI counterpart — the
        # transcript lives on Devin's servers, not in a resumable local
        # process (see the isDevin composer block in app.js: "read-only
        # in CCC - reply at app.devin.ai"). Falling through to
        # `claude --resume` here would try to resume a session Claude Code
        # has never heard of. Mirror the Antigravity app-only fallback:
        # open the web app and drop the user into a plain shell instead.
        url_slug = (
            session_id[len(_core.DEVIN_SESSION_PREFIX):]
            if session_id.startswith(_core.DEVIN_SESSION_PREFIX) else session_id
        )
        resume_cmd = (
            "echo " + _shell_quote(
                "CCC: this is a cloud Devin session - it has no local CLI to "
                "resume. Opening app.devin.ai so you can continue it there."
            )
            + "; open " + _shell_quote(f"https://app.devin.ai/sessions/{url_slug}")
            + " >/dev/null 2>&1 || true"
            + "; exec ${SHELL:-/bin/zsh} -l"
        )
    elif is_antigravity:
        # Two flavors of Antigravity session live on disk:
        #   1. AGY CLI sessions — have a `.pb` in ~/.gemini/antigravity-cli/
        #      conversations/. `agy --conversation <sid>` can resume them.
        #   2. Antigravity App sessions — live in ~/.gemini/antigravity/
        #      conversations/ only. The CLI cannot import these; running
        #      `agy --conversation <sid>` against them silently starts a
        #      fresh AGY chat with a confusing "not in conversation store"
        #      message. For app-only sessions we instead open Antigravity.app
        #      (so the user can pick up the session there) and drop the user
        #      into a login shell at the session's cwd — useful starting
        #      point, no misleading auto-exec into a stranger AGY chat.
        resolved = _core._resolve_antigravity_bin()
        cli_conversation = _core._antigravity_cli_conversation_path(session_id)
        cli_resumable = bool(cli_conversation and cli_conversation.is_file())
        if cli_resumable and resolved.get("available"):
            resume_cmd = (
                "echo " + _shell_quote("CCC: opening AGY conversation in the TUI.")
                + " && exec " + _core._antigravity_shell_command(resolved)
                + " --conversation " + _shell_quote(session_id)
            )
        elif _core._antigravity_app_conversation_path(session_id):
            # App-only session — open Antigravity.app (best-effort) and leave
            # the user at a shell. We deliberately do NOT exec into `agy`:
            # `agy --conversation <sid>` against an app-only id silently
            # starts a fresh AGY chat which is more confusing than helpful.
            # Always reach the final `exec`, even if `open -a Antigravity`
            # fails (no app installed), hence `; ` before exec.
            resume_cmd = (
                "echo " + _shell_quote(
                    "CCC: this Antigravity session lives in the app, not the AGY CLI. "
                    "Opening Antigravity.app so you can resume it there. "
                    "Shell stays at the session cwd."
                )
                + "; open -a Antigravity >/dev/null 2>&1 || true"
                + "; exec ${SHELL:-/bin/zsh} -l"
            )
        elif resolved.get("available"):
            # Antigravity session but neither CLI-store nor App-store has it
            # (rare — e.g. brain-only transcript). Skip the auto-exec into
            # AGY entirely so the user doesn't end up in an unrelated chat.
            resume_cmd = (
                "echo " + _shell_quote(
                    "CCC: this Antigravity session has no resumable conversation "
                    "(neither AGY CLI nor the app store has it). "
                    "Open Antigravity manually if you want to continue it."
                )
                + "; exec ${SHELL:-/bin/zsh} -l"
            )
        else:
            resume_cmd = (
                "echo " + _shell_quote(resolved.get("reason") or "Antigravity CLI not found.")
                + "; exec ${SHELL:-/bin/zsh} -l"
            )
    else:
        resume_cmd = f"claude --resume {session_id} --dangerously-skip-permissions"
    if not cwd:
        return resume_cmd
    q_cwd = _shell_quote(cwd)
    if cwd_exists:
        return f"cd {q_cwd} && {resume_cmd}"
    # Worktree recreation fallback
    m = re.search(r"/\.claude/worktrees/(.+)$", cwd)
    if m:
        branch = m.group(1)
        repo_base = cwd.split("/.claude/worktrees/")[0]
        q_repo = _shell_quote(repo_base)
        q_branch = _shell_quote(branch)
        return (
            f"(cd {q_repo} && git worktree add {q_cwd} {q_branch} 2>/dev/null "
            f"|| git worktree add {q_cwd} -b {q_branch} origin/main) "
            f"&& cd {q_cwd} && {resume_cmd}"
        )
    return f"cd {q_cwd} && {resume_cmd}"


# UUID-format check — Claude Desktop's deep-link handler validates the
# session ID against a UUID regex internally and silently drops anything
# else. We pre-check so the UI gets a clear error instead of an opaque
# "nothing happened".
_SESSION_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def open_session_in_claude_desktop(session_id):
    """Open the macOS Claude Desktop app and resume `session_id`.

    Uses the registered `claude://resume?session=<uuid>` deep-link, which
    the desktop app handles by importing the CLI session and navigating
    to it. macOS only — relies on `open(1)`.

    Returns {ok, error?, url?}.
    """
    if not session_id:
        return {"ok": False, "error": "missing session_id"}
    if not _SESSION_UUID_RE.match(session_id):
        return {"ok": False, "error": "invalid session_id (expected UUID)"}
    if sys.platform != "darwin":
        _core._log_macos_only("desktopDeepLinks")
        return {"ok": False, "error": "Claude Desktop deep-link is macOS-only"}
    url = f"claude://resume?session={session_id}"
    try:
        ctx = _core.repo_from_session(session_id)
        log_dir = _core.repo_log_dir(ctx["repo_path"])
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"desktop-{session_id[:8]}.log"
        lf = open(log_path, "w")
        subprocess.Popen(["open", url], stdout=lf, stderr=lf)
    except _core.RepoContextError as e:
        return e.as_payload()
    except (FileNotFoundError, OSError) as e:
        print(f"open_session_in_claude_desktop: {e!r}", file=sys.stderr, flush=True)
        return {"ok": False, "error": "could not launch Claude Desktop", "url": url}
    return {"ok": True, "url": url}


def open_session_in_codex_desktop(session_id, cwd=None):
    """Open the macOS Codex app for a Codex session.

    Codex.app registers a `codex://` URL scheme. We mirror the Claude
    Desktop launch path with a best-effort resume URL so the UI can expose a
    distinct app destination next to the terminal fallback.
    """
    if not session_id:
        return {"ok": False, "error": "missing session_id"}
    if not _core._is_codex_session(session_id):
        return {"ok": False, "error": "Codex launch only handles Codex sessions"}
    if sys.platform != "darwin":
        _core._log_macos_only("desktopDeepLinks")
        return {"ok": False, "error": "Codex app launch is macOS-only"}
    params = {"session": session_id}
    if cwd:
        params["cwd"] = cwd
    url = "codex://resume?" + urllib.parse.urlencode(params)
    try:
        ctx = _core.repo_from_session(session_id)
        log_dir = _core.repo_log_dir(ctx["repo_path"])
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"codex-desktop-{session_id[:8]}.log"
        lf = open(log_path, "w")
        subprocess.Popen(["open", url], stdout=lf, stderr=lf)
    except _core.RepoContextError as e:
        return e.as_payload()
    except (FileNotFoundError, OSError) as e:
        print(f"open_session_in_codex_desktop: {e!r}", file=sys.stderr, flush=True)
        return {"ok": False, "error": "could not launch Codex", "url": url}
    return {"ok": True, "url": url}


def launch_terminal_for_session(session_id, cwd=None, terminal_app=None, post_slash_commands=None,
                                stop_headless=False):
    """Open a new terminal window and run the resume command for this session.

    Idempotent: if a live claude process with a TTY already exists for this
    session, bring that terminal to the front instead of opening a new one.
    Prevents the "I clicked Launch and got two terminals" race.

    Headless guard (CCC-96): if a live HEADLESS process owns this session
    (live, no tty), refuse — resuming the same transcript in a terminal
    while the headless process keeps appending forks the history ("amnesia"
    when one of them is closed). The caller can retry with
    stop_headless=True to SIGTERM the headless process first.

    Returns {ok, terminal_app, command, error?, existing?, headless_live?}.
    """
    if platform.system() != "Darwin":
        _core._log_macos_only("launchTerminal")
        return {"ok": False, "error": "opening a visible terminal is macOS-only today"}
    if not session_id:
        return {"ok": False, "error": "missing session_id"}
    post_slash_commands = [
        str(cmd).strip()
        for cmd in (post_slash_commands or [])
        if str(cmd or "").strip()
    ]
    # Pre-check: is there already a live claude --resume on this session with a tty?
    try:
        existing = _core.session_live_status(session_id, cwd) or {}
        # Headless guard — live process, no terminal to focus. Launching a
        # terminal resume now would give the transcript two writers.
        if existing.get("live") and not existing.get("tty") and existing.get("pid"):
            hpid = int(existing.get("pid"))
            # bg-pty daemon process (registry kind "bg"): not a headless —
            # it's attached to an open terminal pane. SIGTERM here would
            # close the user's window mid-session. Refuse, even when the
            # caller asked to stop a headless (CCC-104).
            if existing.get("kind") == "bg":
                return {
                    "ok": False,
                    "bg_live": True,
                    "error": (
                        "This session is open in a Claude Code background "
                        "terminal (pid %d). Use that window - resuming from "
                        "CCC would fork the conversation." % hpid
                    ),
                }
            if not stop_headless:
                return {
                    "ok": False,
                    "headless_live": True,
                    "headless_pid": hpid,
                    "error": (
                        "A headless process (pid %d) is still running this session. "
                        "Launching a terminal resume now would fork the conversation "
                        "history. Stop the headless process first." % hpid
                    ),
                }
            try:
                os.kill(hpid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            # Wait briefly for it to exit so the resume sees the final
            # transcript state, not a mid-write snapshot.
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    os.kill(hpid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.2)
            else:
                return {
                    "ok": False,
                    "headless_live": True,
                    "headless_pid": hpid,
                    "error": "Headless process (pid %d) did not exit within 5s - not launching." % hpid,
                }
        if existing.get("live") and existing.get("tty"):
            tty = existing.get("tty")
            term_app = existing.get("terminal_app") or _preferred_terminal_app()
            jr = _core.focus_terminal_by_tty(tty, term_app)
            post_results = []
            if jr.get("ok") and post_slash_commands:
                for command_text in post_slash_commands:
                    post_results.append(_core.inject_input_via_keystroke(tty, term_app, command_text))
            return {
                "ok": bool(jr.get("ok")) and all(r.get("ok") for r in post_results),
                "terminal_app": term_app,
                "existing": True,
                "tty": tty,
                "note": "Live terminal already attached - focused it instead of opening a new one.",
                "post_results": post_results,
            }
    except Exception:
        pass  # fall through to the normal launch path
    if cwd is None:
        cwd = _core.find_session_cwd(session_id)
    try:
        ctx = _core.repo_from_session(session_id)
    except _core.RepoContextError as e:
        return e.as_payload()
    if not cwd:
        cwd = ctx["cwd"]
    cwd_exists = bool(cwd and Path(cwd).is_dir())
    is_codex = _core._is_codex_session(session_id)
    is_non_claude_engine = is_codex or _core._is_gemini_session(session_id) or _core._is_antigravity_session(session_id)
    command = _build_resume_command(session_id, cwd, cwd_exists)
    target = terminal_app or _preferred_terminal_app()

    # AppleScript string needs the command embedded; escape backslashes and
    # double quotes for the AppleScript literal.
    def as_literal(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')
    cmd_lit = as_literal(command)

    # Use a human-readable name for the terminal tab. Look it up cheaply: a
    # user rename override, else the title the parser derives from this
    # session's own transcript tail (custom-title / agent-name / ai-title),
    # which _extract_tail_meta caches by (mtime,size). This previously called
    # find_all_sessions(ctx["repo_path"]), which rebuilds the ENTIRE repo
    # session list (seconds — the dominant synchronous cost of a launch, and
    # far worse under load) just to read one row's display name.
    rename_target = None
    try:
        rename_target = _core._load_session_name_overrides().get(session_id)
    except Exception:
        rename_target = None
    if not rename_target:
        try:
            jsonl = _core._canonical_conversation_path(ctx["repo_path"], session_id)
            if jsonl and jsonl.exists():
                tm = _core._extract_tail_meta(jsonl)
                rename_target = (
                    tm.get("custom_title")
                    or tm.get("agent_name")
                    or tm.get("ai_title")
                )
        except Exception:
            rename_target = None
    if not rename_target:
        rename_target = (session_id or "")[:12]
    # Sanitize for AppleScript (no quotes/backslashes)
    rename_target = rename_target.replace('"', '').replace('\\', '').replace("'", "")[:60]
    color = _core._pick_color_for_session(rename_target)
    slash_commands = []
    if not is_non_claude_engine:
        slash_commands = [f"/rename {rename_target}", f"/color {color}"]
        slash_commands.extend(post_slash_commands)

    def slash_sequence(process_name, activate_block):
        chunks = []
        for idx, command_text in enumerate(slash_commands):
            command_lit = as_literal(command_text)
            chunks.append(activate_block)
            chunks.append("delay 0.3" if idx == 0 else "delay 0.2")
            chunks.append(f'''
        tell application "System Events"
          tell process "{process_name}"
            keystroke "{command_lit}"
            delay 0.25
            key code 36
          end tell
        end tell
        delay 0.7
        ''')
        return "\n".join(chunks)

    if target == "iTerm2":
        if is_non_claude_engine:
            script = f'''
            tell application "iTerm2"
              activate
              set newWin to (create window with default profile)
              tell current session of newWin
                write text "{cmd_lit}"
              end tell
            end tell
            return "ok"
            '''
        else:
            command_sequence = slash_sequence(
                "iTerm2",
                'tell application "iTerm2" to activate',
            )
            script = f'''
        tell application "iTerm2"
          activate
          set newWin to (create window with default profile)
          tell current session of newWin
            write text "{cmd_lit}"
          end tell
        end tell
        delay 2.0
        {command_sequence}
        return "ok"
        '''
    else:
        # Terminal.app: explicitly create a new window, hold onto it, and keep
        # it frontmost across the keystrokes. `do script` returns a tab whose
        # window we can reference.
        if is_non_claude_engine:
            script = f'''
            tell application "Terminal"
              activate
              do script "{cmd_lit}"
            end tell
            return "ok"
            '''
        else:
            command_sequence = slash_sequence(
                "Terminal",
                'tell application "Terminal"\n'
                '          activate\n'
                '          set frontmost of (first window whose id is winId) to true\n'
                '        end tell',
            )
            script = f'''
        set winId to 0
        tell application "Terminal"
          activate
          set newTab to do script "{cmd_lit}"
          set winId to id of window 1
        end tell
        delay 2.0
        {command_sequence}
        return "ok"
        '''

    # Run the osascript in the background (captures stderr to a log for debugging).
    try:
        log_dir = _core.repo_log_dir(ctx["repo_path"])
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"jump-{(session_id or 'x')[:8]}.log"
        lf = open(log_path, "w")
        subprocess.Popen(["osascript", "-e", script], stdout=lf, stderr=lf)
    except (FileNotFoundError, OSError) as e:
        return {"ok": False, "error": str(e)}
    result = {"ok": True, "terminal_app": target, "command": command}
    # GH #71 (mechanism 2) — the terminal we just opened (`claude --resume`)
    # becomes the live process driving this session; a previously-spawned idle
    # headless is now a stale fallback. Retire it right here so CCC won't route
    # to it after the terminal closes. Claude-only and never a busy headless.
    if not is_non_claude_engine:
        retired = _core._retire_idle_headless_for_session(
            session_id, reason="launch-terminal", defer_if_busy=True)
        if retired.get("retired"):
            result["retired_headless_pid"] = retired.get("pid")
        elif retired.get("deferred"):
            # Busy mid-turn — the watcher retires it the moment it goes idle.
            result["headless_retire_deferred"] = True
    return result


_tty_keystroke_locks_guard = threading.Lock()
_tty_keystroke_locks: dict = {}


def _tty_keystroke_lock(tty):
    """Per-tty lock so two concurrent keystroke injections (e.g. a composer
    send racing the terminal-queue-watcher's drain) can't both write their
    body into the terminal's pending input line before either submits —
    CCC-797: observed as two unrelated messages splicing into one line with
    no separator.
    """
    key = str(tty or "")
    with _tty_keystroke_locks_guard:
        lock = _tty_keystroke_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _tty_keystroke_locks[key] = lock
        return lock


def inject_input_via_keystroke(tty, terminal_app, text, submit_key="return"):
    """Serialize per-tty, then delegate to `_inject_input_via_keystroke_impl`.

    Holding the lock for the full write-body + delay + submit sequence is
    what CCC-797 needed: without it, a second injection's `write text` /
    `do script` can land in the terminal's pending input line before the
    first one's submit keystroke fires, gluing two unrelated messages
    together with no separator.
    """
    with _tty_keystroke_lock(tty):
        return _inject_input_via_keystroke_impl(tty, terminal_app, text, submit_key=submit_key)


# Claude Code 2.1.228+ writes a ready-made send-keys target into its peer
# registry when it detects tmux: "<session>:@<window>.%<pane>" (e.g.
# "ccc-tmux-probe:@0.%0"). Validate strictly before interpolating into a
# tmux command line — the value comes from an on-disk JSON file.
_REGISTRY_TMUX_TARGET_RE = re.compile(r"^[A-Za-z0-9_.=-]+:@\d+\.%\d+$")


def _registry_tmux_target_for_session(session_id):
    """The session's validated tmux pane target, or "" when not tmux-hosted."""
    try:
        meta = _core._load_session_registry().get(session_id) or {}
    except Exception:
        return ""
    target = str(meta.get("tmux") or "").strip()
    if not target or not _REGISTRY_TMUX_TARGET_RE.match(target):
        return ""
    if not shutil.which("tmux"):
        return ""
    return target


def _inject_via_tmux(target, text):
    """Paste `text` into a Claude session's tmux pane and submit with Enter.

    Exact-address delivery: no terminal-app window matching, no macOS
    Automation/Accessibility permission, and it reaches detached sessions
    that AppleScript cannot see at all. Bracketed paste (-p) lands multi-line
    text as one paste burst instead of a cascade of Enter keypresses; the
    submit is a separate Enter, mirroring the two-burst semantics the TUIs
    already rely on in _inject_input_via_keystroke_impl.
    """
    buf = f"ccc-inject-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        load = subprocess.run(
            ["tmux", "load-buffer", "-b", buf, "-"],
            input=text, text=True, capture_output=True, timeout=10,
        )
        if load.returncode != 0:
            return {"ok": False, "via": "tmux", "tmux_target": target,
                    "error": (load.stderr or "").strip() or "tmux load-buffer failed"}
        paste = subprocess.run(
            ["tmux", "paste-buffer", "-p", "-d", "-b", buf, "-t", target],
            capture_output=True, timeout=10,
        )
        if paste.returncode != 0:
            return {"ok": False, "via": "tmux", "tmux_target": target,
                    "error": (paste.stderr or "").strip() or "tmux paste-buffer failed"}
        submit = subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            capture_output=True, timeout=10,
        )
        if submit.returncode != 0:
            return {"ok": False, "via": "tmux", "tmux_target": target,
                    "error": (submit.stderr or "").strip() or "tmux send-keys failed"}
        return {"ok": True, "via": "tmux", "tmux_target": target}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return {"ok": False, "via": "tmux", "tmux_target": target,
                "error": str(e) or "tmux failed"}
    finally:
        # paste-buffer -d already consumed the buffer; this is the safety net
        # for the load-then-fail paths.
        try:
            subprocess.run(["tmux", "delete-buffer", "-b", buf],
                           capture_output=True, timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass


def inject_input_via_tmux(target, text):
    """Serialize per-pane (same CCC-797 splice hazard as keystrokes), then send."""
    with _tty_keystroke_lock(f"tmux:{target}"):
        return _inject_via_tmux(target, text)


def _inject_input_via_keystroke_impl(tty, terminal_app, text, submit_key="return"):
    """Find the terminal tab for `tty`, then send `text` + a submit key to it.

    Two stages, both inside one osascript call:
      1. Native terminal input API (`do script` for Terminal.app,
         `write text` for iTerm2) types the text into the TTY without
         needing System Events keystroke permissions for the body.
      2. Submit. For Return (the normal case) this is a second native
         write of an empty string after a short delay: the TUIs treat a
         CR glued to the body as a literal newline inside a paste burst,
         but a lone CR in its own burst as a real Enter keypress. Writing
         to the tab object needs no focus and no Accessibility grant, and
         cannot land in the wrong window. For Tab (queueing Codex slash
         commands while a turn is running) there is no native tty write
         that reads as a Tab keypress, so that path still activates the
         terminal and emits a System Events keystroke (briefly stealing
         focus; the previously-frontmost process is restored after).
    """
    submit_key = str(submit_key or "return").strip().lower()
    submit_key_code = 48 if submit_key == "tab" else 36
    submit_label = "Tab" if submit_key_code == 48 else "Return"
    native_submit = submit_key_code != 48
    tty_short = tty.replace("/dev/", "")
    tty_full = "/dev/" + tty_short

    # Escape text for AppleScript string literal
    def as_lit(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')
    text_lit = as_lit(text)

    def failure_payload(raw_error, *, timed_out=False):
        detail = (raw_error or "").strip()
        low = detail.lower()
        payload = {
            "ok": False,
            "via": "terminal-control",
            "tty": tty,
            "terminal_app": terminal_app,
            "error": detail or "AppleScript failed",
        }
        if timed_out or ("osascript" in low and "timed out" in low):
            payload.update({
                "code": "macos_automation_timeout",
                "error": (
                    f"macOS did not finish the CCC terminal-control request to "
                    f"{terminal_app}. If a permission dialog mentions "
                    "app_mode_loader, app_node, Python, or osascript, click "
                    "Allow; then grant the app running CCC Automation and "
                    "Accessibility access for Terminal and System Events if "
                    "macOS asks again."
                ),
                "detail": detail,
            })
        elif "not allowed to send keystrokes" in low or ("system events" in low and "1002" in low):
            payload.update({
                "code": "macos_keystroke_permission",
                "error": (
                    "macOS blocked CCC from typing into the terminal. Allow the app "
                    "running CCC to use Accessibility in System Settings > Privacy "
                    "& Security, then retry."
                ),
                "detail": detail,
            })
        elif (
            "not authorized to send apple events" in low
            or "not authorised to send apple events" in low
            or "not permitted to send apple events" in low
            or "automation" in low and "not" in low and "allow" in low
        ):
            payload.update({
                "code": "macos_automation_permission",
                "error": (
                    f"macOS blocked CCC from controlling {terminal_app}. Allow the "
                    f"app running CCC to control {terminal_app} in System Settings "
                    "> Privacy & Security > Automation, then retry."
                ),
                "detail": detail,
            })
        return payload

    if terminal_app == "iTerm2" and native_submit:
        # iTerm2: find the session by tty, write the body via the native
        # session API, then after a beat write a lone empty line — the
        # TUI reads that isolated CR as an Enter keypress. No focus, no
        # System Events.
        script = f'''
        tell application "iTerm2"
          set foundSession to missing value
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              repeat with j from 1 to (count of tabs of w)
                try
                  set t to tab j of w
                  repeat with s in sessions of t
                    try
                      if tty of s is "{tty_full}" then
                        set foundSession to s
                        exit repeat
                      end if
                    end try
                  end repeat
                  if foundSession is not missing value then exit repeat
                end try
              end repeat
              if foundSession is not missing value then exit repeat
            end try
          end repeat
          if foundSession is missing value then return "notfound"
          tell foundSession to write text "{text_lit}"
          delay 0.3
          set submitErr to ""
          try
            tell foundSession to write text ""
          on error errMsg
            set submitErr to errMsg
          end try
        end tell
        if submitErr is not "" then return "ok-no-submit:" & submitErr
        return "ok"
        '''
    elif terminal_app == "iTerm2":
        # iTerm2: find the session by tty, write the body via the
        # native session API (no focus needed), then activate iTerm and
        # emit a real submit keystroke so the TUI accepts it.
        script = f'''
        set prevPid to 0
        try
          tell application "System Events" to set prevPid to unix id of first application process whose frontmost is true
        end try
        tell application "iTerm2"
          set foundSession to missing value
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              repeat with j from 1 to (count of tabs of w)
                try
                  set t to tab j of w
                  repeat with s in sessions of t
                    try
                      if tty of s is "{tty_full}" then
                        set foundSession to s
                        select w
                        tell w to select t
                        select s
                        exit repeat
                      end if
                    end try
                  end repeat
                  if foundSession is not missing value then exit repeat
                end try
              end repeat
              if foundSession is not missing value then exit repeat
            end try
          end repeat
          if foundSession is missing value then return "notfound"
          tell foundSession to write text "{text_lit}"
          activate
        end tell
        delay 0.15
        set submitErr to ""
        try
          tell application "System Events"
            tell process "iTerm2"
              key code {submit_key_code}
            end tell
          end tell
        on error errMsg
          set submitErr to errMsg
        end try
        delay 0.05
        try
          if prevPid is not 0 then
            tell application "System Events" to set frontmost of first application process whose unix id is prevPid to true
          end if
        end try
        if submitErr is not "" then return "ok-no-submit:" & submitErr
        return "ok"
        '''
    elif native_submit:
        # Terminal.app: find the tab by tty, send text through Terminal's
        # native `do script ... in tab` API, then after a beat send a lone
        # empty `do script` — its bare CR arrives in its own input burst,
        # which the TUI reads as an Enter keypress. No focus, no System
        # Events.
        script = f'''
        tell application "Terminal"
          set foundTab to missing value
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              repeat with j from 1 to (count of tabs of w)
                try
                  set t to tab j of w
                  if tty of t is "{tty_full}" then
                    set foundTab to t
                    exit repeat
                  end if
                end try
              end repeat
              if foundTab is not missing value then exit repeat
            end try
          end repeat
          if foundTab is missing value then return "notfound"
          do script "{text_lit}" in foundTab
          delay 0.3
          set submitErr to ""
          try
            do script "" in foundTab
          on error errMsg
            set submitErr to errMsg
          end try
        end tell
        if submitErr is not "" then return "ok-no-submit:" & submitErr
        return "ok"
        '''
    else:
        # Terminal.app: find the tab by tty, focus it, send text through
        # Terminal's native `do script ... in tab` API, then activate
        # Terminal and emit a real submit keystroke via System Events so
        # the TUI accepts it. Target "process Terminal" explicitly so the
        # keystroke reaches the right process even if focus briefly shifts.
        script = f'''
        set prevPid to 0
        try
          tell application "System Events" to set prevPid to unix id of first application process whose frontmost is true
        end try
        tell application "Terminal"
          set foundWin to missing value
          set foundTab to missing value
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              repeat with j from 1 to (count of tabs of w)
                try
                  set t to tab j of w
                  if tty of t is "{tty_full}" then
                    set foundWin to w
                    set foundTab to t
                    exit repeat
                  end if
                end try
              end repeat
              if foundTab is not missing value then exit repeat
            end try
          end repeat
          if foundTab is missing value then return "notfound"
          do script "{text_lit}" in foundTab
          activate
          set index of foundWin to 1
          set selected of foundTab to true
        end tell
        delay 0.15
        set submitErr to ""
        try
          tell application "System Events"
            tell process "Terminal"
              key code {submit_key_code}
            end tell
          end tell
        on error errMsg
          set submitErr to errMsg
        end try
        delay 0.05
        try
          if prevPid is not 0 then
            tell application "System Events" to set frontmost of first application process whose unix id is prevPid to true
          end if
        end try
        if submitErr is not "" then return "ok-no-submit:" & submitErr
        return "ok"
        '''

    def _run():
        timeout_s = 5
        try:
            return subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return failure_payload(
                f"osascript timed out after {timeout_s}s while controlling {terminal_app}",
                timed_out=True,
            )
        except FileNotFoundError as e:
            return e

    out = _run()
    if isinstance(out, dict):
        return out
    if isinstance(out, Exception):
        return failure_payload(str(out))
    result_str = (out.stdout or "").strip()
    # Auto-retry once on notfound — the tab often becomes findable ~200ms later
    # after a focus/Spaces transition settles.
    if result_str == "notfound":
        time.sleep(0.2)
        out = _run()
        if isinstance(out, dict):
            return out
        if isinstance(out, Exception):
            return failure_payload(str(out))
        result_str = (out.stdout or "").strip()
    if out.returncode != 0:
        return failure_payload((out.stderr or "").strip() or "AppleScript failed")
    if result_str.startswith("ok-no-submit:"):
        # Body was typed but the submit step failed. On the native path
        # that means the follow-up empty write to the tab errored; on the
        # Tab-keystroke path it typically means macOS Accessibility hasn't
        # been granted to osascript. Either way the text sits in the TUI
        # input buffer and the user has to press Enter.
        detail = result_str.split(":", 1)[1].strip()
        payload = {
            "ok": True,
            "via": "terminal-control",
            "tty": tty,
            "terminal_app": terminal_app,
            "submitted": False,
            "detail": detail,
        }
        if native_submit:
            payload["warning"] = (
                "Text typed but the follow-up Enter write to the terminal "
                "tab failed; press Enter in the session to send it."
            )
            payload["code"] = "native_submit_failed"
        else:
            payload["warning"] = (
                f"Text typed but auto-submit with {submit_label} was blocked. Grant "
                "Accessibility to osascript in System Settings > "
                "Privacy & Security > Accessibility, then retry."
            )
            payload["code"] = "macos_keystroke_permission"
        return payload
    if result_str == "notfound":
        return {
            "ok": False,
            "via": "terminal-control",
            "tty": tty,
            "terminal_app": terminal_app,
            "error": (
                f"No {terminal_app} tab found for {tty_short}; the tab may be "
                "hidden, on another Space, or behind a fullscreen app"
            ),
        }
    return {
        "ok": True,
        "tty": tty,
        "terminal_app": terminal_app,
        "via": "terminal-control",
        "submitted": True,
        "submit_key": "tab" if submit_key_code == 48 else "return",
    }


def interrupt_input_via_keystroke(tty, terminal_app, key_code=53):
    """Focus the terminal tab for `tty`, then send a single key via System Events.

    Mirrors `inject_input_via_keystroke` but delivers a lone keypress instead of
    text — with the default Esc (key code 53), Claude Code's TUI treats it as
    cancel-the-current-stream when a response is in flight, and as
    clear-input-buffer when one isn't. Callers can pass another `key_code` to
    drive a picker (e.g. Return=36 to accept a permission prompt). Same focus +
    restore-prev-process dance so the user's browser doesn't stay buried.
    """
    tty_short = tty.replace("/dev/", "")
    tty_full = "/dev/" + tty_short

    if terminal_app == "iTerm2":
        script = f'''
        set prevPid to 0
        try
          tell application "System Events" to set prevPid to unix id of first application process whose frontmost is true
        end try
        tell application "iTerm2"
          set found to false
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              repeat with j from 1 to (count of tabs of w)
                try
                  set t to tab j of w
                  repeat with s in sessions of t
                    try
                      if tty of s is "{tty_full}" then
                        select w
                        tell w to select t
                        select s
                        set found to true
                        exit repeat
                      end if
                    end try
                  end repeat
                  if found then exit repeat
                end try
              end repeat
              if found then exit repeat
            end try
          end repeat
          if not found then return "notfound"
          activate
        end tell
        delay 0.15
        tell application "System Events"
          tell process "iTerm2"
            key code {key_code}
          end tell
        end tell
        delay 0.08
        try
          if prevPid is not 0 then
            tell application "System Events" to set frontmost of first application process whose unix id is prevPid to true
          end if
        end try
        return "ok"
        '''
    else:
        script = f'''
        set prevPid to 0
        try
          tell application "System Events" to set prevPid to unix id of first application process whose frontmost is true
        end try
        tell application "Terminal"
          set foundWin to missing value
          set foundTab to missing value
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              repeat with j from 1 to (count of tabs of w)
                try
                  set t to tab j of w
                  if tty of t is "{tty_full}" then
                    set foundWin to w
                    set foundTab to t
                    exit repeat
                  end if
                end try
              end repeat
              if foundTab is not missing value then exit repeat
            end try
          end repeat
          if foundTab is missing value then return "notfound"
          activate
          set index of foundWin to 1
          set selected of foundTab to true
        end tell
        delay 0.15
        tell application "System Events"
          tell process "Terminal"
            key code {key_code}
          end tell
        end tell
        delay 0.08
        try
          if prevPid is not 0 then
            tell application "System Events" to set frontmost of first application process whose unix id is prevPid to true
          end if
        end try
        return "ok"
        '''

    def _run():
        try:
            return subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return e

    out = _run()
    if isinstance(out, Exception):
        return {"ok": False, "error": str(out)}
    result_str = (out.stdout or "").strip()
    if result_str == "notfound":
        time.sleep(0.2)
        out = _run()
        if isinstance(out, Exception):
            return {"ok": False, "error": str(out)}
        result_str = (out.stdout or "").strip()
    if out.returncode != 0:
        return {"ok": False, "error": (out.stderr or "").strip() or "AppleScript failed"}
    if result_str == "notfound":
        return {"ok": False, "error": f"No {terminal_app} tab found for {tty_short} - tab may be hidden, on another Space, or behind a fullscreen app"}
    return {"ok": True, "tty": tty}


def respond_claude_permission(session_id, decision):
    """Answer a pending Claude Code permission prompt from the dashboard.

    Claude has no approval API the way Codex does — a permission prompt reaches
    CCC only as the Notification-hook `_needs_approval` marker, and the only way
    to answer it is to drive the interactive TUI picker. Claude highlights "Yes"
    (option 1) by default, so a lone Return approves the tool call once and Esc
    denies it. Delivered as a single System Events keystroke, so it is macOS +
    live-TTY only (the keystroke route does not exist on Linux/headless hosts).

    Guarded to a session that is (a) Claude — Codex/Kimi/etc. have their own
    approval routes, (b) live on a real tty, and (c) actually parked on a
    blocking permission prompt.
    """
    if not session_id:
        return {"ok": False, "error": "missing session_id"}
    decision = (decision or "accept").strip().lower()
    if decision not in ("accept", "decline"):
        return {"ok": False, "error": f"unknown decision: {decision}"}
    if sys.platform != "darwin":
        _core._log_macos_only("claudePermissionAnswer")
        return {
            "ok": False,
            "code": "macos_only",
            "error": (
                "Answering a permission prompt from CCC is macOS-only today — "
                "approve or deny it in the session's own terminal."
            ),
        }
    if not isClaudeSource_backend(session_id):
        return {
            "ok": False,
            "code": "not_claude",
            "error": "This isn't a Claude session; use its own approval control.",
        }
    cwd = _core.find_session_cwd(session_id)
    status = _core.session_live_status(session_id, cwd)
    tty = status.get("tty")
    if not status.get("live") or not _core._is_real_tty(tty):
        return {
            "ok": False,
            "code": "not_live_tty",
            "error": "No live terminal for this session to answer the prompt in.",
        }
    if not _core._notification_blocks_inject(session_id):
        return {
            "ok": False,
            "code": "no_pending_prompt",
            "error": "No permission prompt is pending for this session.",
        }
    # Return (36) accepts the highlighted "Yes"; Esc (53) denies.
    key_code = 36 if decision == "accept" else 53
    result = interrupt_input_via_keystroke(
        tty, status.get("terminal_app") or "Terminal", key_code=key_code
    )
    if isinstance(result, dict) and result.get("ok"):
        # The prompt has been answered — drop the stale marker so the strip
        # stops showing "Needs approval" before the next hook event lands.
        # PostToolUse (accept) or the resumed turn (deny) would clear it anyway.
        try:
            (_core.SIDECAR_STATE_DIR / f"{session_id}_needs_approval.json").unlink()
        except OSError:
            pass
        result["decision"] = decision
    return result


def isClaudeSource_backend(session_id):
    """True when `session_id` is a Claude session (not Codex/Gemini/Cursor/
    Kimi/Antigravity/Hermes). Mirrors the frontend `isClaudeSource` gate for
    Claude-only server actions."""
    return not (
        _core._is_codex_session(session_id)
        or _core._is_gemini_session(session_id)
        or _core._is_cursor_session(session_id)
        or _core._is_kimi_session(session_id)
        or _core._is_antigravity_session(session_id)
        or _core._is_hermes_session(session_id)
    )


def focus_terminal_by_tty(tty, terminal_app):
    """Bring the terminal window/tab backing `tty` to the front.

    `tty` is like "ttys008". `terminal_app` is the friendly name from
    _TERMINAL_APPS. Returns {ok, error}.
    """
    if platform.system() != "Darwin":
        _core._log_macos_only("terminalJump")
        return {"ok": False, "error": "jump to terminal is macOS-only today"}
    if not _core._is_real_tty(tty):
        return {"ok": False, "error": "No tty available"}
    if not terminal_app:
        return {"ok": False, "error": "Unknown terminal app"}

    tty_short = tty.replace("/dev/", "")
    tty_full = "/dev/" + tty_short

    if terminal_app == "iTerm2":
        # Defensive iteration: phantom/minimized windows can throw errors and
        # abort the whole loop. Use index-based iteration with try/on-error.
        script = f'''
        tell application "iTerm2"
          set found to false
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              set tabCount to count of tabs of w
              repeat with j from 1 to tabCount
                try
                  set t to tab j of w
                  set sessList to sessions of t
                  repeat with s in sessList
                    try
                      if tty of s is "{tty_full}" then
                        select w
                        tell w to select t
                        select s
                        set found to true
                        exit repeat
                      end if
                    end try
                  end repeat
                  if found then exit repeat
                end try
              end repeat
              if found then exit repeat
            end try
          end repeat
          if found then
            activate
            return "ok"
          else
            return "notfound"
          end if
        end tell
        '''
    elif terminal_app == "Terminal":
        # Defensive iteration: Terminal.app can have phantom windows whose
        # `tabs` accessor throws, which would abort a naive `repeat with w in windows`.
        # We use index-based loops with try/on-error to skip them.
        script = f'''
        tell application "Terminal"
          set foundWin to missing value
          set foundTab to missing value
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              set tabCount to count of tabs of w
              repeat with j from 1 to tabCount
                try
                  set t to tab j of w
                  if tty of t is "{tty_full}" then
                    set foundWin to w
                    set foundTab to t
                    exit repeat
                  end if
                end try
              end repeat
              if foundTab is not missing value then exit repeat
            end try
          end repeat
          if foundTab is not missing value then
            set selected of foundTab to true
            try
              set index of foundWin to 1
            end try
            activate
            return "ok"
          else
            return "notfound"
          end if
        end tell
        '''
    elif terminal_app == "Ghostty":
        # Ghostty doesn't expose tab-level AppleScript; best we can do is activate it
        script = 'tell application "Ghostty" to activate\nreturn "ok"'
    else:
        # Generic fallback: just activate the app
        script = f'tell application "{terminal_app}" to activate\nreturn "ok"'

    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"ok": False, "error": str(e)}
    result = (out.stdout or "").strip()
    if out.returncode != 0:
        return {"ok": False, "error": (out.stderr or "").strip() or "AppleScript failed"}
    if result == "notfound":
        return {"ok": False, "error": f"No {terminal_app} tab found for {tty_short}"}
    return {"ok": True, "terminal_app": terminal_app}


_CWD_RELOCATION_PRUNE_DIRS = {
    ".git", ".hg", ".svn", ".claude", ".codex", "__pycache__",
    "node_modules", ".next", "dist", "build", ".venv", "venv",
}


def _path_is_within(child, parent):
    try:
        c = Path(child).expanduser().resolve()
        p = Path(parent).expanduser().resolve()
        return c == p or p in c.parents
    except (OSError, ValueError, RuntimeError):
        return False


def _relocation_search_roots(session_id, missing_cwd):
    roots = []
    seen = set()

    def add(raw):
        if not raw:
            return
        try:
            p = Path(raw).expanduser().resolve()
        except (OSError, ValueError, RuntimeError):
            return
        if not p.is_dir():
            return
        # Avoid accidental whole-home scans; repo pins / marked ancestors give
        # us enough locality for moved project folders.
        try:
            if p == Path.home().resolve() or p == Path("/"):
                return
        except OSError:
            return
        s = str(p)
        if s not in seen:
            seen.add(s)
            roots.append(p)

    try:
        pin = _core._load_repo_pins().get(session_id)
    except Exception:
        pin = None
    add(pin)

    try:
        cwd_path = Path(missing_cwd).expanduser()
        existing = None
        for candidate in (cwd_path, *cwd_path.parents):
            if candidate.is_dir():
                existing = candidate
                break
        if existing:
            cur = existing
            for _ in range(4):
                add(cur)
                parent = cur.parent
                if parent == cur:
                    break
                cur = parent
    except (OSError, ValueError, RuntimeError):
        pass

    try:
        for repo in _core._known_repo_paths():
            if _path_is_within(missing_cwd, repo):
                add(repo)
    except Exception:
        pass

    return roots


def _first_existing_dir(*paths):
    """Return the first path that exists as a directory; None if none do.

    Used to pick an "effective" cwd for row metadata that may have been
    captured from a long-dead worktree path. Without this, Launch in
    Terminal would build `cd '/.../no-such-dir' && resume`, fail, and
    leave the user in their home directory.
    """
    for p in paths:
        if not p:
            continue
        try:
            if Path(p).is_dir():
                return p
        except (OSError, ValueError, RuntimeError):
            continue
    return None


def _fallback_cwd_for_deleted_worktree(cwd, parent_session_id=None):
    """Return a usable directory when `cwd` is a deleted git worktree.

    Continuing a session in a new session from a worktree that has since been
    removed should land in the parent repo instead of failing with an invalid
    cwd error. Tries the parent session's recorded repo_path first, then common
    CCC worktree path layouts.
    """
    if not cwd:
        return None

    # 1. Parent session's spawn registry: for CCC worktree spawns repo_path is
    #    the parent repo. This is the most reliable source.
    if parent_session_id:
        try:
            entry = _core._spawn_registry_entry_for_session(parent_session_id)
        except Exception:
            entry = None
        if entry:
            repo_path = entry.get("repo_path")
            if repo_path:
                existing = _core._first_existing_dir(repo_path)
                if existing:
                    return str(existing)

    # 2. CCC current layout: <parent>/<repo>-wt/<slug> -> parent repo is
    #    <parent>/<repo>.
    try:
        p = Path(cwd).expanduser()
        parts = p.parts
        for i in range(len(parts) - 1, 0, -1):
            part = parts[i]
            if part.endswith("-wt"):
                candidate = Path(*parts[:i]) / part[:-3]
                existing = _core._first_existing_dir(candidate)
                if existing:
                    return str(existing)
    except (OSError, ValueError, RuntimeError):
        pass

    # 3. Legacy CCC layout: <parent>/<repo>-wt-<slug> -> parent repo is
    #    <parent>/<repo>. Split from the right so repo names that contain `-wt-`
    #    still resolve to the longest matching prefix.
    try:
        p = Path(cwd).expanduser()
        name = p.name
        parent = p.parent
        if "-wt-" in name:
            segments = name.split("-wt-")
            for i in range(len(segments) - 1, 0, -1):
                repo_name = "-wt-".join(segments[:i])
                candidate = parent / repo_name
                existing = _core._first_existing_dir(candidate)
                if existing:
                    return str(existing)
    except (OSError, ValueError, RuntimeError):
        pass

    # 4. Nested worktree layouts: <repo>/.worktrees/<name> and
    #    <repo>/.claude/worktrees/<name>.
    for marker in ("/.claude/worktrees/", "/.worktrees/"):
        if marker in cwd:
            base = cwd.split(marker)[0]
            existing = _core._first_existing_dir(base)
            if existing:
                return str(existing)

    return None


def _load_session_cwd_overrides():
    """Load persisted user cwd overrides (CCC-128). Best-effort."""
    try:
        data = json.loads(_core._SESSION_CWD_OVERRIDE_FILE.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if k and v:
                _core._session_cwd_override[str(k)] = str(v)


def _save_session_cwd_overrides():
    """Atomic write of the cwd-override map. Best-effort."""
    try:
        _core._SESSION_CWD_OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(_core._SESSION_CWD_OVERRIDE_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_core._session_cwd_override, f, indent=2)
        os.replace(tmp, _core._SESSION_CWD_OVERRIDE_FILE)
    except OSError:
        pass


def _set_session_cwd_override(session_id, cwd):
    """Point a session at a user-chosen folder. Raises ValueError if the path
    isn't an existing directory. Invalidates the resolution cache so the new
    cwd takes effect immediately for the workspace pill, file-open, resume."""
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("missing session_id")
    p = Path(str(cwd or "").strip()).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"not a directory: {p}")
    _core._session_cwd_override[sid] = str(p)
    _core._session_cwd_cache.pop(sid, None)
    _save_session_cwd_overrides()
    return str(p)


def _relocate_missing_session_cwd(session_id, cwd):
    """Best-effort repair for transcripts whose recorded cwd was moved.

    Claude transcripts are immutable history, so a folder reorg can leave old
    `cwd` values pointing at a path that no longer exists. For display and
    repo-context purposes, use the transcript's own absolute tool paths as
    evidence: find a same-named directory under nearby project roots where
    those relative files now exist.
    """
    raw = str(cwd or "").strip()
    if not session_id or not raw:
        return None
    cache_key = (session_id, raw)
    if cache_key in _core._session_cwd_relocation_cache:
        cached = _core._session_cwd_relocation_cache[cache_key]
        # Negative cache: short-circuit. Positive cache: revalidate that
        # the cached target still exists — if a worktree was deleted since
        # the cache was written, drop it and fall through to a fresh walk.
        if cached is None:
            return None
        try:
            if Path(cached).is_dir():
                return cached
        except (OSError, ValueError, RuntimeError):
            pass
        with _core._session_cwd_relocation_cache_lock:
            _core._session_cwd_relocation_cache.pop(cache_key, None)
            _core._session_cwd_relocation_cache_dirty = True

    try:
        cwd_path = Path(raw).expanduser()
        if cwd_path.is_dir():
            result = str(cwd_path.resolve())
            with _core._session_cwd_relocation_cache_lock:
                _core._session_cwd_relocation_cache[cache_key] = result
                _core._session_cwd_relocation_cache_dirty = True
            return result
    except (OSError, ValueError, RuntimeError):
        with _core._session_cwd_relocation_cache_lock:
            _core._session_cwd_relocation_cache[cache_key] = None
            _core._session_cwd_relocation_cache_dirty = True
        return None

    # Past this point we're about to walk the filesystem. Honor the
    # per-request budget so a single cold scan can't burn 40s on
    # worktree-heavy repos. Do NOT cache the skip — let the next request
    # try again so the cache fills progressively.
    if _core._relocation_budget_exhausted():
        return None

    basename = cwd_path.name
    if not basename:
        with _core._session_cwd_relocation_cache_lock:
            _core._session_cwd_relocation_cache[cache_key] = None
            _core._session_cwd_relocation_cache_dirty = True
        return None

    try:
        file_paths, _cd_targets = _core._scan_session_tool_paths(session_id)
    except Exception:
        file_paths = []
    prefix = raw.rstrip("/") + "/"
    rel_paths = []
    rel_seen = set()
    for fp in file_paths:
        if not isinstance(fp, str) or not fp.startswith(prefix):
            continue
        rel = fp[len(prefix):].lstrip("/")
        if not rel or rel in rel_seen:
            continue
        rel_seen.add(rel)
        rel_paths.append(rel)
        if len(rel_paths) >= 25:
            break

    roots = _relocation_search_roots(session_id, raw)
    best = []
    candidate_seen = set()
    # Per-root visit cap. Dialed down from 8000 → 2000: even with the
    # per-request time budget guarding overall latency, a single root with
    # 8000 dirs of unrelated content can stall this loop for seconds. 2000
    # is enough to cover normal worktree layouts (BYM+Finie's worst case is
    # ~150 dirs per root) while keeping pathological roots bounded.
    try:
        visit_cap = int(os.environ.get("CCC_CWD_RELOCATION_VISIT_CAP", "2000"))
    except (TypeError, ValueError):
        visit_cap = 2000
    budget_hit = False
    for root in roots:
        if budget_hit:
            break
        visited = 0
        try:
            for dirpath, dirnames, _filenames in os.walk(root):
                visited += 1
                dirnames[:] = [
                    d for d in dirnames
                    if d not in _CWD_RELOCATION_PRUNE_DIRS and not d.startswith(".")
                ]
                if visited > visit_cap:
                    break
                if _core._relocation_budget_exhausted():
                    budget_hit = True
                    break
                p = Path(dirpath)
                if p.name != basename:
                    continue
                try:
                    p_key = str(p.resolve())
                except OSError:
                    p_key = str(p)
                if p_key in candidate_seen:
                    continue
                candidate_seen.add(p_key)
                score = 1 if not rel_paths else 0
                for rel in rel_paths:
                    try:
                        if (p / rel).exists():
                            score += 1
                    except OSError:
                        continue
                if score > 0:
                    best.append((score, len(p.parts), p))
        except OSError:
            continue

    # If the budget ran out mid-walk we may have an incomplete view; do not
    # cache so the next request can resume.
    if budget_hit and not best:
        return None
    if not best:
        with _core._session_cwd_relocation_cache_lock:
            _core._session_cwd_relocation_cache[cache_key] = None
            _core._session_cwd_relocation_cache_dirty = True
        return None
    best.sort(key=lambda item: (-item[0], item[1], str(item[2])))
    top_score = best[0][0]
    top = [p for score, _depth, p in best if score == top_score]
    if len(top) > 1 and rel_paths:
        # Multiple equally-supported targets means the transcript evidence is
        # ambiguous; keep the historical cwd rather than guessing wrong.
        with _core._session_cwd_relocation_cache_lock:
            _core._session_cwd_relocation_cache[cache_key] = None
            _core._session_cwd_relocation_cache_dirty = True
        return None
    try:
        result = str(top[0].resolve())
    except OSError:
        result = str(top[0])
    with _core._session_cwd_relocation_cache_lock:
        _core._session_cwd_relocation_cache[cache_key] = result
        _core._session_cwd_relocation_cache_dirty = True
    return result


def _resolve_session_cwd(session_id, cwd):
    if not cwd:
        return cwd
    try:
        p = Path(cwd).expanduser()
        if p.is_dir():
            return str(p.resolve())
    except (OSError, ValueError, RuntimeError):
        return cwd
    return _relocate_missing_session_cwd(session_id, cwd) or cwd


def _claude_subagent_parent_session_id(session_id):
    """Return the owning Claude session for a bare ``agent-*`` transcript id.

    Claude stores child transcripts at
    ``<project>/<parent-session>/subagents/agent-*.jsonl``.  Those child ids
    are searchable history references, but they are not independently
    resumable by the Claude CLI; input must resume their parent session.
    """
    sid = str(session_id or "").strip()
    if not re.fullmatch(r"agent-[A-Za-z0-9-]+", sid) or not _core.PROJECTS_ROOT.is_dir():
        return None
    try:
        candidates = _core.PROJECTS_ROOT.glob(f"*/*/subagents/{sid}.jsonl")
        for candidate in candidates:
            if not candidate.is_file():
                continue
            parent_sid = candidate.parent.parent.name
            if re.fullmatch(r"[0-9a-fA-F-]{8,}", parent_sid):
                return parent_sid
    except OSError:
        pass
    return None


# Workflow-run visibility. Claude Code's Workflow tool writes runs to
# <project>/<parent-sid>/subagents/workflows/<run-id>/ — four levels deep, so
# neither the one-level conversation glob nor the plain-subagent glob reaches
# them. Discovery piggybacks on find_conversations' existing project-dir
# iterdir (subdirs are yielded for free), so the scan below only ever runs
# for sessions that actually spawned subagents. The journal parse is cached
# by (mtime_ns, size); per-agent status is re-stat'd each poll (a handful of
# stat calls, only for workflow-owning sessions) because a running agent
# keeps writing its transcript without touching the journal.
_WORKFLOW_JOURNAL_CACHE = {}
_WORKFLOW_JOURNAL_CACHE_LOCK = threading.Lock()
# session_id -> <project>/<sid> dir, but ONLY for sessions that own a
# subagents/workflows dir. Populated for free by the archive build's project
# iterdir so _rehydrate_archive_cached_rows can recompute run status per
# serve (journals change without any top-level transcript changing, so the
# corpus signature deliberately ignores them) without scanning the corpus.
# Replaced wholesale on each full build, so it cannot grow unboundedly.
_ARCHIVE_WORKFLOW_SESSION_DIRS = {}
# Agent descriptions come from the first user event of the agent transcript
# (the task prompt). That head line never changes, so cache by path.
_WORKFLOW_AGENT_DESC_CACHE = {}
# An agent with no journal result whose transcript was written within this
# window counts as running; past it, the run almost certainly died mid-flight
# (the workflow runtime does not write a tombstone line).
_WORKFLOW_RUNNING_FRESH_S = 5 * 60


def _parse_workflow_journal(journal_path):
    """Parse one run's journal.jsonl into per-agent completion state.

    Returns {"order": [agent_id, ...], "agents": {agent_id: {"done": bool,
    "failed": bool}}} or None when the journal is missing/unreadable. Cached
    by (mtime_ns, size) so a poll only re-parses journals that changed.
    """
    try:
        st = journal_path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    with _WORKFLOW_JOURNAL_CACHE_LOCK:
        hit = _core._WORKFLOW_JOURNAL_CACHE.get(str(journal_path))
        if hit and hit[0] == key:
            return hit[1]
    order = []
    agents = {}
    try:
        with open(journal_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                agent_id = ev.get("agentId") or ""
                if not agent_id:
                    continue
                entry = agents.setdefault(agent_id, {"done": False, "failed": False})
                if agent_id not in order:
                    order.append(agent_id)
                etype = ev.get("type")
                if etype == "result":
                    entry["done"] = True
                elif etype == "error":
                    entry["failed"] = True
    except OSError:
        return None
    parsed = {"order": order, "agents": agents}
    with _WORKFLOW_JOURNAL_CACHE_LOCK:
        _core._WORKFLOW_JOURNAL_CACHE[str(journal_path)] = (key, parsed)
        if len(_core._WORKFLOW_JOURNAL_CACHE) > 500:
            _core._WORKFLOW_JOURNAL_CACHE.clear()
    return parsed


def _workflow_agent_description(agent_path):
    """First user-message text of a workflow agent transcript, one line, <=80
    chars. The task prompt is the transcript's first event and never changes,
    so cache by path. Best-effort; '' on any failure."""
    key = str(agent_path)
    if key in _WORKFLOW_AGENT_DESC_CACHE:
        return _WORKFLOW_AGENT_DESC_CACHE[key]
    desc = ""
    try:
        with open(agent_path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") != "user":
                    continue
                content = (ev.get("message") or {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    continue
                desc = " ".join(text.split())[:80]
                break
    except (OSError, UnicodeDecodeError):
        pass
    _WORKFLOW_AGENT_DESC_CACHE[key] = desc
    if len(_WORKFLOW_AGENT_DESC_CACHE) > 1000:
        _WORKFLOW_AGENT_DESC_CACHE.clear()
    return desc


def _session_workflow_runs(session_dir, parent_sid, now=None):
    """Workflow-run dicts for one session subdir, [] when the session has none.

    ``session_dir`` is ``<project>/<parent-sid>`` — discovered for free during
    find_conversations' project-dir iterdir, so this only runs for sessions
    that actually spawned subagents.
    """
    wf_root = session_dir / "subagents" / "workflows"
    if not wf_root.is_dir():
        return []
    now = now if now is not None else time.time()
    try:
        run_dirs = sorted(
            (p for p in wf_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )
    except OSError:
        return []
    runs = []
    for run_dir in run_dirs:
        parsed = _core._parse_workflow_journal(run_dir / "journal.jsonl")
        order = list(parsed["order"]) if parsed else []
        states = parsed["agents"] if parsed else {}
        seen = set(order)
        latest_mtime = 0.0
        try:
            # Agents missing from the journal (truncated/lost write) still
            # surface from the transcript files on disk.
            for f in run_dir.iterdir():
                if not (f.name.startswith("agent-") and f.name.endswith(".jsonl")):
                    continue
                # Journal agentIds are the bare hex; file stems carry the
                # "agent-" prefix. Normalize or every agent lists twice.
                agent_id = f.name[:-6][len("agent-"):]
                if agent_id not in seen:
                    seen.add(agent_id)
                    order.append(agent_id)
        except OSError:
            pass
        agents = []
        for agent_id in order:
            agent_file = run_dir / f"agent-{agent_id}.jsonl"
            try:
                mtime = agent_file.stat().st_mtime
            except OSError:
                mtime = 0.0
            latest_mtime = max(latest_mtime, mtime)
            state = states.get(agent_id) or {}
            if state.get("done"):
                status = "done"
            elif state.get("failed"):
                status = "failed"
            elif mtime and (now - mtime) < _WORKFLOW_RUNNING_FRESH_S:
                status = "running"
            else:
                status = "interrupted"
            agents.append({
                "id": agent_id,
                "conv_id": f"{parent_sid}:agent-{agent_id}",
                "status": status,
                "description": _workflow_agent_description(agent_file),
            })
        if not agents:
            continue
        statuses = {a["status"] for a in agents}
        if "running" in statuses:
            run_status = "running"
        elif "failed" in statuses:
            run_status = "failed"
        elif "interrupted" in statuses:
            run_status = "interrupted"
        else:
            run_status = "done"
        runs.append({
            "run_id": run_dir.name,
            "status": run_status,
            "mtime": latest_mtime,
            "agents": agents,
        })
    # Most recent activity first.
    runs.sort(key=lambda r: r["mtime"], reverse=True)
    return runs


def find_session_cwd(session_id):
    """Locate the .jsonl for a session_id across ~/.claude/projects/*/ and return its cwd.

    Sessions may have been run in a worktree or other directory; `claude --resume`
    only finds them when run from the original cwd, so we need to `cd` there first.
    """
    if not session_id:
        return None
    # CCC-128: a user-set override wins over all automatic resolution.
    override = _core._session_cwd_override.get(session_id)
    if override:
        return override
    if session_id in _core._session_cwd_cache:
        return _core._session_cwd_cache[session_id]
    hermes_row = _core._hermes_session_row(session_id)
    if hermes_row:
        cwd = hermes_row.get("cwd")
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    if _core._is_kimi_session(session_id):
        snap = _core._acp_session_snapshot("kimi", session_id) or {}
        cwd = snap.get("cwd")
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    codex_row = _core._codex_thread_row(session_id)
    if codex_row:
        cwd = codex_row.get("cwd")
        path = _core._codex_rollout_path_from_row(codex_row)
        if path:
            try:
                tail = _core._extract_codex_tail_meta(path) or {}
                cwd = tail.get("cwd") or cwd
            except Exception:
                pass
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    gemini_path = _core._resolve_gemini_chat_path(session_id)
    if gemini_path:
        cwd = _core._gemini_project_root_for_chat(gemini_path)
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    if _core._is_antigravity_session(session_id):
        cwd = _core._extract_antigravity_cwd(session_id)
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    cursor_path = _core._cursor_transcript_path(session_id)
    if cursor_path:
        tail = _core._extract_cursor_tail_meta(cursor_path) or {}
        cwd = tail.get("cwd") or _core._cursor_cwd_from_transcript_path(cursor_path)
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    if _core._is_opencode_session(session_id):
        cwd = _core._opencode_session_cwd(session_id)
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    if _core._is_grok_session(session_id):
        cwd = _core.grok_session_cwd(session_id)
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    if _core._is_devin_cli_session(session_id):
        cwd = _core._devin_cli_session_cwd(_core._devin_cli_raw_id(session_id))
        if cwd:
            cwd = _resolve_session_cwd(session_id, cwd)
            _core._session_cwd_cache[session_id] = cwd
            return cwd
    if not _core.PROJECTS_ROOT.is_dir():
        return None

    jsonl_name = session_id + ".jsonl"
    for project_dir in _core.PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / jsonl_name
        if not candidate.is_file():
            continue
        # Read until we find the first event with a `cwd` field
        try:
            with open(candidate, "r") as f:
                for i, line in enumerate(f):
                    if i >= 40:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = ev.get("cwd")
                    if cwd:
                        cwd = _resolve_session_cwd(session_id, cwd)
                        _core._session_cwd_cache[session_id] = cwd
                        return cwd
        except (OSError, UnicodeDecodeError):
            continue
        # File matched but cwd wasn't in the first 40 lines — likely a very
        # young session that hasn't logged a user event yet. Try a sibling
        # .jsonl in the same project dir; sessions are grouped by cwd, so any
        # sibling with a cwd tells us ours too. We do NOT decode the project
        # dir name: Claude's encoding replaces '/' with '-' without escaping
        # literal hyphens, so `claude-command-center` round-trips as
        # `claude/command/center`, breaking `cd` in Launch-in-Terminal.
        for sibling in project_dir.glob("*.jsonl"):
            if sibling.name == jsonl_name:
                continue
            try:
                with open(sibling, "r") as f:
                    for i, line in enumerate(f):
                        if i >= 40:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        cwd = ev.get("cwd")
                        if cwd:
                            cwd = _resolve_session_cwd(session_id, cwd)
                            _core._session_cwd_cache[session_id] = cwd
                            return cwd
            except (OSError, UnicodeDecodeError):
                continue
        # Don't cache the miss — let a later call succeed once Claude writes
        # a cwd-bearing event. Callers treat None as "resume without cd".
        return None
    return None


# GitHub enrichment caches (issue titles, issue states, backlog cards) were
# in-process only, so every dashboard restart re-forked `gh issue list` on the
# first render -- ~2s of network inside /api/sessions, which is time the user
# spends staring at the "No sessions yet" empty state. These are enrichment,
# never correctness: a title or label a few minutes old is fine, a blocking
# network call in the render path is not.
#
# So: hydrate from disk once per process, serve stale immediately and refresh
# in the background, and only block when there is genuinely nothing on disk.
_GH_CACHE_DIR = _core.COMMAND_CENTER_STATE_DIR / "gh-cache"
_GH_CACHE_HYDRATED = set()
_GH_CACHE_LOCK = threading.Lock()
_GH_CACHE_REFRESHING = set()


def _hydrate_gh_cache(name, target):
    """Warm an in-process {key: {ts, data}} cache from disk, once per process."""
    with _GH_CACHE_LOCK:
        if name in _core._GH_CACHE_HYDRATED:
            return
        _core._GH_CACHE_HYDRATED.add(name)
    try:
        with (_GH_CACHE_DIR / (name + ".json")).open("r") as f:
            disk = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(disk, dict):
        return
    for key, value in disk.items():
        if isinstance(value, dict) and "ts" in value and key not in target:
            target[key] = value


def _persist_gh_cache(name, target):
    try:
        _GH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _GH_CACHE_DIR / (name + ".json.tmp")
        with tmp.open("w") as f:
            json.dump(target, f)
        tmp.replace(_GH_CACHE_DIR / (name + ".json"))
    except (OSError, TypeError, ValueError):
        pass


def _refresh_gh_cache_async(name, key, fetch):
    """Run one background refresh for (name, key); coalesce concurrent asks."""
    token = (name, key)
    with _GH_CACHE_LOCK:
        if token in _GH_CACHE_REFRESHING:
            return
        _GH_CACHE_REFRESHING.add(token)

    def _run():
        try:
            fetch()
        except Exception:
            pass
        finally:
            with _GH_CACHE_LOCK:
                _GH_CACHE_REFRESHING.discard(token)

    try:
        threading.Thread(target=_run, name="gh-cache-refresh", daemon=True).start()
    except RuntimeError:
        with _GH_CACHE_LOCK:
            _GH_CACHE_REFRESHING.discard(token)


_issue_titles_cache = {}  # repo_path -> {"ts": float, "data": dict}

# Per-repo issue state map: repo_path -> {"ts": float, "data": {number_str: ...}}
_issue_state_cache = {}


_desktop_meta_cache = {}
_desktop_meta_cache_mtime = 0


def _claude_desktop_sessions_root():
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude-code-sessions"
    )


def _claude_desktop_metadata_files(root=None):
    root = root or _claude_desktop_sessions_root()
    if not root.is_dir():
        return []
    try:
        return [p for p in root.glob("*/*/local_*.json") if p.is_file()]
    except OSError:
        return []


def _claude_desktop_metadata_index(root=None):
    root = root or _claude_desktop_sessions_root()
    index = {
        "by_name": {},
        "by_cli": {},
        "data_by_path": {},
        "workspace_dirs": [],
        "workspace_by_cwd": {},
    }
    if not root.is_dir():
        return index
    workspace_rows = []
    cwd_rows = {}
    try:
        org_dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return index
    for org_dir in org_dirs:
        try:
            workspace_dirs = [p for p in org_dir.iterdir() if p.is_dir()]
        except OSError:
            continue
        for workspace_dir in workspace_dirs:
            try:
                newest = workspace_dir.stat().st_mtime
            except OSError:
                newest = 0.0
            try:
                meta_paths = list(workspace_dir.glob("local_*.json"))
            except OSError:
                meta_paths = []
            for path in meta_paths:
                try:
                    st = path.stat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                newest = max(newest, st.st_mtime)
                index["by_name"][path.name] = path
                data = _read_json_object(path)
                if not data:
                    continue
                index["data_by_path"][str(path)] = data
                cli_sid = str(data.get("cliSessionId") or "").strip()
                if cli_sid and cli_sid not in index["by_cli"]:
                    index["by_cli"][cli_sid] = path
                try:
                    score = float(data.get("lastActivityAt") or 0) / 1000.0
                except (TypeError, ValueError):
                    score = 0.0
                if not score:
                    score = st.st_mtime
                for cwd in (data.get("cwd"), data.get("originCwd")):
                    cwd = str(cwd or "").strip()
                    if cwd:
                        cwd_rows.setdefault(cwd, []).append((score, workspace_dir))
            workspace_rows.append((newest, workspace_dir))
    workspace_rows.sort(key=lambda item: item[0], reverse=True)
    index["workspace_dirs"] = [p for _mtime, p in workspace_rows]
    for cwd, rows in cwd_rows.items():
        rows.sort(key=lambda item: item[0], reverse=True)
        index["workspace_by_cwd"][cwd] = [p for _score, p in rows]
    return index


def _claude_desktop_metadata_cache_key(root=None):
    root = root or _claude_desktop_sessions_root()
    if not root.is_dir():
        return None
    try:
        root_mtime = root.stat().st_mtime_ns
    except OSError:
        return None
    count = 0
    newest = 0
    total_mtime = 0
    total_size = 0
    for path in _core._claude_desktop_metadata_files(root):
        try:
            st = path.stat()
        except OSError:
            continue
        count += 1
        newest = max(newest, st.st_mtime_ns)
        total_mtime += st.st_mtime_ns
        total_size += st.st_size
    return (root_mtime, count, newest, total_mtime, total_size)


def _bust_claude_desktop_metadata_cache():
    global _desktop_meta_cache, _desktop_meta_cache_mtime
    _desktop_meta_cache = {}
    _desktop_meta_cache_mtime = 0


def _read_json_object(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_desktop_app_metadata():
    """Read the Claude desktop app's per-session metadata overlay.

    The desktop app stores session metadata at
      ~/Library/Application Support/Claude/claude-code-sessions/<org>/<ws>/local_<sid>.json
    Each file has `cliSessionId` linking back to the CLI's .jsonl, plus
    human-friendly fields (title, model, cwd) the desktop UI surfaces.

    Returns {cliSessionId: {title, model, cwd, is_archived}}.
    Re-scans when the metadata file count, mtimes, or sizes change; cheap
    enough to call on every request.
    """
    global _desktop_meta_cache, _desktop_meta_cache_mtime
    root = _claude_desktop_sessions_root()
    cache_key = _claude_desktop_metadata_cache_key(root)
    if cache_key is None:
        _desktop_meta_cache = {}
        _desktop_meta_cache_mtime = 0
        return {}
    if cache_key == _desktop_meta_cache_mtime and _desktop_meta_cache:
        return _desktop_meta_cache
    out = {}
    for path in _core._claude_desktop_metadata_files(root):
        data = _read_json_object(path)
        if not data:
            continue
        cli_sid = data.get("cliSessionId")
        if not cli_sid:
            continue
        out[cli_sid] = {
            "title": data.get("title") or None,
            "model": data.get("model") or None,
            "cwd": data.get("cwd") or None,
            "is_archived": bool(data.get("isArchived")),
            "last_activity_at": data.get("lastActivityAt") or None,
        }
    _desktop_meta_cache = out
    _desktop_meta_cache_mtime = cache_key
    return out


def _claude_desktop_workspace_dirs(root=None):
    root = root or _claude_desktop_sessions_root()
    if not root.is_dir():
        return []
    dirs = []
    try:
        org_dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []
    for org_dir in org_dirs:
        try:
            workspace_dirs = [p for p in org_dir.iterdir() if p.is_dir()]
        except OSError:
            continue
        for workspace_dir in workspace_dirs:
            newest = 0.0
            try:
                newest = workspace_dir.stat().st_mtime
            except OSError:
                pass
            for meta_path in workspace_dir.glob("local_*.json"):
                try:
                    newest = max(newest, meta_path.stat().st_mtime)
                except OSError:
                    continue
            dirs.append((newest, workspace_dir))
    dirs.sort(key=lambda item: item[0], reverse=True)
    return [p for _mtime, p in dirs]


def _claude_desktop_metadata_path_for_cli_session(session_id, metadata_index=None):
    sid = str(session_id or "").strip()
    if not sid:
        return None
    filename = f"local_{sid}.json"
    if isinstance(metadata_index, dict):
        by_cli = metadata_index.get("by_cli") or {}
        by_name = metadata_index.get("by_name") or {}
        path = by_cli.get(sid) or by_name.get(filename)
        if path:
            return path
        return None
    fallback = None
    for path in _core._claude_desktop_metadata_files():
        if path.name == filename:
            fallback = path
        data = _read_json_object(path)
        if data and data.get("cliSessionId") == sid:
            return path
    return fallback


def _claude_desktop_workspace_for_summary(summary, existing_path=None, metadata_index=None):
    if existing_path:
        try:
            parent = Path(existing_path).expanduser().resolve().parent
            if parent.is_dir():
                return parent
        except (OSError, RuntimeError, ValueError):
            pass

    if isinstance(metadata_index, dict):
        dirs = list(metadata_index.get("workspace_dirs") or [])
    else:
        dirs = _claude_desktop_workspace_dirs()
    if not dirs:
        return None

    cwd = str((summary or {}).get("cwd") or "").strip()
    if cwd:
        if isinstance(metadata_index, dict):
            matches = (metadata_index.get("workspace_by_cwd") or {}).get(cwd) or []
            if matches:
                return matches[0]
        else:
            matches = []
            for workspace_dir in dirs:
                for path in workspace_dir.glob("local_*.json"):
                    data = _read_json_object(path)
                    if not data:
                        continue
                    if cwd not in (data.get("cwd"), data.get("originCwd")):
                        continue
                    try:
                        score = float(data.get("lastActivityAt") or 0) / 1000.0
                    except (TypeError, ValueError):
                        score = 0.0
                    if not score:
                        try:
                            score = path.stat().st_mtime
                        except OSError:
                            score = 0.0
                    matches.append((score, workspace_dir))
            if matches:
                matches.sort(key=lambda item: item[0], reverse=True)
                return matches[0][1]

    return dirs[0]


def _recent_session_ids(hours=2):
    """Session IDs whose JSONL was written to in the last ``hours`` hours.
    More stable than the process-registry gate: catches sessions that ended
    but whose conversation is still relevant for model-routing advice."""
    cutoff = time.time() - hours * 3600
    sids = {}  # sid -> (mtime, path)
    if not _core.PROJECTS_ROOT.is_dir():
        return sids
    try:
        for path in _core.PROJECTS_ROOT.glob("*/*.jsonl"):
            try:
                mt = path.stat().st_mtime
            except OSError:
                continue
            if mt < cutoff:
                continue
            sid = path.stem
            if sid not in sids or mt > sids[sid][0]:
                sids[sid] = (mt, path)
    except (OSError, RuntimeError):
        pass
    return sids  # {sid: (mtime, path)}


def _claude_session_jsonl_path(session_id):
    sid = str(session_id or "").strip()
    if not sid or not _core.PROJECTS_ROOT.is_dir():
        return None
    matches = []
    try:
        for path in _core.PROJECTS_ROOT.glob(f"*/{sid}.jsonl"):
            if path.is_file():
                try:
                    matches.append((path.stat().st_mtime, path))
                except OSError:
                    matches.append((0.0, path))
    except (OSError, RuntimeError, ValueError):
        return None
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


# --------------------------------------------------------------------------
# Model-drift advisor — fleet scan + savings monitor
# --------------------------------------------------------------------------
MODEL_ADVISOR_LOG_FILE = str(_core.COMMAND_CENTER_STATE_DIR / "model-advisor-log.json")

# Cache cumulative output-token counts by (mtime,size) so the savings refresh
# does not re-walk a session JSONL on every poll. Bounded to the logged set.
_advisor_token_cache = {}
_advisor_token_cache_lock = threading.Lock()


def _session_cumulative_out_tokens(sid):
    """Sum assistant output tokens across a session's transcript. Cached by
    (mtime,size) — only the changed files re-parse. Called only for sessions
    that already carry a recommendation, so the working set is small."""
    path = _claude_session_jsonl_path(sid)
    if not path:
        return 0
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return 0
    with _advisor_token_cache_lock:
        hit = _advisor_token_cache.get(sid)
        if hit and hit[0] == key:
            return hit[1]
    total = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"output_tokens"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") != "assistant":
                    continue
                u = (o.get("message") or {}).get("usage") or {}
                total += int(u.get("output_tokens") or 0)
    except OSError:
        return 0
    with _advisor_token_cache_lock:
        _advisor_token_cache[sid] = (key, total)
        if len(_advisor_token_cache) > 200:
            _advisor_token_cache.clear()
    return total


def _advisor_session_name(sid, path):
    """Best-effort display name from the cheap conv-meta cache (no compute).
    Falls back to the short sid so a historical log row is still legible."""
    try:
        entry = _core._conv_meta_cache.get(str(path))
        if isinstance(entry, dict):
            name = (entry.get("custom_title") or entry.get("agent_name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return str(sid)[:8]


def _advisor_current_model(sid, turns):
    """The model effectively in force: a queued override wins, else the last
    assistant turn's model from the transcript."""
    ov = _core._get_session_override(sid)
    if ov and ov.get("model"):
        return ov["model"]
    for t in reversed(turns):
        if t.get("role") == "assistant" and t.get("model"):
            return t["model"]
    return ""


def build_model_advisor_report(persist=True):
    """Scan sessions active in the last 2 hours and emit model-routing advice.
    Uses mtime-based candidacy (more stable than process registry) so sessions
    that ended recently are still scored. The process-live set is kept separately
    only for expiring stale pending log entries."""
    live_recs = []
    scanned = []
    recent = _recent_session_ids(hours=2)  # {sid: (mtime, path)}
    overrides = _core._load_session_overrides()  # hoisted once for the whole scan
    for sid, (_mt, path) in list(recent.items()):
        try:
            if not path:
                continue
            turns = model_advisor.read_recent_turns(path)
            if not turns:
                continue
            current = _advisor_current_model(sid, turns)
            window = model_advisor.score_window(turns)
            rec = model_advisor.recommend(current, turns)
            name = _advisor_session_name(sid, path)
            family = model_advisor._family(current)
            effort = _core._conv_row_reasoning_effort(sid, overrides)

            # Every checked session goes into the scan log (transparency).
            scanned.append({
                "session_id": sid,
                "name": name,
                "current_model": family,
                "reasoning_effort": effort,
                "score": window.get("score"),
                "recent_score": window.get("recent_score"),
                "phase": window.get("phase"),
                "recent_phase": window.get("recent_phase"),
                "transition": window.get("transition"),
                "features": window.get("features", {}),
                "rec": {
                    "action": rec["action"],
                    "to_model": rec["to_model"],
                    "reason": rec.get("reason", ""),
                    "confidence": rec.get("confidence", ""),
                } if rec else None,
            })

            if not rec:
                continue
            # Already switched to (or past) the target? Nothing to nudge.
            if model_advisor.model_tier(current) <= model_advisor.model_tier(rec["to_model"]) \
                    and rec["action"] != "upgrade":
                continue
            baseline = _session_cumulative_out_tokens(sid)
            stored = (
                model_advisor.log_recommendation(
                    MODEL_ADVISOR_LOG_FILE, sid, name, rec, baseline
                )
                if persist
                else None
            )
            # Don't surface recently-dismissed recs as live (user clicked X).
            if (stored or {}).get("status") == "dismissed":
                continue
            live_recs.append(
                {
                    "id": (stored or {}).get("id"),
                    "session_id": sid,
                    "name": name,
                    "current_model": family,
                    "reasoning_effort": effort,
                    "status": (stored or {}).get("status", "pending"),
                    **rec,
                }
            )
        except Exception:
            continue
    # Expire pending entries for sessions no longer in the recent window.
    try:
        model_advisor.expire_stale_pending(MODEL_ADVISOR_LOG_FILE, set(recent.keys()))
    except Exception:
        pass
    # Roll forward realized/missed savings using fresh token counts.
    try:
        model_advisor.refresh_savings(MODEL_ADVISOR_LOG_FILE, _session_cumulative_out_tokens)
    except Exception:
        pass
    import datetime as _dt
    data = model_advisor._load_log(MODEL_ADVISOR_LOG_FILE)
    return {
        "ok": True,
        "live": live_recs,
        "scanned": scanned,
        "scan_window_hours": 2,
        "scanned_at": _dt.datetime.now().strftime("%H:%M"),
        "log": list(reversed(data.get("recommendations", [])))[:100],
        "summary": model_advisor.summarize(data),
    }


def get_model_advisor_report(fresh=""):
    """Read cached advice, or explicitly request a coalesced refresh.

    ``fresh=1`` is cooldown-limited background work. ``fresh=force`` is reserved
    for the user's explicit modal-open action and bypasses that cooldown.
    """
    mode = str(fresh or "").lower()
    if mode not in ("1", "true", "force"):
        return _core._model_advisor_report_cache.get_cached()
    return _core._model_advisor_report_cache.refresh(
        _core.build_model_advisor_report,
        force=mode == "force",
    )


def apply_model_advisor_recommendation(rec_id, session_id, model, context_1m=False):
    """Apply a recommended downgrade/upgrade: set the session model (injects
    `/model` live via the existing override path) and mark the log entry."""
    result = _core._set_session_model(session_id, model, context_1m)
    if result.get("ok") and rec_id:
        try:
            model_advisor.mark(MODEL_ADVISOR_LOG_FILE, rec_id, "applied")
        except Exception:
            pass
    return result


def _claude_desktop_title_from_text(text, max_len=120):
    text = _core._strip_ccc_session_state_instruction(str(text or "")).strip()
    if not text:
        return ""
    return _core._prompt_fragment(text, max_len).strip()


def _claude_desktop_epoch_ms(value):
    if not value:
        return 0
    epoch = _core._iso_to_epoch(value) if isinstance(value, str) else None
    if epoch is None:
        try:
            epoch = float(value)
        except (TypeError, ValueError):
            return 0
        if epoch > 100000000000:
            return int(epoch)
    return int(epoch * 1000)


def _claude_session_desktop_summary(session_id, spawn_entry=None):
    sid = str(session_id or "").strip()
    entry = spawn_entry if isinstance(spawn_entry, dict) else {}
    now_ms = int(time.time() * 1000)
    started_epoch = _core._spawn_registry_entry_epoch(entry)
    started_ms = int(started_epoch * 1000) if started_epoch else 0
    summary = {
        "cwd": entry.get("cwd") or entry.get("repo_path") or "",
        "originCwd": entry.get("cwd") or entry.get("repo_path") or "",
        "title": (
            _claude_desktop_title_from_text(entry.get("prompt"))
            or _claude_desktop_title_from_text(entry.get("command_summary"))
            or _claude_desktop_title_from_text((entry.get("name") or "").replace("-", " "))
        ),
        "model": entry.get("model") or "",
        "createdAt": started_ms or now_ms,
        "lastActivityAt": started_ms or now_ms,
        "completedTurns": 0,
        "jsonl_path": "",
        "has_cli_transcript": False,
    }

    path = _claude_session_jsonl_path(sid)
    if not path:
        if summary["cwd"]:
            try:
                summary["cwd"] = _resolve_session_cwd(sid, summary["cwd"])
                summary["originCwd"] = summary["cwd"]
            except Exception:
                pass
        return summary
    summary["jsonl_path"] = str(path)
    summary["has_cli_transcript"] = True

    try:
        st = path.stat()
        stat_ms = int(st.st_mtime * 1000)
    except OSError:
        st = None
        stat_ms = now_ms

    first_prompt = ""
    first_ts_ms = 0
    last_ts_ms = 0
    completed_turns = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_ms = _claude_desktop_epoch_ms(ev.get("timestamp"))
                if ts_ms:
                    if not first_ts_ms:
                        first_ts_ms = ts_ms
                    last_ts_ms = ts_ms
                if not summary["cwd"] and ev.get("cwd"):
                    summary["cwd"] = ev.get("cwd") or ""
                    summary["originCwd"] = summary["cwd"]
                if not first_prompt:
                    first_prompt = _core._extract_user_prompt_text(ev) or ""
                if ev.get("type") == "assistant":
                    completed_turns += 1
                    msg = ev.get("message")
                    if isinstance(msg, dict) and msg.get("model"):
                        summary["model"] = msg.get("model") or summary["model"]
    except (OSError, UnicodeDecodeError):
        pass

    try:
        tail = _core._extract_tail_meta(path) or {}
    except Exception:
        tail = {}
    title = (
        tail.get("custom_title")
        or tail.get("agent_name")
        or tail.get("ai_title")
        or _claude_desktop_title_from_text(first_prompt)
        or summary["title"]
    )
    if title:
        summary["title"] = title
    if tail.get("model"):
        summary["model"] = tail.get("model") or summary["model"]
    if tail.get("last_meaningful_ts"):
        try:
            summary["lastActivityAt"] = int(float(tail["last_meaningful_ts"]) * 1000)
        except (TypeError, ValueError):
            pass
    elif last_ts_ms:
        summary["lastActivityAt"] = last_ts_ms
    elif stat_ms:
        summary["lastActivityAt"] = stat_ms
    if first_ts_ms:
        summary["createdAt"] = first_ts_ms
    elif started_ms:
        summary["createdAt"] = started_ms
    elif st:
        summary["createdAt"] = int(min(st.st_ctime, st.st_mtime) * 1000)
    summary["completedTurns"] = completed_turns
    if summary["cwd"]:
        try:
            summary["cwd"] = _resolve_session_cwd(sid, summary["cwd"])
            summary["originCwd"] = summary["cwd"]
        except Exception:
            pass
    return summary


def _write_claude_desktop_metadata(path, payload, last_activity_ms):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        if last_activity_ms:
            ts = max(0.0, float(last_activity_ms) / 1000.0)
            os.utime(path, (ts, ts))
    except (OSError, TypeError, ValueError):
        return False
    _bust_claude_desktop_metadata_cache()
    return True


def _ensure_claude_desktop_session_visible(session_id, spawn_entry=None, metadata_index=None):
    """Create or refresh Claude Desktop's sidebar metadata for a CLI session."""
    sid = str(session_id or "").strip()
    if not sid or not _SESSION_UUID_RE.match(sid):
        return False

    summary = _claude_session_desktop_summary(sid, spawn_entry=spawn_entry)
    existing_path = _claude_desktop_metadata_path_for_cli_session(
        sid,
        metadata_index=metadata_index,
    )
    existing = None
    if existing_path and isinstance(metadata_index, dict):
        existing = (metadata_index.get("data_by_path") or {}).get(str(existing_path))
    if existing is None:
        existing = _read_json_object(existing_path) if existing_path else {}
    existing = existing or {}
    if not summary.get("has_cli_transcript"):
        return False
    if not (summary.get("cwd") or existing.get("cwd")):
        return False
    if not (summary.get("title") or existing.get("title")):
        return False
    workspace_dir = _claude_desktop_workspace_for_summary(
        summary,
        existing_path,
        metadata_index=metadata_index,
    )
    if not workspace_dir:
        return False
    path = existing_path or (workspace_dir / f"local_{sid}.json")
    app_session_id = existing.get("sessionId") or path.stem or f"local_{sid}"
    if path.name.startswith("local_") and not str(app_session_id).startswith("local_"):
        app_session_id = path.stem
    user_title = (
        existing.get("title")
        if existing.get("titleSource") == "user" and existing.get("title")
        else None
    )
    title = user_title or summary.get("title") or existing.get("title") or ""

    payload = dict(existing)
    payload.update({
        "sessionId": app_session_id,
        "cliSessionId": sid,
        "cwd": summary.get("cwd") or existing.get("cwd") or "",
        "originCwd": summary.get("originCwd") or existing.get("originCwd") or summary.get("cwd") or "",
        "createdAt": int(summary.get("createdAt") or existing.get("createdAt") or time.time() * 1000),
        "lastActivityAt": int(summary.get("lastActivityAt") or existing.get("lastActivityAt") or time.time() * 1000),
        "completedTurns": int(summary.get("completedTurns") or existing.get("completedTurns") or 0),
        "isArchived": bool(existing.get("isArchived", False)),
        "permissionMode": existing.get("permissionMode") or "default",
        "chromePermissionMode": existing.get("chromePermissionMode") or "skip_all_permission_checks",
        "alwaysAllowedReasons": existing.get("alwaysAllowedReasons") if isinstance(existing.get("alwaysAllowedReasons"), list) else [],
        "enabledMcpTools": existing.get("enabledMcpTools") if isinstance(existing.get("enabledMcpTools"), dict) else {},
        "remoteMcpServersConfig": existing.get("remoteMcpServersConfig") if isinstance(existing.get("remoteMcpServersConfig"), list) else [],
    })
    model = summary.get("model") or existing.get("model")
    if model:
        payload["model"] = model
    if title:
        payload["title"] = title
        payload["titleSource"] = existing.get("titleSource") or ("user" if user_title else "auto")
    elif "title" in payload and not payload["title"]:
        payload.pop("title", None)
        payload.pop("titleSource", None)

    if existing == payload and path.is_file():
        try:
            desired_ts = float(payload.get("lastActivityAt") or 0) / 1000.0
            if desired_ts and abs(path.stat().st_mtime - desired_ts) > 1.0:
                os.utime(path, (desired_ts, desired_ts))
                _bust_claude_desktop_metadata_cache()
        except (OSError, TypeError, ValueError):
            pass
        return True

    return _write_claude_desktop_metadata(path, payload, payload.get("lastActivityAt"))


_claude_desktop_visibility_retry_sids = set()
_claude_desktop_visibility_retry_lock = threading.Lock()


def _schedule_claude_desktop_visibility_retry(session_id, spawn_entry=None, attempts=60, delay=1.0):
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _claude_desktop_visibility_retry_lock:
        if sid in _claude_desktop_visibility_retry_sids:
            return False
        _claude_desktop_visibility_retry_sids.add(sid)

    entry = dict(spawn_entry or {})

    def worker():
        try:
            for _idx in range(max(1, int(attempts or 1))):
                if _core._ensure_claude_desktop_session_visible(sid, spawn_entry=entry):
                    return
                time.sleep(max(0.05, float(delay or 0.5)))
        except Exception:
            pass
        finally:
            with _claude_desktop_visibility_retry_lock:
                _claude_desktop_visibility_retry_sids.discard(sid)

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"ccc-claude-desktop-visible-{sid[:8]}",
    ).start()
    return True


def _is_synthetic_claude_desktop_metadata(path, data):
    if not isinstance(data, dict):
        return False
    sid = str(data.get("cliSessionId") or "").strip()
    if not sid:
        return False
    if path.name != f"local_{sid}.json":
        return False
    if data.get("sessionId") not in (sid, f"local_{sid}"):
        return False
    if data.get("enabledMcpTools") not in ({}, None):
        return False
    if data.get("remoteMcpServersConfig") not in ([], None):
        return False
    if data.get("alwaysAllowedReasons") not in ([], None):
        return False
    return data.get("permissionMode") in (None, "default")


def _is_claude_desktop_transcript_unavailable_placeholder(path, data):
    if not isinstance(data, dict) or data.get("transcriptUnavailable") is not True:
        return False
    sid = str(data.get("sessionId") or "").strip()
    if not sid or path.name != f"{sid}.json":
        return False
    if data.get("enabledMcpTools") not in ({}, None):
        return False
    if data.get("remoteMcpServersConfig") not in ([], None):
        return False
    return data.get("permissionMode") in (None, "default")


def prune_unresumable_claude_desktop_metadata(dry_run=False):
    """Remove CCC-synthetic Desktop rows whose CLI transcript is unavailable."""
    pruned = []
    for path in _core._claude_desktop_metadata_files():
        data = _read_json_object(path)
        if not _is_synthetic_claude_desktop_metadata(path, data):
            continue
        sid = data.get("cliSessionId")
        if _claude_session_jsonl_path(sid):
            continue
        pruned.append(str(path))
        if dry_run:
            continue
        try:
            path.unlink()
        except OSError:
            pass
    root = _claude_desktop_sessions_root()
    if root.is_dir():
        try:
            placeholder_paths = [
                p for p in root.glob("*/*/*.json")
                if p.is_file() and not p.name.startswith("local_")
            ]
        except OSError:
            placeholder_paths = []
        for path in placeholder_paths:
            data = _read_json_object(path)
            if not _is_claude_desktop_transcript_unavailable_placeholder(path, data):
                continue
            pruned.append(str(path))
            if dry_run:
                continue
            try:
                path.unlink()
            except OSError:
                pass
    if pruned and not dry_run:
        _bust_claude_desktop_metadata_cache()
    return {"ok": True, "dry_run": bool(dry_run), "pruned": len(pruned), "paths": pruned}


def _fetch_issue_states(repo_path, _blocking=False):
    """Bulk-fetch state+labels+title for all issues. Cached 60s, disk-backed.

    Stale entries are served immediately with a background refresh behind
    them; only a completely cold cache blocks on `gh`. Mutations bust the
    cache explicitly (see _bust_issue_state_cache), so a close/reopen still
    shows up right away rather than waiting out the TTL.
    """
    repo_path = _core.resolve_repo_path(repo_path)
    _core._hydrate_gh_cache("issue_states", _issue_state_cache)
    cached = _issue_state_cache.get(repo_path) or {}
    if time.time() - cached.get("ts", 0) < 60 and cached.get("data"):
        return cached["data"]
    if cached.get("data") and not _blocking:
        _refresh_gh_cache_async(
            "issue_states", repo_path,
            lambda: _core._fetch_issue_states(repo_path, _blocking=True),
        )
        return cached["data"]
    data = cached.get("data") or {}
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--state", "all", "--limit", "500",
             "--json", "number,title,state,labels"],
            capture_output=True, text=True, timeout=15, cwd=str(repo_path),
        )
        if out.returncode == 0:
            issues = json.loads(out.stdout)
            data = {
                str(i["number"]): {
                    "state": i.get("state") or "OPEN",
                    "labels": [l.get("name", "") for l in (i.get("labels") or [])],
                    "title": _core._strip_title_prefix(i.get("title", "")),
                }
                for i in issues
            }
            _issue_state_cache[repo_path] = {"ts": time.time(), "data": data}
            _core._persist_gh_cache("issue_states", _issue_state_cache)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return data


def _bust_issue_state_cache(repo_path=None):
    """Force next _fetch_issue_states() to re-query gh. Call after any mutation
    (close/reopen/label change) so the UI doesn't serve 5-minute-stale state."""
    # Mark hydration done before popping: otherwise a bust that lands before
    # this process has read the disk snapshot gets silently undone by a later
    # _hydrate_gh_cache re-adding the pre-mutation entry.
    with _GH_CACHE_LOCK:
        _core._GH_CACHE_HYDRATED.add("issue_states")
    if repo_path:
        try:
            _issue_state_cache.pop(_core.resolve_repo_path(repo_path), None)
        except _core.RepoContextError:
            pass
    else:
        _issue_state_cache.clear()
    _core._persist_gh_cache("issue_states", _issue_state_cache)


# Backlog: full issue data (labels, body) for open issues
_backlog_issues_cache = {}  # repo_path -> {"ts": float, "data": list}


def _bust_issue_titles_cache(repo_path=None):
    """Drop cached issue titles after a mutation, on disk as well as in memory."""
    # See _bust_issue_state_cache for why hydration is claimed first.
    with _GH_CACHE_LOCK:
        _core._GH_CACHE_HYDRATED.add("issue_titles")
    if repo_path:
        try:
            _issue_titles_cache.pop(_core.resolve_repo_path(repo_path), None)
        except _core.RepoContextError:
            _issue_titles_cache.pop(repo_path, None)
    else:
        _issue_titles_cache.clear()
    _core._persist_gh_cache("issue_titles", _issue_titles_cache)


def _fetch_issue_titles(repo_path, _blocking=False):
    """Bulk-fetch GitHub issue titles. Cached for 5 minutes, disk-backed.

    Stale entries are served immediately with a background refresh behind
    them; only a completely cold cache blocks on `gh`.
    """
    repo_path = _core.resolve_repo_path(repo_path)
    _core._hydrate_gh_cache("issue_titles", _issue_titles_cache)
    cached = _issue_titles_cache.get(repo_path) or {}
    if time.time() - cached.get("ts", 0) < 300 and cached.get("data"):
        return cached["data"]
    if cached.get("data") and not _blocking:
        _refresh_gh_cache_async(
            "issue_titles", repo_path,
            lambda: _fetch_issue_titles(repo_path, _blocking=True),
        )
        return cached["data"]
    data = cached.get("data") or {}
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--state", "all", "--limit", "200",
             "--json", "number,title"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if out.returncode == 0:
            issues = json.loads(out.stdout)
            data = {
                str(i["number"]): _core._strip_title_prefix(i["title"])
                for i in issues
            }
            _issue_titles_cache[repo_path] = {"ts": time.time(), "data": data}
            _core._persist_gh_cache("issue_titles", _issue_titles_cache)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return data


