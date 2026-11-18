"""Fixed kit bytes can use either loader without a critical HTTP read."""
import base64

import pytest

from ccc_server.app_embed import application_page
from ccc_server.app_kit_delivery import MAX_BYTES, script_url


@pytest.mark.parametrize("loader", [
    '<script src="/static/app-kit.js" data-app="demo" data-ready="event" defer></script>',
    "<script>const s=document.createElement('script');s.src='/static/app-kit.js';"
    "s.dataset.app='demo';s.dataset.ready='event';document.head.appendChild(s)</script>",
])
def test_canonical_bytes_and_loader_attributes_survive(tmp_path, loader):
    source = b'// canonical\nwindow.example = "</script> & literal";'
    (tmp_path / "app-kit.js").write_bytes(source)
    url = script_url(loader, tmp_path, "/static/app-kit.js?v=1")
    assert base64.b64decode(url.split(",", 1)[1]) == source
    page = application_page(loader, "demo", "", kit_url=url)
    assert loader.replace("/static/app-kit.js", url) in page
    assert 'rel="preload" as="script" href="data:' not in page


@pytest.mark.parametrize("policy", [
    '<META HTTP-EQUIV="Content-Security-Policy" content="script-src self">',
    "<meta content='script-src self' http-equiv=content-security-policy>",
    '<script src="/static/app-kit.js" integrity="sha256-example"></script>',
])
def test_restrictive_pages_keep_external_delivery(tmp_path, policy):
    (tmp_path / "app-kit.js").write_bytes(b"window.example = true;")
    assert script_url(policy, tmp_path, "/static/app-kit.js?v=1") == "/static/app-kit.js?v=1"


def test_source_edits_invalidate_embedded_bytes_and_failures_keep_http(tmp_path):
    fallback = "/static/app-kit.js?v=1"
    assert script_url("", tmp_path, fallback) == fallback
    kit = tmp_path / "app-kit.js"
    kit.write_bytes(b"window.example=1;")
    first = script_url("", tmp_path, fallback)
    kit.write_bytes(b"window.example=2;")
    assert script_url("", tmp_path, fallback) != first
    kit.write_bytes(b"x" * (MAX_BYTES + 1))
    assert script_url("", tmp_path, fallback) == fallback


def test_empty_http_equiv_attribute_does_not_break_app_loading(tmp_path):
    (tmp_path / "app-kit.js").write_bytes(b"window.example = true;")
    assert script_url('<meta http-equiv>', tmp_path, '/static/app-kit.js').startswith('data:')
