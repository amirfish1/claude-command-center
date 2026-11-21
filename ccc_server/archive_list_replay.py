# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Reuse encoded archive runs while keeping live overlay timestamps fresh.

Entries contain only immutable bytes and offsets. Decoding on replay gives
ticket processing its own mutable rows without reprojecting the archive.
"""
import hashlib
import io
import json
import pickle
import struct
import zlib

from ccc_server.bounded_cache import ByteBudgetCache


# Snapshot versions change when only a few rows change. Keep small encoded
# runs reusable across those versions, bounded independently of full bodies.
_RUN_CACHE = ByteBudgetCache(32 * 1024 * 1024, 512)
# Packed All responses have different row slots and must not evict the
# smaller sidebar windows' DEFLATE runs.
_PACKED_RUN_CACHE = ByteBudgetCache(32 * 1024 * 1024, 700)
_HISTORY_MANIFESTS = ByteBudgetCache(4 * 1024 * 1024, 32)
# Multiple manifests can share an order. Keep IDs under a separate budget
# so retaining them never evicts the run guards needed for ordinary deltas.
_HISTORY_ORDERS = ByteBudgetCache(8 * 1024 * 1024, 16)
_RUN_MAX_ROWS = 256
_RUN_BOUNDARY_MASK = 63
# Bound how many packed value arrays a conditional history request retains.
# Larger responses still use the original run guard and pack only to send.
_HISTORY_PACK_MAX_ROWS = 32768


class _GuardPickler(pickle.Pickler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # JSON replay does not preserve shared object identity. Guard values
        # independently of aliases so an unchanged decoded run still matches.
        self.fast = True

    def reducer_override(self, value):
        # Only the built-in JSON containers/scalars use pickle's fast paths.
        # Custom reducers must not hide a JSON-visible mutation in the guard.
        raise TypeError('custom values are not encoding cache guards')


def payload_fingerprint(payload):
    """Hash ordered JSON-compatible values without keeping the serialized copy."""
    class DigestWriter:
        def __init__(self):
            self.digest = hashlib.sha256()

        def write(self, data):
            self.digest.update(data)

    writer = DigestWriter()
    try:
        _GuardPickler(writer, protocol=pickle.HIGHEST_PROTOCOL).dump(payload)
    except (TypeError, ValueError, pickle.PicklingError, RecursionError):
        return None
    return writer.digest.digest()


def order_token(ids):
    """Identify the exact visible order independently of row contents."""
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()


def pack_row_layouts(rows, *, check_client=None):
    """Collect the ordered keys and corresponding values for each row."""
    layouts = {}
    packed = []
    for position, row in enumerate(rows):
        if check_client is not None and position % _RUN_MAX_ROWS == 0:
            check_client()
        layout = tuple(row)
        index = layouts.setdefault(layout, len(layouts))
        packed.append((index, list(row.values())))
    return list(layouts), packed


def prepare_history(payload, *, check_client=None):
    """Build the shared manifest and, when bounded, the reusable row layout."""
    rows = payload["conversations"]
    packed = layout_digests = None
    if len(rows) <= _HISTORY_PACK_MAX_ROWS and all(type(row) is dict for row in rows):
        packed = pack_row_layouts(rows, check_client=check_client)
        layout_digests = [payload_fingerprint(layout) if all(type(key) is str for key in layout)
                          else None for layout in packed[0]]
        if None in layout_digests:
            packed = layout_digests = None
    ids, seen, runs = [], set(), []
    start = 0
    for end, row in enumerate(rows, 1):
        if check_client is not None and (end - 1) % _RUN_MAX_ROWS == 0:
            check_client()
        sid = row.get("session_id") or row.get("id")
        if not isinstance(sid, str) or not sid or sid in seen:
            return payload, None, packed
        ids.append(sid)
        seen.add(sid)
        if (end - start == _RUN_MAX_ROWS or end == len(rows)
                or zlib.crc32(sid.encode("utf-8", "surrogatepass")) & _RUN_BOUNDARY_MASK == 0):
            if packed is None:
                content = rows[start:end]
            else:
                # Slot numbers change if a shape first appears earlier in
                # another response. Use the ordered field names' digest.
                content = [(layout_digests[index], values)
                           for index, values in packed[1][start:end]]
            digest = payload_fingerprint(content)
            if digest is None:
                return payload, None, packed
            runs.append((digest, start, end))
            start = end

    metadata = payload_fingerprint({key: value for key, value in payload.items() if key != "conversations"})
    if metadata is None:
        return payload, None, packed
    fingerprint = hashlib.sha256(metadata + b"".join(digest for digest, _, _ in runs)).digest()
    return payload, (ids, runs, fingerprint, order_token(ids)), packed


def _packed_history_fragments(rows, runs, check_client):
    """Reuse exactly guarded JSON fragments for full packed history replies."""
    chunks = [b"["]
    for position, (digest, start, end) in enumerate(runs):
        if check_client is not None and position % 8 == 0:
            check_client()
        group = rows[start:end]
        # The manifest hashes the field names, so its digest stays stable
        # even when another layout first appears earlier in a response.
        # Packed JSON contains the wire slot numbers too.
        slots = tuple(index for index, _ in group)
        key = (digest, slots)
        raw = _PACKED_RUN_CACHE.get(key)
        if raw is None:
            raw = json.dumps(group).encode()[1:-1]
            _PACKED_RUN_CACHE.put(key, raw, len(raw) + 224 + 32 * len(slots))
        if start:
            chunks.append(b", ")
        chunks.append(raw)
    chunks.append(b"]")
    return chunks


def _order_spans(previous_ids, ids):
    """Describe current order with slices of the saved order and new IDs."""
    positions = {sid: index for index, sid in enumerate(previous_ids)}
    spans = []
    for sid in ids:
        index = positions.get(sid)
        last = spans[-1] if spans else None
        if index is None:
            spans.append(sid)
        elif isinstance(last, list) and index == last[0] + last[1]:
            last[1] += 1
        else:
            spans.append([index, 1])
        if len(spans) > 256:
            return None
    return spans if len(spans) * 4 < len(ids) else None


def history_delta(history, scope, nonce, since, saved_order, *, order_spans=False):
    """Apply one client's base and saved order to a prepared history."""
    payload, manifest, _ = history
    if manifest is None:
        return payload, None
    ids, runs, fingerprint, order = manifest
    token = f"{nonce}.history.{fingerprint.hex()}"
    if since == token:
        return None, None

    previous = _HISTORY_MANIFESTS.get((scope, since)) if since else None
    current = frozenset(digest for digest, _, _ in runs)
    previous_runs, previous_order = previous if previous is not None else ((), None)
    _HISTORY_MANIFESTS.put((scope, token), (current, order), 512 + 128 * len(current))
    retain_order = order_spans and len(ids) <= _HISTORY_PACK_MAX_ROWS
    previous_ids = None
    if retain_order:
        if saved_order == previous_order and saved_order != order:
            previous_ids = _HISTORY_ORDERS.get((scope, saved_order))
        order_key = (scope, order)
        if _HISTORY_ORDERS.get(order_key) is None:
            _HISTORY_ORDERS.put(order_key, ids, 512 + sum(64 + 4 * len(sid) for sid in ids))
    # The projection can be shared by concurrent readers of the same source.
    # Tokens and the changed-row slice belong to this response alone.
    payload = dict(payload, order_token=order, delta_token=token)
    if not current.isdisjoint(previous_runs):
        rows = payload["conversations"]
        changed = [row for digest, start, end in runs if digest not in previous_runs for row in rows[start:end]]
        same_order = saved_order == order
        payload.update(conversations=changed, delta=True, since=since, ids=[] if same_order else ids)
        if same_order:
            payload["same_order"] = True
        elif previous_ids is not None:
            spans = _order_spans(previous_ids, ids)
            if spans is not None:
                payload.update(ids=[], order_spans=spans, base_order=saved_order)
        return payload, None
    # Domain-separate the assembled manifest from the plain payload guard.
    return payload, (nonce, fingerprint)


