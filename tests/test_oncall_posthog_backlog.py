"""HogQL response contract only; this is not a PostHog server."""
import json
import re
from ccc_server.oncall.sources import PostHogSource


def test_baseline_reads_all_500_groups_before_marking_ready():
    groups = [{'group_key': str(i), 'event_count': 3} for i in range(500)]
    calls = []
    def http(method, url, *, data=None, **kwargs):
        sql = json.loads(data)['query']['query']
        calls.append(sql)
        match = re.search(r'OFFSET (\d+)', sql)
        offset = int(match[1]) if match else 0
        return 200, json.dumps({'results': groups[offset:offset+100]}).encode()
    source = PostHogSource({'id': 'proof', 'project_id': '1', 'token_vault': 'test'},
                           secret=lambda _: 'test-token', http=http, clock=lambda: 1794300000)
    assert source.poll() == []
    assert len(calls) == 6
    groups.reverse()
    assert source.poll() == []
    assert len(calls) == 10  # bounded steady scan now reads four pages


def test_failed_baseline_page_is_not_saved():
    def http(method, url, *, data=None, **kwargs):
        if 'OFFSET' in json.loads(data)['query']['query']:
            return 429, b'{}'
        return 200, json.dumps({'results': [{'group_key': str(i), 'event_count': 3}
                                           for i in range(100)]}).encode()
    source = PostHogSource({'id': 'proof', 'project_id': '1', 'token_vault': 'test'},
                           secret=lambda _: 'test-token', http=http)
    assert source.poll() == []
    assert '429' in source.last_error
    assert not source._get('baselined')
    assert source._filed() == []
