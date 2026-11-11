"""Actions intake contracts, using an in-memory transport (no credentials)."""
import json
from ccc_server.oncall.ci_source import CISource, scrub
from ccc_server.oncall.sources import _MemoryKV, build, required_fields


def source():
    src = CISource({'id': 'ci', 'repo': 'example/demo', 'base': 'http://localhost/api/v1'}, kv=_MemoryKV())
    rows = []
    calls = []
    def request(url, headers):
        calls.append((url, headers))
        if '/jobs?' in url:
            obj = {'jobs': [{'id': 1, 'name': 'unit', 'conclusion': 'failure',
                            'steps': [{'name': 'tests', 'conclusion': 'failure'}]}]}
        elif '/logs' in url:
            return 200, b'password=test-secret\nAssertionError: -1 != 5', {}
        elif '/runs?' in url:
            obj = {'workflow_runs': rows}
        else:
            obj = {'default_branch': 'main'}
        return 200, json.dumps(obj).encode(), {'ETag': 'test-etag'}
    src.request = request
    # This helper represents the engine accepting every returned finding.
    poll = src.poll
    def accepted_poll():
        for item in src._get('outbox', []):
            src.acknowledge(item['fingerprint'])
        return poll()
    src.poll = accepted_poll
    return src, rows, calls


def run(i, result='failure', sha=None, branch='main'):
    return dict(id=i, workflow_id=1, head_branch=branch, head_sha=sha or str(i),
                status='completed', conclusion=result, name='tests')


def test_registration():
    assert build({'type': 'ci', 'id': 'ci', 'repo': 'example/demo'}).type == 'ci'
    assert 'repo' in [f['name'] for f in required_fields('ci')]


def test_baseline_red_is_left_alone_until_green():
    src, rows, calls = source()
    rows[:] = [run(1)]
    src._credential = lambda: 'test-only'
    assert src.check()[0]
    rows[:] = [run(2)]
    assert src.poll() == []
    rows[:] = [run(3, 'success')]
    assert src.poll() == []
    rows[:] = [run(4)]
    findings = src.poll()
    assert len(findings) == 1
    assert '3..4' in findings[0].text
    assert 'Job: unit' in findings[0].text
    assert 'tests' in findings[0].text
    assert 'test-secret' not in findings[0].text
    assert 'Never force-push' in findings[0].text
    assert len(calls) == 7
    assert src.poll() == []


def test_flaky_same_commit_no_second_ticket_and_restart():
    src, rows, calls = source()
    assert src.poll() == []
    rows[:] = [run(1)]
    finding = src.poll()[0]
    rows[:] = [run(2, 'success', sha='1')]
    assert src.poll() == []
    assert finding.fingerprint in src._get('recovered')
    rows[:] = [run(3, sha='1')]
    assert src.poll() == []
    other = CISource(src.cfg, kv=src._kv)
    other.request = src.request
    assert other.poll() == []


def test_branch_and_conditional_bound():
    src, rows, calls = source()
    src.poll()
    rows[:] = [run(1, branch='feature')]
    assert src.poll() == []
    assert calls[-1][1]['If-None-Match'] == 'test-etag'
    src.request = lambda *args: (304, b'', {})
    assert src.poll() == []


def test_scrub():
    assert 'test-secret' not in scrub('Authorization: Bearer test-secret')
    assert 'test-secret' not in scrub('https://user:test-secret@example.test/log')
    assert 'test-secret' not in scrub('-----BEGIN PRIVATE KEY-----\ntest-secret')


def test_enrichment_budget():
    src, rows, calls = source()
    src.poll()
    rows[:] = [dict(run(i), workflow_id=i) for i in range(1, 11)]
    before = len(calls)
    assert len(src.poll()) == 3
    assert len(calls) - before == 7
    assert src._get('etags') == {}
    assert len(src.poll()) == 3


def test_recovery_close_retries_and_does_not_claim_a_fix():
    src, rows, calls = source()
    src.poll()
    rows[:] = [run(1)]
    fp = src.poll()[0].fingerprint
    rows[:] = [run(2, 'success')]
    src.poll()
    class Store:
        def reports(self, **kwargs):
            return [{'source': 'source:ci', 'fingerprint': fp, 'ticket_ref': 'DEMO-1'}]
    class WT:
        def find(self, ref):
            return {'status': 'open'}
        def _ok(self, *args):
            assert '--no-code' in args and '--force' not in args
            assert 'does not prove a code fix' in args[-1]
    src.reconcile(Store(), WT())
    assert not src._get('recovered')
    assert fp in src._get('confirmed')


def test_old_run_appearing_after_empty_baseline_is_ignored():
    src, rows, calls = source()
    src.poll()
    rows[:] = [dict(run(1), created_at='2000-01-01T00:00:00Z')]
    assert src.poll() == []


def test_custom_server_never_reads_gh_login():
    src, rows, calls = source()
    src._run = lambda *a, **k: (_ for _ in ()).throw(AssertionError('gh must not run'))
    assert src.poll() == []


def test_cli_detects_origin_without_questions(tmp_path, monkeypatch):
    import subprocess
    from tests.test_oncall_cli import Box
    from ccc_server.oncall.ci_source import CISource
    subprocess.run(['git', 'init', str(tmp_path / 'repo')], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path / 'repo')
    subprocess.run(['git', 'remote', 'add', 'origin', 'git@github.com:example/demo.git'], check=True)
    box = Box(tmp_path / 'state', monkeypatch)
    box.flag = True
    monkeypatch.setattr(CISource, '_check', lambda self: 'Connected')
    assert box.run('source', 'add', 'ci') == 0
    assert box.asked == []
    assert box.eng.config()['sources'][0]['repo'] == 'example/demo'


def test_older_green_cannot_resolve_newer_red():
    src, rows, calls = source()
    src.poll()
    rows[:] = [run(4)]
    src.poll()
    rows[:] = [run(3, 'success')]
    src.poll()
    assert not src._get('confirmed')


def test_gitea_null_steps():
    src, rows, calls = source()
    original = src.request
    def request(url, headers):
        if '/jobs?' in url:
            return 200, json.dumps({'jobs': [{'id': 1, 'name': 'unit', 'conclusion': 'failure', 'steps': None}]}).encode(), {}
        return original(url, headers)
    src.request = request
    src.poll()
    rows[:] = [run(1)]
    finding = src.poll()[0]
    assert 'Failing steps: unavailable' in finding.text
    assert 'AssertionError' in finding.text


def test_closed_notice_waits_for_observed_green(monkeypatch):
    from ccc_server.oncall import customers
    notices = []
    monkeypatch.setattr(customers, '_closed_notice', lambda *args: notices.append(args))
    class Store:
        confirmed = []
        def reports(self, **kwargs):
            return [{'id': 1, 'source': 'source:ci', 'fingerprint': 'failure', 'ticket_ref': 'DEMO-1'}]
        def kv_get(self, key, default=None):
            return {} if key.endswith(":journal") else self.confirmed
        def report_update(self, *args, **kwargs):
            pass
    class WT:
        def ls(self, *args, **kwargs):
            return []
        def find(self, ref):
            return {'status': 'closed'}
    store = Store()
    cfg = {'queue': 'DEMO', 'sources': [{'type': 'ci', 'id': 'ci'}]}
    assert customers.collect_closed(cfg, store, {}, WT()) == 0
    assert notices == []
    store.confirmed = ['failure']
    assert customers.collect_closed(cfg, store, {}, WT()) == 1
    assert len(notices) == 1
