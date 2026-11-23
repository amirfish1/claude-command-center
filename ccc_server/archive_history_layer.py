# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Bounded change detection for a complete sidebar archive projection."""

from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
import gzip
import hashlib
from itertools import chain
import json
from operator import itemgetter
import os
from pathlib import Path
from struct import Struct
import tempfile
import uuid
import zlib

from ccc_server import archive_list_replay, archive_store, list_encoding


# Bump when persisted projection or guard semantics change.
VERSION = 8
ENCODINGS = ("zstd", "gzip")
_HEADER = Struct(">III")
_GZIP_TRAILER = Struct("<II")
_LAYER_PREFIX = b'{"base":'
MAX_ROWS = 32768
MAX_PATCH_ROWS = 8192
_MAX_BASE_BYTES = 16 << 20
_MAX_INDEX_BYTES = 32 << 20
_MAX_REBASE_BYTES = 96 << 20
REBASED_TOKEN_PREFIX = "layer-rebased."
_GZIP_LEVEL = 3
_ENCODE_GUARD = json.JSONEncoder(separators=(",", ":")).encode


def row_token(row):
    encoded = _ENCODE_GUARD(row).encode("ascii")
    return hashlib.blake2b(encoded, digest_size=16).hexdigest()


@lru_cache(maxsize=128)
def _hot_key_order(keys):
    # Hot rows share a small number of field layouts. Keep only the order
    # and the field lookup here; every encode reads the row's current values.
    ordered = tuple(sorted(keys))
    return (ordered, itemgetter(*ordered)) if ordered != keys else (ordered, None)


def encode_hot_row(row):
    # SQLite sorts persisted metadata keys, while refresh workers use the
    # parser's insertion order. The sidebar projects top-level fields in
    # layout order. Keep the order of nested objects that reach the wire.
    if type(row) is not dict:
        return _ENCODE_GUARD(dict(sorted(row.items())))
    keys = tuple(row)
    if not keys:
        return _ENCODE_GUARD(row)
    if set(map(type, keys)) != {str}:
        return _ENCODE_GUARD(dict(sorted(row.items())))
    ordered, get_values = _hot_key_order(keys)
    return _ENCODE_GUARD(dict(zip(ordered, get_values(row))) if get_values else row)


def hot_row_token(row):
    return hot_body_token(encode_hot_row(row))


def hot_body_token(body):
    return hashlib.blake2b(body.encode("ascii"), digest_size=16).hexdigest()


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
    frames: dict
    gzip_check: tuple
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


def basis_path(cache_file):
    return cache_file.with_suffix(f".sidebar-layer-v{VERSION}")


def rebased_path(cache_file):
    return cache_file.with_suffix(f".sidebar-layer-rebased-v{VERSION}")


def _digest(parts):
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
    return digest.digest()


def make_basis(inputs, working, activity, payload, *, effect_rows=None, rebased=False):
    rows = payload["conversations"]
    packed = archive_list_replay.pack_row_layouts(rows)
    token = (REBASED_TOKEN_PREFIX if rebased else "layer-base.") + uuid.uuid4().hex
    order = archive_list_replay.order_token(inputs.order)
    raw = archive_list_replay.encode_payload(
        dict(payload, row_layouts=packed[0], conversations=packed[1],
             delta_token=token, order_token=order), compact=True)
    frames = {}
    # Publication runs out of process and each frame can serve many cold
    # reads. Spend more compression work once on large, reusable frames.
    zstd_frame = None
    if len(raw) >= (8 << 20):
        zstd_frame = list_encoding._zstd(raw, level=6)
    if zstd_frame is None:
        zstd_frame = list_encoding._zstd(raw)
    if zstd_frame and len(zstd_frame) <= _MAX_BASE_BYTES:
        frames["zstd"] = zstd_frame
    # Leave the gzip member open so a small patch can complete it later.
    gzip_encoder = zlib.compressobj(_GZIP_LEVEL, wbits=31)
    gzip_prefix = (gzip_encoder.compress(_LAYER_PREFIX) + gzip_encoder.compress(raw)
                   + gzip_encoder.flush(zlib.Z_FULL_FLUSH))
    gzip_check = ()
    if len(gzip_prefix) <= _MAX_BASE_BYTES:
        frames["gzip"] = gzip_prefix
        gzip_check = (zlib.crc32(raw, zlib.crc32(_LAYER_PREFIX)), len(_LAYER_PREFIX) + len(raw))
    if not frames:
        return None
    metadata = {key: value for key, value in payload.items() if key != "conversations"}
    effect_rows = effect_rows if effect_rows and len(effect_rows) <= MAX_PATCH_ROWS else {}
    return Basis(inputs, frozenset(working), activity, token, order, frames, gzip_check,
                 metadata, effect_rows)


