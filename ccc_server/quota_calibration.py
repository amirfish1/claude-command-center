# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Historical API-dollar to weekly-quota estimates, using cached aggregates only."""

from bisect import bisect_left
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import threading
import time

LOOKBACK_DAYS = 28
BOUNDARY_TOLERANCE = 20 * 60
MAX_OBSERVATION_GAP = 6 * 3600
_CACHE = {}
_LOCK = threading.Lock()


def _epoch(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def provider_observations(snapshots, engine):
    """Normalize quota points; a carried-forward provider snapshot is not new data."""
    points = {}
    for snapshot in snapshots:
        ts = _epoch(snapshot.get('ts'))
        if ts is None:
            continue
        plan = ''
        if engine == 'codex':
            provider = snapshot.get('codex') or {}
            block = provider.get('weekly') or {}
            observed = _epoch(provider.get('snapshot_ts') or provider.get('fetched_at'))
            if observed is None or abs(ts - observed) > BOUNDARY_TOLERANCE:
                continue
            ts = observed
            pct = block.get('pct')
            plan = provider.get('plan_type') or ''
        else:
            block = snapshot.get('seven_day') or {}
            pct = block.get('utilization')
        reset = _epoch(block.get('resets_at'))
        if not _number(pct) or not 0 <= pct <= 100 or reset is None or reset <= ts:
            continue
        points[ts] = {'ts': ts, 'pct': pct, 'resets_at': reset, 'plan': plan}
    ordered = sorted(points.values(), key=lambda point: point['ts'])
    if engine == 'codex' and ordered:
        current_plan = ordered[-1].get('plan', '')
        ordered = [point for point in ordered if point.get('plan', '') == current_plan]
    return ordered


def calibrate_days(days, observations, *, now):
    """Dollar-weighted conversion over complete, observed, reset-free days."""
    points = sorted(observations, key=lambda point: point['ts'])
    times = [point['ts'] for point in points]
    samples = []
    excluded = 0

    def nearest(ts):
        index = bisect_left(times, ts)
        candidates = [i for i in (index - 1, index) if 0 <= i < len(times)]
        if not candidates:
            return None
        best = min(candidates, key=lambda i: abs(times[i] - ts))
        return best if abs(times[best] - ts) <= BOUNDARY_TOLERANCE else None

    previous_end = None
    for day in sorted(days, key=lambda item: item['start']):
        start, end, cost = day['start'], day['end'], day.get('cost_usd')
        if (not _number(cost) or cost <= 0 or end > now or end <= start
                or start < now - LOOKBACK_DAYS * 86400
                or (previous_end is not None and start < previous_end)):
            excluded += 1
            continue
        left, right = nearest(start), nearest(end)
        if left is None or right is None or left >= right:
            excluded += 1
            continue
        segment = points[left:right + 1]
        first, last = segment[0], segment[-1]
        valid = all(
            abs(point['resets_at'] - first['resets_at']) <= 60
            and point.get('plan', '') == first.get('plan', '')
            and point['pct'] < 100
            for point in segment
        ) and all(
            0 <= after['pct'] - before['pct']
            and after['ts'] - before['ts'] <= MAX_OBSERVATION_GAP
            for before, after in zip(segment, segment[1:])
        )
        if not valid:
            excluded += 1
            continue
        samples.append({'start': start, 'end': end, 'cost_usd': cost,
                        'pct': last['pct'] - first['pct']})
        previous_end = end
    dollars = sum(sample['cost_usd'] for sample in samples)
    pct = sum(sample['pct'] for sample in samples)
    available = len(samples) >= 2 and dollars >= 1 and pct >= 1
    return {
        'available': available,
        'pct_per_usd': pct / dollars if available else None,
        'sample_days': len(samples),
        'sampled_cost_usd': round(dollars, 6),
        'sampled_pct': round(pct, 6),
        'excluded_days': excluded,
        'period_start': samples[0]['start'] if samples else None,
        'period_end': samples[-1]['end'] if samples else None,
        'calibrated_at': now,
        'source': 'daily_observed',
        'reason': '' if available else 'Not enough matched cost and quota history',
        'assumption': 'Historical conversion assumes the same subscription plan and account.',
    }


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _daily_costs(directory, engine, now):
    """Finished daily records; aggregate summaries are fallback, never tail turns."""
    days = []
    suffix = '' if engine == 'claude' else '-' + engine
    for path in sorted(directory.glob('daily-*' + suffix + '.json')):
        data = _read_json(path)
        if data.get('engine') != engine or not data.get('final') or not data.get('ok'):
            continue
        start, end = data.get('day_start_epoch'), data.get('day_end_epoch')
        if not _number(start) or not _number(end) or start < now - LOOKBACK_DAYS * 86400:
            continue
        if any(row.get('unpriced_turns') for row in data.get('per_model', [])):
            continue
        days.append({'start': start, 'end': end, 'cost_usd': (data.get('totals') or {}).get('cost_usd')})
    # Aggregate summary buckets use the server's local dates, like the daily
    # digests. Keep the direct digest whenever periods overlap. The scope cutoff excludes
    # the first partially covered date and generated_at excludes the last.
    data = _read_json(directory / ('aggregate-all_7_days' + suffix + '.json'))
    payload = data.get('payload') or {}
    scope = payload.get('scope') or {}
    cutoff = scope.get('cutoff_epoch')
    generated = data.get('generated_at')
    if not payload.get('ok') or not _number(cutoff) or not _number(generated):
        return days
    if scope.get('engine', 'claude') != engine:
        return days
    for bucket in (payload.get('summary') or {}).get('daily', []):
        try:
            local_day = datetime.fromisoformat(str(bucket.get('hour', '')))
            start = local_day.timestamp()
            end = (local_day + timedelta(days=1)).timestamp()
        except (ValueError, OverflowError):
            continue
        if start < cutoff or end > generated or bucket.get('unpriced_turns'):
            continue
        if any(start < day['end'] and end > day['start'] for day in days):
            continue
        days.append({'start': start, 'end': end, 'cost_usd': bucket.get('cost_usd')})
    return days


def _compute_calibration(snapshot_path, throughput_dir, *, now=None):
    """Memoized, persisted calibration; never opens session transcripts."""
    now = time.time() if now is None else now
    snapshot_path, directory = Path(snapshot_path), Path(throughput_dir)
    key = (str(snapshot_path), str(directory))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and 0 <= now - cached['ts'] < 300:
            return cached['value']
        sources = [snapshot_path] + sorted(directory.glob('daily-*.json')) + [
            directory / ('aggregate-all_7_days' + suffix + '.json')
            for suffix in ('', '-codex')]
        signatures = []
        for path in sources:
            try:
                stat = path.stat()
                signatures.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                pass
        signature = hashlib.sha256(json.dumps([3, int(now // 86400), signatures]).encode()).hexdigest()
        saved_path = directory / 'quota-dollar-calibration.json'
        saved = _read_json(saved_path)
        if saved.get('signature') == signature and isinstance(saved.get('value'), dict):
            value = saved['value']
        else:
            snapshots = []
            try:
                with snapshot_path.open(encoding='utf-8') as stream:
                    for line in stream:
                        try:
                            item = json.loads(line)
                            if isinstance(item, dict):
                                stamp = _epoch(item.get('ts'))
                                if stamp is not None and stamp >= now - (LOOKBACK_DAYS + 1) * 86400:
                                    snapshots.append(item)
                        except ValueError:
                            continue
            except OSError:
                pass
            value = {
                engine: calibrate_days(_daily_costs(directory, engine, now),
                                       provider_observations(snapshots, engine), now=now)
                for engine in ('claude', 'codex')
            }
            try:
                directory.mkdir(parents=True, exist_ok=True)
                temp = saved_path.with_suffix('.tmp')
                temp.write_text(json.dumps({'signature': signature, 'value': value}), encoding='utf-8')
                temp.replace(saved_path)
            except OSError:
                pass
        _CACHE[key] = {'ts': now, 'value': value}
        return value


_REFRESHING = set()
_REFRESH_LOCK = threading.Lock()


def quota_cost_calibration(snapshot_path, throughput_dir, *, now=None, synchronous=False):
    """Return the last calibration immediately; refresh changed sources off-request."""
    now = time.time() if now is None else now
    if synchronous:
        return _compute_calibration(snapshot_path, throughput_dir, now=now)
    key = (str(snapshot_path), str(throughput_dir))
    with _REFRESH_LOCK:
        cached = _CACHE.get(key)
        if cached and 0 <= now - cached['ts'] < 300:
            return cached['value']
        value = cached['value'] if cached else None
        if value is None:
            saved = _read_json(Path(throughput_dir) / 'quota-dollar-calibration.json')
            stored = saved.get('value') or {}
            # Persisted ratios can span weeks, but cannot outlive the lookback.
            if stored and all(isinstance(row, dict) and _number(row.get('calibrated_at'))
                              and 0 <= now - row['calibrated_at'] < LOOKBACK_DAYS * 86400
                              for row in stored.values()):
                value = stored
        if key not in _REFRESHING:
            _REFRESHING.add(key)

            def refresh():
                try:
                    _compute_calibration(snapshot_path, throughput_dir, now=now)
                finally:
                    with _REFRESH_LOCK:
                        _REFRESHING.discard(key)

            threading.Thread(target=refresh, daemon=True, name='ccc-quota-calibration').start()
        return value or {engine: {'available': False, 'pct_per_usd': None,
                                 'reason': 'Historical calibration is loading'}
                         for engine in ('claude', 'codex')}
