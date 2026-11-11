"""Old Sentry issues must stay ignored when they move onto the first page."""
import json
from ccc_server.oncall.sources import SentrySource


def test_old_issues_beyond_first_page_do_not_become_new():
    rows = [{'id': str(i), 'count': 1, 'firstSeen': '2020-01-01T00:00:00Z'} for i in range(500)]
    page = rows[:25]
    def http(method, url, **kwargs):
        return 200, json.dumps({} if '/events/' in url else page).encode()
    source = SentrySource({'id': 'proof', 'org': 'proof', 'project': 'sample', 'token_vault': 'test'},
                          secret=lambda _: 'test-token', http=http, clock=lambda: 1794300000)
    assert source.poll() == []
    page = rows[25:50]  # resolving the first page exposes pre-existing issues
    assert source.poll() == []
    page = [{'id': 'new', 'count': 1, 'firstSeen': '2026-11-11T00:00:00Z'}] + page
    assert [f.fingerprint for f in source.poll()] == ['sentry:new']
    assert source.poll() == []