def write_basis(target, scope, basis):
    """Replace one bounded, private artifact. A failed write leaves the old one."""
    target = Path(target)
    inputs = basis.inputs
    state = (VERSION, scope, inputs.order, inputs.sources, inputs.overlays,
             inputs.edges, sorted(basis.working),
             basis.activity, basis.token, basis.order_token, basis.gzip_check,
             basis.metadata, basis.effect_rows)
    metadata = _ENCODE_GUARD(state).encode("ascii")
    frames = tuple(basis.frames.get(encoding, b"") for encoding in ENCODINGS)
    if (len(metadata) > _MAX_INDEX_BYTES or not any(frames)
            or any(len(frame) > _MAX_BASE_BYTES for frame in frames)):
        return False
    parts = (*frames, metadata)
    header = _HEADER.pack(*(len(part) for part in parts))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(header)
            stream.write(_digest((header, *parts)))
            for part in parts:
                stream.write(part)
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
            header = stream.read(_HEADER.size)
            if len(header) != _HEADER.size:
                return None
            *frame_bytes, index_bytes = _HEADER.unpack(header)
            digest = stream.read(32)
            if (not 0 < index_bytes <= _MAX_INDEX_BYTES or not any(frame_bytes)
                    or any(length > _MAX_BASE_BYTES for length in frame_bytes)
                    or len(digest) != 32
                    or size != _HEADER.size + 32 + sum(frame_bytes) + index_bytes):
                return None
            frames = {encoding: stream.read(length)
                      for encoding, length in zip(ENCODINGS, frame_bytes) if length}
            metadata = stream.read(index_bytes)
        if _digest((header, *frames.values(), metadata)) != digest:
            return None
        (version, saved_scope, order, sources, overlays, edges,
         working, activity, token, order_token, gzip_check, payload_metadata, effect_rows) = json.loads(metadata)
        if (version != VERSION or saved_scope != scope or len(order) > MAX_ROWS
                or len(order) != len(sources) or set(order) != sources.keys()
                or order_token != archive_list_replay.order_token(order)
                or not isinstance(payload_metadata, dict) or not isinstance(effect_rows, dict)
                or len(effect_rows) > MAX_PATCH_ROWS or not effect_rows.keys() <= sources.keys()):
            return None
        if (not isinstance(gzip_check, list)
                or len(gzip_check) != (2 if "gzip" in frames else 0)
                or any(type(item) is not int or not 0 <= item < 2**32 for item in gzip_check)):
            return None
        inputs = Inputs(tuple(order), {sid: tuple(value) for sid, value in sources.items()},
                        tuple(overlays), tuple(map(tuple, edges)))
        return Basis(inputs, frozenset(working), activity, token, order_token, frames,
                     tuple(gzip_check), payload_metadata, effect_rows)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


@lru_cache(maxsize=8)
def order_token(order):
    """Reuse the wire digest of an immutable, bounded history order."""
    return archive_list_replay.order_token(order)


def delta_payload(previous_order, current_order, previous_token, current_token, payload, *, saved_order=True):
    """Describe stable, spliced, and explicitly reordered archive responses."""
    order = order_token(current_order)
    patch = dict(payload, delta=True, since=previous_token, delta_token=current_token,
                 order_token=order, ids=[])
    if saved_order and previous_order == current_order:
        patch["same_order"] = True
    elif (saved_order and
          (spans := archive_list_replay._order_spans(previous_order, current_order)) is not None):
        patch.update(base_order=order_token(previous_order), order_spans=spans)
    else:
        patch["ids"] = current_order
    return patch


