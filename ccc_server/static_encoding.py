"""Optional system Brotli for immutable assets; no Python dependency required."""

import ctypes
import ctypes.util
from functools import lru_cache


@lru_cache(maxsize=1)
def _encoder():
    try:
        name = ctypes.util.find_library("brotlienc")
        if not name:
            return None
        library = ctypes.CDLL(name)
        bound = library.BrotliEncoderMaxCompressedSize
        bound.argtypes = (ctypes.c_size_t,)
        bound.restype = ctypes.c_size_t
        encode = library.BrotliEncoderCompress
        encode.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           ctypes.c_size_t, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p)
        encode.restype = ctypes.c_int
        return library
    except (AttributeError, OSError):
        return None


def brotli(body):
    library = _encoder()
    if library is None:
        return None
    capacity = library.BrotliEncoderMaxCompressedSize(len(body))
    if not capacity:
        return None
    output = ctypes.create_string_buffer(capacity)
    length = ctypes.c_size_t(capacity)
    # Quality 6 avoids the multi-second maximum-quality build cost. CDLL
    # releases the GIL while the system encoder processes the immutable bytes.
    if not library.BrotliEncoderCompress(6, 22, 0, len(body), body,
                                         ctypes.byref(length), output):
        return None
    return output.raw[:length.value]


def accepted_encodings(header):
    """Supported encodings in client preference order, respecting q=0."""
    weights = {}
    for item in (header or "").lower().split(","):
        token, *parameters = item.strip().split(";")
        weight = 1.0
        for parameter in parameters:
            key, _, value = parameter.strip().partition("=")
            if key == "q":
                try:
                    weight = float(value)
                    if not 0 <= weight <= 1:
                        weight = 0.0
                except ValueError:
                    weight = 0.0
        weights[token] = weight
    return tuple(sorted((encoding for encoding in ("br", "gzip")
                         if weights.get(encoding, weights.get("*", 0)) > 0),
                        key=lambda encoding: -weights.get(encoding, weights.get("*", 0))))
