import concurrent.futures
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import pytest

from ccc_server import headroom as hr

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc).timestamp()


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


def snapshot(ts, pct=50, *, reset=None, codex_pct=None, kimi_pct=None):
    reset = NOW + 9 * 3600 if reset is None else reset
    item = {'ts': iso(ts), 'seven_day': {'utilization': pct, 'resets_at': iso(reset)},
            'five_hour': {'utilization': pct, 'resets_at': iso(NOW + 3600)}}
    for engine, value in (('codex', codex_pct), ('kimi', kimi_pct)):
        if value is not None:
            item[engine] = {'snapshot_ts': iso(ts), 'plan_type': 'test-plan',
                            'weekly': {'pct': value, 'resets_at': iso(reset)}}
    return item


def write_snapshots(path, items):
    path.write_text(''.join(json.dumps(item) + '\n' for item in items))


@pytest.fixture
def sources(tmp_path):
    hr._HISTORY_CACHE.clear()
    return {'now': NOW, 'snapshot_path': tmp_path / 'snapshots.jsonl',
            'legacy_path': tmp_path / 'legacy.json', 'cache_path': tmp_path / 'headroom.json',
            'throughput_dir': tmp_path / 'throughput', 'token_paths': [], 'calibration': {}}


def account(sources, engine='claude'):
    return next(row for row in hr.headroom_payload(**sources)['accounts'] if row['engine'] == engine)


def test_contract_and_exact_expiry_math(sources, tmp_path):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    sources['calibration'] = {'claude': {'available': True, 'pct_per_usd': 0.5, 'calibrated_at': NOW}}
    token = tmp_path / 'tokens.json'
    token.write_text(json.dumps({'real_pct': 50, 'tokens': 5_000_000, 'calibrated_at': NOW}))
    sources['token_paths'] = [token]
    row = account(sources)
    assert row['id'] == 'claude:current'
    assert row['account_id'] == 'current'
    assert row['available'] and not row['stale']
    assert row['percent_left'] == 50
    assert row['resets_at'] == iso(NOW + 9 * 3600)
    assert row['hours_to_reset'] == 9
    assert row['burn_rate_pct_per_hour'] == 2
    assert row['expires_unused_pct'] == 32
    assert row['expires_unused_usd'] == 64
    assert row['expires_unused_tokens'] == 3_200_000
    assert row['leftover_candidate'] and row['forecast_available']
    session = row['windows'][1]
    assert session['window'] == 'session'
    assert session['expires_unused_pct'] == 48
    assert session['expires_unused_usd'] is None
    assert session['expires_unused_tokens'] is None
    assert 'not cash' in row['estimate_note']
    payload = hr.headroom_payload(**sources)
    assert payload['ok'] and payload['updated_at'] == iso(NOW)
    json.dumps(payload, allow_nan=False)


def test_projection_includes_time_since_last_observation(sources):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 4200, 48), snapshot(NOW - 600, 50)])
    row = account(sources)
    assert row['percent_left'] == 50
    assert row['expires_unused_pct'] == pytest.approx(31.6667)


def test_independent_engine_balances_and_calibrations(sources):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 45, codex_pct=60, kimi_pct=20),
                                              snapshot(NOW, 50, codex_pct=63, kimi_pct=20)])
    sources['calibration'] = {'codex': {'available': True, 'pct_per_usd': 2, 'calibrated_at': NOW}}
    codex = account(sources, 'codex')
    assert codex['percent_left'] == 37
    assert codex['burn_rate_pct_per_hour'] == 3
    assert codex['expires_unused_pct'] == 10
    assert codex['expires_unused_usd'] == 5
    assert codex['expires_unused_tokens'] is None
    assert not codex['leftover_candidate']
    kimi = account(sources, 'kimi')
    assert kimi['expires_unused_pct'] == 80
    assert kimi['burn_rate_pct_per_hour'] == 0
    assert kimi['expires_unused_usd'] is None
    assert kimi['leftover_candidate']
    assert account(sources)['expires_unused_usd'] is None


