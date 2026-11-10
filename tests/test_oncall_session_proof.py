"""Failure handling for the opt-in real-session harness."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

SPEC = importlib.util.spec_from_file_location(
    'session_proof', Path(__file__).resolve().parents[1] / 'scripts/oncall-session-proof.py')
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)


def test_cleanup_reaps_owned_process(tmp_path):
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                            env={'HOME': str(tmp_path)}, start_new_session=True)
    proof.cleanup(tmp_path, [proc])
    assert proc.returncode is not None


def test_watcher_has_hard_deadline():
    start = time.monotonic()
    try:
        proof.wait_for(lambda: False, 0, 'missing event')
    except TimeoutError as exc:
        assert str(exc) == 'missing event'
    else:
        raise AssertionError('Expected a bounded failure')
    assert time.monotonic() - start < 1
