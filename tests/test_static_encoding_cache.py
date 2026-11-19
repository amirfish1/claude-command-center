"""Restart reuse must preserve source invalidation, negotiation and fallback."""

import gzip
import os
from concurrent.futures import ThreadPoolExecutor

from ccc_server.static_encoding_cache import StaticEncodingCache
from tests.test_static_asset_caching import _get, _server, _static_dir


def test_new_instance_reuses_only_matching_identity(tmp_path):
    cache = StaticEncodingCache(tmp_path)
    cache.put(("source", 1, "br"), b"encoded bytes")
    restarted = StaticEncodingCache(tmp_path)
    assert restarted.get(("source", 1, "br")) == b"encoded bytes"
    assert restarted.get(("source", 2, "br")) is None
    assert restarted.get(("source", 1, "gzip")) is None


def test_corrupt_truncated_and_oversize_packets_are_misses(tmp_path):
    cache = StaticEncodingCache(tmp_path, max_bytes=100)
    for packet in (b"partial", b"x" * 40, b"x" * 101):
        cache._path("key").write_bytes(packet)
        assert cache.get("key") is None
    cache.put("key", b"repaired")
    assert cache.get("key") == b"repaired"


def test_storage_failure_is_optional(tmp_path):
    obstructed = tmp_path / "not-a-directory"
    obstructed.write_text("occupied")
    cache = StaticEncodingCache(obstructed)
    cache.put("key", b"encoded")
    assert cache.get("key") is None


def test_disk_budget_and_entry_limit(tmp_path):
    cache = StaticEncodingCache(tmp_path, max_bytes=110, max_entries=2)
    for index in range(5):
        cache.put(index, b"x" * 20)
    entries = list(tmp_path.glob("*.cache"))
    assert len(entries) <= 2
    assert sum(entry.stat().st_size for entry in entries) <= 110
    assert cache.get(4) == b"x" * 20
    cache.put("oversize", b"x" * 111)
    assert cache.get("oversize") is None


def test_agent_bundle_survives_static_churn_and_expires_on_edit(tmp_path):
    from ccc_server.conversation_assets import ConversationScript, STYLE_NAME, encoding_cache_keys

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    style = static_dir / "app.css"
    style.write_text(".pane { color: black; }")
    bundle = ConversationScript(static_dir, STYLE_NAME)

    def cache_instance():
        return StaticEncodingCache(
            tmp_path / "cache", max_bytes=120, max_entries=2,
            protected_keys=lambda: encoding_cache_keys(static_dir),
        )
    cache = cache_instance()
    _, key, _ = bundle.metadata()
    cache.put((key, "br"), b"encoded pane")
    for index in range(4):
        cache.put(index, b"ordinary asset")
    restarted = cache_instance()
    assert restarted.get((key, "br")) == b"encoded pane"
    assert restarted.get(3) == b"ordinary asset"
    assert restarted.get(2) is None

    style.write_text(".pane { color: white; }")
    _, edited_key, _ = bundle.metadata()
    assert restarted.get((edited_key, "br")) is None
    restarted.put((edited_key, "br"), b"edited pane")
    restarted.put(4, b"ordinary asset")
    assert restarted.get((key, "br")) is None
    assert restarted.get((edited_key, "br")) == b"edited pane"
    assert len(list((tmp_path / "cache").glob("*.cache"))) == 2


def test_concurrent_writes_publish_complete_packets(tmp_path):
    cache = StaticEncodingCache(tmp_path)
    bodies = [bytes([index]) * 8192 for index in range(20)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: cache.put(index, bodies[index]), range(len(bodies))))
    restarted = StaticEncodingCache(tmp_path)
    assert [restarted.get(index) for index in range(len(bodies))] == bodies
    assert not list(tmp_path.glob("tmp*"))


def test_handler_reuses_disk_after_memory_reset(tmp_path, monkeypatch):
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    headers = {"Accept-Encoding": "gzip"}
    _, _, encoded = _get(server, "/static/x.js", headers)
    server._STATIC_GZIP_CACHE.clear()
    monkeypatch.setattr(server, "_STATIC_ENCODING_DISK_CACHE",
                        StaticEncodingCache(tmp_path / "encoded"))

    def unexpected_encode(*args, **kwargs):
        raise AssertionError("unchanged asset was recompressed after restart")

    monkeypatch.setattr(server.gzip, "compress", unexpected_encode)
    status, response_headers, body = _get(server, "/static/x.js", headers)
    assert status == 200 and body == encoded
    assert gzip.decompress(body) == asset.read_bytes()
    assert 'cache;desc="hit"' in response_headers["server-timing"]
    # A persisted compressed version cannot override the client's choice.
    _, response_headers, body = _get(server, "/static/x.js")
    assert "content-encoding" not in response_headers
    assert body == asset.read_bytes()


def test_preserved_mtime_same_size_edit_invalidates_disk(tmp_path, monkeypatch):
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    headers = {"Accept-Encoding": "gzip"}
    _get(server, "/static/x.js", headers)
    original = asset.stat()
    asset.write_bytes(b"/" * original.st_size)
    os.utime(asset, ns=(original.st_atime_ns, original.st_mtime_ns))
    server._STATIC_GZIP_CACHE.clear()
    _, _, body = _get(server, "/static/x.js", headers)
    assert gzip.decompress(body) == asset.read_bytes()


def test_corrupt_disk_entry_is_rebuilt_by_handler(tmp_path, monkeypatch):
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    headers = {"Accept-Encoding": "gzip"}
    _get(server, "/static/x.js", headers)
    next((tmp_path / "encoded").glob("*.cache")).write_bytes(b"corrupt")
    server._STATIC_GZIP_CACHE.clear()
    _, _, body = _get(server, "/static/x.js", headers)
    assert gzip.decompress(body) == asset.read_bytes()
