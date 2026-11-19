"""Optional compression of canonical list JSON without changing its payload."""

import hashlib
import threading

from .bounded_cache import ByteBudgetCache
from . import static_encoding


_CACHE = ByteBudgetCache(8 << 20, 32)
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
            # Live list bodies change more often than static assets. Quality 5
            # saves encoding time while keeping most of the transfer savings.
            compressed = static_encoding.brotli(raw, quality=5)
            if compressed is not None:
                _CACHE.put(key, compressed, len(compressed))
            return compressed
    finally:
        with _LOCK:
            flight[1] -= 1
            if not flight[1]:
                del _FLIGHTS[key]


def representation(raw, gzip_body, accept):
    """Choose a supported representation, preserving gzip on optional failure."""
    if len(raw) >= 1024:
        for encoding in static_encoding.accepted_encodings(accept):
            if encoding == "gzip" and gzip_body is not None:
                return gzip_body, "gzip"
            if encoding == "br":
                try:
                    body = _brotli(raw)
                except Exception:
                    body = None
                if body is not None:
                    return body, "br"
    return raw, None
