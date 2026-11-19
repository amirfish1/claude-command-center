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


@pytest.mark.parametrize('encoding', ['gzip', 'br'])
@pytest.mark.parametrize('path,query', [('/', 'ccc_app=demo'),
    ('/', 'ccc_popout=conversation&embed=app&conv=session-test'), ('/view/demo', '')])
def test_private_page_reply_matches_canonical_http_and_keeps_no_store(tmp_path, monkeypatch, path, query, encoding):
    from ccc_server import static_encoding
    from tests.test_static_encoding import decode
    if encoding == 'br' and static_encoding._encoder() is None:
        pytest.skip('optional system Brotli encoder unavailable')
    def unpack(body):
        return decode(body, 1 << 20) if encoding == 'br' else gzip.decompress(body)
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
    head, body = wire_page(server,path,query,encoding)
    assert head['ok'] and head['encoding']==encoding
    assert set(head['page_timing']) == {'page_build', 'page_build_cpu', 'page_encode', 'page_encode_cpu'}
    assert all(value >= 0 for value in head['page_timing'].values())
    canonical = unpack(body)
    # The foreground fallback uses exactly the same template and encoder.
    monkeypatch.setattr(conv_reader,'forward_page',lambda *_: None)
    status, headers, fallback = _get(server,path+'?'+query,{'Accept-Encoding':encoding})
    assert status==200 and unpack(fallback)==canonical
    # A prepared reply crosses HTTP with the same security/cache contract.
    calls=[]
    monkeypatch.setattr(conv_reader,'forward_page',lambda *args: (calls.append(args),{'body':body,'encoding':encoding,'cache':'reader-html-hit','page_timing':head['page_timing']})[1])
    status, headers, forwarded = _get(server,path+'?'+query,{'Accept-Encoding':encoding})
    assert status==200 and forwarded==body
    assert headers['content-encoding']==encoding and headers['vary']=='Accept-Encoding'
    assert headers['cache-control']=='no-store, must-revalidate'
    assert 'reader-html-hit' in headers['server-timing']
    for name in head['page_timing']:
        assert name + ';dur=' in headers['server-timing']
    assert calls==[(path,query,encoding)]


def test_page_timing_separates_builder_and_encoder_clocks(monkeypatch):
    server = _server()
    monkeypatch.setattr(server, '_load_application_html', lambda _: '<body>Current page</body>')
    monkeypatch.setattr(server, '_prepare_html_response', lambda *_: {'body': b'encoded', 'encoding': 'gzip'})
    wall = iter((10.0, 10.2, 10.5))
    cpu = iter((2.0, 2.01, 2.04))
    monkeypatch.setattr(server.time, 'perf_counter', lambda: next(wall))
    monkeypatch.setattr(server.time, 'thread_time', lambda: next(cpu))
    out = server._app_page_response('/view/demo', '', 'gzip')
    assert out['body'] == b'encoded' and out['encoding'] == 'gzip'
    assert out['page_timing'] == pytest.approx({
        'page_build': 200, 'page_build_cpu': 10, 'page_encode': 300, 'page_encode_cpu': 30,
    })


def test_page_timing_headers_accept_only_bounded_numeric_fixed_metrics(monkeypatch):
    server = _server()
    prepared = {'body': b'<body>Current page</body>', 'page_timing': {
        'page_build': 1.25, 'page_build_cpu': float('nan'),
        'page_encode': '5\r\nInjected: value', 'page_encode_cpu': float('inf'),
        'arbitrary-name': 123,
    }}
    monkeypatch.setattr(conv_reader, 'forward_page', lambda *_: prepared)
    status, headers, body = _get(server, '/?ccc_app=demo')
    assert status == 200 and body == prepared['body']
    assert 'page_build;dur=1.250' in headers['server-timing']
    for invalid in ('page_build_cpu', 'page_encode', 'arbitrary-name', 'Injected'):
        assert invalid not in headers['server-timing']


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


def _document_payload(html, element_id):
    import re
    match = re.search(r'<script type="application/json" id="'+element_id+r'">(.*?)</script>', html, re.S)
    return json.loads(match[1]) if match else None


