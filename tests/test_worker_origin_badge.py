"""WT-27: worker-spawned sessions carry a durable, visible origin badge."""
import importlib
import json

import pytest


@pytest.fixture
def server(monkeypatch, tmp_path):
    server = importlib.import_module('server')
    path = tmp_path / 'session-origins.json'
    monkeypatch.setenv('WATCHTOWER_SESSION_ORIGINS_FILE', str(path))
    monkeypatch.setattr(server, '_wt_read_worker_session_ids', lambda: ['ledger-only-session'])
    monkeypatch.setattr(server, '_wt_read_workers', lambda *a, **k: [])
    monkeypatch.setattr(server, '_wt_list_items_display_cached', lambda: [])
    server._WT_ORIGINS_CACHE.update(sig=None, rows={})
    server._origins_path = path
    return server


def _write(server, origins):
    server._origins_path.write_text(json.dumps({'origins': origins}))
    server._WT_ORIGINS_CACHE.update(sig=None, rows={})


def _stamp(server, *sids):
    rows = [{'session_id': s, 'display_name': 'Sleep forty seconds and reply done'} for s in sids]
    return {r['session_id']: r for r in server._apply_watchtower_worker_display_names(rows)}


def test_probe_row_gets_badge_parent_and_worker_lane(server):
    _write(server, {'probe-1': {'role': 'probe', 'ref': 'Q-24',
                                'parent_worker_id': 'planner-x',
                                'parent_session_id': 'parent-sess'}})
    row = _stamp(server, 'probe-1')['probe-1']
    assert row['worker_origin']['label'] == 'Worker probe · Q-24'
    assert row['parent_session_id'] == 'parent-sess'
    assert row['is_watchtower_worker'] is True


def test_verifier_role_label(server):
    _write(server, {'v-1': {'role': 'verifier', 'ref': 'Q-2'}})
    assert _stamp(server, 'v-1')['v-1']['worker_origin']['label'] == 'Worker verifier · Q-2'


def test_user_spawn_and_human_session_stay_unmarked(server):
    _write(server, {'user-adhoc': {'role': 'adhoc'}})
    rows = _stamp(server, 'user-adhoc', 'human-session')
    assert 'worker_origin' not in rows['user-adhoc']
    assert 'worker_origin' not in rows['human-session']
    assert not rows['human-session'].get('is_watchtower_worker')


def test_ledger_only_worker_badged_without_invented_ticket(server):
    row = _stamp(server, 'ledger-only-session')['ledger-only-session']
    assert row['worker_origin']['label'] == 'Worker' and row['worker_origin']['ref'] == ''


def test_origin_survives_title_change_and_missing_worker_record(server):
    _write(server, {'probe-2': {'role': 'probe', 'ref': 'Q-24'}})
    rows = [{'session_id': 'probe-2', 'display_name': 'Renamed by a human', 'name_overridden': True}]
    out = server._apply_watchtower_worker_display_names(rows)[0]
    assert out['worker_origin']['ref'] == 'Q-24'


def test_session_run_adhoc_spawn_nests_under_its_spawner_unbadged(server):
    # CCC-231: a reviewer a user's session started with `wt spawn` joins the
    # spawner's tree (and so its idea ticket) without a worker badge.
    _write(server, {'reviewer': {'role': 'adhoc', 'parent_session_id': 'spawner'}})
    row = _stamp(server, 'reviewer')['reviewer']
    assert row['parent_session_id'] == 'spawner'
    assert 'worker_origin' not in row
