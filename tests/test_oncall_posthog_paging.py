"""Bounded steady pagination finds exception groups beyond the first page."""
import json
import re
from ccc_server.oncall.sources import PostHogSource, PAGES_PER_POLL


def test_500_group_burst_drains_across_polls():
    groups, calls = [], []
    def http(method, url, *, data, **kwargs):
        sql = json.loads(data)['query']['query']
        calls.append(sql)
        offset = re.search(r'OFFSET (\d+)', sql)
        start = int(offset[1]) if offset else 0
        return 200, json.dumps({'results': groups[start:start+100]}).encode()
    source = PostHogSource({'id': 'ph', 'project_id': '1', 'token_vault': 'test'},
                          http=http, secret=lambda _: 'test-token')
    assert source.poll() == []
    groups.extend({'group_key': str(i), 'event_count': 3} for i in range(500))
    seen = []
    for _ in range(4):
        calls.clear()
        seen.extend(f.fingerprint for f in source.poll())
        assert len(calls) <= PAGES_PER_POLL
    assert len(seen) == len(set(seen)) == 500
