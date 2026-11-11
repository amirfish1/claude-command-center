import json
from ccc_server.oncall.alertmanager_source import AlertmanagerSource, MAX_GROUPS


def group(name, state='active', silenced=None):
    return {'receiver': {'name': 'default'}, 'labels': {'group': name}, 'alerts': [
        {'status': {'state': state, 'silencedBy': silenced or [], 'inhibitedBy': []},
         'labels': {'alertname': name}, 'annotations': {'description': 'password=sk-ant-test-XXXX'}}]}


def test_baseline_recovery_regression_and_reconcile():
    rows = [group('old')]
    calls = []
    def http(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return 200, json.dumps(rows).encode()
    source = AlertmanagerSource({'id': 'a', 'url': 'https://example.com'}, http=http)
    assert source.check()[0]
    assert source.poll() == []
    rows.append(group('new'))
    found = source.poll()
    assert len(found) == 1 and 'sk-ant-test-XXXX' not in found[0].text
    fp = found[0].fingerprint
    assert source.poll() == []
    rows[:] = [group('old'), group('new', silenced=['s'])]
    assert source.poll() == []
    class Store:
        def reports(self, **kwargs):
            return [{'source': 'source:a', 'fingerprint': fp, 'ticket_ref': 'TEST-1'}]
    class WT:
        status = 'in_progress'
        closed = []
        def find(self, ref): return {'status': self.status, 'claimed_by': None}
        def comment(self, *args): self.closed.append(args)
        def _ok(self, *args): raise AssertionError('must never close')
    wt = WT()
    source.reconcile(Store(), wt)
    assert not wt.closed
    rows[:] = [group('old'), group('new')]
    assert source.poll() == []
    assert not source._get('recovered', {})
    rows[:] = [group('old')]
    assert source.poll() == []
    wt.status = 'open'
    source.reconcile(Store(), wt)
    assert len(wt.closed) == 1 and 'does not establish a code fix' in wt.closed[0][1]
    source.reconcile(Store(), wt)
    assert len(wt.closed) == 1
    rows[:] = [group('old'), group('new')]
    again = source.poll()
    assert len(again) == 1 and again[0].fingerprint != fp
    assert all(c[0] == 'GET' and c[1].endswith('/api/v2/alerts/groups') for c in calls)


def test_overflow_and_bad_response_do_not_create_false_recovery():
    rows = []
    def http(*args, **kwargs): return 200, json.dumps(rows).encode()
    source = AlertmanagerSource({'id': 'a', 'base': 'http://example.com'}, http=http)
    assert source.check()[0]
    rows.append(group('new'))
    fp = source.poll()[0].fingerprint
    rows[:] = [group(str(i)) for i in range(MAX_GROUPS + 1)]
    assert source.poll() == [] and source.last_error
    assert fp not in source._get('recovered', {})
    rows[:] = [group('new')]
    assert source.poll() == []


def test_claim_guard_and_bounded_request():
    rows = []
    requests = []
    def http(method, url, **kwargs):
        requests.append(url)
        return 200, json.dumps(rows).encode()
    source = AlertmanagerSource({'id': 'a', 'url': 'http://example.com'}, http=http)
    source.check()
    rows.append(group('x'))
    fp = source.poll()[0].fingerprint
    rows.clear()
    source.poll()
    class Store:
        def reports(self, **kw):
            assert kw['limit'] == 500
            return [{'source': 'source:a', 'fingerprint': fp, 'ticket_ref': 'T-1'}]
    class WT:
        def find(self, ref): return {'status': 'open', 'claimed_by': 'worker'}
        def _ok(self, *args): raise AssertionError('claimed ticket closed')
        def comment(self, ref, text): assert 'no longer reports' in text
    source.reconcile(Store(), WT())
    assert len(requests) == 3
    assert fp not in source._get('recovered')


def test_group_and_reconcile_budgets():
    rows = []
    def http(*args, **kwargs): return 200, json.dumps(rows).encode()
    source = AlertmanagerSource({'id': 'a', 'url': 'http://example.com'}, http=http)
    assert source.check()[0]
    rows.extend(group(str(i)) for i in range(8))
    findings = source.poll() + source.poll() + source.poll()
    assert len(findings) == 8 and len({x.fingerprint for x in findings}) == 8
    rows.clear()
    source.poll()
    reports = [{'source': 'source:a', 'fingerprint': f.fingerprint, 'ticket_ref': 'T-' + str(i)}
               for i, f in enumerate(findings)]
    class Store:
        def reports(self, **kw): return reports
    class WT:
        def __init__(self): self.lookups = []; self.closes = []
        def find(self, ref): self.lookups.append(ref); return {'status': 'open'}
        def comment(self, *args): self.closes.append(args)
        def _ok(self, *args): raise AssertionError('must never close')
    wt = WT()
    source.reconcile(Store(), wt)
    assert len(wt.lookups) == len(wt.closes) == 3
    assert all('normal worker path' in args[1] for args in wt.closes)
    source.reconcile(Store(), wt)
    source.reconcile(Store(), wt)
    assert len(wt.closes) == 8


def test_retry_after_and_auth():
    from ccc_server.oncall.sources import HttpReply
    now = [100]
    calls = []
    replies = [HttpReply(429, b'', {'Retry-After': '60'}), HttpReply(403, b'')]
    def http(*args, **kwargs):
        calls.append(1)
        return replies.pop(0)
    source = AlertmanagerSource({'id': 'a', 'url': 'http://example.com'}, http=http, clock=lambda: now[0])
    assert not source.check()[0]
    assert not source.check()[0] and len(calls) == 1
    now[0] += 61
    ok, error = source.check()
    assert not ok and '403' in error and len(calls) == 2
