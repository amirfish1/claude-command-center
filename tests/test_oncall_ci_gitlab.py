"""Synthetic recorded-shape fixtures from GitLab's documented API examples.

Not run against a real GitLab server. No captured account or project data.
"""
import json
from ccc_server.oncall.sources import build


def test_documented_pipeline_job_trace_shapes():
    source = build({'id': 'ci', 'type': 'ci', 'provider': 'gitlab', 'repo': 'example/group/demo'})
    rows, calls = [], []
    def request(url, headers):
        calls.append(url)
        assert '/projects/example%2Fgroup%2Fdemo' in url
        if '/trace' in url:
            return 200, b'AssertionError: test\npassword=test-only', {}
        if '/jobs' in url:
            data = [{'id': 8, 'name': 'unit', 'stage': 'test', 'status': 'failed'}]
        elif '/pipelines?' in url:
            data = rows
        else:
            data = {'default_branch': 'main'}
        return 200, json.dumps(data).encode(), {}
    source.request = request
    source.poll()
    rows[:] = [{'id': 10, 'status': 'failed', 'ref': 'main', 'sha': 'test-sha'}]
    finding = source.poll()[0]
    assert 'Job: unit' in finding.text and 'AssertionError' in finding.text
    assert 'test-only' not in finding.text
    source.acknowledge(finding.fingerprint)
    rows[:] = [{'id': 11, 'status': 'success', 'ref': 'main', 'sha': 'fixed-sha'}]
    source.poll()
    assert finding.fingerprint in source._get('confirmed')
    assert any('/pipelines/10/jobs' in url for url in calls)
    assert any('/jobs/8/trace' in url for url in calls)
