# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Bounded change detection for a complete sidebar archive projection."""

from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from ccc_server import archive_list_replay, archive_store, list_encoding


# Bump when persisted projection or guard semantics change.
VERSION = 7
MAX_ROWS = 32768
MAX_PATCH_ROWS = 8192
_MAX_BASE_BYTES = 16 << 20
_MAX_INDEX_BYTES = 32 << 20
_ENCODE_GUARD = json.JSONEncoder(separators=(",", ":")).encode


def row_token(row):
    encoded = _ENCODE_GUARD(row).encode("ascii")
    return hashlib.blake2b(encoded, digest_size=16).hexdigest()


def _guard_value(value):
    if type(value) is str:
        return "s" + value
    if type(value) is tuple and len(value) == 4:
        origin, root, title, named = value
        if type(origin) is str and type(root) is str and type(title) is str and type(named) is int:
            # The durable topic table uses only these four SQLite types.
            return "t" + repr(value)
    if value is True:
        return "b1"
    if value is False:
        return "b0"
    # Keep types and field order that Python dict equality can collapse.
    return _ENCODE_GUARD(value)


@dataclass(frozen=True)
class Inputs:
    """Ordered membership and exact row-level dependencies of one projection."""

    order: tuple
    sources: dict
    overlays: tuple
    edges: tuple


@dataclass(frozen=True)
class Basis:
    """One complete encoded response and its projection dependencies."""

    inputs: Inputs
    working: frozenset
    activity: dict
    token: str
    order_token: str
    frame: bytes
    metadata: dict
    effect_rows: dict


@dataclass(frozen=True)
class View:
    inputs: Inputs
    working: frozenset
    activity: dict
    token: str
    metadata: dict
    effect_rows: dict


def observed_inputs(inputs, rows, projected, *, previous=None, changed=()):
    """Keep raw and projected ancestry for rows skipped by the scoped read."""
    edges = set(inputs.edges)
    if previous is not None:
        edges.update((sid, parent) for sid, parent in previous.edges
                     if sid in inputs.sources and sid not in changed)
    edges.update(row_edges(rows))
    edges.update(row_edges(projected))
    return Inputs(inputs.order, inputs.sources, inputs.overlays, tuple(edges))


def view_token():
    return "layer-view." + uuid.uuid4().hex


def same_projection(first, second):
    """Compare the inputs that determine result bytes and row order."""
    return (first.order == second.order and first.sources == second.sources
            and first.overlays == second.overlays)


def make_basis(inputs, working, activity, payload, *, effect_rows=None):
    rows = payload["conversations"]
    packed = archive_list_replay.pack_row_layouts(rows)
    token = "layer-base." + uuid.uuid4().hex
    order = archive_list_replay.order_token(inputs.order)
    raw = archive_list_replay.encode_payload(
        dict(payload, row_layouts=packed[0], conversations=packed[1],
             delta_token=token, order_token=order), compact=True)
    frame = list_encoding._zstd(raw)
    if frame is None or len(frame) > _MAX_BASE_BYTES:
        return None
    metadata = {key: value for key, value in payload.items() if key != "conversations"}
    effect_rows = effect_rows if effect_rows and len(effect_rows) <= MAX_PATCH_ROWS else {}
    return Basis(inputs, frozenset(working), activity, token, order, frame, metadata, effect_rows)


def write_basis(target, scope, basis):
    """Replace one bounded, private artifact. A failed write leaves the old one."""
    target = Path(target)
    inputs = basis.inputs
    state = (VERSION, scope, inputs.order, inputs.sources, inputs.overlays,
             inputs.edges, sorted(basis.working),
             basis.activity, basis.token, basis.order_token, basis.metadata, basis.effect_rows)
    metadata = _ENCODE_GUARD(state).encode("ascii")
    if len(metadata) > _MAX_INDEX_BYTES or len(basis.frame) > _MAX_BASE_BYTES:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(len(metadata).to_bytes(4, "big"))
            stream.write(hashlib.sha256(basis.frame + metadata).digest())
            stream.write(basis.frame)
            stream.write(metadata)
        os.replace(name, target)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
    return True


