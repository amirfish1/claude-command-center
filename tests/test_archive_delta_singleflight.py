"""Overlapping delta readers share bytes, never a retained response cache."""
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import threading

import pytest

import server
from tests.test_archive_source_singleflight import ObservedEvent


@pytest.fixture
def delta_flights(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DELTA_FLIGHTS', {})
    return server._ARCHIVE_LIST_DELTA_FLIGHTS


def state(token='process.2.context.overlay'):
    return {'token': token}


def read(**kwargs):
    return server._archive_list_delta_body(
        kwargs.pop('state', state()), kwargs.pop('since', 'process.1.context.old'),
        [], window=kwargs.pop('window', '7d'), cached=kwargs.pop('cached', True), **kwargs)


@pytest.mark.parametrize('outcome', ['success', 'unavailable', 'exception', 'owner_disconnected'])
def test_overlap_cleanup_retry_and_no_retained_delta(monkeypatch, delta_flights, outcome):
    entered, release, disconnected = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def check_owner_socket():
        if disconnected.is_set():
            raise server._ArchiveListClientDisconnected()

    def payload(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            if outcome == 'exception':
                raise ValueError('projection unavailable')
            if outcome == 'unavailable':
                return None
            if outcome == 'owner_disconnected':
                kwargs['check_client']()
        return {'ok': True, 'delta': True, 'ids': ['example'],
                'conversations': [{'session_id': 'example', 'display_name': 'x' * 2000}]}

    monkeypatch.setattr(server, '_archive_list_delta_payload', payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(read, check_client=check_owner_socket)
        assert entered.wait(5)
        observed = ObservedEvent()
        next(iter(delta_flights.values()))['event'] = observed
        diagnostics = {}
        follower = pool.submit(read, diagnostics=diagnostics)
        assert observed.waiting.wait(5)
        if outcome == 'owner_disconnected':
            disconnected.set()
        release.set()
        if outcome == 'exception':
            with pytest.raises(ValueError, match='projection unavailable'):
                owner.result(timeout=5)
            assert follower.result(timeout=5) is not None
            assert len(calls) == 2
            assert diagnostics['delta_coalesced'] is False
        elif outcome == 'owner_disconnected':
            with pytest.raises(server._ArchiveListClientDisconnected):
                owner.result(timeout=5)
            assert follower.result(timeout=5) is not None
            assert len(calls) == 1
            assert diagnostics['delta_coalesced'] is True
        else:
            assert follower.result(timeout=5) is owner.result(timeout=5)
            assert len(calls) == 1
            assert diagnostics['delta_coalesced'] is True
        if outcome == 'success':
            raw, compressed, etag = owner.result()
            assert gzip.decompress(compressed) == raw
            assert json.loads(raw)['delta_token'] == state()['token']
            assert etag == '"' + hashlib.sha1(raw).hexdigest() + '"'
    assert delta_flights == {}
    before = len(calls)
    assert read() is not None
    assert len(calls) == before + 1, 'later polls must re-evaluate membership and ticket work'
    assert delta_flights == {}


@pytest.mark.parametrize('different', [
    {'state': state('process.3.context.overlay')},
    {'state': state('process.2.changed.overlay')},
    {'state': state('process.2.context.changed')},
    {'since': 'process.0.context.old'},
    {'window': '1d'}, {'cached': False}, {'share': False},
])
def test_fresh_reads_and_distinct_snapshots_do_not_join(monkeypatch, delta_flights, different):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def payload(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return {'conversations': [], 'ids': []}

    monkeypatch.setattr(server, '_archive_list_delta_payload', payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(read)
        assert entered.wait(5)
        try:
            distinct = pool.submit(read, **different)
            assert distinct.result(timeout=5) is not None
            assert not owner.done()
            assert len(calls) == 2
        finally:
            release.set()
        owner.result(timeout=5)
    assert delta_flights == {}


def test_http_delta_shares_gzip_and_preserves_etag_and_fresh_reads(monkeypatch, delta_flights):
    import urllib.error
    import urllib.request

    entered, release = threading.Event(), threading.Event()
    nonce = server._ARCHIVE_DELTA_NONCE
    current = dict(token=f'{nonce}.2.context.0', nonce=nonce, ver=2,
                   ctx='context', track_since=1)
    since = f'{nonce}.1.context.0'
    calls, sharing = [], []
    monkeypatch.setattr(server, '_archive_list_disk_candidate', lambda *a, **kw: None)
    monkeypatch.setattr(server, '_archive_list_source_rows_cached',
                        lambda *a, **kw: ([{'session_id': 'example'}], True, 2))
    monkeypatch.setattr(server, '_archive_list_delta_state', lambda *a, **kw: current)

    def payload(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return {'ok': True, 'delta': True, 'ids': ['example'],
                'conversations': [{'session_id': 'example', 'display_name': 'x' * 2000}]}

    monkeypatch.setattr(server, '_archive_list_delta_payload', payload)
    original = server._archive_list_delta_body

    def recorded(*args, **kwargs):
        sharing.append(kwargs['share'])
        return original(*args, **kwargs)

    monkeypatch.setattr(server, '_archive_list_delta_body', recorded)
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=7d&since={since}'

    def request(*, fresh=False, etag=None):
        headers = {'Accept-Encoding': 'gzip'}
        if etag:
            headers['If-None-Match'] = etag
        req = urllib.request.Request(url + '&stale_ok=' + ('0' if fresh else '1'), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            owner = pool.submit(request)
            assert entered.wait(5)
            observed = ObservedEvent()
            next(iter(delta_flights.values()))['event'] = observed
            follower = pool.submit(request)
            assert observed.waiting.wait(5)
            release.set()
            first, second = owner.result(5), follower.result(5)
        assert len(calls) == 1
        assert first[0] == second[0] == 200
        assert first[2] == second[2]
        assert first[1]['Content-Encoding'] == 'gzip'
        assert first[1]['ETag'] == second[1]['ETag']
        body = json.loads(gzip.decompress(first[2]))
        assert body['delta_token'] == current['token']
        assert body['ids'] == ['example']
        assert 'list;dur=' in first[1]['Server-Timing']
        assert request(etag=first[1]['ETag'])[0] == 304
        assert len(calls) == 2, 'even conditional later polls must rebuild current delta state'
        assert request(fresh=True)[0] == 200
        assert sharing == [True, True, True, False]
        assert len(calls) == 3
        assert delta_flights == {}
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