def _encoded_run(rows):
    try:
        buffer = io.BytesIO()
        _GuardPickler(buffer, protocol=pickle.HIGHEST_PROTOCOL).dump(rows)
        guard = buffer.getvalue()
    except (TypeError, pickle.PicklingError):
        raw = json.dumps(rows).encode()[1:-1]
        return raw, _deflate_fragment(raw)
    # The immutable bytes already describe the exact projected values.
    # Dictionary lookup compares them on a hash match.
    entry = _RUN_CACHE.get(guard)
    if entry is not None:
        return entry
    raw = json.dumps(rows).encode()[1:-1]
    compressed = _deflate_fragment(raw)
    _RUN_CACHE.put(guard, (raw, compressed),
                   len(guard) + len(raw) + len(compressed) + 512)
    return raw, compressed


class TicketContextEntry(tuple):
    """Keep ticket provenance with the bytes it describes, under one budget."""
    def __new__(cls, entry, context):
        result = super().__new__(cls, entry)
        result.ticket_context = context
        return result


class EncodedListPayload(dict):
    def __init__(self, payload, conversations_json, conversations_deflate=None):
        super().__init__(payload)
        self.conversations_json = conversations_json
        self.conversations_deflate = conversations_deflate


def _deflate_fragment(raw):
    # FULL_FLUSH leaves a byte-aligned, non-final block and resets the
    # dictionary. A following independently compressed fragment needs no
    # preceding bytes; only the very last block ends the DEFLATE stream.
    encoder = zlib.compressobj(level=1, wbits=-zlib.MAX_WBITS)
    return encoder.compress(raw) + encoder.flush(zlib.Z_FULL_FLUSH)


