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
