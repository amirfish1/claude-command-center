"""The native app response stays canonical and invalidates on every source edit."""
import gzip
import os
import pytest
from pathlib import Path

from ccc_server.conversation_assets import SCRIPT_NAME, SCRIPTS, BUNDLES, ConversationScript
from tests.test_static_asset_caching import _get, _server


def test_bundle_manifest_follows_dashboard_controller_order():
    html = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()
    import re
    names = re.findall(r'<script src="/static/(conversation-[^"]+\.js)">', html)
    assert tuple(name for name in names if name not in (
        "conversation-details.js", "conversation-issues.js")) == SCRIPTS[:-1]
    assert SCRIPTS[-1] == "conversation-entry.js"


@pytest.mark.parametrize("name", BUNDLES)
def test_canonical_bundle_cache_and_page_stamp_follow_same_size_edits(tmp_path, monkeypatch, name):
    server = _server()
    for index, source_name in enumerate(BUNDLES[name]):
        (tmp_path / source_name).write_text(f"// module {index}\n" + "void 1;\n" * 100)
    monkeypatch.setattr(server, "STATIC_DIR", tmp_path)
    source = tmp_path / "index.html"
    source.write_text(f'<script src="/static/{name}"></script>')
    monkeypatch.setattr(server, "_INDEX_HTML_PATH", source)
    script = ConversationScript(tmp_path, name)
    before = server._load_index_html()
    url = server._static_asset_url(name)
    status, headers, compressed = _get(server, url, {"Accept-Encoding":"gzip"})
    assert status == 200 and gzip.decompress(compressed) == script.read_bytes()
    assert "immutable" in headers["cache-control"]
    status, _, body = _get(server, url, {"If-None-Match":headers["etag"]})
    assert status == 304 and body == b""
    # Preserve mtime and length: a real edit must still replace the stamp,
    # compressed response and cached HTML through its ctime dependency.
    edited = tmp_path / BUNDLES[name][0]
    info = edited.stat()
    edited.write_text(edited.read_text().replace("void 1;", "void 2;"))
    os.utime(edited, ns=(info.st_atime_ns, info.st_mtime_ns))
    after = server._load_index_html()
    assert after != before
    changed_url = server._static_asset_url(name)
    assert changed_url != url
    status, changed_headers, changed = _get(server, changed_url, {"Accept-Encoding":"gzip"})
    assert status == 200 and changed_headers["etag"] != headers["etag"]
    assert b"void 2;" in gzip.decompress(changed)
    assert _get(server, "/static/../conversation-app.js")[0] == 404


def test_extensions_retain_canonical_order_and_execution_phase():
    import re
    from ccc_server.app_embed import conversation_page
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "index.html").read_text()
    output = conversation_page(html)
    for name, sources in BUNDLES.items():
        if name == SCRIPT_NAME:
            continue
        positions = [html.index('/static/' + source + '"') for source in sources]
        assert positions == sorted(positions)
        for source in sources:
            assert '/static/' + source + '"' not in output
            if name.endswith('.js'):
                attributes = re.search(r'<script src="/static/' + re.escape(source) + r'"([^>]*)>', html)[1]
                assert ('defer' in attributes) == (name == 'conversation-deferred.js')
        separator = b"\n" if name.endswith('.css') else b"\n;\n"
        assert ConversationScript(root, name).read_bytes() == separator.join((root/source).read_bytes() for source in sources)
    assert output.index('/static/conversation-extensions.js') < output.rindex('/static/conversation-app.js')
    assert '<script src="/static/conversation-deferred.js" defer>' in output
