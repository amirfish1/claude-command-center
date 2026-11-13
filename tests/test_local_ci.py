"""Local runner discovery and progress semantics (no network)."""
import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ccc_server import local_ci

_discover_hosted = local_ci._hosted_identities


@pytest.fixture(autouse=True)
def isolated_ci_store(tmp_path, monkeypatch):
    monkeypatch.setattr(local_ci, 'DB_PATH', tmp_path / 'ci.sqlite3')
    monkeypatch.setattr(local_ci, "_hosted_identities", lambda: [])
    discover = local_ci._recent_identities
    monkeypatch.setattr(local_ci, '_recent_identities', lambda: [])
    monkeypatch.setattr(local_ci, '_checked', 0)
    monkeypatch.setattr(local_ci, '_cached', {'jobs': [], 'completed': []})
    yield discover


def test_worker_discovery_ignores_listeners_and_shell_commands():
    output = ('Runner.Listener /opt/runner/bin/Runner.Listener run\n'
              'bash bash -c Runner.Worker\n'
              'Runner.Worker /opt/runner/bin.123/Runner.Worker spawnclient\n')
    with patch.object(local_ci.subprocess, 'run', return_value=SimpleNamespace(stdout=output)):
        assert local_ci._worker_roots() == [Path('/opt/runner')]


def test_identity_reads_bom_config_and_context_only(tmp_path):
    (tmp_path / '.runner').write_text('\ufeff' + json.dumps({
        'gitHubUrl': 'https://github.com/example/project', 'agentName': 'test-runner'}))
    (tmp_path / '_diag').mkdir()
    (tmp_path / '_diag' / 'Worker_20260101.log').write_text(
        '"k": "run_id", "v": "123"\n unrelated diagnostic content')
    assert local_ci._identity(tmp_path) == {
        'repo': 'example/project', 'runner': 'test-runner', 'run_id': '123', 'attempt': 1}


def test_only_local_active_job_counts_and_no_fake_percentage_on_api_failure():
    identity = {'repo': 'example/project', 'runner': 'local', 'run_id': '123'}
    jobs = [
        {'runner_name': 'elsewhere', 'status': 'in_progress', 'name': 'remote'},
        {'runner_name': 'local', 'status': 'completed', 'name': 'old'},
        {'runner_name': 'local', 'status': 'in_progress', 'name': 'test', 'started_at': '2026-01-01T12:00:00Z', 'steps': [
            {'status': 'completed', 'name': 'Checkout'},
            {'status': 'in_progress', 'name': 'Tests'},
            {'status': 'queued', 'name': 'Cleanup'}]}]
    with patch.object(local_ci, '_worker_roots', return_value=[Path('/unused')]), \
         patch.object(local_ci, '_identity', return_value=identity), \
         patch.object(local_ci.subprocess, 'run') as run:
        run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': jobs}))
        rows = local_ci._collect()['jobs']
        assert len(rows) == 1
        assert rows[0]['percent'] == 33
        assert rows[0]['current_step'] == 'Tests'
        assert rows[0]['started_at'] == '2026-01-01T12:00:00Z'
        run.return_value = SimpleNamespace(returncode=1, stdout='')
        assert local_ci._collect()['jobs'][0]['percent'] is None


def test_idle_has_no_network_calls():
    with patch.object(local_ci, '_worker_roots', return_value=[]), \
         patch.object(local_ci.subprocess, 'run') as run:
        assert local_ci._collect()['jobs'] == []
        run.assert_not_called()


def test_multiple_dashboard_reads_share_ten_second_cache():
    with patch.object(local_ci, '_checked', 0), \
         patch.object(local_ci, '_cached', {'jobs': []}), \
         patch.object(local_ci.time, 'monotonic', return_value=100), \
         patch.object(local_ci, '_collect', return_value={'jobs': [{'name': 'test'}]}) as collect:
        assert local_ci.local_ci_progress() == local_ci.local_ci_progress()
        assert collect.call_count == 1


def test_setup_reports_missing_cli_and_no_runners_without_network():
    with patch.object(local_ci.shutil, 'which', return_value=None), \
         patch.object(local_ci, '_runner_roots', return_value=[]), \
         patch.object(local_ci.subprocess, 'run') as run:
        result = local_ci.local_ci_setup()
        assert not result['github_cli']
        assert not result['github_authenticated']
        assert result['runners'] == []
        run.assert_not_called()