def test_parent_carries_current_canonical_documents_and_agent_changes(tmp_path, monkeypatch):
    server = _server()
    from ccc_server import app_routines
    source = tmp_path / 'index.html'
    source.write_text('<html lang="en"><head><meta charset="utf-8">'
                      '<script src="/static/app-shell-boot.js"></script></head>'
                      '<body class="voice-call-btn-off"><p>Canonical conversation</p></body></html>')
    app = tmp_path / 'demo'
    app.mkdir()
    (app / 'app.json').write_text('{}')
    page = '<head></head><main>Canonical app</main><script>window.test="&<>"</script>'
    (app / 'index.html').write_text(page)
    state = app / app_routines.STATE_NAME
    state.write_text(json.dumps({'agent': {'session_id': 'first-agent'}}))
    monkeypatch.setattr(server, '_INDEX_HTML_PATH', source)
    monkeypatch.setattr(server, 'STATIC_DIR', tmp_path)
    monkeypatch.setattr(server, 'USER_APPS_DIR', tmp_path)
    monkeypatch.setattr(server, '_conversation_open_response', lambda *_a, **_kw: None)
    selected = {'id': 'demo', 'nav': '/view/demo'}
    for sid in ('first-agent', 'next-agent'):
        state.write_text(json.dumps({'agent': {'session_id': sid}}))
        html = server._load_app_shell_html(selected)
        outer = _document_payload(html, 'cccAppStartupDocument')
        assert outer['url'] == '/view/demo'
        assert '<main>Canonical app</main><script>window.test="&<>"</script>' in outer['html']
        inner = _document_payload(outer['html'], 'cccAppConversationDocument')
        assert inner['url'] == '/?ccc_popout=conversation&embed=app&conv=' + sid
        assert inner['html'] == server._load_index_html(app_embed=True)
        assert _document_payload(outer['html'], 'cccAppAgent')['session_id'] == sid
    (app / 'index.html').write_text('<head></head><main>Edited app</main>')
    assert 'Edited app' in _document_payload(server._load_app_shell_html(selected), 'cccAppStartupDocument')['html']
    # A direct view retains its normal child navigation and standalone usage.
    assert 'cccAppConversationDocument' not in server._load_application_html('demo')
    for nav in ('https://example.com/view/demo', '/custom', '/view/demo?alternate=1'):
        assert _document_payload(server._load_app_shell_html({**selected, 'nav': nav}), 'cccAppStartupDocument') is None


def test_document_json_does_not_turn_child_markup_into_parent_markup():
    from ccc_server.app_embed import application_document
    document = '<script>window.test="</script><img id=escaped>"</script>&'
    html = application_document('<head></head><body></body>', 'cccAppStartupDocument', '/view/demo', document)
    assert '<img' not in html and html.count('</script>') == 1
    assert _document_payload(html, 'cccAppStartupDocument') == {'url': '/view/demo', 'html': document}


def test_child_document_cache_reuses_exact_inputs_before_serialization(monkeypatch):
    from ccc_server import app_embed
    from ccc_server.bounded_cache import ByteBudgetCache
    cache = ByteBudgetCache(max_bytes=2048, max_entries=32)
    monkeypatch.setattr(app_embed, '_JSON_ELEMENTS', cache)
    dumps = json.dumps
    serialized = []

    def record(value):
        serialized.append(value)
        return dumps(value)

    monkeypatch.setattr(app_embed.json, 'dumps', record)
    html, element_id = '<head></head>', 'payload'
    cases = [('/view/demo', '<b>one</b>'),
             ('/view/other', '<b>one</b>'),
             ('/view/other', '<b>two</b>')]
    for url, body in cases:
        result = app_embed.application_document(html, element_id, url, body)
        assert _document_payload(result, element_id) == {'url': url, 'html': body}
        assert app_embed.application_document(html, element_id, url, body) == result
        assert cache.bytes <= cache.max_bytes
    assert serialized == [{'url': url, 'html': body} for url, body in cases]

    oversized = '<p>' + 'x' * cache.max_bytes + '</p>'
    for _ in range(2):
        result = app_embed.application_document(html, element_id, '/view/demo', oversized)
        assert _document_payload(result, element_id)['html'] == oversized
    assert len(serialized) == len(cases) + 2
    assert cache.bytes <= cache.max_bytes


