"""Bounded pagination and server-clock baselines."""
import json
from urllib.parse import urlsplit, parse_qs
from ccc_server.oncall.sources import SentrySource, HttpReply, PAGES_PER_POLL, ENRICH_MAX


def test_500_new_issues_drain_with_bounded_calls_even_with_wrong_host_clock():
    rows, calls = [], []
    def http(method, url, **kwargs):
        calls.append(url)
        if '/events/' in url:
            return HttpReply(200, b'{}')
        offset = int(parse_qs(urlsplit(url).query).get('cursor', ['0'])[0])
        page = rows[offset:offset+25]
        headers = {'Date': 'Wed, 11 Nov 2026 00:00:00 GMT'}
        if offset+25 < len(rows):
            headers['Link'] = f'<http://untrusted.invalid/ignored?cursor={offset+25}>; rel="next"; results="true"'
        return HttpReply(200, json.dumps(page).encode(), headers)
    src = SentrySource({'id': 's', 'org': 'o', 'project': 'p', 'token_vault': 'test', 'base': 'http://localhost'},
                       secret=lambda _: 'test-token', http=http, clock=lambda: 9999999999)
    rows.extend({'id': 'old'+str(i), 'count': 1, 'firstSeen': '2026-11-10T00:00:00Z'} for i in range(500))
    assert src.poll() == []
    rows[:0] = [{'id': str(i), 'count': 1, 'firstSeen': '2026-11-11T00:00:01Z'} for i in range(500)]
    seen = []
    for _ in range(10):
        calls.clear()
        seen += [f.fingerprint for f in src.poll()]
        assert len(calls) <= PAGES_PER_POLL + ENRICH_MAX
        assert all(urlsplit(u).hostname == 'localhost' for u in calls)
    assert len(seen) == len(set(seen)) == 500
    assert all(not f.startswith('sentry:old') for f in seen)
    assert src.poll() == []
