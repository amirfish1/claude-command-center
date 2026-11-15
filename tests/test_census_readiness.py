"""Census availability is based on a finished build, never process age."""
import concurrent.futures
import json
import threading
import time
import urllib.error
import urllib.request

import pytest
import server
from ccc_server.census_readiness import CensusCache


def settled(cache, key=86400):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with cache.lock:
            if key in cache.slots and not cache.slots[key]['running']:
                return
        time.sleep(.005)
    pytest.fail('census build did not finish')


def test_only_completed_default_census_proves_readiness():
    cache = CensusCache()
    assert cache.readiness()['state'] == 'warming'
    payload = {'ok': True, 'now': time.time(), 'sessions': [{'session_id': 'sample'}]}
    cache.get(None, lambda key: payload)
    settled(cache, None)
    assert cache.readiness()['state'] == 'warming', 'live-only does not warm the default 24h query'
    cache.get(86400, lambda key: payload)
    settled(cache)
    assert cache.readiness()['state'] == 'ready'
    response, code = cache.get(86400, lambda key: pytest.fail('duplicate warm build'))
    assert code == 200 and response['sessions'] == payload['sessions']


def test_failed_build_never_announces_ready_or_empty_success():
    cache = CensusCache()
    calls = []
    def broken(key):
        calls.append(key)
        raise RuntimeError('index unavailable')
    cache.get(86400, broken)
    settled(cache)
    for _ in range(20):
        body, code = cache.get(86400, broken)
        assert code == 503 and not body['ok']
        assert body['sessions_readiness']['state'] == 'warming'
    assert calls == [86400], 'retrying clients must not storm a failing builder'


def test_stale_census_has_a_bound_and_cannot_hide_busy_state():
    cache = CensusCache(fresh_s=0, max_stale_s=.01)
    cache.get(86400, lambda key: {'ok': True, 'sessions': [{'session_id': 'old'}]})
    settled(cache)
    release = threading.Event()
    try:
        with cache.lock:
            cache.slots[86400]['at'] -= 1
        body, code = cache.get(86400, lambda key: (release.wait(2), {'ok': True, 'sessions': []})[1])
        assert code == 503 and body['sessions_readiness']['state'] == 'busy'
    finally:
        release.set()
        settled(cache)


def test_warmup_refreshes_and_recovers_without_a_request():
    cache = CensusCache(fresh_s=.01, max_stale_s=.05)
    first = threading.Event()
    failed = threading.Event()
    recover = threading.Event()
    recovered = threading.Event()
    calls = []

    def build(key):
        calls.append(key)
        if len(calls) == 1:
            first.set()
            return {'ok': True, 'sessions': [{'session_id': 'first'}]}
        if not recover.is_set():
            failed.set()
            raise RuntimeError('temporary index failure')
        recovered.set()
        return {'ok': True, 'sessions': [{'session_id': 'recovered'}]}

    stop = cache.start_warmup(build)
    try:
        assert cache.start_warmup(build) is stop, 'one warmup per server'
        assert first.wait(1)
        settled(cache)
        with cache.lock:
            cache.slots[86400]['at'] -= 1
        assert failed.wait(1), 'refresh cannot depend on a client request'
        assert cache.readiness()['state'] == 'busy', 'expired data cannot mask a failure'
        recover.set()
        assert recovered.wait(1), 'warmup must recover without another CLI call'
        settled(cache)
        assert cache.readiness()['state'] == 'ready'
        body, status = cache.get(86400, build)
        assert status == 200 and body['sessions'] == [{'session_id': 'recovered'}]
    finally:
        stop.set()


def test_real_http_reports_warming_promptly_then_serves_the_census(monkeypatch):
    cache = CensusCache()
    release = threading.Event()
    started = threading.Event()
    def build(key):
        started.set()
        release.wait(3)
        return {'ok': True, 'sessions': [{'session_id': 'ready'}], 'now': time.time()}
    monkeypatch.setattr(server, '_SESSION_CENSUS_CACHE', cache)
    monkeypatch.setattr(server, 'build_session_census', build)
    httpd = server.ThreadedHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    try:
        t0 = time.monotonic()
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(base + '/api/sessions/census?since=86400', timeout=1)
        assert time.monotonic() - t0 < 1
        assert failure.value.code == 503
        assert json.loads(failure.value.read())['sessions_readiness']['state'] == 'warming'
        assert started.wait(1)
        assert server.build_ccc_health()['sessions_readiness']['state'] == 'warming'
        release.set()
        settled(cache)
        with urllib.request.urlopen(base + '/api/sessions/census?since=86400', timeout=1) as response:
            assert json.load(response)['sessions'] == [{'session_id': 'ready'}]
        assert server.build_ccc_health()['sessions_readiness']['state'] == 'ready'
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
        settled(cache)


def test_warmup_keeps_requested_live_only_census_fresh():
    cache = CensusCache(fresh_s=.01, max_stale_s=.1)
    refreshed = threading.Event()
    live_calls = []

    def build(key):
        if key is None:
            live_calls.append(key)
            if len(live_calls) > 1:
                refreshed.set()
            return {'ok': True, 'sessions': [{'session_id': 'live'}]}
        return {'ok': True, 'sessions': [{'session_id': 'archive'}]}

    cache.get(None, build)
    settled(cache, None)
    with cache.lock:
        cache.slots[None]['at'] -= 1
    stop = cache.start_warmup(build)
    try:
        assert refreshed.wait(3), 'live-only refresh must not require another request'
        settled(cache, None)
        settled(cache)
        body, status = cache.get(None, build)
        assert status == 200
        assert body['sessions'] == [{'session_id': 'live'}], 'query scopes stay separate'
        assert cache.readiness()['state'] == 'ready'
    finally:
        stop.set()
