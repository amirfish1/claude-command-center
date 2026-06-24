#!/usr/bin/env python3
"""Durable server-side storage for Flow **objects** (the day-view tracker).

Objects are first-class containers that own session rows. Until now their
definitions and parent links lived only in browser ``localStorage`` keys
(``ccc-flow-custom-objects``, ``ccc-flow-node-parents``, ``ccc-objects-order``),
so the organization was lost on a cache clear, never crossed machines, and was
invisible to the server — which blocked any server-side automation reading or
arranging "what I have to do today" (see docs/objects-day-view-tracker.md,
GOAL-3 / GOAL-4).

This module mirrors that client state to a single JSON file in the CCC state
dir, exactly the way COO tracking mirrors to ``coo-notes.json``: one small file,
atomic temp-file + ``os.replace`` write, missing/corrupt file degrades to empty
state instead of throwing.

Storage file: ``~/.claude/command-center/objects.json`` (override with
``CCC_OBJECTS_FILE`` or the ``CCC_STATE_DIR`` env var — tests point both at a
tmpdir).

Schema::

    {
      "objects": [
        {
          "id":         "<stable client id>",
          "title":      "Ship the billing fix",
          "created_at": "2026-06-23T12:00:00Z",
          "updated_at": "2026-06-23T12:34:00Z",
          "status":     "in progress",   # OPTIONAL — owned by a sibling session
          "objective":  "land the patch"  # OPTIONAL — owned by a sibling session
        },
        ...
      ],
      "parents": { "<sessionNodeId>": "<objectNodeId>", ... },
      "order":   { "<nodeId>": <rank int|float>, ... },
      "drafts": [
        {
          "id":             "<stable client id>",
          "title":          "Draft the release notes",
          "repo_path":      "/path/to/repo",       # may be "" for a reminder
          "parent_node_id": "object:<objectId>",   # links the draft to its object
          "prompt":         "...",                 # OPTIONAL kickoff prompt
          "created_at":     "2026-06-23T12:00:00Z",
          "updated_at":     "2026-06-23T12:34:00Z"
        },
        ...
      ]
    }

``drafts`` are lightweight not-yet-started tasks (Flow's "draft-session" nodes).
The client owns their creation/editing and pushes them via ``import_state``; this
module only merges them in (additive upsert by id) and deletes one by id.

The ``status`` and ``objective`` fields are OPTIONAL and owned by a different
session's client wiring. This module never sets them, but it stores and returns
them losslessly: ``create``/``update`` pass them straight through when present,
and they are preserved across unrelated mutations (a rename never drops a
status someone else wrote).

Concurrency: writers are serialised with an ``fcntl`` lock file and writes are
atomic (temp-file + ``os.replace``), so a concurrent reader never sees a
half-written file. Reads are cached by ``(mtime, size)`` so repeated GETs don't
re-parse an unchanged file (CLAUDE.md § Performance gates — small JSON only, no
subprocess, no all-conversations scan).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # POSIX cross-process locking; degrade gracefully if unavailable.
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore


def _state_dir() -> Path:
    return Path(
        os.environ.get("CCC_STATE_DIR")
        or (Path.home() / ".claude" / "command-center")
    )


def _objects_file() -> Path:
    """Resolved at call time so tests can set CCC_OBJECTS_FILE / CCC_STATE_DIR
    after import without re-importing the module."""
    override = os.environ.get("CCC_OBJECTS_FILE")
    if override:
        return Path(override)
    return _state_dir() / "objects.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Locking + cached read
# ---------------------------------------------------------------------------
_lock = threading.Lock()  # in-process guard around read-modify-write
# Cache the parsed state keyed by (path, mtime, size) so repeated GETs on an
# unchanged file skip the json.loads. Invalidated automatically when the file
# changes on disk (or is written by us).
_cache: Dict[str, Any] = {"key": None, "state": None}


class _FileLock:
    """Best-effort cross-process exclusive lock via an fcntl lock file.

    Degrades to a no-op where fcntl is unavailable (non-POSIX); the in-process
    threading.Lock still serialises same-process writers there.
    """

    def __init__(self, path: Path):
        self._path = path
        self._fh = None

    def __enter__(self):
        if fcntl is None:
            return self
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "w")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            # Locking is best-effort; never block a write on lock failure.
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
        return False


def _empty_state() -> Dict[str, Any]:
    return {"objects": [], "parents": {}, "order": {}, "drafts": []}


def _coerce_object(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalise one object dict, dropping anything without a usable id.

    Preserves the optional ``status`` / ``objective`` fields verbatim (owned by
    a sibling session) plus any future unknown keys, so we never lose data we
    didn't author.
    """
    if not isinstance(raw, dict):
        return None
    oid = raw.get("id")
    if not isinstance(oid, str) or not oid:
        return None
    obj: Dict[str, Any] = dict(raw)  # keep unknown keys losslessly
    obj["id"] = oid
    title = raw.get("title")
    obj["title"] = title if isinstance(title, str) else ""
    obj["created_at"] = raw.get("created_at") or _now_iso()
    obj["updated_at"] = raw.get("updated_at") or obj["created_at"]
    return obj


