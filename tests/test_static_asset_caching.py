"""Static assets (app.js is 3.9 MB) must be cacheable and revalidatable.

index.html stamps every asset URL with the file mtime, so `no-store` only
forced a full re-download plus a full re-parse on every boot (no-store also
disables the V8 code cache). Serve an ETag, answer If-None-Match with 304,
mark stamped URLs immutable, and gzip each file version once.
"""
import email
import io
import sys
import time
from pathlib import Path


def _server():
    sys.argv = ["server.py"]
    import server
    return server


def _get(server, path, headers=None):
    handler = server.CommandCenterHandler.__new__(server.CommandCenterHandler)
    handler.path = path
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = True
    handler.headers = email.message_from_string(
        "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items()) + "\r\n"
    )
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler._ccc_req_t0 = time.time()
    handler._ccc_req_cpu0 = time.thread_time()
    handler._do_GET()
    raw = handler.wfile.getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    hdrs = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        hdrs[k.strip().lower()] = v.strip()
    return status, hdrs, body


def _static_dir(server, tmp_path, monkeypatch):
    from ccc_server.static_encoding_cache import StaticEncodingCache
    asset = tmp_path / "x.js"
    asset.write_bytes(b"// " + b"x" * 4096)
    monkeypatch.setattr(server, "STATIC_DIR", Path(tmp_path))
    server._STATIC_GZIP_CACHE.clear()
    monkeypatch.setattr(server, "_STATIC_ENCODING_DISK_CACHE",
                        StaticEncodingCache(tmp_path / "encoded"))
    return asset


def test_static_asset_carries_etag_and_answers_304(tmp_path, monkeypatch):
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    st = asset.stat()

    status, hdrs, body = _get(server, "/static/x.js")
    assert status == 200
    assert hdrs["etag"] == f'"{int(st.st_mtime)}-{st.st_size}"'
    assert "no-store" not in hdrs["cache-control"]
    assert body == asset.read_bytes()
    assert 'app;dur=' in hdrs['server-timing']
    assert 'cpu;dur=' in hdrs['server-timing']
    assert 'cache;desc="miss"' in hdrs['server-timing']

    status, hdrs, body = _get(server, "/static/x.js", {"If-None-Match": hdrs["etag"]})
    assert status == 304
    assert body == b""
    assert hdrs["etag"] == f'"{int(st.st_mtime)}-{st.st_size}"'
    assert 'cache;desc="validated"' in hdrs['server-timing']

    status, _, _ = _get(server, "/static/x.js", {"If-None-Match": '"stale"'})
    assert status == 200


def test_stamped_asset_url_is_immutable_and_bare_url_revalidates(tmp_path, monkeypatch):
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    stamp = int(asset.stat().st_mtime)

    _, hdrs, _ = _get(server, f"/static/x.js?v={stamp}")
    assert "immutable" in hdrs["cache-control"]
    assert "max-age=31536000" in hdrs["cache-control"]

    _, hdrs, _ = _get(server, "/static/x.js?v=old")
    assert "no-cache" in hdrs["cache-control"]
    assert "immutable" not in hdrs["cache-control"]

    _, hdrs, _ = _get(server, "/static/x.js")
    assert "no-cache" in hdrs["cache-control"]


def test_gzipped_asset_is_compressed_once_per_file_version(tmp_path, monkeypatch):
    import gzip as gzip_mod
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    calls = []
    real = gzip_mod.compress
    monkeypatch.setattr(server.gzip, "compress", lambda b, **kw: (calls.append(len(b)), real(b, **kw))[1])

    for attempt in range(3):
        status, hdrs, body = _get(server, "/static/x.js", {"Accept-Encoding": "gzip"})
        assert status == 200
        assert hdrs.get("content-encoding") == "gzip"
        assert gzip_mod.decompress(body) == asset.read_bytes()
        expected_cache = 'miss' if attempt == 0 else 'hit'
        assert f'cache;desc="{expected_cache}"' in hdrs['server-timing']
    assert len(calls) == 1

    asset.write_bytes(b"// changed " + b"y" * 4096)
    _, _, body = _get(server, "/static/x.js", {"Accept-Encoding": "gzip"})
    assert gzip_mod.decompress(body) == asset.read_bytes()
    assert len(calls) == 2


def test_full_boot_asset_set_does_not_flush_the_shared_renderer(tmp_path, monkeypatch):
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    headers = {"Accept-Encoding": "gzip"}
    _, _, expected = _get(server, "/static/x.js", headers)
    for i in range(64):
        (tmp_path / f"extension-{i}.js").write_bytes(b"// " + b"y" * 4096)
        assert _get(server, f"/static/extension-{i}.js", headers)[0] == 200

    def no_read(candidate):
        assert candidate != asset, "cached renderer was read and compressed again"
        return original_read(candidate)

    original_read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", no_read)
    status, hdrs, body = _get(server, "/static/x.js", headers)
    assert status == 200 and hdrs["content-encoding"] == "gzip"
    assert body == expected


def test_static_cache_evicts_within_its_byte_budget(tmp_path, monkeypatch):
    from ccc_server.bounded_cache import ByteBudgetCache
    server = _server()
    asset = _static_dir(server, tmp_path, monkeypatch)
    cache = ByteBudgetCache(max_bytes=100, max_entries=256)
    monkeypatch.setattr(server, "_STATIC_GZIP_CACHE", cache)
    for i in range(8):
        (tmp_path / f"extension-{i}.js").write_bytes(asset.read_bytes())
        assert _get(server, f"/static/extension-{i}.js", {"Accept-Encoding": "gzip"})[0] == 200
    assert 0 < cache.bytes <= 100
    assert len(cache) < 8
