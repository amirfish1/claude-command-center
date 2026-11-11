"""Retry-After is a persisted request boundary, including forced engine polls."""
import pytest
from ccc_server.oncall.sources import SentrySource, HttpReply, _MemoryKV


@pytest.mark.parametrize('status', [429, 503])
@pytest.mark.parametrize('header', ['3600', 'Wed, 11 Nov 2026 01:00:00 GMT'])
def test_retry_after_survives_source_recreation_and_clock_skew(status, header):
    calls = []
    now = [1000.0]  # host and server disagree by years
    kv = _MemoryKV()
    def http(*args, **kwargs):
        calls.append(args)
        return HttpReply(status, b'{}', {'Retry-After': header, 'Date': 'Wed, 11 Nov 2026 00:00:00 GMT'})
    cfg = {'id': 's', 'type': 'sentry', 'org': 'example', 'project': 'app', 'token_vault': 'test'}
    source = SentrySource(cfg, kv=kv, clock=lambda: now[0], secret=lambda _: 'test-token', http=http)
    assert source.poll() == [] and str(status) in source.last_error
    source = SentrySource(cfg, kv=kv, clock=lambda: now[0], secret=lambda _: 'test-token', http=http)
    now[0] += 3599
    assert not source.due()
    assert source.poll() == []
    assert len(calls) == 1
    now[0] += 1
    assert source.due()
    source.poll()
    assert len(calls) == 2


def test_invalid_retry_after_does_not_hide_http_error():
    source = SentrySource({'id': 's', 'org': 'example', 'project': 'app', 'token_vault': 'test'},
                         secret=lambda _: 'test-token', http=lambda *a, **k: HttpReply(429, b'{}', {'Retry-After': 'bad'}))
    assert source.poll() == []
    assert '429' in source.last_error
    assert not source._get('retry_at')