def test_empty_unknown_and_unmetered_are_not_fake_balances(sources):
    payload = hr.headroom_payload(**sources)
    for row in payload['accounts']:
        assert not row['available']
        for key in ('percent_left', 'hours_to_reset', 'expires_unused_usd', 'expires_unused_pct'):
            assert row[key] is None
        assert not row['leftover_candidate']
    assert 'own limits' in account(sources, 'free_router')['reason']
    assert 'does not share' in account(sources, 'devin')['reason']


@pytest.mark.parametrize('pct', [None, True, -1, 101, float('nan'), float('inf'), '50'])
def test_invalid_percentage_is_unknown_not_clamped(sources, pct):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW, pct)])
    assert not account(sources)['available']
    json.dumps(hr.headroom_payload(**sources), allow_nan=False)


@pytest.mark.parametrize('reset', ['not a date', '2026-10-06T20:00:00', None])
def test_invalid_reset_is_unknown(sources, reset):
    item = snapshot(NOW)
    item['seven_day']['resets_at'] = reset
    write_snapshots(sources['snapshot_path'], [item])
    assert not account(sources)['available']


def test_bad_lines_and_non_object_windows_do_not_break_other_engines(sources):
    item = snapshot(NOW, codex_pct=20)
    item['seven_day'] = [50]
    item['kimi'] = 'bad'
    sources['snapshot_path'].write_text('bad json\n[]\n' + json.dumps(item) + '\n')
    assert not account(sources)['available']
    assert account(sources, 'codex')['percent_left'] == 80


@pytest.mark.parametrize('last_ts,reset', [(NOW - 90000, NOW + 3600), (NOW, NOW),
                                        (NOW, NOW - 3600), (NOW + 60, NOW + 3600)])
def test_stale_expired_or_future_data_never_offer_leftovers(sources, last_ts, reset):
    write_snapshots(sources['snapshot_path'], [snapshot(last_ts - 3600, 40, reset=reset),
                                              snapshot(last_ts, 50, reset=reset)])
    row = account(sources)
    assert row['available'] and row['stale']
    assert row['expires_unused_pct'] is None
    assert not row['forecast_available'] and not row['leftover_candidate']


def test_provider_own_time_not_native_poll_time_controls_freshness(sources):
    items = []
    for ts in (NOW - 3600, NOW):
        item = snapshot(ts, codex_pct=30)
        item['codex']['snapshot_ts'] = iso(NOW - 90000)
        items.append(item)
    write_snapshots(sources['snapshot_path'], items)
    assert account(sources, 'codex')['stale']
    assert account(sources, 'codex')['expires_unused_pct'] is None


def test_carried_forward_snapshot_does_not_fabricate_idle_burn(sources):
    items = []
    for ts in (NOW - 3600, NOW):
        item = snapshot(ts, codex_pct=30)
        item['codex']['snapshot_ts'] = iso(NOW)
        items.append(item)
    write_snapshots(sources['snapshot_path'], items)
    assert account(sources, 'codex')['burn_rate_pct_per_hour'] is None


@pytest.mark.parametrize('change', ['reset', 'decrease', 'plan', 'gap', 'short', 'old'])
def test_insufficient_or_discontinuous_burn_is_unknown(sources, change):
    first = snapshot(NOW - 3600, 40, codex_pct=40)
    last = snapshot(NOW, 50, codex_pct=50)
    if change == 'reset':
        first['codex']['weekly']['resets_at'] = iso(NOW + 10 * 3600)
    elif change == 'decrease':
        first['codex']['weekly']['pct'] = 60
    elif change == 'plan':
        first['codex']['plan_type'] = 'old-plan'
    elif change == 'gap':
        first['codex']['snapshot_ts'] = iso(NOW - 7200)
    elif change == 'short':
        first['codex']['snapshot_ts'] = iso(NOW - 600)
    elif change == 'old':
        first['codex']['snapshot_ts'] = iso(NOW - 4600)
        last['codex']['snapshot_ts'] = iso(NOW - 1000)
    write_snapshots(sources['snapshot_path'], [first, last])
    row = account(sources, 'codex')
    assert row['expires_unused_pct'] is None
    assert not row['forecast_available'] and not row['leftover_candidate']


def test_history_before_reset_does_not_poison_new_window(sources):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 7200, 99, reset=NOW - 3600),
                                              snapshot(NOW - 3600, 10), snapshot(NOW, 12)])
    assert account(sources)['burn_rate_pct_per_hour'] == 2