def test_cached_json_escaping_tracks_mutated_values_and_bounds_both_strings(monkeypatch):
    from ccc_server import app_embed
    from ccc_server.bounded_cache import ByteBudgetCache
    cache = ByteBudgetCache(max_bytes=4096, max_entries=32)
    monkeypatch.setattr(app_embed, '_JSON_ELEMENTS', cache)
    data = {'text': '</script><img id=escaped>&'}
    for number in range(30):
        data['version'] = number
        first = app_embed.application_json('<head></head>', 'payload', data)
        assert first == app_embed.application_json('<head></head>', 'payload', data)
        assert _document_payload(first, 'payload') == data
        assert '<img' not in first and first.count('</script>') == 1
        assert cache.bytes <= cache.max_bytes
    retained = cache.bytes
    data['text'] = 'x' * cache.max_bytes
    html = app_embed.application_json('<head></head>', 'payload', data)
    assert _document_payload(html, 'payload') == data
    assert cache.bytes <= retained


def test_initial_tail_uses_current_canonical_read_and_escaped_payload(monkeypatch):
    server = _server()
    calls = []
    current = {'events': [{'type': 'assistant', 'text': '</script><img id=escaped>&'}],
               'last_line': 73, 'queued_inputs': [{'text': 'Held input'}]}
    monkeypatch.setattr(server, '_load_index_html', lambda **_: '<head></head><body></body>')

    def read(sid, query, accept, **options):
        calls.append((sid, query, accept, options))
        return {'body': json.dumps(current).encode(), 'encoding': None, 'cache': 'miss'}

    monkeypatch.setattr(server, '_conversation_open_response', read)
    for line in (73, 74):
        current['last_line'] = line
        html = server._load_app_conversation_document('session-test')
        initial = _document_payload(html, 'cccAppInitialTail')
        assert initial['url'] == '/api/conversations/session-test?tail=120'
        assert json.loads(initial['text']) == current
        assert '<img' not in html and html.count('</script>') == 1
    assert calls == [('session-test', 'tail=120', '', {'in_reader': True, 'cached_only': True})] * 2
    assert 'cccAppInitialTail' not in server._load_app_conversation_document('__new__')


@pytest.mark.parametrize('result', [None, {'body': b'{"error":"missing"}'},
    {'body': b'not JSON'}, {'body': b'{}'}, {'body': b'x' * (512 * 1024 + 1)}])
def test_declined_invalid_or_large_tail_retains_regular_read(monkeypatch, result):
    server = _server()
    template = '<head></head><body>Canonical controls</body>'
    monkeypatch.setattr(server, '_load_index_html', lambda **_: template)
    monkeypatch.setattr(server, '_conversation_open_response', lambda *_a, **_kw: result)
    assert server._load_app_conversation_document('session-test') == template


def test_page_tail_absent_candidate_never_resolves_engine_or_transcript(monkeypatch):
    from ccc_server.bounded_cache import ByteBudgetCache
    server = _server()
    cache = ByteBudgetCache(4096, 8)
    monkeypatch.setattr(server, '_CONV_BYTES_CACHE', cache)
    cache.put(('other-session', 0, 'mtime', (120, None), 'overlay'), {}, 1)
    cache.put(('session-test', 42, 'mtime', (120, None), 'overlay'), {}, 1)

    def forbidden(*_a, **_kw):
        pytest.fail('An absent cached tail performed session resolution')
    for name in ('_session_acp_harness', '_detect_session_engine',
                 '_refresh_queued_view_if_file_changed', '_conv_response_bytes_get',
                 'parse_conversation'):
        monkeypatch.setattr(server, name, forbidden)
    assert server._conversation_open_response('session-test', 'tail=120', '',
                                              in_reader=True, cached_only=True) is None