def layer_representation(basis, patch, encoding):
    """Compose one HTTP response from an encoded archive and the current patch."""
    return join_layer(basis, layer_tail(patch), encoding)


def layer_tail(patch):
    layouts, rows = archive_list_replay.pack_row_layouts(patch["conversations"])
    patch = dict(patch, row_layouts=layouts, conversations=rows)
    return b',"history_layer":1,"patch":' + _ENCODE_GUARD(patch).encode("ascii") + b'}'


def join_layer(basis, tail, encoding):
    if encoding == "gzip":
        # The published prefix ends at a full flush. Start an independent
        # deflate block and finish the same gzip member with a cumulative CRC.
        encoder = zlib.compressobj(_GZIP_LEVEL, wbits=-15)
        block = encoder.compress(tail) + encoder.flush()
        checksum, length = basis.gzip_check
        trailer = _GZIP_TRAILER.pack(zlib.crc32(tail, checksum), (length + len(tail)) % 2**32)
        return b"".join((basis.frames["gzip"], block, trailer))
    prefix = list_encoding._zstd(_LAYER_PREFIX)
    suffix = list_encoding._zstd(tail)
    return None if prefix is None or suffix is None else b"".join((prefix, basis.frames["zstd"], suffix))


def rebase_basis(seed):
    """Flatten an exact layered response into a new independently guarded basis."""
    decoder = zlib.decompressobj(wbits=31)
    try:
        raw = decoder.decompress(seed.frames["gzip"], _MAX_REBASE_BYTES + 1)
        if len(raw) > _MAX_REBASE_BYTES or not decoder.eof or decoder.unused_data:
            return None
        document = json.loads(raw)
        if document["history_layer"] != 1 or document["patch"]["delta_token"] != seed.token:
            return None
        rows = {}
        order = seed.inputs.order
        wanted = seed.inputs.sources
        for part in (document["base"], document["patch"]):
            layouts = part["row_layouts"]
            for index, values in part["conversations"]:
                fields = layouts[index]
                row = dict(zip(fields, values, strict=True))
                sid = row.get("session_id") or row.get("id")
                if sid in wanted:
                    rows[sid] = row
        if rows.keys() != wanted.keys():
            return None
        return make_basis(seed.inputs, seed.working, seed.activity,
                          dict(seed.metadata, conversations=[rows[sid] for sid in order]),
                          effect_rows=seed.effect_rows, rebased=True)
    except (KeyError, TypeError, ValueError, IndexError, zlib.error):
        return None


def delta_representation(patch, encoding):
    raw = _ENCODE_GUARD(patch).encode("ascii")
    if encoding == "zstd":
        return list_encoding._zstd(raw)
    return gzip.compress(raw, compresslevel=_GZIP_LEVEL, mtime=0)


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
_PINNED_OVERLAY_GUARDS = ((), ())
_PINNED_MEMBERSHIP = (None, None, None)


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


def _overlay_guards(overlay_maps, pinned):
    """Reuse encoded guards for maps owned by one immutable read snapshot."""
    global _PINNED_OVERLAY_GUARDS
    previous, encoded = _PINNED_OVERLAY_GUARDS
    if len(pinned) != len(previous) or any(a is not b for a, b in zip(pinned, previous)):
        encoded = tuple({sid: _guard_value(value) for sid, value in values.items()} for values in pinned)
        _PINNED_OVERLAY_GUARDS = pinned, encoded
    by_identity = {id(values): guard for values, guard in zip(pinned, encoded)}
    return tuple(guard if (guard := by_identity.get(id(values))) is not None else
                 {sid: _guard_value(value) for sid, value in values.items()} for values in overlay_maps)