def test_setup_checks_active_and_idle_runners_without_exposing_config(tmp_path):
    active, idle = tmp_path / 'active', tmp_path / 'idle'
    for root in (active, idle):
        root.mkdir()
        (root / '.runner').write_text(json.dumps({
            'agentName': root.name, 'serverUrl': 'private-value-never-returned'}))
    with patch.object(local_ci.shutil, 'which', return_value='/usr/bin/gh'), \
         patch.object(local_ci, '_runner_roots', return_value=[active, idle]), \
         patch.object(local_ci, '_worker_roots', return_value=[active]), \
         patch.object(local_ci.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
        result = local_ci.local_ci_setup()
        assert result['github_authenticated']
        assert result['runners'] == [{'name': 'active', 'state': 'running_job'},
                                     {'name': 'idle', 'state': 'listening'}]
        assert 'private-value-never-returned' not in json.dumps(result)


def test_setup_auth_timeout_is_unverified_not_ready():
    with patch.object(local_ci.shutil, 'which', return_value='/usr/bin/gh'), \
         patch.object(local_ci, '_runner_roots', return_value=[]), \
         patch.object(local_ci.subprocess, 'run', side_effect=local_ci.subprocess.TimeoutExpired('gh', 5)):
        result = local_ci.local_ci_setup()
        assert result['github_cli']
        assert not result['github_authenticated']


def test_completion_survives_runner_exit_restart_and_dismissal(monkeypatch):
    identity = {'repo': 'example/project', 'runner': 'local', 'run_id': '123', 'attempt': 1}
    monkeypatch.setattr(local_ci, '_worker_roots', lambda: [Path('/unused')])
    monkeypatch.setattr(local_ci, '_identity', lambda root: identity)
    monkeypatch.setattr(local_ci, '_api', lambda route: {'status':'in_progress'} if '/jobs?' not in route else {'jobs':[]})
    assert local_ci._collect()['jobs']
    # The worker disappears before GitHub's final state is read.
    monkeypatch.setattr(local_ci, '_worker_roots', lambda: [])
    records = local_ci._load_records()
    for record in records.values(): record['polled_at'] = 0
    local_ci._save_records(records)
    monkeypatch.setattr(local_ci, '_api', lambda route: {'status':'completed', 'conclusion':'failure', 'name':'CI', 'run_started_at':'2026-01-01T11:58:00Z', 'updated_at':'2026-01-01T12:00:00Z'})
    result = local_ci._collect()
    assert not result['jobs']
    assert result['completed'][0]['conclusion'] == 'failure'
    assert result['completed'][0]['started_at'] == '2026-01-01T11:58:00Z'
    # A fresh reader obtains persisted history without re-querying GitHub.
    monkeypatch.setattr(local_ci, '_api', lambda route: pytest.fail('Completed runs must not keep polling GitHub'))
    assert local_ci._collect()['completed'][0]['id'] == 'example/project:123:1'
    assert local_ci.dismiss_local_ci('example/project:123:1')
    assert not local_ci._collect()['completed']
    assert not local_ci.dismiss_local_ci('missing')


def test_only_two_latest_completed_runs_and_dismissed_does_not_backfill():
    records = {str(n): {'repo':'example/project', 'run_id':str(n), 'status':'completed',
                       'completed_at':f'2026-01-0{n}T12:00:00Z'} for n in (1,2,3)}
    local_ci._save_records(records)
    assert [row['id'] for row in local_ci._recent_completed()] == ['3','2']
    local_ci.dismiss_local_ci('3')
    # A polling upsert must not overwrite a dismissal from another client.
    local_ci._save_records(records)
    assert [row['id'] for row in local_ci._recent_completed()] == ['2']


def test_api_error_after_worker_exit_does_not_invent_success(monkeypatch):
    local_ci._save_records({'example/project:123:1': {
        'repo':'example/project', 'run_id':'123', 'attempt':1, 'status':'pending', 'runners':['local']}})
    monkeypatch.setattr(local_ci, '_worker_roots', lambda: [])
    monkeypatch.setattr(local_ci, '_api', lambda route: None)
    result = local_ci._collect()
    assert result['jobs'] == result['completed'] == []


def test_rerun_attempt_is_not_hidden_by_previous_attempt_dismissal(monkeypatch):
    old = {'repo':'example/project', 'run_id':'123', 'attempt':1, 'runners':['local'],
           'status':'completed', 'completed_at':'2026-01-01T12:00:00Z'}
    local_ci._save_records({'example/project:123:1':old})
    local_ci.dismiss_local_ci('example/project:123:1')
    monkeypatch.setattr(local_ci, '_worker_roots', lambda: [Path('/unused')])
    monkeypatch.setattr(local_ci, '_identity', lambda root: dict(old, attempt=2, runner='local'))
    monkeypatch.setattr(local_ci, '_api', lambda route: {'status':'completed', 'name':'CI', 'conclusion':'success', 'updated_at':'2026-01-02T12:00:00Z'})
    result = local_ci._collect()
    assert [row['id'] for row in result['completed']] == ['example/project:123:2']


def test_recent_discovery_counts_distinct_runs_not_job_logs(tmp_path, monkeypatch, isolated_ci_store):
    diag = tmp_path / '_diag'
    diag.mkdir()
    for n in (1,2,3): (diag / f'Worker_00{n}.log').touch()
    monkeypatch.setattr(local_ci, '_runner_roots', lambda names: [tmp_path])
    monkeypatch.setattr(local_ci, '_identity', lambda root, log: {
        'repo':'example/project', 'runner':'local', 'attempt':1,
        'run_id':'123' if log.name in ('Worker_003.log','Worker_002.log') else '122'})
    assert [row['run_id'] for row in isolated_ci_store()] == ['123','122']


def test_hosted_run_visible_without_local_runner(monkeypatch):
    identity = {'repo':'example/project', 'runner':'', 'run_id':'987', 'attempt':1, 'hosted':True}
    monkeypatch.setattr(local_ci, '_worker_roots', lambda: [])
    monkeypatch.setattr(local_ci, '_hosted_identities', lambda: [identity])
    def api(route):
        if '/jobs?' in route:
            return {'jobs':[{'runner_name':'GitHub Actions 123', 'status':'in_progress',
                'name':'unittest', 'steps':[{'status':'completed','name':'Checkout'},
                                          {'status':'in_progress','name':'Tests'}]}]}
        return {'status':'in_progress', 'run_started_at':'2026-01-01T12:00:00Z'}
    monkeypatch.setattr(local_ci, '_api', api)
    result = local_ci._collect()
    assert len(result['jobs']) == 1
    assert result['jobs'][0]['name'] == 'unittest'
    assert result['jobs'][0]['current_step'] == 'Tests'
    assert result['jobs'][0]['percent'] == 50
    assert local_ci._collect()['jobs'], 'hosted jobs stay visible between polls'
    monkeypatch.setattr(local_ci, '_hosted_identities', lambda: [])
    records = local_ci._load_records()
    for record in records.values(): record['polled_at'] = 0
    local_ci._save_records(records)
    monkeypatch.setattr(local_ci, '_api', lambda route: {'status':'completed','name':'CI',
        'conclusion':'success', 'updated_at':'2026-01-01T12:30:00Z'})
    result = local_ci._collect()
    assert not result['jobs']
    assert result['completed'][0]['conclusion'] == 'success'


@pytest.mark.parametrize('remote', ['https://github.com/example/project.git', 'git@github.com:example/project.git'])
def test_hosted_discovery_uses_checkout_origin_and_includes_queued(remote, monkeypatch):
    monkeypatch.setattr(local_ci.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=remote))
    routes = []
    def api(route):
        routes.append(route)
        return {'workflow_runs':[{'id':123 if 'in_progress' in route else 124, 'run_attempt':2}]}
    monkeypatch.setattr(local_ci, '_api', api)
    identities = _discover_hosted()
    assert [identity['run_id'] for identity in identities] == ['123', '124']
    assert all(identity['repo'] == 'example/project' and identity['hosted'] for identity in identities)
    assert all(route.startswith('repos/example/project/actions/runs?') for route in routes)
