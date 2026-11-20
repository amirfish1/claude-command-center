"""Optional compression of canonical list JSON without changing its payload."""

import gzip
import hashlib
import threading

from .bounded_cache import ByteBudgetCache
from . import archive_list_replay, static_encoding


_CACHE = ByteBudgetCache(8 << 20, 32)
_HISTORY_CACHE = ByteBudgetCache(12 << 20, 4)
_LOCK = threading.Lock()
_FLIGHTS = {}


def _brotli(raw):
    key = hashlib.sha256(raw).digest()
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    # Coalesce only identical bodies. A large full response must not hold up
    # an unrelated incremental response while the optional encoder runs.
    with _LOCK:
        flight = _FLIGHTS.setdefault(key, [threading.Lock(), 0])
        flight[1] += 1
    try:
        with flight[0]:
            cached = _CACHE.get(key)
            if cached is not None:
                return cached
            # Live list bodies change frequently. A 1 MiB window cut encoder
            # CPU by 13–19% on full sidebar bodies for under 2% more bytes.
            compressed = static_encoding.brotli(raw, quality=5, window_bits=20)
            if compressed is not None:
                _CACHE.put(key, compressed, len(compressed))
            return compressed
    finally:
        with _LOCK:
            flight[1] -= 1
            if not flight[1]:
                del _FLIGHTS[key]


def representation(raw, gzip_body, accept, *, encode_gzip=None):
    """Choose a supported representation, preserving gzip on optional failure."""
    if len(raw) >= 1024:
        for encoding in static_encoding.accepted_encodings(accept):
            if encoding == "gzip":
                if gzip_body is not None:
                    return gzip_body, "gzip"
                if encode_gzip is not None:
                    return encode_gzip(), "gzip"
            if encoding == "br":
                try:
                    body = _brotli(raw)
                except Exception:
                    body = None
                if body is not None:
                    return body, "br"
    return raw, None


def _with_row_layouts(payload):
    """Encode full history rows with shared, ordered column names."""
    layouts = {}
    rows = []
    for row in payload["conversations"]:
        layout = tuple(row)
        index = layouts.setdefault(layout, len(layouts))
        rows.append((index, list(row.values())))
    return dict(payload, conversations=rows, row_layouts=list(layouts))


def history_representation(payload, accept, validator, *, diagnostics=None, fingerprint=None,
                           row_layouts=False):
    """Reuse encoded bytes after a fresh full-history projection."""
    accepted = static_encoding.accepted_encodings(accept)
    if not accepted:
        fingerprint = None
    elif fingerprint is None:
        fingerprint = archive_list_replay.payload_fingerprint(payload)
    key = (fingerprint, accepted, row_layouts)
    cached = _HISTORY_CACHE.get(key) if fingerprint is not None else None
    if diagnostics is not None:
        diagnostics["encoding_hit"] = cached is not None
        diagnostics["row_layouts"] = row_layouts
    if cached is not None:
        body, encoding, etag = cached
        return (None, None, etag) if validator == etag else cached

    if row_layouts:
        payload = _with_row_layouts(payload)
    raw = archive_list_replay.encode_payload(payload)
    etag = '"' + hashlib.sha1(raw).hexdigest() + '"'
    if validator == etag:
        return None, None, etag

    def encode_gzip():
        return (archive_list_replay.encode_gzip_payload(payload, raw)
                or gzip.compress(raw, compresslevel=1))

    body, encoding = representation(raw, None, accept, encode_gzip=encode_gzip)
    if fingerprint is not None and encoding:
        _HISTORY_CACHE.put(key, (body, encoding, etag), len(body) + 256)
    return body, encoding, etag