@pytest.mark.parametrize('valid', [False, True])
def test_page_tail_candidate_keeps_freshness_validation(monkeypatch, valid):
    from ccc_server.bounded_cache import ByteBudgetCache
    server = _server()
    cache = ByteBudgetCache(4096, 8)
    monkeypatch.setattr(server, '_CONV_BYTES_CACHE', cache)
    cache.put(('session-test', 0, 'old-mtime', (120, None), 'old-overlay'), {}, 1)
    calls = []
    monkeypatch.setattr(server, '_session_acp_harness', lambda _: None)
    monkeypatch.setattr(server, '_detect_session_engine', lambda _: 'claude')
    monkeypatch.setattr(server, '_refresh_queued_view_if_file_changed', lambda sid: calls.append(('queue', sid)))

    def validate(sid, after, window):
        calls.append(('validate', sid, after, window))
        return {'raw': b'current'} if valid else None

    monkeypatch.setattr(server, '_conv_response_bytes_get', validate)
    result = server._conversation_open_response('session-test', 'tail=120', '',
                                               in_reader=True, cached_only=True)
    assert result == ({'body': b'current', 'encoding': None, 'cache': 'hit'} if valid else None)
    assert calls == [('queue', 'session-test'), ('validate', 'session-test', 0, (120, None))]


def test_page_tail_cache_miss_never_parses_or_attaches(monkeypatch):
    server = _server()
    monkeypatch.setattr(server, '_session_acp_harness', lambda _: None)
    monkeypatch.setattr(server, '_detect_session_engine', lambda _: 'claude')
    monkeypatch.setattr(server, '_refresh_queued_view_if_file_changed', lambda _: None)
    monkeypatch.setattr(server, '_conv_response_bytes_get', lambda *_a, **_kw: None)
    def forbidden(*_a, **_kw):
        pytest.fail('Page preparation parsed or attached a cold transcript')
    monkeypatch.setattr(server, 'parse_conversation', forbidden)
    monkeypatch.setattr(server, '_acp_maybe_attach_on_view', forbidden)
    assert server._conversation_open_response('session-test', 'tail=120', '',
                                              in_reader=True, cached_only=True) is None
    monkeypatch.setattr(server, '_session_acp_harness', lambda _: 'kimi')
    assert server._conversation_open_response('session-test', 'tail=120', '',
                                              in_reader=True, cached_only=True) is None


def test_html_encoding_cache_negotiation_and_optional_fallback(monkeypatch):
    from ccc_server import static_encoding
    from ccc_server.bounded_cache import ByteBudgetCache
    from tests.test_static_encoding import decode
    server = _server()
    monkeypatch.setattr(server, '_HTML_GZIP_CACHE', ByteBudgetCache(max_bytes=4096, max_entries=8))
    html = '<body>' + 'same canonical page ' * 200 + '</body>'
    raw = html.replace('<body>', '<body data-repo="">', 1).encode()
    if static_encoding._encoder() is not None:
        for encoding in ('br', 'gzip'):
            first = server._prepare_html_response(html, encoding)
            second = server._prepare_html_response(html, encoding)
            assert first['cache'] == 'html-miss' and second['cache'] == 'html-hit'
            assert second['encoding'] == encoding
            assert (decode(second['body'], len(raw)) if encoding == 'br'
                    else gzip.decompress(second['body'])) == raw
        assert server._prepare_html_response(html, 'br;q=0, gzip')['encoding'] == 'gzip'
        assert server._prepare_html_response(html, 'br;q=.1, gzip;q=.9')['encoding'] == 'gzip'
    for accept in ('', 'br;q=0, gzip;q=0'):
        assert server._prepare_html_response(html, accept) == {'body': raw, 'encoding': None}
    server._HTML_GZIP_CACHE.clear()
    monkeypatch.setattr(static_encoding, 'brotli', lambda _: None)
    fallback = server._prepare_html_response(html, 'br, gzip')
    assert fallback['encoding'] == 'gzip' and gzip.decompress(fallback['body']) == raw
    assert server._prepare_html_response(html, 'br') == {'body': raw, 'encoding': None}
    assert server._prepare_html_response('<body>small</body>', 'br, gzip')['encoding'] is None
