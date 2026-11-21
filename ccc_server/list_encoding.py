"""Optional compression of canonical list JSON without changing its payload."""

import ctypes
import ctypes.util
from functools import lru_cache
import gzip
import hashlib
import threading

from .bounded_cache import ByteBudgetCache
from . import archive_list_replay, static_encoding


_CACHE = ByteBudgetCache(8 << 20, 32)
_HISTORY_CACHE = ByteBudgetCache(12 << 20, 4)
_LOCK = threading.Lock()
_FLIGHTS = {}


@lru_cache(maxsize=1)
def _zstd_encoder():
    try:
        name = ctypes.util.find_library("zstd")
        if not name:
            return None
        library = ctypes.CDLL(name)
        size, ptr, integer = ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int
        for symbol, result, args in (
            ("createCCtx", ptr, ()),
            ("freeCCtx", size, (ptr,)),
            ("compressBound", size, (size,)),
            ("isError", ctypes.c_uint, (size,)),
            ("CCtx_setParameter", size, (ptr, integer, integer)),
            ("compress2", size, (ptr, ptr, size, ptr, size)),
        ):
            function = getattr(library, "ZSTD_" + symbol)
            function.argtypes = args
            function.restype = result
        return library
    except (AttributeError, OSError):
        return None


def _zstd(raw):
    library = _zstd_encoder()
    if library is None:
        return None
    context = library.ZSTD_createCCtx()
    if not context:
        return None
    try:
        # ZSTD_c_compressionLevel and ZSTD_c_windowLog. RFC 9659 caps the
        # window at 8 MiB for HTTP content encoding.
        for parameter, value in ((100, 3), (101, 23)):
            if library.ZSTD_isError(library.ZSTD_CCtx_setParameter(context, parameter, value)):
                return None
        capacity = library.ZSTD_compressBound(len(raw))
        if not capacity or library.ZSTD_isError(capacity):
            return None
        output = ctypes.create_string_buffer(capacity)
        length = library.ZSTD_compress2(context, output, capacity, raw, len(raw))
        return None if library.ZSTD_isError(length) else output[:length]
    finally:
        library.ZSTD_freeCCtx(context)


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


def representation(raw, gzip_body, accept, *, encode_gzip=None, encodings=None):
    """Choose a supported representation, preserving gzip on optional failure."""
    if len(raw) >= 1024:
        if encodings is None:
            encodings = static_encoding.accepted_encodings(accept)
        for encoding in encodings:
            if encoding == "gzip":
                if gzip_body is not None:
                    return gzip_body, "gzip"
                if encode_gzip is not None:
                    return encode_gzip(), "gzip"
            else:
                try:
                    body = _zstd(raw) if encoding == "zstd" else _brotli(raw)
                except Exception:
                    body = None
                if body is not None:
                    return body, encoding
    return raw, None


def history_representation(payload, accept, validator, *, diagnostics=None, fingerprint=None,
                           row_layouts=False, packed_rows=None, prefer_zstd=False):
    """Reuse encoded bytes after a fresh full-history projection."""
    supported = ("zstd", "br", "gzip") if row_layouts and prefer_zstd else ("br", "gzip")
    accepted = static_encoding.accepted_encodings(accept, supported=supported)
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
        layouts, rows = (packed_rows if packed_rows is not None
                         else archive_list_replay.pack_row_layouts(payload["conversations"]))
        payload = dict(payload, conversations=rows, row_layouts=layouts)
    raw = archive_list_replay.encode_payload(payload)
    etag = '"' + hashlib.sha1(raw).hexdigest() + '"'
    if validator == etag:
        return None, None, etag

    def encode_gzip():
        return (archive_list_replay.encode_gzip_payload(payload, raw)
                or gzip.compress(raw, compresslevel=1))

    body, encoding = representation(raw, None, accept, encode_gzip=encode_gzip, encodings=accepted)
    if fingerprint is not None and encoding:
        _HISTORY_CACHE.put(key, (body, encoding, etag), len(body) + 256)
    return body, encoding, etag
