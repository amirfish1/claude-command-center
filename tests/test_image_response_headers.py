"""Image-serving routes send nosniff; /api/local-image also sandboxes via CSP."""

import importlib
import threading
import urllib.parse
import urllib.request

import pytest


@pytest.fixture
def live(tmp_path):
    server = importlib.import_module("server")
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_local_image_has_csp_and_nosniff(live, tmp_path):
    _, base = live
    svg = tmp_path / "x.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>')
    url = base + "/api/local-image?path=" + urllib.parse.quote(str(svg))
    with urllib.request.urlopen(url, timeout=5) as r:
        assert r.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
        assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_image_cache_has_nosniff(live, monkeypatch, tmp_path):
    server, base = live
    sid = "11111111-2222-3333-4444-555555555555"
    d = tmp_path / ".claude" / "image-cache" / sid
    d.mkdir(parents=True)
    (d / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: tmp_path))
    with urllib.request.urlopen(f"{base}/image-cache/{sid}/1.png", timeout=5) as r:
        assert r.headers["X-Content-Type-Options"] == "nosniff"
