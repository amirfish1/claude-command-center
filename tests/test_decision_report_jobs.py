"""Receipts remain fast and retry-safe across slow filing and process loss."""
import json
import threading
import time
from unittest import mock

import pytest
import server
from ccc_server import decision_report_jobs as jobs
from ccc_server import decision_reports as reports


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    # Tests explicitly start recovery when they need it.
    monkeypatch.setattr(jobs, 'kick_worker', mock.Mock())
    return {'session_id': 'test-session', 'submission_id': 'a' * 32,
            'reporter_note': 'The status explanation is missing.', 'background': True}


def test_receipt_is_committed_without_capture_or_enqueue(env, monkeypatch):
    create = mock.Mock(side_effect=AssertionError('must not block the request'))
    monkeypatch.setattr(reports, 'create_report', create)
    started = time.monotonic()
    result, code = jobs.submit(env)
    assert time.monotonic() - started < 1
    assert code == 202 and result['saved'] and result['state'] == 'queued'
    create.assert_not_called()
    # A fresh reader can recover the complete original evidence before any work.
    with jobs._connect() as connection:
        row = connection.execute('SELECT * FROM jobs').fetchone()
    assert json.loads(row['payload']) == env
    assert jobs.status(env['submission_id'])[0]['saved']
    assert (jobs._directory() / 'receipts.sqlite3').stat().st_mode & 0o777 == 0o600


def test_retry_keeps_one_job_and_rejects_edited_body(env):
    assert jobs.submit(env)[1] == 202
    assert jobs.submit(env)[1] == 202
    assert jobs.submit({**env, 'reporter_note': 'different'})[1] == 409
    with jobs._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1


@pytest.mark.parametrize('extra', [{'submission_id': '../bad'}, {'session_id': '../bad'},
    {'reporter_note': None}, {'viewport': {'x': 'x' * 5000}}])
def test_invalid_input_never_saves_or_starts_filing(env, extra):
    assert jobs.submit({**env, **extra})[1] == 400
    jobs.kick_worker.assert_not_called()


def test_recovery_uses_original_id_after_ambiguous_enqueue(env, monkeypatch):
    jobs.submit(env)
    with jobs._connect() as connection:
        connection.execute("UPDATE jobs SET state='working'")
    create = mock.Mock(return_value=({'ok': True, 'ref': 'DECISIONS-FIX-TEST'}, 200))
    monkeypatch.setattr(reports, 'create_report', create)
    jobs._drain()
    create.assert_called_once_with(env, submission_id=env['submission_id'])
    result, code = jobs.status(env['submission_id'])
    assert code == 200 and result['state'] == 'complete' and result['ref'] == 'DECISIONS-FIX-TEST'
    jobs._drain()
    assert create.call_count == 1
    assert jobs.submit(env)[0]['ref'] == 'DECISIONS-FIX-TEST'


def test_slow_filing_does_not_hold_receipt_requests(env, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {'ok': True, 'ref': 'DECISIONS-FIX-TEST'}, 200
    monkeypatch.setattr(reports, 'create_report', slow)
    jobs.submit(env)
    thread = threading.Thread(target=jobs._drain)
    thread.start()
    try:
        assert entered.wait(1)
        started = time.monotonic()
        result, code = jobs.submit(env)
        assert code == 202 and result['state'] == 'working'
        assert jobs.status(env['submission_id'])[0]['saved']
        assert time.monotonic() - started < 1
    finally:
        release.set()
        thread.join(3)
    assert jobs.status(env['submission_id'])[0]['state'] == 'complete'


def test_failed_filing_is_honest_and_explicit_retry_recovers(env, monkeypatch):
    jobs.submit(env)
    create = mock.Mock(return_value=({'ok': False, 'error': 'queue unavailable'}, 503))
    monkeypatch.setattr(reports, 'create_report', create)
    jobs._drain()
    result, _ = jobs.status(env['submission_id'])
    assert result['saved'] and not result['ok'] and result['state'] == 'failed'
    assert 'ref' not in result
    jobs.submit(env)
    create.return_value = {'ok': True, 'ref': 'DECISIONS-FIX-TEST'}, 200
    jobs._drain()
    assert jobs.status(env['submission_id'])[0]['ref'] == 'DECISIONS-FIX-TEST'


def test_unknown_status_does_not_return_other_receipts(env):
    jobs.submit(env)
    assert jobs.status('../bad')[1] == 404
    assert jobs.status('b' * 32)[1] == 404
