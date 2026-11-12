"""Local runner discovery and progress semantics (no network)."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ccc_server import local_ci


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
        'repo': 'example/project', 'runner': 'test-runner', 'run_id': '123'}


def test_only_local_active_job_counts_and_no_fake_percentage_on_api_failure():
    identity = {'repo': 'example/project', 'runner': 'local', 'run_id': '123'}
    jobs = [
        {'runner_name': 'elsewhere', 'status': 'in_progress', 'name': 'remote'},
        {'runner_name': 'local', 'status': 'completed', 'name': 'old'},
        {'runner_name': 'local', 'status': 'in_progress', 'name': 'test', 'steps': [
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
