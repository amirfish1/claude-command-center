from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import threading
import time

from ccc_server import core as _core
from ccc_server.quota_calibration import quota_cost_calibration

HISTORY_HOURS = 24
BURN_HOURS = 6
MIN_BURN_SECONDS = 1800
MAX_GAP_SECONDS = 3600
FORECAST_FRESH_SECONDS = 900
STALE_SECONDS = 86400
CALIBRATION_MAX_SECONDS = 28 * 86400
_HISTORY_CACHE = {}
_HISTORY_LOCK = threading.Lock()
_JSON_CACHE = {}
_JSON_LOCK = threading.Lock()
_LABELS = {'claude': 'Claude', 'codex': 'Codex', 'kimi': 'Kimi',
           'devin': 'Devin', 'free_router': 'Free models'}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _epoch(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def _json(path):
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return {}
    signature = (stat.st_mtime_ns, stat.st_size)
    with _JSON_LOCK:
        cached = _JSON_CACHE.get(str(path))
        if cached and cached['signature'] == signature:
            return cached['value']
        try:
            value = _mapping(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError, UnicodeError):
            value = {}
        if len(_JSON_CACHE) >= 16:
            _JSON_CACHE.clear()
        _JSON_CACHE[str(path)] = {'signature': signature, 'value': value}
        return value


def _valid_history(value):
    if not isinstance(value, dict):
        return False
    for key, points in value.items():
        if key not in {engine + ':' + window for engine in ('claude', 'codex', 'kimi')
                       for window in ('weekly', 'session')} or not isinstance(points, list):
            return False
        previous = None
        for point in points:
            if not isinstance(point, dict) or any(not _number(point.get(field))
                                                  for field in ('ts', 'pct', 'reset')):
                return False
            if (not 0 <= point['pct'] <= 100 or not isinstance(point.get('plan'), str)
                    or not isinstance(point.get('stale'), bool)
                    or previous is not None and point['ts'] <= previous):
                return False
            previous = point['ts']
    return True


def _signature(paths):
    signatures = []
    for path in paths:
        try:
            stat = path.stat()
            signatures.append([str(path), stat.st_mtime_ns, stat.st_size])
        except OSError:
            signatures.append([str(path), None, None])
    return hashlib.sha256(json.dumps([1, signatures]).encode()).hexdigest()


def _point(block, observed, *, engine, plan='', stale=False):
    block = _mapping(block)
    pct = block.get('utilization') if engine == 'claude' else block.get('pct')
    reset = _epoch(block.get('resets_at'))
    if observed is None or reset is None or not _number(pct) or not 0 <= pct <= 100:
        return None
    return {'ts': observed, 'pct': pct, 'reset': reset, 'plan': str(plan or ''),
            'stale': bool(stale)}


def _normalize(snapshots, legacy):
    """The persisted provider windows used by ccc quota, without pace scans."""
    points = {}

    def add(engine, window, block, observed, **kwargs):
        point = _point(block, observed, engine=engine, **kwargs)
        if point is not None:
            points.setdefault(engine + ':' + window, {})[observed] = point

    for snapshot in snapshots:
        snapshot = _mapping(snapshot)
        observed = _epoch(snapshot.get('ts'))
        for window, key in (('weekly', 'seven_day'), ('session', 'five_hour')):
            add('claude', window, snapshot.get(key), observed)
        for engine in ('codex', 'kimi'):
            provider = _mapping(snapshot.get(engine))
            observed_provider = _epoch(provider.get('snapshot_ts') or provider.get('fetched_at'))
            if observed is None or observed_provider is None or observed_provider > observed + 60:
                continue
            for window in ('weekly', 'session'):
                add(engine, window, provider.get(window), observed_provider,
                    plan=provider.get('plan_type'), stale=provider.get('stale', False))
    legacy = _mapping(legacy)
    observed = _epoch(legacy.get('fetched_at'))
    for window, key in (('weekly', 'seven_day'), ('session', 'five_hour')):
        existing = points.get('claude:' + window, {})
        if observed is not None and (not existing or max(existing) < observed):
            add('claude', window, _mapping(legacy.get('usage')).get(key), observed)
    result = {}
    for key, by_time in points.items():
        newest = max(by_time)
        result[key] = sorted((p for ts, p in by_time.items()
                              if ts >= newest - HISTORY_HOURS * 3600), key=lambda p: p['ts'])
    return result


def _load_history(snapshot_path, legacy_path, cache_path):
    """Single-flight, (mtime,size)-keyed history reduction persisted across restarts."""
    key = (str(snapshot_path), str(legacy_path), str(cache_path))
    signature = _signature((snapshot_path, legacy_path))
    with _HISTORY_LOCK:
        cached = _HISTORY_CACHE.get(key)
        if cached and cached['signature'] == signature:
            return cached['points']
        saved = _json(cache_path)
        if saved.get('signature') == signature and _valid_history(saved.get('points')):
            points = saved['points']
        else:
            snapshots = []
            try:
                with snapshot_path.open(encoding='utf-8') as stream:
                    for line in stream:
                        try:
                            item = json.loads(line)
                            if isinstance(item, dict):
                                snapshots.append(item)
                        except ValueError:
                            continue
            except OSError:
                pass
            points = _normalize(snapshots, _json(legacy_path))
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temp = cache_path.with_suffix('.tmp')
                temp.write_text(json.dumps({'signature': signature, 'points': points}), encoding='utf-8')
                temp.replace(cache_path)
            except OSError:
                pass
        if len(_HISTORY_CACHE) >= 8:
            _HISTORY_CACHE.clear()
        _HISTORY_CACHE[key] = {'signature': signature, 'points': points}
        return points


def _burn_rate(points, now):
    """Quota percentage points per wall-clock hour in the last reset-free six hours."""
    if not points:
        return None
    latest = points[-1]
    if latest['stale'] or not 0 <= now - latest['ts'] <= FORECAST_FRESH_SECONDS:
        return None
    segment = [latest]
    for previous in reversed(points[:-1]):
        current = segment[-1]
        if (previous['ts'] < latest['ts'] - BURN_HOURS * 3600
                or abs(previous['reset'] - latest['reset']) > 60
                or previous['plan'] != latest['plan'] or previous['stale']
                or current['pct'] < previous['pct']
                or current['ts'] - previous['ts'] > MAX_GAP_SECONDS):
            break
        segment.append(previous)
    elapsed = latest['ts'] - segment[-1]['ts']
    if elapsed < MIN_BURN_SECONDS:
        return None
    return (latest['pct'] - segment[-1]['pct']) * 3600 / elapsed


def _conversion(calibration, key, now):
    calibration = _mapping(calibration)
    ratio = calibration.get(key)
    stamp = calibration.get('calibrated_at')
    if (not _number(ratio) or ratio <= 0 or not _number(stamp)
            or not 0 <= now - stamp <= CALIBRATION_MAX_SECONDS):
        return None
    if key == 'pct_per_usd' and not calibration.get('available'):
        return None
    return ratio


def _token_conversion(paths, now):
    for path in paths:
        calibration = _json(path)
        pct, tokens = calibration.get('real_pct'), calibration.get('tokens')
        if _number(pct) and 0 < pct <= 100 and _number(tokens) and tokens > 0:
            ratio = _conversion({**calibration, 'pct_per_token': pct / tokens}, 'pct_per_token', now)
            if ratio is not None:
                return ratio
    return None


def _window(points, *, window, now, dollar_ratio=None, token_ratio=None):
    row = {'window': window, 'available': False, 'stale': False,
           'percent_used': None, 'percent_left': None, 'resets_at': None,
           'hours_to_reset': None, 'observed_at': None, 'burn_rate_pct_per_hour': None,
           'expires_unused_pct': None, 'expires_unused_usd': None,
           'expires_unused_tokens': None, 'forecast_available': False,
           'leftover_candidate': False, 'reason': 'No account usage has been recorded yet.'}
    if not points:
        return row
    latest = points[-1]
    age = now - latest['ts']
    hours = max(0, (latest['reset'] - now) / 3600)
    stale = latest['stale'] or age < 0 or age > STALE_SECONDS or latest['reset'] <= now
    row.update(available=True, stale=stale, percent_used=latest['pct'],
               percent_left=round(100 - latest['pct'], 4), resets_at=_iso(latest['reset']),
               hours_to_reset=round(hours, 4), observed_at=_iso(latest['ts']))
    if stale:
        row['reason'] = 'This usage is out of date. Wait for the next account update.'
        return row
    rate = _burn_rate(points, now)
    if rate is None:
        row['reason'] = 'More recent usage history is needed to estimate what will be left.'
        return row
    unused = max(0, 100 - latest['pct'] - rate * (latest['reset'] - latest['ts']) / 3600)
    row.update(burn_rate_pct_per_hour=round(rate, 6), expires_unused_pct=round(unused, 4),
               forecast_available=True, reason='',
               leftover_candidate=unused >= 20 and 0 < hours <= 24)
    if window == 'weekly':
        if dollar_ratio is not None and _number(unused / dollar_ratio):
            row['expires_unused_usd'] = round(unused / dollar_ratio, 2)
        if token_ratio is not None and _number(unused / token_ratio):
            row['expires_unused_tokens'] = round(unused / token_ratio)
    return row


def _contract_row(row, now):
    observed = _epoch(row['observed_at'])
    reset = _epoch(row['resets_at'])
    stale = row['stale'] or observed is not None and now - observed > 1800
    available = row['available']
    forecast = available and not stale
    engine = row['engine']
    return {'id': engine + ':default', 'engine': engine, 'account': 'default',
            'label': row['label'], 'available': available, 'stale': stale,
            'unlimited': engine == 'free_router',
            'percent_left': row['percent_left'] if available else None,
            'resets_at': int(reset) if available and reset is not None else None,
            'hours_to_reset': row['hours_to_reset'] if available else None,
            'burn_pct_per_hour': row['burn_rate_pct_per_hour'] if forecast else None,
            'projected_expiring_pct': row['expires_unused_pct'] if forecast else None,
            'expiring_usd_estimate': row['expires_unused_usd'] if forecast else None,
            'expiring_tokens_estimate': row['expires_unused_tokens'] if forecast else None,
            'reason': '' if available else str(row.get('reason') or ''),
            'source': 'free_router' if engine == 'free_router' else 'quota'}


def headroom_payload(*, now=None, snapshot_path=None, throughput_dir=None, cache_path=None,
                     legacy_path=None, token_paths=None, calibration=None):
    """GET /api/headroom. Only the current account per engine is observed.

    accounts[*] describes the weekly quota; windows includes session limits.
    Expiring dollars are estimated API-priced work, not a cash balance or
    refund. Null means unknown. No transcript discovery, subprocesses, or
    provider requests are added by this endpoint.
    """
    now = time.time() if now is None else now
    state = Path(getattr(_core, 'COMMAND_CENTER_STATE_DIR', Path.home() / '.claude' / 'command-center'))
    snapshot_path = Path(snapshot_path) if snapshot_path is not None else Path(
        getattr(_core, '_USAGE_SNAPSHOTS_FILE', state / 'usage' / 'usage-snapshots.jsonl'))
    throughput_dir = Path(throughput_dir) if throughput_dir is not None else Path(
        getattr(_core, '_THROUGHPUT_DISK_CACHE_DIR', Path.home() / '.cache' / 'ccc-throughput-cache'))
    cache_path = Path(cache_path) if cache_path is not None else state / 'usage' / 'headroom-history.json'
    legacy_path = Path(legacy_path) if legacy_path is not None else Path(
        getattr(_core, '_WEEKLY_PCT_FILE', Path.home() / '.cache' / 'claude-usage-pct.json'))
    token_paths = [Path(p) for p in token_paths] if token_paths is not None else [
        Path(getattr(_core, '_CCC_WEEKLY_CAL_FILE', state / 'usage' / 'calibration.json')),
        Path(getattr(_core, '_WEEKLY_CAL_FILE', Path.home() / '.cache' / 'claude-usage-cal.json'))]
    points = _load_history(snapshot_path, legacy_path, cache_path)
    calibration = quota_cost_calibration(snapshot_path, throughput_dir, now=now) if calibration is None else calibration
    token_ratio = _token_conversion(token_paths, now)
    accounts = []
    for engine, label in _LABELS.items():
        dollar_ratio = _conversion(_mapping(calibration).get(engine), 'pct_per_usd', now)
        weekly = _window(points.get(engine + ':weekly', []), window='weekly', now=now,
                         dollar_ratio=dollar_ratio, token_ratio=token_ratio if engine == 'claude' else None)
        row = {'id': engine + ':current', 'engine': engine, 'account_id': 'current',
               'label': label, **weekly, 'windows': [dict(weekly)],
               'estimate_note': 'Based on recent use. Dollar values estimate API-priced work, not cash or a refund.'}
        if engine == 'claude' and not weekly['available']:
            row['reason'] = ('CCC has no Claude plan usage reading on this machine. '
                             'Run `claude auth login` here to show what is left.')
        session = points.get(engine + ':session')
        if session:
            row['windows'].append(_window(session, window='session', now=now))
        if engine == 'devin':
            row['reason'] = 'Devin does not share account limits with CCC yet.'
            row['windows'] = []
        elif engine == 'free_router':
            row['reason'] = 'Free models do not share one quota. Each provider has its own limits.'
            row['windows'] = []
        accounts.append(row)
    generated_at = _iso(now)
    return {'ok': True, 'generated_at': generated_at,
            'rows': [_contract_row(row, now) for row in accounts],
            'accounts': accounts, 'updated_at': generated_at}
