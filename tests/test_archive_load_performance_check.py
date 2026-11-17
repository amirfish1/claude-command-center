"""Startup cannot disappear into the steady-state exclusion window."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

scripts = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('archive_load_check', scripts / 'check-archive-load-performance.py')
check = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(scripts))
try:
    spec.loader.exec_module(check)
finally:
    sys.path.remove(str(scripts))


def events(tmp_path, startup=5000, steady=1000, warm=True, **overrides):
    rows = []
    for up_s, ms in ((900, startup), (3600, steady)):
        rows.extend(dict(kind='archive_load', ts=datetime.now(timezone.utc).isoformat(),
                         ms=ms, warm=warm, warmup=False, load1=1, ncpu=4, up_s=up_s)
                    for _ in range(5))
    for row in rows:
        row.update(overrides)
    target = tmp_path / 'events.jsonl'
    target.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return target


@pytest.mark.parametrize('startup,steady,warm,status', [
    (5000, 1000, True, 'pass'), (5001, 1000, True, 'fail'),
    (5000, 1001, True, 'fail'), (5000, 5000, False, 'pass'),
    (5000, 5001, False, 'fail'),
])
def test_original_budgets(tmp_path, startup, steady, warm, status):
    result = check.check(events(tmp_path, startup, steady, warm), tmp_path, '')
    assert result['status'] == status and result['real_data']


@pytest.mark.parametrize('overrides', [{'warmup': True}, {'load1': 5}, {'up_s': None}])
def test_ineligible_samples_never_pass(tmp_path, overrides):
    result = check.check(events(tmp_path, **overrides), tmp_path, '')
    assert result['status'] == 'pending' and not result['real_data']


def test_missing_cohort_or_unadopted_build_never_passes(tmp_path):
    target = events(tmp_path, startup=1000, up_s=3600)
    assert check.check(target, tmp_path, '')['status'] == 'pending'
    result = check.check(target, tmp_path, 'missing-commit')
    assert result['status'] == 'pending' and not result['real_data']
