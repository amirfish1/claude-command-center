"""Burst, redirect and durable-delivery checks for Actions intake."""
import json
from urllib.parse import parse_qs, urlsplit

from ccc_server.oncall.ci_source import CISource
from ccc_server.oncall.sources import _MemoryKV
from tests.test_oncall_ci import run


def fixture():
    kv = _MemoryKV()
    source = CISource({'id': 'ci', 'repo': 'example/demo', 'base': 'http://localhost/api/v1'}, kv=kv)
    rows, calls = [], []
    def request(url, headers):
        calls.append(url)
        if '/runs?' in url:
            page = int(parse_qs(urlsplit(url).query).get('page', ['1'])[0])
            result = {'workflow_runs': rows[(page-1)*100:page*100]}
        elif '/jobs?' in url:
            result = {'jobs': []}
        else:
            result = {'default_branch': 'main'}
        return 200, json.dumps(result).encode(), {}
    source.request = request
    source.poll()
    return source, rows, calls


def test_burst_more_than_three_pages_survives_restart_and_new_arrivals():
    source, rows, calls = fixture()
    rows[:] = [dict(run(i), workflow_id=i) for i in range(350, 0, -1)]
    before = len(calls)
    assert source.poll() == []
    assert len(calls) - before == 2
    source2 = CISource(source.cfg, kv=source._kv)
    source2.request = source.request
    # New arrivals shift offsets. Overlap must be deduped without a gap.
    rows[:0] = [dict(run(i), workflow_id=i) for i in range(370, 350, -1)]
    found = set()
    for _ in range(130):
        before = len(calls)
        for finding in source2.poll():
            assert finding.fingerprint not in found
            found.add(finding.fingerprint)
            source2.acknowledge(finding.fingerprint)
        assert len(calls) - before <= 8  # two run pages plus three jobs/log pairs
    assert len(found) == 370


def test_crash_before_ack_replays_same_finding():
    source, rows, calls = fixture()
    rows[:] = [run(1)]
    finding = source.poll()[0]
    resumed = CISource(source.cfg, kv=source._kv)
    resumed.request = source.request
    assert resumed.poll()[0].fingerprint == finding.fingerprint
    resumed.acknowledge(finding.fingerprint)
    assert resumed.poll() == []
    rows[:] = [run(2, 'success')]
    resumed.poll()
    assert finding.fingerprint in resumed._get('confirmed')


def test_crash_before_atomic_commit_does_not_advance_cursor():
    source, rows, calls = fixture()
    rows[:] = [run(1)]
    original = source._kv.kv_set
    def fail(key, value):
        if key.endswith(':journal') and value.get('outbox'):
            raise OSError('simulated crash')
        original(key, value)
    source._kv.kv_set = fail
    assert source.poll() == []
    source._kv.kv_set = original
    resumed = CISource(source.cfg, kv=source._kv)
    resumed.request = source.request
    assert len(resumed.poll()) == 1


def test_log_redirect_does_not_forward_token():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from ccc_server.oncall.ci_source import transport
    received = []
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get('Authorization'))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'AssertionError: fixture')
        def log_message(self, *args): pass
    target = ThreadingHTTPServer(('127.0.0.1', 0), Target)
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get('Authorization') == 'Bearer test-only'
            self.send_response(302)
            self.send_header('Location', f'http://localhost:{target.server_port}/log')
            self.end_headers()
        def log_message(self, *args): pass
    origin = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
    servers = [target, origin]
    threads = [threading.Thread(target=s.serve_forever) for s in servers]
    for thread in threads: thread.start()
    try:
        status, body, _ = transport(f'http://127.0.0.1:{origin.server_port}/log', {'Authorization': 'Bearer test-only'})
        assert status == 200 and b'AssertionError' in body
        assert received == [None]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads: thread.join()


