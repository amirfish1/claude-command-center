"""Focused checks for the first-run runner's isolation and accounting."""
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

MODULE = Path(__file__).resolve().parents[1] / 'scripts/first-run-matrix/run.py'
spec = importlib.util.spec_from_file_location('first_run_matrix', MODULE)
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def test_documented_routes_and_quick_target():
    assert matrix.IMAGES == ('ubuntu:24.04', 'ubuntu:22.04', 'debian:12', 'fedora:latest')
    assert set(matrix.ROUTES) == {'curl', 'uvx', 'pipx', 'brew'}
    assert 'file:///fixture/source/scripts/install.sh' in matrix.ROUTES['curl']
    assert 'CCC_FROM=readme bash' in matrix.ROUTES['curl']
    assert 'uvx --from "$ccc_wheel"' in matrix.ROUTES['uvx']
    assert 'pipx run --spec "$ccc_wheel"' in matrix.ROUTES['pipx']


def test_container_isolation_and_cleanup(monkeypatch, tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=127, stdout='curl missing')
    monkeypatch.setattr(matrix.subprocess, 'run', run)
    result = matrix.execute('ubuntu:24.04', 'curl', tmp_path, 5)
    args = calls[0][0]
    assert args[args.index('--network') + 1] == 'none'
    assert any('dst=/fixture,readonly' in arg for arg in args)
    for env in ('HOME=/tmp/ccc-home', 'CCC_STATE_DIR=/tmp/ccc-home/state',
                'WATCHTOWER_STORE=/tmp/ccc-home/watchtower.json', 'WT_NO_AUTOSTART=1',
                'CCC_TELEMETRY_DISABLED=1'):
        assert env in args
    assert calls[0][1]['stdin'] == subprocess.DEVNULL
    assert calls[-1][0][:3] == ['docker', 'rm', '-f']
    assert calls[-1][0][-1] == args[args.index('--name') + 1]
    assert result['exit_code'] == 127 and not result['timed_out']


def test_timeout_is_recorded_and_container_removed(monkeypatch, tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['docker', 'run']:
            raise subprocess.TimeoutExpired(args, 1, output=b'partial output')
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setattr(matrix.subprocess, 'run', run)
    result = matrix.execute('debian:12', 'pipx', tmp_path, 1)
    assert result['timed_out'] and result['exit_code'] is None
    assert result['output'] == 'partial output'
    assert calls[-1][:3] == ['docker', 'rm', '-f']