def encode_gzip_payload(payload, raw=None):
    """Wrap reusable DEFLATE fragments in one browser-compatible gzip member."""
    if not isinstance(payload, EncodedListPayload) or payload.conversations_deflate is None:
        return None
    chunks, pending = [], [b'{']
    for index, (key, value) in enumerate(payload.items()):
        if index:
            pending.append(b', ')
        pending.extend((json.dumps(key).encode(), b': '))
        if key == 'conversations':
            chunks.extend((_deflate_fragment(b''.join(pending)),
                           payload.conversations_deflate))
            pending = []
        else:
            pending.append(json.dumps(value).encode())
    pending.append(b'}')
    chunks.append(_deflate_fragment(b''.join(pending)))
    raw = encode_payload(payload) if raw is None else raw
    # One gzip header, one final empty DEFLATE block, one CRC/size trailer.
    # Concatenated gzip members are not decoded fully by browser fetch.
    return b''.join((b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x04\xff', *chunks,
                     b'\x03\x00', struct.pack('<II', zlib.crc32(raw), len(raw) & 0xffffffff)))


def encode_payload(payload, *, packed_runs=None, check_client=None):
    if packed_runs is None and not isinstance(payload, EncodedListPayload):
        return json.dumps(payload).encode()
    chunks = [b'{']
    for index, (key, value) in enumerate(payload.items()):
        if index:
            chunks.append(b', ')
        chunks.extend((json.dumps(key).encode(), b': '))
        if key == 'conversations':
            if packed_runs is None:
                chunks.append(payload.conversations_json)
            else:
                chunks.extend(_packed_history_fragments(value, packed_runs, check_client))
        else:
            chunks.append(json.dumps(value).encode())
    chunks.append(b'}')
    return b''.join(chunks)


