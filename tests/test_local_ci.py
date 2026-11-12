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
