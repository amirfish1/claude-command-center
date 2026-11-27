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


def _events(tmp_path, timings, up_s=3600, warmup=False, load1=1, automated=False):
    rows = []
    for kind, ms, count in timings:
        rows.extend({'kind': kind, 'ts': datetime.now(timezone.utc).isoformat(),
                     'ms': ms, 'warmup': warmup, 'load1': load1, 'ncpu': 4, 'up_s': up_s,
                     **({'automated': True} if automated else {}),
                     'detail': {'status': 304 if kind in ('list_read', 'list_poll') and ms == 0 else 200}}
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


def test_all_gates_and_deployment_proof_are_required(tmp_path):
    target = _events(tmp_path, [('list_read', 1000, 50), ('live_build', 1000, 100),
                                 ('archive_load', 2000, 5), ('list_poll', 3000, 50)])
    assert check.check(target, tmp_path, '')['status'] == 'pass'
    # Actual git ancestry checks reject missing build stamps, including when
    # the requested commit is unavailable; fixtures cannot claim adoption.
    result = check.check(target, tmp_path, 'missing-build')
    assert result['status'] == 'pending' and not result['real_data']


def test_post_warmup_boot_samples_count(tmp_path):
    # Restarts are merged to one per 30 minutes, so the gate must see
    # samples between warmup and the 30-minute steady mark (CCC-600).
    target = _events(tmp_path, [('list_read', 1001, 50)], up_s=600)
    result = check.check(target, tmp_path, '')
    assert result['status'] == 'fail'
    assert result['evidence']['gates'][0]['samples'] == 50


def test_warmup_and_saturated_samples_stay_excluded(tmp_path):
    warm = _events(tmp_path, [('list_read', 5000, 50)], up_s=60, warmup=True)
    assert check.check(warm, tmp_path, '')['evidence']['gates'][0]['samples'] == 0
    busy = _events(tmp_path, [('list_read', 5000, 50)], up_s=600, load1=9)
    assert check.check(busy, tmp_path, '')['evidence']['gates'][0]['samples'] == 0


def test_scripted_browser_probes_are_not_user_samples(tmp_path):
    probes = _events(tmp_path, [('list_read', 5000, 50)], up_s=600, automated=True)
    assert check.check(probes, tmp_path, '')['evidence']['gates'][0]['samples'] == 0


def test_fully_hidden_reads_do_not_dilute_the_p95(tmp_path):
    # A read that ran entirely in a hidden tab reports 0 ms; 213 of 329
    # samples were these and pulled a 2314 ms visible p95 to 1087 (CCC-689).
    rows = [{'kind': 'list_read', 'ts': datetime.now(timezone.utc).isoformat(), 'ms': ms,
             'warmup': False, 'load1': 1, 'ncpu': 4, 'up_s': 3600,
             'detail': {'status': 200, 'wall_ms': wall, 'hidden_ms': wall - ms}}
            for ms, wall, count in ((2000, 2000, 50), (0, 900, 1000)) for _ in range(count)]
    target = tmp_path / 'events.jsonl'
    target.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    gate = check.check(target, tmp_path, '')['evidence']['gates'][0]
    assert gate['samples'] == 50 and gate['status'] == 'fail'
    assert check.perf_gate._steady_reason(rows[-1]) == 'hidden'
    # A partly hidden read still counts its visible time.
    assert check.perf_gate._steady_reason(dict(rows[-1], ms=10)) == ''


def test_background_polls_have_their_own_looser_gate(tmp_path):
    # Owner Q1416 (CCC-691): refreshes of the window already on screen are
    # gated at 3 s p95, apart from the 1 s reads a user waits for.
    rows = [('list_read', 1000, 50), ('live_build', 1000, 100), ('archive_load', 2000, 5)]
    within = check.check(_events(tmp_path, rows + [('list_poll', 2900, 50)]), tmp_path, '')
    assert within['status'] == 'pass'
    over = check.check(_events(tmp_path, rows + [('list_poll', 3001, 50)]), tmp_path, '')
    gate = over['evidence']['gates'][3]
    assert over['status'] == 'fail' and gate['kind'] == 'list_poll' and gate['budget_ms'] == 3000
    # A poll never counts toward the list_read gate, and 304s count in neither.
    unchanged = check.check(_events(tmp_path, rows + [('list_poll', 0, 500), ('list_poll', 100, 50)]),
                            tmp_path, '')
    assert unchanged['evidence']['gates'][0]['samples'] == 50
    assert unchanged['evidence']['gates'][3]['samples'] == 50