def _membership_guards(canonical, hot, hot_tokens, pinned):
    # A captured hot cut owns its token map for its lifetime. Keep the
    # tokens as the identity proof without retaining the copied row bodies.
    global _PINNED_MEMBERSHIP
    previous, tokens, guard = _PINNED_MEMBERSHIP
    if pinned and previous is canonical and tokens is hot_tokens:
        return guard
    canonical_sources, identities, canonical_edges = canonical
    sources = canonical_sources.copy()
    for sid, row in hot.items():
        if sid not in hot_tokens:
            return None
        identity = identities.get(sid)
        if identity is not None and archive_store.identity(row) != identity:
            return None
        sources[sid] = ("hot", hot_tokens[sid])
    guard = sources, (*canonical_edges, *row_edges(hot.values()))
    if pinned:
        _PINNED_MEMBERSHIP = canonical, hot_tokens, guard
    return guard


def read_inputs(inventory, hot, hot_tokens, extra, *, overlay_maps, edges,
                removed=frozenset(), pinned_overlays=(), pinned_hot=False):
    """Read projection guards. A pinned hot cut must own an immutable token map."""
    canonical = _canonical_inputs(inventory, removed)
    if canonical is None:
        return None
    guard = _membership_guards(canonical, hot, hot_tokens, pinned_hot)
    if guard is None:
        return None
    members, membership_edges = guard
    # Overlay rows vary independently, and a published membership is shared
    # by in-flight reads and previous views. Copy only when adding overlays.
    sources = members
    for row in extra:
        sid = row.get("session_id") or row.get("id")
        if not isinstance(sid, str) or not sid or sid in sources:
            return None
        if sources is members:
            sources = members.copy()
        sources[sid] = ("overlay", row_token(row))
    if not sources or len(sources) > MAX_ROWS:
        return None

    overlays = _overlay_guards(overlay_maps, pinned_overlays)
    return Inputs(tuple(sources), sources, overlays, (*edges, *membership_edges, *row_edges(extra)))


def _add_neighbors(neighbors, edges):
    for first, second in edges:
        if first and second and first != second:
            neighbors[first].add(second)
            neighbors[second].add(first)


@lru_cache(maxsize=4)
def _prior_neighbors(previous, current):
    """Share the adjacency of exact pre-hydration edge sequences."""
    neighbors = defaultdict(set)
    # Edges unique to either cut still affect the family of a removed row.
    _add_neighbors(neighbors, set(chain(previous, current)))
    return neighbors


class Changes:
    """Reuse the graph and dirty set while candidate hydration exposes edges."""

    def __init__(self, previous, current, *, allow_shrink=False, max_rows=MAX_PATCH_ROWS):
        self.ids = current.sources.keys()
        self.max_rows = max_rows
        self._observed_neighbors = defaultdict(set)
        self._prior_neighbors = {}
        self.removed = previous.sources.keys() - self.ids
        valid = ((allow_shrink or not self.removed)
                 and len(previous.overlays) == len(current.overlays))
        self.dirty = None
        if valid:
            self.dirty = set(self.removed)
            previous_source = previous.sources.get
            self.dirty.update(sid for sid, source in current.sources.items()
                              if previous_source(sid) != source)
            for old, new in zip(previous.overlays, current.overlays):
                if old != new:
                    old_value = old.get
                    self.dirty.update(sid for sid, value in new.items() if old_value(sid) != value)
                    self.dirty.update(sid for sid in old.keys() - new.keys() if old[sid] is not None)
            if len(self.dirty) <= self.max_rows:
                self._prior_neighbors = _prior_neighbors(previous.edges, current.edges)

    def add_edges(self, edges):
        _add_neighbors(self._observed_neighbors, edges)

    def scope(self, additional=()):
        if self.dirty is None:
            return None
        dirty = self.dirty.union(additional)
        if len(dirty) > self.max_rows:
            return None
        pending = deque(dirty)
        while pending:
            sid = pending.popleft()
            # Edges discovered while hydrating rows belong only to this read.
            for member in chain(self._prior_neighbors.get(sid, ()), self._observed_neighbors.get(sid, ())):
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
