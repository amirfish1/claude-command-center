from ccc_server import quota_calibration as qc


def observations(engine, start, values, resets=900000):
    return [{"ts": start + index * 3600, "pct": pct, "resets_at": resets, "plan": "pro"}
            for index, pct in enumerate(values)]


def day(start, cost):
    return {"start": start, "end": start + 86400, "cost_usd": cost}


def test_ratio_pools_dollars_instead_of_averaging_day_ratios():
    points = observations('claude', 0, [i / 2 for i in range(49)])
    result = qc.calibrate_days([day(0, 100), day(86400, 300)], points, now=200000)
    assert result['available']
    assert result['pct_per_usd'] == 24 / 400
    assert result['sample_days'] == 2
    assert result['sampled_cost_usd'] == 400
    assert result['sampled_pct'] == 24


def test_reset_inside_day_is_rejected_even_if_end_pct_is_higher():
    points = observations('claude', 0, [i for i in range(49)])
    points[12]['pct'] = 0
    result = qc.calibrate_days([day(0,100), day(86400,100)], points, now=200000)
    assert not result['available']
    assert result['sample_days'] == 1


def test_zero_increase_with_spend_still_contributes_to_denominator():
    points = observations('claude', 0, [0]*25 + list(range(1,25)))
    result = qc.calibrate_days([day(0,100), day(86400,300)], points, now=200000)
    assert result['pct_per_usd'] == 24 / 400


def test_sparse_observations_and_saturation_do_not_calibrate():
    points = [{'ts':0,'pct':0,'resets_at':900000}, {'ts':86400,'pct':10,'resets_at':900000}]
    assert not qc.calibrate_days([day(0,100)],points,now=200000)['available']
    saturated = observations('claude',0,[100]*49)
    assert not qc.calibrate_days([day(0,100),day(86400,100)],saturated,now=200000)['available']


def test_provider_snapshot_time_prevents_reusing_stale_codex_percent():
    snaps = [{'ts':'2026-09-01T00:00:00Z',
              'seven_day':{'utilization':20,'resets_at':'2026-09-04T00:00:00Z'},
              'codex':{'snapshot_ts':'2026-08-30T00:00:00Z',
                       'weekly':{'pct':5,'resets_at':'2026-09-04T00:00:00Z'}}}]
    assert len(qc.provider_observations(snaps,'claude')) == 1
    assert qc.provider_observations(snaps,'codex') == []


def test_different_plan_and_reset_windows_are_excluded():
    points = observations('codex',0,list(range(49)))
    points[12]['plan'] = 'different'
    points[36]['resets_at'] = 990000
    assert not qc.calibrate_days([day(0,100),day(86400,100)],points,now=200000)['available']


def test_aggregate_cost_days_follow_local_calendar_and_exclude_partial_days(tmp_path, monkeypatch):
    import json
    import os
    import time
    from datetime import datetime

    previous_tz = os.environ.get('TZ')
    monkeypatch.setenv('TZ', 'America/Los_Angeles')
    time.tzset()
    try:
        first = datetime(2026,9,1).timestamp()
        last = datetime(2026,9,4,12).timestamp()
        payload = {'generated_at':last,'payload':{'ok':True,
                   'scope':{'engine':'codex','cutoff_epoch':first+3600},
                   'summary':{'daily':[{'hour':f'2026-09-0{i}','cost_usd':10,'unpriced_turns':0}
                                       for i in range(1,5)]}}}
        (tmp_path/'aggregate-all_7_days-codex.json').write_text(json.dumps(payload))
        days = qc._daily_costs(tmp_path,'codex',last)
        assert [d['start'] for d in days] == [datetime(2026,9,2).timestamp(),datetime(2026,9,3).timestamp()]
    finally:
        if previous_tz is None:
            monkeypatch.delenv('TZ')
        else:
            monkeypatch.setenv('TZ',previous_tz)
        time.tzset()


def test_loader_reuses_persisted_result_without_rereading_snapshots(tmp_path, monkeypatch):
    snapshots = tmp_path/'snapshots.jsonl'
    snapshots.write_text('')
    first = qc.quota_cost_calibration(snapshots,tmp_path,now=200000,synchronous=True)
    qc._CACHE.clear()
    monkeypatch.setattr(qc,'provider_observations',lambda *_: (_ for _ in ()).throw(AssertionError('reparsed')))
    assert qc.quota_cost_calibration(snapshots,tmp_path,now=200001,synchronous=True) == first


def test_day_without_matching_boundary_is_not_assumed_zero():
    points = observations('claude',0,list(range(49)))
    points = [p for p in points if p['ts'] != 86400]
    result = qc.calibrate_days([day(0,100),day(86400,100)],points,now=200000)
    assert result['sample_days'] == 0
    assert result['pct_per_usd'] is None


def test_direct_daily_record_does_not_hide_nonoverlapping_aggregate_history(tmp_path):
    import json
    from datetime import datetime
    first = datetime(2026,9,1).timestamp()
    third = datetime(2026,9,3).timestamp()
    last = datetime(2026,9,4).timestamp()
    (tmp_path/'daily-2026-09-03-codex.json').write_text(json.dumps({
        'ok':True,'engine':'codex','final':True,'day_start_epoch':third,
        'day_end_epoch':last,'totals':{'cost_usd':30},'per_model':[]}))
    (tmp_path/'aggregate-all_7_days-codex.json').write_text(json.dumps({
        'generated_at':last,'payload':{'ok':True,
        'scope':{'engine':'codex','cutoff_epoch':first},
        'summary':{'daily':[{'hour':f'2026-09-0{i}','cost_usd':10} for i in range(1,4)]}}}))
    days = qc._daily_costs(tmp_path,'codex',last)
    assert len(days) == 3
    assert sum(d['cost_usd'] for d in days) == 50


def test_request_returns_before_background_calibration_finishes(tmp_path, monkeypatch):
    import threading
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def compute(*args, **kwargs):
        entered.set()
        release.wait(2)
        finished.set()
    monkeypatch.setattr(qc,'_compute_calibration',compute)
    try:
        result = qc.quota_cost_calibration(tmp_path/'missing',tmp_path,now=200000)
        assert entered.wait(1)
        assert not finished.is_set()
        assert not result['claude']['available']
    finally:
        release.set()
        assert finished.wait(1)
