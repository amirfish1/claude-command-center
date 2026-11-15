"""Offline budget/SSE checks; no Codex process or model completion is run."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('codex_restart_fixture',
    Path(__file__).resolve().parents[1] / 'scripts' / 'codex_restart_fixture.py')
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


@pytest.fixture
def grant():
    return {'reference': 'offline-unit-test-only', 'max_lifetimes': 2,
            'max_completions': 48, 'max_lifetime_seconds': 1200,
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}


def private(tmp_path):
    tmp_path.chmod(0o700)
    return tmp_path


def test_reconstruction_preserves_consumed_credits_and_cannot_overlap(tmp_path, grant):
    root = private(tmp_path)
    first = fixture.Budget(root, grant)
    assert first.begin_lifetime() == 1
    assert first.completion() == 1
    first.end_lifetime()
    replacement = fixture.Budget(root, grant)
    assert replacement.begin_lifetime() == 2
    assert replacement.completion() == 2
    replacement.end_lifetime()
    with pytest.raises(fixture.Stop, match='cap exhausted'):
        replacement.begin_lifetime()
    with pytest.raises(fixture.Stop, match='halted'):
        fixture.Budget(root, grant)


def test_unfinished_previous_lifetime_cannot_be_silently_retried(tmp_path, grant):
    budget = fixture.Budget(private(tmp_path), grant)
    budget.begin_lifetime()
    with pytest.raises(fixture.Stop, match='has not ended'):
        budget.begin_lifetime()


def test_concurrent_requests_share_one_hard_durable_cap(tmp_path, grant):
    grant['max_completions'] = 8
    root = private(tmp_path)
    budget = fixture.Budget(root, grant)
    budget.begin_lifetime()
    def reserve(_):
        try:
            return budget.completion()
        except fixture.Stop:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(reserve, range(24)))
    assert sorted(value for value in result if value is not None) == list(range(1, 9))
    state = json.loads((root / 'budget.json').read_text())
    assert state['lifetimes'] == 1 and state['completions'] == 8 and state['halted']


@pytest.mark.parametrize('field,value', [('max_lifetimes', 3), ('max_completions', 49),
    ('max_lifetime_seconds', 1201), ('max_lifetimes', True), ('max_completions', 0)])
def test_grant_cannot_raise_caps(tmp_path, grant, field, value):
    grant[field] = value
    with pytest.raises(fixture.Stop):
        fixture.Budget(private(tmp_path), grant)


def test_changed_or_missing_budget_never_reinitializes(tmp_path, grant):
    root = private(tmp_path)
    fixture.Budget(root, grant)
    changed = dict(grant, reference='replacement grant cannot reset')
    with pytest.raises(fixture.Stop, match='grant changed'):
        fixture.Budget(root, changed)
    (root / 'budget.json').unlink()
    with pytest.raises(fixture.Stop, match='cannot reset'):
        fixture.Budget(root, grant)


@pytest.mark.parametrize('corrupt', ['{', '{}', '{"format":1,"format":1}',
    '{"format":1,"last_wall":NaN}', '{"format":1,"last_wall":1e999}', '[1,2]'])
def test_corrupt_state_refuses(tmp_path, grant, corrupt):
    root = private(tmp_path)
    fixture.Budget(root, grant)
    (root / 'budget.json').write_text(corrupt)
    with pytest.raises(fixture.Stop):
        fixture.Budget(root, grant)


def test_clock_regression_or_expiry_halts_grant(tmp_path, grant, monkeypatch):
    root = private(tmp_path)
    budget = fixture.Budget(root, grant)
    current = fixture.time.time()
    monkeypatch.setattr(fixture.time, 'time', lambda: current - 5)
    with pytest.raises(fixture.Stop, match='clock regressed'):
        budget.begin_lifetime()
    monkeypatch.setattr(fixture.time, 'time', lambda: current)
    with pytest.raises(fixture.Stop, match='halted'):
        fixture.Budget(root, grant)


def test_expired_lifetime_stops_instead_of_consuming_a_completion(tmp_path, grant, monkeypatch):
    budget = fixture.Budget(private(tmp_path), grant)
    budget.begin_lifetime()
    later = fixture.time.time() + 1201
    monkeypatch.setattr(fixture.time, 'time', lambda: later)
    with pytest.raises(fixture.Stop, match='no active'):
        budget.completion()


@pytest.mark.parametrize('target', ['budget.json', 'budget.lock'])
def test_symlink_cannot_redirect_grant_state(tmp_path, grant, target):
    root = tmp_path / 'state'
    root.mkdir(mode=0o700, exist_ok=True)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.write_text('{}')
    (root / target).symlink_to(elsewhere)
    with pytest.raises((fixture.Stop, OSError)):
        fixture.Budget(root, grant)
    assert elsewhere.read_text() == '{}'


def test_events_are_deterministic_and_keep_text_usage_and_tool_payloads():
    first = fixture.response_events(1, text='bounded reply')
    assert first == fixture.response_events(1, text='bounded reply')
    def decode(frames):
        return [json.loads(frame.decode().split('\ndata: ', 1)[1]) for frame in frames]
    events = decode(first)
    assert [event['sequence_number'] for event in events] == list(range(len(events)))
    assert events[-1]['response']['output'][0]['content'][0]['text'] == 'bounded reply'
    assert events[-1]['response']['usage']['total_tokens'] == 15
    tool = {'name': 'exec_command', 'arguments': '{"cmd":"printf fixture"}'}
    tool_events = decode(fixture.response_events(2, tool=tool))
    deltas = ''.join(event['delta'] for event in tool_events if event['type'].endswith('arguments.delta'))
    assert deltas == tool['arguments']
    assert tool_events[-1]['response']['output'][0]['name'] == tool['name']


@pytest.fixture
def http_fixture(tmp_path, grant):
    import threading
    budget = fixture.Budget(private(tmp_path), grant)
    budget.begin_lifetime()
    records = []
    server = fixture.Fixture(budget, lambda event, **fields: records.append((event, fields)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, records
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def request(server, method, route, body=None):
    import http.client
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    try:
        connection.request(method, route, body, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_http_sse_and_health_use_only_loopback_and_record_exact_request(http_fixture):
    import hashlib
    server, records = http_fixture
    assert server.server_address[0] == '127.0.0.1'
    assert request(server, 'GET', '/health') == (200, b'{"fixture":true}')
    status, raw = request(server, 'POST', '/v1/responses', b'{"input":"offline"}')
    assert status == 200 and b'event: response.completed' in raw
    assert b'fixture reply' in raw
    assert [event for event, _ in records] == ['fixture_request', 'fixture_completed']
    assert records[0][1]['body_sha256'] == hashlib.sha256(b'{"input":"offline"}').hexdigest()
    assert server.budget.completion() == 2


@pytest.mark.parametrize('method,route,body', [('GET', '/unexpected', None),
    ('POST', '/unexpected', b'{}'), ('POST', '/v1/responses', b'{')])
def test_unexpected_http_operation_permanently_halts(http_fixture, method, route, body):
    import http.client
    server, records = http_fixture
    try:
        status, _ = request(server, method, route, body)
        assert status == 404
    except http.client.RemoteDisconnected:
        assert route == '/v1/responses'
    assert records[-1][0] == 'fixture_stop'
    with pytest.raises(fixture.Stop, match='halted'):
        server.budget.completion()


def test_held_response_is_released_by_harness_without_extra_completion(http_fixture):
    import threading
    server, records = http_fixture
    release = threading.Event()
    server.phase(text='after disconnect', release=release)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(request, server, 'POST', '/v1/responses', b'{}')
        # Request receipt is observable before the model output is released.
        import time
        deadline = time.monotonic() + 2
        while not records and time.monotonic() < deadline:
            release.wait(0.01)
        assert records[0][0] == 'fixture_request' and not future.done()
        release.set()
        status, raw = future.result(timeout=3)
    assert status == 200 and b'after disconnect' in raw
    assert records[-1] == ('fixture_completed', {'sequence': 1})


def test_phase_snapshots_tool_and_rejects_invalid_controls(http_fixture):
    server, _ = http_fixture
    tool = {'name': 'local_fixture', 'arguments': '{}'}
    server.phase(tool=tool)
    tool['name'] = 'changed'
    _, raw = request(server, 'POST', '/v1/responses', b'{}')
    assert b'local_fixture' in raw and b'changed' not in raw
    with pytest.raises(fixture.Stop):
        server.phase(release=True)
