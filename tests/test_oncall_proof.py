"""Local proof fixtures and worker cleanup, without a paid agent call."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location(
    'oncall_proof', Path(__file__).resolve().parents[1] / 'scripts/oncall-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def test_seed_has_real_failing_test_and_no_remote(tmp_path, monkeypatch):
    monkeypatch.setenv('PROOF_GIT', shutil.which('git'))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    baseline = proof.seed_repo(tmp_path)
    assert len(baseline) == 40
    assert proof.run('git', 'remote', cwd=tmp_path) == ''
    (tmp_path / 'sum.py').write_text('def add(a, b):\n    return a + b\n')
    proof.run(sys.executable, '-m', 'unittest', '-v', cwd=tmp_path)


@pytest.mark.skipif(not hasattr(proof.signal, 'SIGALRM'), reason='POSIX worker proof')
def test_worker_deadline_kills_child(tmp_path):
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                            start_new_session=True)
    try:
        with pytest.raises(TimeoutError):
            proof.wait_worker({'pid': proc.pid}, timeout=1)
        # wait_worker also reaps the killed child.
        with pytest.raises(ChildProcessError):
            os.waitpid(proc.pid, os.WNOHANG)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux process cleanup')
def test_cleanup_finds_detached_worker_by_isolated_home(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             env={**os.environ, 'HOME': str(tmp_path)},
                             start_new_session=True)
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             env={**os.environ, 'HOME': str(tmp_path / 'other')},
                             start_new_session=True)
    try:
        proof.cleanup_isolated_processes(tmp_path)
        assert child.wait(timeout=5) == -proof.signal.SIGKILL
        assert other.poll() is None
    finally:
        for proc in (child, other):
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def test_observer_requires_worker_record_and_ticket_closure(tmp_path):
    import json
    from types import SimpleNamespace
    records = tmp_path / '.watchtower/workers.json'
    records.parent.mkdir()
    records.write_text(json.dumps({'workers': [{'pid': 123, 'argv': ['app-server']}]}))
    wt = SimpleNamespace(find=lambda ref: {'status': 'closed'})
    watcher = SimpleNamespace(poll=lambda: None)
    observed = proof.wait_automatic_worker(tmp_path, 'PROOF-1', wt, watcher,
                                          proof.time.monotonic(), timeout=1)
    assert observed['pid'] == 123
    # A stopped supervisor is a failure even if a ticket happens to be closed.
    with pytest.raises(RuntimeError, match='watcher exited'):
        proof.wait_automatic_worker(tmp_path, 'PROOF-1', wt,
                                   SimpleNamespace(poll=lambda: 1),
                                   proof.time.monotonic(), timeout=1)
