# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Reuse encoded archive runs while keeping live overlay timestamps fresh.

Entries contain only immutable bytes and offsets. Decoding on replay gives
ticket processing its own mutable rows without reprojecting the archive.
"""
import json
import pickle
import struct
import zlib


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


def encode_payload(payload):
    if not isinstance(payload, EncodedListPayload):
        return json.dumps(payload).encode()
    chunks = [b'{']
    for index, (key, value) in enumerate(payload.items()):
        if index:
            chunks.append(b', ')
        chunks.extend((json.dumps(key).encode(), b': ',
            payload.conversations_json if key == 'conversations'
            else json.dumps(value).encode()))
    chunks.append(b'}')
    return b''.join(chunks)


def encode_rows(rows, overlay_ids):
    """Encode archive runs in bulk, recording only overlay row offsets."""
    chunks, spans = [], []
    offset = 1  # opening bracket
    index = 0
    while index < len(rows):
        start = index
        sid = str(rows[index].get('session_id') or rows[index].get('id') or '')
        overlay = sid in overlay_ids
        index += 1
        if not overlay:
            while index < len(rows):
                sid = str(rows[index].get('session_id') or rows[index].get('id') or '')
                if sid in overlay_ids:
                    break
                index += 1
        raw = json.dumps(rows[start:index]).encode()[1:-1]
        if chunks:
            offset += 2  # comma and space
        if overlay:
            spans.append((start, offset, offset + len(raw)))
        chunks.append(raw)
        offset += len(raw)
    return b''.join((b'[', b', '.join(chunks), b']')), tuple(spans)


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


def compressed_entry(raw, spans):
    """Keep immutable DEFLATE fragments for archive runs, not mutable rows."""
    fixed, offset = [], 0
    for _, start, end in spans:
        fixed.append(_deflate_fragment(raw[offset:start]))
        offset = end
    fixed.append(_deflate_fragment(raw[offset:]))
    return raw, spans, tuple(fixed)


def compress_replay(entry, rows):
    _, spans, fixed = entry[:3]
    chunks = []
    for slot, (index, _, _) in enumerate(spans):
        chunks.extend((fixed[slot], _deflate_fragment(json.dumps(rows[index]).encode())))
    chunks.append(fixed[-1])
    return b''.join(chunks)
