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
