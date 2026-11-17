# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Reuse encoded archive runs while keeping live overlay timestamps fresh.

Entries contain only immutable bytes and offsets. Decoding on replay gives
ticket processing its own mutable rows without reprojecting the archive.
"""
import json


class EncodedListPayload(dict):
    def __init__(self, payload, conversations_json):
        super().__init__(payload)
        self.conversations_json = conversations_json


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


def replay_rows(entry, overlays):
    raw, spans = entry
    rows = json.loads(raw)
    for index, _, _ in spans:
        row = rows[index]
        source = overlays[str(row.get('session_id') or row.get('id') or '')]
        for key in ('mtime', 'modified'):
            if key in source:
                row[key] = source[key]
            else:
                row.pop(key, None)
    return rows


def encode_replay(entry, rows):
    raw, spans = entry
    chunks, offset = [], 0
    for index, start, end in spans:
        chunks.extend((raw[offset:start], json.dumps(rows[index]).encode()))
        offset = end
    chunks.append(raw[offset:])
    return b''.join(chunks)