def _coerce_draft(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalise one draft-session dict, dropping anything without a usable id.

    A draft is a lightweight not-yet-started task. ``repo_path`` may be empty
    (a pure reminder) and ``prompt`` is optional. Unknown keys are preserved
    losslessly so the client can carry extra fields without us dropping them.
    """
    if not isinstance(raw, dict):
        return None
    did = raw.get("id")
    if not isinstance(did, str) or not did:
        return None
    draft: Dict[str, Any] = dict(raw)  # keep unknown keys losslessly
    draft["id"] = did
    title = raw.get("title")
    draft["title"] = title if isinstance(title, str) else ""
    repo_path = raw.get("repo_path")
    draft["repo_path"] = repo_path if isinstance(repo_path, str) else ""
    parent = raw.get("parent_node_id")
    draft["parent_node_id"] = parent if isinstance(parent, str) else ""
    if "prompt" in raw and not isinstance(raw.get("prompt"), str):
        draft.pop("prompt", None)
    draft["created_at"] = raw.get("created_at") or _now_iso()
    draft["updated_at"] = raw.get("updated_at") or draft["created_at"]
    return draft


def _normalise_state(raw: Any) -> Dict[str, Any]:
    """Turn whatever was on disk into the canonical schema. Never throws."""
    state = _empty_state()
    if not isinstance(raw, dict):
        return state
    objs = raw.get("objects")
    if isinstance(objs, list):
        seen = set()
        for item in objs:
            obj = _coerce_object(item)
            if obj is None or obj["id"] in seen:
                continue
            seen.add(obj["id"])
            state["objects"].append(obj)
    parents = raw.get("parents")
    if isinstance(parents, dict):
        for k, v in parents.items():
            if isinstance(k, str) and isinstance(v, str) and k and v:
                state["parents"][k] = v
    order = raw.get("order")
    if isinstance(order, dict):
        for k, v in order.items():
            if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool):
                state["order"][k] = v
    drafts = raw.get("drafts")
    if isinstance(drafts, list):
        seen_d = set()
        for item in drafts:
            draft = _coerce_draft(item)
            if draft is None or draft["id"] in seen_d:
                continue
            seen_d.add(draft["id"])
            state["drafts"].append(draft)
    return state


def _read_unlocked() -> Dict[str, Any]:
    """Read + parse the file with an (mtime,size) cache. Caller holds _lock."""
    path = _objects_file()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime, st.st_size)
    except OSError:
        # Missing file → empty state. Cache the miss so we don't stat-storm.
        if _cache.get("key") == ("__missing__", str(path)):
            return json.loads(json.dumps(_cache["state"]))  # defensive copy
        empty = _empty_state()
        _cache["key"] = ("__missing__", str(path))
        _cache["state"] = empty
        return json.loads(json.dumps(empty))
    if _cache.get("key") == key and _cache.get("state") is not None:
        return json.loads(json.dumps(_cache["state"]))  # copy so callers can mutate
    try:
        raw = json.loads(path.read_text() or "null")
    except (OSError, ValueError):
        raw = None  # corrupt/unreadable → empty, never throw
    state = _normalise_state(raw)
    _cache["key"] = key
    _cache["state"] = state
    return json.loads(json.dumps(state))


def _write_unlocked(state: Dict[str, Any]) -> None:
    """Atomic write + refresh cache. Caller holds _lock and the file lock."""
    path = _objects_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    try:
        st = path.stat()
        _cache["key"] = (str(path), st.st_mtime, st.st_size)
        _cache["state"] = json.loads(json.dumps(state))
    except OSError:  # pragma: no cover
        _cache["key"] = None
        _cache["state"] = None


# ---------------------------------------------------------------------------
# Public API — all read-modify-write under the lock
# ---------------------------------------------------------------------------
def load_state() -> Dict[str, Any]:
    """Return the full ``{objects, parents, order}`` state (cached read)."""
    with _lock:
        return _read_unlocked()


def _find(objects: List[Dict[str, Any]], oid: str) -> Optional[Dict[str, Any]]:
    for o in objects:
        if o.get("id") == oid:
            return o
    return None


def create_object(
    title: str,
    id: Optional[str] = None,
    status: Optional[str] = None,
    objective: Optional[str] = None,
) -> Dict[str, Any]:
    """Create (or upsert by id) an object. Returns the created/updated object.

    If ``id`` is supplied and already exists, this is an idempotent upsert:
    the title (and any supplied status/objective) are patched onto the
    existing object rather than creating a duplicate.
    """
    import uuid

    oid = id if (isinstance(id, str) and id) else uuid.uuid4().hex
    now = _now_iso()
    with _lock, _FileLock(_objects_file().with_suffix(".lock")):
        state = _read_unlocked()
        existing = _find(state["objects"], oid)
        if existing is not None:
            if isinstance(title, str):
                existing["title"] = title
            if status is not None:
                existing["status"] = status
            if objective is not None:
                existing["objective"] = objective
            existing["updated_at"] = now
            obj = existing
        else:
            obj = {
                "id": oid,
                "title": title if isinstance(title, str) else "",
                "created_at": now,
                "updated_at": now,
            }
            if status is not None:
                obj["status"] = status
            if objective is not None:
                obj["objective"] = objective
            state["objects"].append(obj)
        _write_unlocked(state)
        return json.loads(json.dumps(obj))


def update_object(
    id: str,
    title: Optional[str] = None,
    status: Optional[str] = None,
    objective: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Patch an existing object. Only fields that are not None are touched, so
    a title-only edit never clobbers a status a sibling session wrote.

    Returns the updated object, or None if no object with ``id`` exists.
    """
    with _lock, _FileLock(_objects_file().with_suffix(".lock")):
        state = _read_unlocked()
        obj = _find(state["objects"], id)
        if obj is None:
            return None
        if title is not None:
            obj["title"] = title
        if status is not None:
            obj["status"] = status
        if objective is not None:
            obj["objective"] = objective
        obj["updated_at"] = _now_iso()
        _write_unlocked(state)
        return json.loads(json.dumps(obj))


def delete_object(id: str) -> bool:
    """Remove an object and every parent link pointing at it. Also drops the
    object's own order rank. Returns True if an object was removed.

    The object node id used in ``parents`` values is ``'object:' + id`` (Flow's
    convention); we remove links keyed to either form to be robust.
    """
    object_node_id = "object:" + id
    with _lock, _FileLock(_objects_file().with_suffix(".lock")):
        state = _read_unlocked()
        before = len(state["objects"])
        state["objects"] = [o for o in state["objects"] if o.get("id") != id]
        removed = len(state["objects"]) < before
        # Drop child links pointing at this object (either node-id form).
        state["parents"] = {
            k: v
            for k, v in state["parents"].items()
            if v != object_node_id and v != id
        }
        # Drop the object's own rank entry (either form).
        state["order"] = {
            k: v for k, v in state["order"].items() if k != object_node_id and k != id
        }
        _write_unlocked(state)
        return removed


def upsert_draft(raw: Any) -> Optional[Dict[str, Any]]:
    """Insert or update a draft-session by id (idempotent upsert).

    The incoming dict is normalised; an existing draft with the same id is
    patched per-field (incoming wins) while preserving its ``created_at`` and
    any fields the incoming object omitted. Returns the stored draft, or None
    if ``raw`` had no usable id.
    """
    draft = _coerce_draft(raw)
    if draft is None:
        return None
    with _lock, _FileLock(_objects_file().with_suffix(".lock")):
        state = _read_unlocked()
        existing = _find(state["drafts"], draft["id"])
        if existing is not None:
            merged = dict(existing)
            merged.update(draft)  # incoming wins per-field, keeps existing extras
            merged["created_at"] = existing.get("created_at") or draft.get("created_at")
            merged["updated_at"] = draft.get("updated_at") or _now_iso()
            stored = merged
            state["drafts"] = [
                stored if d.get("id") == draft["id"] else d for d in state["drafts"]
            ]
        else:
            stored = draft
            state["drafts"].append(stored)
        _write_unlocked(state)
        return json.loads(json.dumps(stored))


def delete_draft(id: str) -> bool:
    """Remove a draft-session by id. Returns True if one was removed."""
    if not isinstance(id, str) or not id:
        return False
    with _lock, _FileLock(_objects_file().with_suffix(".lock")):
        state = _read_unlocked()
        before = len(state["drafts"])
        state["drafts"] = [d for d in state["drafts"] if d.get("id") != id]
        removed = len(state["drafts"]) < before
        if removed:
            _write_unlocked(state)
        return removed


def assign_session(session_node_id: str, object_id: str) -> Dict[str, Any]:
    """Parent ``session_node_id`` under the object. The parent value stored is
    the Flow object node id (``'object:' + object_id``). Returns the new state.
    """
    if not session_node_id or not object_id:
        raise ValueError("session_node_id and object_id are required")
    object_node_id = "object:" + object_id
    with _lock, _FileLock(_objects_file().with_suffix(".lock")):
        state = _read_unlocked()
        state["parents"][session_node_id] = object_node_id
        _write_unlocked(state)
        return json.loads(json.dumps(state))


def unassign_session(session_node_id: str) -> Dict[str, Any]:
    """Remove the parent link for ``session_node_id`` (no-op if absent)."""
    if not session_node_id:
        raise ValueError("session_node_id is required")
    with _lock, _FileLock(_objects_file().with_suffix(".lock")):
        state = _read_unlocked()
        state["parents"].pop(session_node_id, None)
        _write_unlocked(state)
        return json.loads(json.dumps(state))


def import_state(
    objects: Any = None,
    parents: Any = None,
    order: Any = None,
    drafts: Any = None,
) -> Dict[str, Any]:
    """Sync the browser's existing localStorage organization up to the server.

    MERGE semantics (documented choice — NOT replace):

    The import is a one-way bootstrap that runs whenever a browser with local
    state meets a server that may already hold state (its own earlier import,
    or another machine's). A destructive *replace* would let the last browser
    to load silently wipe objects another surface created — exactly the
    cross-machine durability GOAL-3 exists to provide. So we MERGE:

      * objects:  upsert by id. An incoming object updates title/status/
        objective/timestamps for an existing id and is appended if new. No
        server object is ever deleted by an import.
      * parents:  incoming links overwrite the same key (a session can only
        have one parent); keys absent from the import are left untouched.
      * order:    incoming ranks overwrite the same key; others untouched.
      * drafts:   upsert by id (same policy as objects). An incoming draft
        patches an existing id and is appended if new; no server draft is ever
        deleted by an import. (To intentionally drop a draft, call delete_draft.)

    Net effect: importing is always safe and additive — re-importing the same
    browser is idempotent, and a second browser's import augments rather than
    clobbers. (To intentionally drop an object, call delete_object.)

    Returns the merged state.
    """
    inc = _normalise_state(
        {
            "objects": objects or [],
            "parents": parents or {},
            "order": order or {},
            "drafts": drafts or [],
        }
    )
    with _lock, _FileLock(_objects_file().with_suffix(".lock")):
        state = _read_unlocked()
        # Merge objects by id (upsert), preserving fields not present incoming.
        by_id = {o["id"]: o for o in state["objects"]}
        for obj in inc["objects"]:
            cur = by_id.get(obj["id"])
            if cur is None:
                by_id[obj["id"]] = obj
            else:
                merged = dict(cur)
                merged.update(obj)  # incoming wins per-field, keeps cur extras
                merged["created_at"] = cur.get("created_at") or obj.get("created_at")
                by_id[obj["id"]] = merged
        # Stable order: existing objects first (original order), then new ones.
        ordered: List[Dict[str, Any]] = []
        emitted = set()
        for o in state["objects"]:
            ordered.append(by_id[o["id"]])
            emitted.add(o["id"])
        for obj in inc["objects"]:
            if obj["id"] not in emitted:
                ordered.append(by_id[obj["id"]])
                emitted.add(obj["id"])
        state["objects"] = ordered
        state["parents"].update(inc["parents"])
        state["order"].update(inc["order"])
        # Merge drafts by id (upsert), preserving fields not present incoming.
        d_by_id = {d["id"]: d for d in state["drafts"]}
        for dr in inc["drafts"]:
            cur = d_by_id.get(dr["id"])
            if cur is None:
                d_by_id[dr["id"]] = dr
            else:
                merged = dict(cur)
                merged.update(dr)  # incoming wins per-field, keeps cur extras
                merged["created_at"] = cur.get("created_at") or dr.get("created_at")
                d_by_id[dr["id"]] = merged
        d_ordered: List[Dict[str, Any]] = []
        d_emitted = set()
        for d in state["drafts"]:
            d_ordered.append(d_by_id[d["id"]])
            d_emitted.add(d["id"])
        for dr in inc["drafts"]:
            if dr["id"] not in d_emitted:
                d_ordered.append(d_by_id[dr["id"]])
                d_emitted.add(dr["id"])
        state["drafts"] = d_ordered
        _write_unlocked(state)
        return json.loads(json.dumps(state))