def encode_rows(rows, overlay_ids):
    """Return the JSON array, overlay offsets, and fixed DEFLATE fragments."""
    chunks, spans, fixed = [b'['], [], []
    pending = [_deflate_fragment(b'[')]
    offset = 1  # opening bracket
    index = 0
    while index < len(rows):
        start = index
        sid = str(rows[index].get('session_id') or rows[index].get('id') or '')
        overlay = sid in overlay_ids
        index += 1
        if not overlay:
            while index < len(rows) and index - start < _RUN_MAX_ROWS:
                sid = str(rows[index].get('session_id') or rows[index].get('id') or '')
                if sid in overlay_ids:
                    break
                # Anchor boundaries to identities rather than list positions:
                # an insertion should not invalidate every subsequent run.
                # The complete content guard still decides cache validity.
                previous_sid = str(rows[index - 1].get('session_id') or rows[index - 1].get('id') or '')
                if zlib.crc32(previous_sid.encode()) & _RUN_BOUNDARY_MASK == 0:
                    break
                index += 1
        if overlay:
            raw = json.dumps(rows[start:index]).encode()[1:-1]
        else:
            raw, compressed = _encoded_run(rows[start:index])
        if start:
            chunks.append(b', ')
            offset += 2  # comma and space
            pending.append(_deflate_fragment(b', '))
        if overlay:
            spans.append((start, offset, offset + len(raw)))
            fixed.append(b''.join(pending))
            pending = []
        else:
            pending.append(compressed)
        chunks.append(raw)
        offset += len(raw)
    pending.append(_deflate_fragment(b']'))
    fixed.append(b''.join(pending))
    chunks.append(b']')
    return b''.join(chunks), tuple(spans), tuple(fixed)


def replay_rows(entry, overlays, *, overlay_order=None):
    raw, spans = entry[:2]
    # Clone bytes are created only from our own JSON-decoded rows below;
    # they are never accepted from disk, HTTP or another process.
    rows = pickle.loads(entry[3]) if len(entry) > 3 and entry[3] is not None else json.loads(raw)
    by_sid = {str(rows[index].get('session_id') or rows[index].get('id') or ''): rows[index]
              for index, _, _ in spans} if overlay_order is not None else None
    if overlay_order is not None and len(overlay_order) != len(spans):
        raise ValueError('overlay order does not match replacement slots')
    for slot, (index, _, _) in enumerate(spans):
        row = by_sid[overlay_order[slot]] if by_sid is not None else rows[index]
        rows[index] = row
        source = overlays[str(row.get('session_id') or row.get('id') or '')]
        for key in ('mtime', 'modified'):
            if key in source:
                row[key] = source[key]
            else:
                row.pop(key, None)
    return rows


def with_owned_rows(entry, rows):
    """Retain an immutable clone of normalized rows, before ticket mutation."""
    cloned = (*entry[:3], pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL))
    return (TicketContextEntry(cloned, entry.ticket_context)
            if isinstance(entry, TicketContextEntry) else cloned)


def entry_weight(entry):
    return (len(entry[0]) + sum(map(len, entry[2]))
            + (len(entry[3] or b'') if len(entry) > 3 else 0)
            + 256 * len(entry[1]) + 1024
            + (256 + 2 * len(entry.ticket_context) if isinstance(entry, TicketContextEntry) else 0))


def encode_replay(entry, rows):
    raw, spans = entry[:2]
    chunks, offset = [], 0
    for index, start, end in spans:
        chunks.extend((raw[offset:start], json.dumps(rows[index]).encode()))
        offset = end
    chunks.append(raw[offset:])
    return b''.join(chunks)


def compress_replay(entry, rows):
    _, spans, fixed = entry[:3]
    chunks = []
    for slot, (index, _, _) in enumerate(spans):
        chunks.extend((fixed[slot], _deflate_fragment(json.dumps(rows[index]).encode())))
    chunks.append(fixed[-1])
    return b''.join(chunks)
