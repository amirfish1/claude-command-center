"""Temporary restart pauses recover without an operator Resume click."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import server
from ccc_worker import WorkerRuntime, _release_stale_restart_drain
from control_plane import WorkLedger


@pytest.fixture
def restart(tmp_path, monkeypatch):
    runtime = WorkerRuntime(WorkLedger(tmp_path / 'work.sqlite'), token='test')
    engines = Mock()
    engines.dispatch_queued.return_value = []
    engines.reconcile_uncertain.return_value = []
    engines.retire_stale_uncertain.return_value = []
    runtime._engine_host = engines
    monkeypatch.setattr(server, '_dashboard_owned_active_executions', lambda: [])

    def request(method, params=None, **kwargs):
        if method == 'engine.adopt':
            return {'ok': True, 'available': True}
        return runtime.dispatch(method, params or {})

    monkeypatch.setattr(server, '_control_plane_request', request)
    return runtime, engines


def worker_endpoint():
    # Execute the actual endpoint body without binding a production HTTP server.
    tree = ast.parse(Path(server.__file__).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                and ast.unparse(n.test) == "path == '/api/restart/worker'")
    fn = ast.FunctionDef(name='endpoint', args=ast.arguments(posonlyargs=[],
        args=[ast.arg(arg='self')], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=node.body, decorator_list=[])
    scope = dict(vars(server))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                 '<worker-restart-endpoint>', 'exec'), scope)
    return scope['endpoint']


@pytest.mark.parametrize('entry', ['automatic', 'endpoint'])
@pytest.mark.parametrize('restarted,healthy', [(True, True), (False, True), (True, False), (False, False)])
@pytest.mark.parametrize('new_pause', [False, True])
def test_restart_completion_recovers_only_its_own_pause(restart, monkeypatch, entry, restarted, healthy, new_pause):
    runtime, engines = restart
    replacement = None

    def restart_process():
        nonlocal replacement
        if new_pause:
            replacement = runtime.ledger.set_drain(True, 'operator paused dispatch')
        return {'restarted': restarted}

    monkeypatch.setattr(server, '_restart_worker_process', restart_process)
    monkeypatch.setattr(server, '_wait_worker_healthy', lambda: healthy)
    if entry == 'automatic':
        server._worker_compat_self_heal_restart()
    else:
        reply = Mock()
        worker_endpoint()(SimpleNamespace(send_json=reply))
        if healthy and not restarted:
            assert reply.call_args.args[0]['note'] == 'Worker restart failed; the existing worker is still healthy.'
    current = runtime.ledger.drain_state()
    if new_pause:
        assert current == replacement
    else:
        assert current['enabled'] is not healthy
    assert engines.dispatch_queued.call_count == int(healthy and not new_pause)


@pytest.mark.parametrize('prefix', ['worker-restart:', 'worker-compat-selfheal:'])
def test_slow_worker_boot_recovers_restart_pause(restart, prefix):
    runtime, _ = restart
    runtime.ledger.set_drain(True, prefix + 'test')
    assert _release_stale_restart_drain(runtime)
    assert not runtime.ledger.drain_state()['enabled']


@pytest.mark.parametrize('reason', ['operator paused dispatch', 'dashboard-restart:test', 'unknown'])
def test_worker_boot_preserves_other_pauses(restart, reason):
    runtime, _ = restart
    current = runtime.ledger.set_drain(True, reason)
    assert not _release_stale_restart_drain(runtime)
    assert runtime.ledger.drain_state() == current


def test_worker_boot_does_not_erase_a_racing_manual_pause(restart, monkeypatch):
    runtime, _ = restart
    runtime.ledger.set_drain(True, 'worker-compat-selfheal:test')
    release = runtime.ledger.release_drain
    replacement = None

    def raced_release(expected, reason):
        nonlocal replacement
        replacement = runtime.ledger.set_drain(True, 'operator paused dispatch')
        return release(expected, reason)

    monkeypatch.setattr(runtime.ledger, 'release_drain', raced_release)
    assert not _release_stale_restart_drain(runtime)
    assert runtime.ledger.drain_state() == replacement


def test_failed_dashboard_exec_resumes_dispatch(restart, monkeypatch):
    runtime, engines = restart
    _, _, _, protected = server._safe_worker_restart_precheck('dashboard-restart')
    monkeypatch.setattr(server, '_quiesce_http_for_restart', lambda: True)
    monkeypatch.setattr(server.threading, 'Timer', lambda delay, fn: SimpleNamespace(start=fn))
    monkeypatch.setattr(server.os, 'execvp', Mock(side_effect=OSError('test exec failure')))
    monkeypatch.setattr(server.sys, 'platform', 'linux')
    monkeypatch.setattr(server, '_server_launch_argv', lambda: ['python3', 'server.py'])
    monkeypatch.setattr(server._ACCEPT_PAUSED, 'clear', Mock())
    server._schedule_restart(protected=protected)
    assert not runtime.ledger.drain_state()['enabled']
    engines.dispatch_queued.assert_called_once()
    server._ACCEPT_PAUSED.clear.assert_called_once()
