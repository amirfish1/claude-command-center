"""App HTML uses one canonical builder through the private reader channel."""
import gzip
import json
import socket
import threading
from pathlib import Path

import pytest
from ccc_server import conv_reader
from tests.test_static_asset_caching import _get, _server


def wire_page(server, path, query, accept):
    client, child = socket.socketpair()
    thread = threading.Thread(target=conv_reader._handle, args=(child, server))
    thread.start()
    try:
        client.sendall(json.dumps({'op':'app_page','path':path,'query':query,'accept':accept}).encode()+b'\n')
        reader = client.makefile('rb')
        head = json.loads(reader.readline())
        return head, reader.read(head.get('len', 0))
    finally:
        client.close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize('path,query', [('/', 'ccc_app=demo'),
    ('/', 'ccc_popout=conversation&embed=app&conv=session-test'), ('/view/demo', '')])
def test_private_page_reply_matches_canonical_http_and_keeps_no_store(tmp_path, monkeypatch, path, query):
    server = _server()
    source = tmp_path/'index.html'
    source.write_text('<html lang="en"><head><meta charset="utf-8"></head><body class="voice-call-btn-off">'+ 'shared page '*300 +'</body></html>')
    app = tmp_path/'demo'
    app.mkdir()
    (app/'index.html').write_text('<head></head><body>'+'app content '*200+'</body>')
    (app/'app.json').write_text('{}')
    monkeypatch.setattr(server,'STATIC_DIR',tmp_path)
    monkeypatch.setattr(server,'USER_APPS_DIR',tmp_path)
    monkeypatch.setattr(server,'_INDEX_HTML_PATH',source)
    monkeypatch.setattr(server,'_resolve_apps',lambda **_: [{'id':'demo','nav':'/view/demo'}])
    head, body = wire_page(server,path,query,'gzip')
    assert head['ok'] and head['encoding']=='gzip'
    canonical = gzip.decompress(body)
    # The foreground fallback uses exactly the same template and encoder.
    monkeypatch.setattr(conv_reader,'forward_page',lambda *_: None)
    status, headers, fallback = _get(server,path+'?'+query,{'Accept-Encoding':'gzip'})
    assert status==200 and gzip.decompress(fallback)==canonical
    # A prepared reply crosses HTTP with the same security/cache contract.
    calls=[]
    monkeypatch.setattr(conv_reader,'forward_page',lambda *args: (calls.append(args),{'body':body,'encoding':'gzip','cache':'reader-html-hit'})[1])
    status, headers, forwarded = _get(server,path+'?'+query,{'Accept-Encoding':'gzip'})
    assert status==200 and forwarded==body
    assert headers['cache-control']=='no-store, must-revalidate'
    assert 'reader-html-hit' in headers['server-timing']
    assert calls==[(path,query,'gzip')]


@pytest.mark.parametrize('path,query',[('/', ''),('/', 'ccc_app=sessions'),
    ('/', 'ccc_popout=conversation'),('/api/sessions',''),('/view/../private',''),
    ('/view/demo/extra',''),('/view/%2e%2e',''),('/static/app.js','')])
def test_reader_declines_other_page_and_api_paths(path, query):
    head, body=wire_page(_server(),path,query,'gzip')
    assert not head['ok'] and not body


def test_missing_app_page_falls_back_to_existing_404(tmp_path, monkeypatch):
    server=_server()
    monkeypatch.setattr(server,'STATIC_DIR',tmp_path)
    monkeypatch.setattr(server,'USER_APPS_DIR',tmp_path)
    monkeypatch.setattr(conv_reader,'forward_page',lambda *_: None)
    assert _get(server,'/view/missing')[0]==404
