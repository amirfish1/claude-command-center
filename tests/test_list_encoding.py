"""List compression preserves canonical bytes, validators and fallback paths."""

from concurrent.futures import ThreadPoolExecutor
import gzip
import threading
import time

import pytest

from ccc_server import list_encoding
from tests.test_static_encoding import decode


@pytest.fixture(autouse=True)
def clear_cache():
    list_encoding._CACHE.clear()
    yield
    list_encoding._CACHE.clear()


@pytest.mark.parametrize("accept,encoding", [
    ("gzip, br", "br"), ("br;q=0, gzip", "gzip"),
    ("br;q=.2, gzip;q=.9", "gzip"), ("br;q=0, gzip;q=0", None),
    ("", None), ("*", "br"),
])
def test_exact_bytes_and_client_preferences(monkeypatch, accept, encoding):
    raw = b'{"conversations":[]}' * 200
    zipped = gzip.compress(raw)
    monkeypatch.setattr(list_encoding.static_encoding, "brotli",
                        lambda body, *, quality, window_bits: b"brotli:" + body)
    body, actual = list_encoding.representation(raw, zipped, accept)
    assert actual == encoding
    assert (gzip.decompress(body) if actual == "gzip" else
            body.removeprefix(b"brotli:") if actual == "br" else body) == raw


def test_native_encoder_round_trip():
    raw = b'{"conversations":[{"id":"example","status":"working"}]}' * 100
    body, encoding = list_encoding.representation(raw, gzip.compress(raw), "br, gzip")
    if encoding != "br":
        pytest.skip("optional system Brotli unavailable")
    assert decode(body, len(raw)) == raw


@pytest.mark.parametrize("failure", [None, RuntimeError("encoder failed")])
def test_missing_or_failed_encoder_keeps_gzip_or_identity(monkeypatch, failure):
    def compress(raw, *, quality, window_bits):
        if failure:
            raise failure
        return None
    monkeypatch.setattr(list_encoding.static_encoding, "brotli", compress)
    raw = b"a" * 3000
    zipped = gzip.compress(raw)
    assert list_encoding.representation(raw, zipped, "br, gzip") == (zipped, "gzip")
    assert list_encoding.representation(raw, zipped, "br") == (raw, None)
    assert list_encoding._FLIGHTS == {}


def test_content_cache_and_simultaneous_identical_bodies(monkeypatch):
    calls = []
    def compress(raw, *, quality, window_bits):
        calls.append((raw, quality, window_bits))
        time.sleep(.02)
        return raw[:10]
    monkeypatch.setattr(list_encoding.static_encoding, "brotli", compress)
    raw = b"a" * 3000
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: list_encoding.representation(raw, None, "br"), range(4)))
    assert results == [(b"a" * 10, "br")] * 4
    assert calls == [(raw, 5, 20)]
    assert list_encoding._FLIGHTS == {}
    list_encoding.representation(b"b" * 3000, None, "br")
    assert len(calls) == 2, "changed payloads must not reuse stale bytes"


def test_small_bodies_skip_encoder(monkeypatch):
    monkeypatch.setattr(list_encoding.static_encoding, "brotli",
                        lambda *args, **kwargs: pytest.fail("small body"))
    assert list_encoding.representation(b"{}", None, "br, gzip") == (b"{}", None)


def test_handler_validators_lengths_and_vary(monkeypatch):
    import io
    import server
    from email.message import Message

    class Handler:
        _send_archive_list_body = server.CommandCenterHandler._send_archive_list_body
        _send_archive_list_representation = server.CommandCenterHandler._send_archive_list_representation
        def __init__(self, accept, etag=None):
            self.headers = Message()
            self.headers["Accept-Encoding"] = accept
            if etag:
                self.headers["If-None-Match"] = etag
            self.response = {}
            self.wfile = io.BytesIO()
        def send_response(self, status): self.status = status
        def send_header(self, key, value): self.response[key] = value
        def _send_list_server_timing(self): pass
        def end_headers(self): pass

    raw = b"a" * 3000
    monkeypatch.setattr(list_encoding.static_encoding, "brotli",
                        lambda body, *, quality, window_bits: b"encoded")
    for accept, encoding, expected in [("br", "br", b"encoded"), ("gzip", "gzip", gzip.compress(raw)), ("", None, raw)]:
        handler = Handler(accept)
        assert handler._send_archive_list_body(raw, gzip.compress(raw), '"current"') == 200
        assert handler.wfile.getvalue() == expected
        assert handler.response["Content-Length"] == str(len(expected))
        assert handler.response.get("Content-Encoding") == encoding
        assert handler.response["Vary"] == "Accept-Encoding"
        assert handler.response["ETag"] == '"current"'
    monkeypatch.setattr(list_encoding, "representation", lambda *args: pytest.fail("304 compressed"))
    handler = Handler("br", '"current"')
    assert handler._send_archive_list_body(raw, None, '"current"') == 304
    assert handler.wfile.getvalue() == b""
    assert handler.response["Vary"] == "Accept-Encoding"
