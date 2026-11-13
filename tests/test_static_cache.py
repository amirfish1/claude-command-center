"""Every /static js/css URL in index.html is mtime-stamped so it can be cached
for a year; an edit changes the URL, so a stale script is impossible."""
import re
import sys
from pathlib import Path


def _server():
    sys.argv = ["server.py"]
    import server
    return server


def test_all_index_assets_are_stamped():
    server = _server()
    html = server._load_index_html()
    urls = re.findall(r'(?:src|href)="(/static/[^"]+\.(?:js|css)[^"]*)"', html)
    assert len(urls) > 20
    bare = [u for u in urls if "?v=" not in u]
    assert bare == []


def test_stamp_follows_mtime(tmp_path, monkeypatch):
    server = _server()
    monkeypatch.setattr(server, "STATIC_DIR", Path(tmp_path))
    f = tmp_path / "a.js"
    f.write_text("1")
    import os
    os.utime(f, (1000, 1000))
    one = server._stamp_static_urls('<script src="/static/a.js" defer></script>')
    os.utime(f, (2000, 2000))
    two = server._stamp_static_urls('<script src="/static/a.js" defer></script>')
    assert "a.js?v=1000" in one and "a.js?v=2000" in two and one != two


def test_non_assets_untouched():
    server = _server()
    out = server._stamp_static_urls('<link href="/static/icon.svg"><script src="https://x/y.js">')
    assert out == '<link href="/static/icon.svg"><script src="https://x/y.js">'