@pytest.mark.parametrize('used,before,reset_hours,unused,candidate', [
    (50, 48, 9, 32, True), (50, 40, 9, 0, False), (100, 99, 9, 0, False),
    (80, 80, 24, 20, True), (80, 80, 24.1, 20, False), (81, 81, 1, 19, False),
])
def test_expiry_clamps_and_candidate_boundaries(sources, used, before, reset_hours, unused, candidate):
    reset = NOW + reset_hours * 3600
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, before, reset=reset),
                                              snapshot(NOW, used, reset=reset)])
    row = account(sources)
    assert row['expires_unused_pct'] == unused
    assert row['leftover_candidate'] == candidate


@pytest.mark.parametrize('ratio,stamp,available', [(0, NOW, True), (-1, NOW, True),
    (float('inf'), NOW, True), (True, NOW, True), (1, NOW + 1, True),
    (1, NOW - 29 * 86400, True), (1, NOW, False)])
def test_invalid_or_old_dollar_calibration_does_not_invent_money(sources, ratio, stamp, available):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    sources['calibration'] = {'claude': {'available': available, 'pct_per_usd': ratio,
                                      'calibrated_at': stamp}}
    assert account(sources)['expires_unused_usd'] is None
    assert account(sources)['expires_unused_pct'] == 32


def test_legacy_fallback_uses_observation_time_and_never_overrides_newer_native(sources):
    sources['legacy_path'].write_text(json.dumps({'fetched_at': iso(NOW),
        'usage': {'seven_day': {'utilization': 30, 'resets_at': iso(NOW + 9 * 3600)}}}))
    assert account(sources)['percent_left'] == 70
    write_snapshots(sources['snapshot_path'], [snapshot(NOW + 1, 50)])
    assert account(sources)['percent_left'] == 50


def test_unchanged_sources_never_reparse_even_after_restart(sources, monkeypatch):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    calls = []
    original = hr._normalize
    monkeypatch.setattr(hr, '_normalize', lambda *args: calls.append(1) or original(*args))
    assert account(sources)['expires_unused_pct'] == 32
    assert calls == [1]
    for _ in range(20):
        account(sources)
    assert calls == [1]
    hr._HISTORY_CACHE.clear()
    hr._JSON_CACHE.clear()
    original_open = Path.open

    def reject_transcript_read(path, *args, **kwargs):
        assert path != sources['snapshot_path'], 'unchanged snapshot history reread after restart'
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', reject_transcript_read)
    assert account(sources)['expires_unused_pct'] == 32
    assert calls == [1]


def test_size_change_invalidates_even_if_mtime_unchanged(sources):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW - 60, 50)])
    assert account(sources)['percent_left'] == 50
    stat = sources['snapshot_path'].stat()
    with sources['snapshot_path'].open('a') as stream:
        stream.write(json.dumps(snapshot(NOW, 51)) + '\n')
    os.utime(sources['snapshot_path'], ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert account(sources)['percent_left'] == 49


def test_countdown_and_stale_change_with_unchanged_sources(sources):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    assert account(sources)['hours_to_reset'] == 9
    sources['now'] = NOW + 3600
    row = account(sources)
    assert row['hours_to_reset'] == 8
    assert not row['forecast_available']
    sources['now'] = NOW + 10 * 3600
    assert account(sources)['stale']


def test_concurrent_requests_reduce_history_once_and_return_independent_rows(sources, monkeypatch):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    calls = []
    original = hr._normalize
    monkeypatch.setattr(hr, '_normalize', lambda *args: calls.append(1) or original(*args))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: account(sources), range(16)))
    assert calls == [1]
    results[0]['windows'][0]['percent_left'] = 0
    assert results[1]['windows'][0]['percent_left'] == 50
    assert account(sources)['percent_left'] == 50


