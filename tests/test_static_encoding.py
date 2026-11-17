"""Wire-format negotiation, optional-library fallback and exact source bytes."""

import ctypes
import ctypes.util
import gzip

import pytest

from ccc_server import static_encoding
from tests.test_static_asset_caching import _get, _server, _static_dir


@pytest.mark.parametrize("header,expected", [
    ("", ()), ("gzip, br", ("br", "gzip")),
    ("br;q=0, gzip", ("gzip",)), ("br;q=.3, gzip;q=.9", ("gzip", "br")),
    ("gzip;q=0, *;q=.4", ("br",)), ("br;q=invalid", ()),
    ("br;q=nan, gzip;q=2", ()),
])
def test_negotiation(header, expected):
    assert static_encoding.accepted_encodings(header) == expected


def decode(body, expected_size):
    name = ctypes.util.find_library("brotlidec")
    if not name or static_encoding._encoder() is None:
        pytest.skip("optional system Brotli libraries unavailable")
    function = ctypes.CDLL(name).BrotliDecoderDecompress
    function.argtypes = (ctypes.c_size_t, ctypes.c_void_p,
                         ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p)
    function.restype = ctypes.c_int
    output = ctypes.create_string_buffer(expected_size)
    length = ctypes.c_size_t(expected_size)
    assert function(len(body), body, ctypes.byref(length), output) == 1
    return output.raw[:length.value]


def test_native_encoder_round_trips_canonical_bundle():
    from pathlib import Path
    from ccc_server.conversation_assets import ConversationScript
    body = ConversationScript(Path(__file__).resolve().parents[1] / "static").read_bytes()
    compressed = static_encoding.brotli(body)
    if compressed is None:
        pytest.skip("optional system Brotli encoder unavailable")
    assert decode(compressed, len(body)) == body
    assert len(compressed) < len(gzip.compress(body, compresslevel=5))


def test_encoding_cache_is_separate_and_invalidates(tmp_path, monkeypatch):
    if static_encoding._encoder() is None:
        pytest.skip("optional system Brotli encoder unavailable")
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    for encoding in ("br", "gzip", "br", "gzip"):
        _, headers, body = _get(server, "/static/x.js", {"Accept-Encoding": encoding})
        assert headers["content-encoding"] == encoding
        assert headers["vary"] == "Accept-Encoding"
        assert (decode(body, asset.stat().st_size) if encoding == "br"
                else gzip.decompress(body)) == asset.read_bytes()
    assert 'cache;desc="hit"' in headers['server-timing']
    asset.write_bytes(b"// replacement " + b"z" * 6000)
    _, headers, body = _get(server, "/static/x.js", {"Accept-Encoding": "br, gzip"})
    assert decode(body, asset.stat().st_size) == asset.read_bytes()
    assert 'cache;desc="miss"' in headers['server-timing']
    _, headers, body = _get(server, "/static/x.js", {"Accept-Encoding": "br;q=0, gzip;q=0"})
    assert "content-encoding" not in headers and body == asset.read_bytes()
    assert headers["vary"] == "Accept-Encoding"


def test_missing_encoder_and_encoding_failure_keep_gzip(tmp_path, monkeypatch):
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    monkeypatch.setattr(static_encoding, "_encoder", lambda: None)
    assert static_encoding.brotli(asset.read_bytes()) is None
    _, headers, body = _get(server, "/static/x.js", {"Accept-Encoding": "br, gzip"})
    assert headers["content-encoding"] == "gzip"
    assert gzip.decompress(body) == asset.read_bytes()
    _, headers, body = _get(server, "/static/x.js", {"Accept-Encoding": "br"})
    assert "content-encoding" not in headers and body == asset.read_bytes()

    class FailedEncoder:
        def BrotliEncoderMaxCompressedSize(self, size):
            return size + 100

        def BrotliEncoderCompress(self, *args):
            return 0

    monkeypatch.setattr(static_encoding, "_encoder", FailedEncoder)
    server._STATIC_GZIP_CACHE.clear()
    _, headers, body = _get(server, "/static/x.js", {"Accept-Encoding": "br, gzip"})
    assert headers["content-encoding"] == "gzip"
    assert gzip.decompress(body) == asset.read_bytes()
