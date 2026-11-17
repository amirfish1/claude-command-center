"""The permanent check cannot accept missing data or dilute the original gates."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sys


scripts = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('sidebar_performance_check', scripts / 'check-sidebar-performance.py')
check = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(scripts))
try:
    spec.loader.exec_module(check)
finally:
    sys.path.remove(str(scripts))


def _events(tmp_path, timings):
    rows = []
    for kind, ms, count in timings:
        rows.extend({'kind': kind, 'ts': datetime.now(timezone.utc).isoformat(),
                     'ms': ms, 'warmup': False, 'load1': 1, 'ncpu': 4, 'up_s': 3600,
                     'detail': {'status': 304 if kind == 'list_read' and ms == 0 else 200}}
                    for _ in range(count))
    target = tmp_path / 'events.jsonl'
    target.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return target


def test_missing_archive_samples_never_pass(tmp_path):
    target = _events(tmp_path, [('list_read', 100, 50), ('live_build', 100, 100)])
    result = check.check(target, tmp_path, '')
    assert result['status'] == 'pending'
    assert result['evidence']['gates'][2]['samples'] == 0


def test_original_budget_failure_survives_other_missing_samples(tmp_path):
    target = _events(tmp_path, [('list_read', 1001, 50), ('list_read', 0, 1000)])
    result = check.check(target, tmp_path, '')
    assert result['status'] == 'fail' and result['real_data']
    assert result['evidence']['gates'][0]['samples'] == 50


def test_all_three_gates_and_deployment_proof_are_required(tmp_path):
    target = _events(tmp_path, [('list_read', 1000, 50), ('live_build', 1000, 100),
                                 ('archive_load', 2000, 5)])
    assert check.check(target, tmp_path, '')['status'] == 'pass'
    # Actual git ancestry checks reject missing build stamps, including when
    # the requested commit is unavailable; fixtures cannot claim adoption.
    result = check.check(target, tmp_path, 'missing-build')
    assert result['status'] == 'pending' and not result['real_data']