def test_green_preserves_active_worker_claim():
    source, rows, calls = fixture()
    rows[:] = [run(1)]
    finding = source.poll()[0]
    source.acknowledge(finding.fingerprint)
    rows[:] = [run(2, 'success')]
    source.poll()
    class Store:
        def reports(self, **kwargs):
            return [{'source': 'source:ci', 'fingerprint': finding.fingerprint, 'ticket_ref': 'PROOF-1'}]
    class WT:
        def find(self, ref):
            return {'status': 'open', 'claimed_by': 'active-worker'}
        def _ok(self, *args):
            raise AssertionError('Must not close an active worker claim')
    source.reconcile(Store(), WT())
    assert finding.fingerprint in source._get('recovered')


def test_deleted_inflight_run_does_not_block_new_failures():
    source, rows, calls = fixture()
    source._set('active_runs', [1])
    original = source.request
    source.request = lambda url, headers: (404, b'', {}) if url.endswith('/actions/runs/1') else original(url, headers)
    rows[:] = [run(2)]
    assert len(source.poll()) == 1
    assert source._get('active_runs') == []


def test_recheck_refreshes_candidate_after_it_leaves_first_page():
    source, rows, calls = fixture()
    source.recheck = True
    rows[:] = [run(1, sha='same')]
    assert source.poll() == []
    assert source._get('active_runs') == [1]
    original = source.request
    source.request = lambda url, headers: (200, json.dumps(dict(run(1, sha='same'), run_attempt=2)).encode(), {}) if url.endswith('/actions/runs/1') else original(url, headers)
    rows[:] = []  # the candidate is no longer on the runs page
    assert len(source.poll()) == 1


def test_poll_call_budget_includes_inflight_pages_and_log_enrichment():
    source, rows, calls = fixture()
    source._set('active_runs', list(range(1000, 1010)))
    rows[:] = [dict(run(i), workflow_id=i) for i in range(350, 0, -1)]
    original = source.request
    def request(url, headers):
        if '/actions/runs/' in url and '/jobs?' not in url:
            calls.append(url)
            return 200, json.dumps({'id': int(url.rsplit('/', 1)[1]), 'status': 'in_progress'}).encode(), {}
        if '/jobs?' in url:
            calls.append(url)
            return 200, json.dumps({'jobs': [{'id': 1, 'name': 'unit', 'conclusion': 'failure'}]}).encode(), {}
        if '/logs' in url:
            calls.append(url)
            return 200, b'AssertionError', {}
        return original(url, headers)
    source.request = request
    before = len(calls)
    assert source.poll() == []
    assert len(calls) - before == 4
    before = len(calls)
    assert len(source.poll()) == 3
    assert len(calls) - before == 10


def test_run_listing_larger_than_one_megabyte_is_read_whole():
    """A real page of 100 GitHub runs is over 1 MB; a cut-off read broke setup."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import pytest
    from ccc_server.oncall import ci_source
    from ccc_server.oncall.sources import _Problem
    pad = 'x' * 15000
    listing = json.dumps({'workflow_runs': [
        {'id': n, 'status': 'completed', 'conclusion': 'success', 'head_branch': 'main',
         'head_sha': f'{n:040x}', 'name': 'CI', 'workflow_id': 1, 'repository': {'description': pad}}
        for n in range(1, 101)]}).encode()
    assert len(listing) > 1024 * 1024
    class Server(BaseHTTPRequestHandler):
        def do_GET(self):
            if '/runs' in self.path:
                body = listing
            elif self.path.endswith('/huge'):
                body = b'[' + b' ' * (ci_source.MAX_REPLY + 10) + b']'
            elif self.path.endswith('/logs'):
                body = b'a' * (ci_source.MAX_LOG + 500)
            else:
                body = b'{"default_branch": "main"}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        source = CISource({'id': 'ci', 'repo': 'example/demo',
                           'base': f'http://127.0.0.1:{server.server_port}/api/v1'}, kv=_MemoryKV())
        rows = source._fetch('/actions/runs?per_page=100&limit=100', 'test-only')['workflow_runs']
        assert len(rows) == 100
        with pytest.raises(_Problem, match='too large'):
            source._fetch('/huge', 'test-only')
        assert len(source._fetch('/actions/jobs/1/logs', 'test-only', raw=True)) == ci_source.MAX_LOG
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