def test_hot_path_never_discovers_sessions_or_launches_subprocesses(sources, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('headroom must not scan sessions or launch subprocesses')

    monkeypatch.setattr(Path, 'glob', forbidden)
    monkeypatch.setattr(Path, 'rglob', forbidden)
    monkeypatch.setattr(Path, 'iterdir', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    for _ in range(10):
        assert account(sources)['expires_unused_pct'] == 32


def test_corrupt_persisted_cache_is_rebuilt(sources):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    assert account(sources)['expires_unused_pct'] == 32
    saved = json.loads(sources['cache_path'].read_text())
    saved['points'] = {'claude:weekly': [{'ts': 'bad'}]}
    sources['cache_path'].write_text(json.dumps(saved))
    hr._HISTORY_CACHE.clear()
    assert account(sources)['expires_unused_pct'] == 32


def test_token_calibration_fallback_and_mtime_size_cache(sources, tmp_path, monkeypatch):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    primary, fallback = tmp_path / 'primary.json', tmp_path / 'fallback.json'
    primary.write_text('{}')
    fallback.write_text(json.dumps({'real_pct': 50, 'tokens': 5_000_000, 'calibrated_at': NOW}))
    sources['token_paths'] = [primary, fallback]
    assert account(sources)['expires_unused_tokens'] == 3_200_000
    original = Path.read_text
    reads = []

    def counting(path, *args, **kwargs):
        if path in (primary, fallback):
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', counting)
    account(sources)
    assert reads == []
    primary.write_text(json.dumps({'real_pct': 50, 'tokens': 10_000_000, 'calibrated_at': NOW}))
    assert account(sources)['expires_unused_tokens'] == 6_400_000
    assert reads == [primary]


def test_overflowing_conversion_remains_unknown_json(sources):
    write_snapshots(sources['snapshot_path'], [snapshot(NOW - 3600, 48), snapshot(NOW, 50)])
    sources['calibration'] = {'claude': {'available': True, 'pct_per_usd': 1e-320, 'calibrated_at': NOW}}
    assert account(sources)['expires_unused_usd'] is None
    json.dumps(hr.headroom_payload(**sources), allow_nan=False)


def test_dollar_values_reuse_real_matched_day_calibration(sources):
    from ccc_server import quota_calibration as qc

    directory = sources['throughput_dir']
    directory.mkdir()
    items = [snapshot(NOW - (48 - index) * 3600, 26 + index * 0.5) for index in range(49)]
    write_snapshots(sources['snapshot_path'], items)
    for index in range(2):
        start = NOW - (48 - index * 24) * 3600
        (directory / f'daily-test-{index}.json').write_text(json.dumps({
            'ok': True, 'final': True, 'engine': 'claude', 'day_start_epoch': start,
            'day_end_epoch': start + 86400, 'totals': {'cost_usd': 50}, 'per_model': []}))
    sources['calibration'] = qc.quota_cost_calibration(sources['snapshot_path'], directory,
                                                    now=NOW, synchronous=True)
    assert sources['calibration']['claude']['pct_per_usd'] == 0.24
    row = account(sources)
    assert row['burn_rate_pct_per_hour'] == 0.5
    assert row['expires_unused_pct'] == 45.5
    assert row['expires_unused_usd'] == 189.58


def test_partial_provider_poll_preserves_other_accounts_original_observation(sources):
    items = [snapshot(NOW - 3600, 48, codex_pct=30), snapshot(NOW - 60, 50, codex_pct=30)]
    items.append({'ts': iso(NOW), 'codex': None, 'kimi': {
        'snapshot_ts': iso(NOW), 'weekly': {'pct': 20, 'resets_at': iso(NOW + 3600)}}})
    write_snapshots(sources['snapshot_path'], items)
    assert account(sources)['percent_left'] == 50
    assert account(sources)['observed_at'] == iso(NOW - 60)
    assert account(sources, 'codex')['percent_left'] == 70
    assert account(sources, 'codex')['observed_at'] == iso(NOW - 60)
    assert account(sources, 'kimi')['observed_at'] == iso(NOW)


def test_scale_fixture_reduction_is_bounded_by_recent_provider_history(sources):
    items = [snapshot(NOW - (10000 - index) * 300, 50) for index in range(10001)]
    write_snapshots(sources['snapshot_path'], items)
    assert account(sources)['expires_unused_pct'] == 50
    summary = json.loads(sources['cache_path'].read_text())['points']
    assert all(len(points) <= 289 for points in summary.values())
