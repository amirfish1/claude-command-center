"""The sidebar must know parentage before any conversation is selected."""
import importlib


def test_projection_uses_graph_when_cached_parent_is_empty(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    from ccc_server.session_graph import _SessionGraph
    graph = _SessionGraph(tmp_path / 'graph.json')
    monkeypatch.setattr(server, '_session_graph', graph)
    graph.add_edge('parent', 'child', source='ccc-spawn')
    cached = {'session_id': 'child', 'parent_session_id': ''}
    assert server._archive_list_project_row(cached)['parent_session_id'] == 'parent'
    assert cached['parent_session_id'] == ''
    assert server._archive_list_project_row({'session_id': 'child', 'parent_session_id': 'explicit'})['parent_session_id'] == 'explicit'


def test_parent_revision_changes_only_when_relationships_change(tmp_path):
    importlib.import_module('server')
    from ccc_server.session_graph import _SessionGraph
    graph = _SessionGraph(tmp_path / 'graph.json')
    revision = graph.parent_revision()
    graph.add_edge('parent', 'child')
    assert graph.parent_revision() > revision
    revision = graph.parent_revision()
    graph.add_edge('parent', 'child', model='haiku')
    assert graph.parent_revision() == revision
    graph.remove_edge('parent', 'child')
    assert graph.parent_revision() > revision


def test_cached_list_refreshes_when_parent_is_discovered(monkeypatch, tmp_path):
    import json
    import threading
    import urllib.request
    server = importlib.import_module('server')
    from ccc_server.session_graph import _SessionGraph
    graph = _SessionGraph(tmp_path / 'graph.json')
    monkeypatch.setattr(server, '_session_graph', graph)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_BODY_CACHE', {})
    monkeypatch.setattr(server, '_archive_list_source_rows_cached', lambda *a, **kw: ([{'session_id': 'child', 'parent_session_id': ''}], True, 7))
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=all'
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            etag = response.headers['ETag']
            assert json.load(response)['conversations'][0]['parent_session_id'] == ''
        graph.add_edge('parent', 'child')
        request = urllib.request.Request(url, headers={'If-None-Match': etag})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
            assert response.headers['ETag'] != etag
            assert json.load(response)['conversations'][0]['parent_session_id'] == 'parent'
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