def load_basis(target, scope):
    """Ignore older, incomplete, or damaged projection artifacts."""
    try:
        with open(target, "rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            index_bytes = int.from_bytes(stream.read(4), "big")
            digest = stream.read(32)
            frame_bytes = size - 36 - index_bytes
            if not 0 < index_bytes <= _MAX_INDEX_BYTES or not 0 < frame_bytes <= _MAX_BASE_BYTES:
                return None
            frame = stream.read(frame_bytes)
            metadata = stream.read(index_bytes)
        if hashlib.sha256(frame + metadata).digest() != digest:
            return None
        (version, saved_scope, order, sources, overlays, edges,
         working, activity, token, order_token, payload_metadata, effect_rows) = json.loads(metadata)
        if (version != VERSION or saved_scope != scope or len(order) > MAX_ROWS
                or len(order) != len(sources) or set(order) != sources.keys()
                or order_token != archive_list_replay.order_token(order)
                or not isinstance(payload_metadata, dict) or not isinstance(effect_rows, dict)
                or len(effect_rows) > MAX_PATCH_ROWS or not effect_rows.keys() <= sources.keys()):
            return None
        inputs = Inputs(tuple(order), {sid: tuple(value) for sid, value in sources.items()},
                        tuple(overlays), tuple(map(tuple, edges)))
        return Basis(inputs, frozenset(working), activity, token, order_token, frame,
                     payload_metadata, effect_rows)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def delta_payload(previous_order, current_order, previous_token, current_token, payload, *, saved_order=True):
    """Describe stable, spliced, and explicitly reordered archive responses."""
    order = archive_list_replay.order_token(current_order)
    patch = dict(payload, delta=True, since=previous_token, delta_token=current_token,
                 order_token=order, ids=[])
    if saved_order and previous_order == current_order:
        patch["same_order"] = True
    elif (saved_order and
          (spans := archive_list_replay._order_spans(previous_order, current_order)) is not None):
        patch.update(base_order=archive_list_replay.order_token(previous_order), order_spans=spans)
    else:
        patch["ids"] = current_order
    return patch


def layer_representation(basis, patch):
    """Use independent RFC 9659-compatible frames in one HTTP response."""
    layouts, rows = archive_list_replay.pack_row_layouts(patch["conversations"])
    patch = dict(patch, row_layouts=layouts, conversations=rows)
    prefix = list_encoding._zstd(b'{"base":')
    tail = list_encoding._zstd(b',"history_layer":1,"patch":' + _ENCODE_GUARD(patch).encode("ascii") + b'}')
    return None if prefix is None or tail is None else b"".join((prefix, basis.frame, tail))


def delta_representation(patch):
    return list_encoding._zstd(_ENCODE_GUARD(patch).encode("ascii"))


def row_edges(rows):
    """Record raw and projected ancestry used by the topic resolver."""
    for row in rows:
        sid = row.get("session_id") or row.get("id")
        for field in ("parent_session_id", "hermes_parent_session_id", "continued_from_session_id",
                      "continuation_origin", "hermes_continued_from", "continuation_topic_root_session_id"):
            related = row.get(field)
            if isinstance(sid, str) and sid and isinstance(related, str) and related and sid != related:
                yield sid, related


_INVENTORY = (None, frozenset(), None)


def _canonical_inputs(inventory, removed):
    # The SQLite cut retains one immutable inventory for its exact connection
    # and data version. Recycle the ordered static guards for that cut only.
    global _INVENTORY
    saved, excluded, guard = _INVENTORY
    if saved is inventory and excluded == removed:
        return guard
    sources = {}
    identities = {}
    edges = []
    for sid, identity, parent, digest in inventory:
        if sid in removed:
            continue
        if not sid or sid in sources or not identity or not digest:
            return None
        identities[sid] = identity
        sources[sid] = ("canonical", digest)
        if parent:
            edges.append((sid, parent))
    guard = sources, identities, tuple(edges)
    if type(inventory) is tuple:
        _INVENTORY = inventory, frozenset(removed), guard
    return guard


def read_inputs(inventory, hot, hot_tokens, extra, *, overlay_maps, edges, removed=frozenset()):
    """Build guards from the same sources the complete reader would merge."""
    canonical = _canonical_inputs(inventory, removed)
    if canonical is None:
        return None
    canonical_sources, identities, canonical_edges = canonical
    sources = canonical_sources.copy()
    observed_edges = [*edges, *canonical_edges]
    for sid, row in hot.items():
        if sid not in hot_tokens:
            return None
        identity = identities.get(sid)
        if identity is not None and archive_store.identity(row) != identity:
            return None
        sources[sid] = ("hot", hot_tokens[sid])
    for row in extra:
        sid = row.get("session_id") or row.get("id")
        if not isinstance(sid, str) or not sid or sid in sources:
            return None
        sources[sid] = ("overlay", row_token(row))
    if not sources or len(sources) > MAX_ROWS:
        return None

    observed_edges.extend(row_edges(hot.values()))
    observed_edges.extend(row_edges(extra))
    overlays = tuple({sid: _guard_value(value) for sid, value in values.items()}
                     for values in overlay_maps)
    return Inputs(tuple(sources), sources, overlays, tuple(observed_edges))


class Changes:
    """Reuse the graph and dirty set while candidate hydration exposes edges."""

    def __init__(self, previous, current, *, allow_shrink=False, max_rows=MAX_PATCH_ROWS):
        self.ids = current.sources.keys()
        self.max_rows = max_rows
        self.neighbors = defaultdict(set)
        self.removed = previous.sources.keys() - self.ids
        valid = ((allow_shrink or not self.removed)
                 and len(previous.overlays) == len(current.overlays))
        self.dirty = None
        if valid:
            self.dirty = set(self.removed)
            self.dirty.update(sid for sid, source in current.sources.items()
                              if previous.sources.get(sid) != source)
            for old, new in zip(previous.overlays, current.overlays):
                self.dirty.update(sid for sid in old.keys() | new.keys() if old.get(sid) != new.get(sid))
            self.add_edges(previous.edges)
            self.add_edges(current.edges)

    def add_edges(self, edges):
        for first, second in edges:
            if first and second and first != second:
                self.neighbors[first].add(second)
                self.neighbors[second].add(first)

    def scope(self, additional=()):
        if self.dirty is None:
            return None
        dirty = self.dirty.union(additional)
        if len(dirty) > self.max_rows:
            return None
        pending = deque(dirty)
        while pending:
            for member in self.neighbors.get(pending.popleft(), ()):
                if member not in dirty:
                    dirty.add(member)
                    pending.append(member)
            if len(dirty) > self.max_rows:
                return None
        return dirty & self.ids


def candidates(previous, current, *, additional=(), observed_edges=(), max_rows=MAX_PATCH_ROWS):
    """Return the conservative row set, or None when a full read is required."""
    changes = Changes(previous, current, max_rows=max_rows)
    changes.add_edges(observed_edges)
    return changes.scope(additional)
