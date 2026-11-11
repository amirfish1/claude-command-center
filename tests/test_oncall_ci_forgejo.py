"""Forgejo 17 response shapes observed on the disposable local server."""
import json
from ccc_server.oncall.sources import build


def test_forgejo_17_run_and_jobs_shapes():
    source = build({'id': 'ci', 'type': 'ci', 'provider': 'forgejo', 'repo': 'example/demo', 'base': 'http://localhost/api/v1'})
    rows = []
    def request(url, headers):
        if '/logs' in url:
            return 200, b'AssertionError: fixture', {}
        if '/jobs?' in url:
            data = [{'id': 1, 'name': 'unit', 'status': 'failure', 'attempt': 1}]
        elif '/runs?' in url:
            data = {'workflow_runs': rows}
        else:
            data = {'default_branch': 'main'}
        return 200, json.dumps(data).encode(), {}
    source.request = request
    source.poll()
    rows[:] = [{'id': 1, 'workflow_id': 'test.yml', 'status': 'failure', 'prettyref': 'main', 'commit_sha': 'red'}]
    finding = source.poll()[0]
    assert 'AssertionError' in finding.text and 'Job: unit' in finding.text
    source.acknowledge(finding.fingerprint)
    rows[:] = [{'id': 2, 'workflow_id': 'test.yml', 'status': 'success', 'prettyref': 'main', 'commit_sha': 'green'}]
    source.poll()
    assert finding.fingerprint in source._get('confirmed')
