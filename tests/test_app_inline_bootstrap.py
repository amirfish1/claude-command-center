"""The owning document includes exact canonical bridges, with live source stamps."""
import os
import pytest
import server
from ccc_server.app_bootstrap import inline, mark
from ccc_server.index_page_cache import IndexPageCache


@pytest.mark.parametrize('name', ['app-shell-boot.js','app-embed-boot.js'])
def test_inline_keeps_canonical_source_and_static_dependency_stamp(tmp_path, monkeypatch, name):
    source = tmp_path / name
    source.write_text('window.bridge = 1;')
    index = tmp_path / 'index.html'
    index.write_text(f'<head><script src="/static/{name}"></script></head>')
    monkeypatch.setattr(server, 'STATIC_DIR', tmp_path)
    cache = IndexPageCache()
    load = lambda:cache.load(index,tmp_path,name,lambda html:mark(html,name),server._stamp_static_urls)
    before = load()
    assert '>window.bridge = 1;</script>' in before
    assert 'data-ccc-bootstrap-src="/static/'+name+'?v=' in before
    assert '<script src=' not in before
    stat = source.stat()
    source.write_text('window.bridge = 2;')
    os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    after = load()
    assert after != before
    assert '>window.bridge = 2;</script>' in after
    assert load() == after


@pytest.mark.parametrize('source', [None, 'const text="</script>";', '<!-- a legacy script marker'])
def test_ineligible_source_keeps_original_parser_blocking_script(tmp_path, source):
    if source is not None:
        (tmp_path/'app-embed-boot.js').write_text(source)
    html = mark('<script src="/static/app-embed-boot.js"></script>','app-embed-boot.js')
    assert inline(html,tmp_path) == html
    assert '<script data-ccc-inline-bootstrap src=' in html
