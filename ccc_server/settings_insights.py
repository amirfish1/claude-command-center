"""Small, read-only projections for Settings and a scoped queue-routing edit."""
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
import re
import tempfile
import stat
import threading
from functools import wraps


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def model_usage(records, now):
    """Count distinct recorded starts, never last-active timestamps or resumes."""
    starts = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        sid = row.get('session_id') or row.get('resumed_sid')
        if not sid:
            continue
        value = row.get('spawned_at')
        try:
            stamp = float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError, OverflowError):
            continue
        engine = row.get('engine') or 'unknown'
        model = row.get('model') or 'Unrecorded model'
        if engine == 'claude' and re.match(r'^(opus|sonnet|haiku|fable)-', model):
            model = 'claude-' + model
        if sid not in starts or stamp < starts[sid][0]:
            starts[sid] = (stamp, engine, model)
    counts = Counter((engine, model) for stamp, engine, model in starts.values() if now - 7 * 86400 <= stamp <= now)
    return {'days': 7, 'total': sum(counts.values()), 'models': [
        {'engine': engine, 'model': model, 'count': count}
        for (engine, model), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))],
        'coverage': 'Indexed local Claude and Codex transcripts plus retained CCC launch history. Unrecorded initial models are shown separately.'}


def queue_overview(config, defaults, resolve=None):
    rows = []
    for name, conf in sorted(config.items()):
        if not isinstance(conf, dict) or conf.get('archived'):
            continue
        engine = conf.get('engine') or defaults.get('worker_engine') or 'WatchTower default'
        model = conf.get('model') or defaults.get('worker_model') or (defaults.get('models') or {}).get(engine) or 'Engine default'
        effort = conf.get('effort') or defaults.get('worker_reasoning_effort') or 'Engine default'
        if resolve:
            engine, model, effort = resolve(name)
        rows.append({'queue': name, 'auto_drain': bool(conf.get('auto_drain')),
                     'engine': engine, 'model': model or 'Engine default', 'effort': effort or 'Engine default',
                     'workers': conf.get('desired_workers', 1),
                     'pins': {k: conf[k] for k in ('engine', 'model', 'effort') if conf.get(k)},
                     'fallback': conf.get('fallback_to_default_worker')})
    return {'ok': True, 'queues': rows, 'revision': revision(config), 'defaults_revision': revision(defaults),
            'worker_default': {'engine': defaults.get('worker_engine') or 'WatchTower default',
                               'model': defaults.get('worker_model') or (defaults.get('models') or {}).get(defaults.get('worker_engine')) or 'Engine default',
                               'effort': defaults.get('worker_reasoning_effort') or 'Engine default'}}


def apply_worker_defaults(path, payload, defaults):
    """Clear only routing pins on the reviewed set; preserve all other fields.

    Optimistic revisions reject previews made before another settings edit.
    A unique temporary file and replace publish the whole batch together.
    """
    if payload.get('scope') not in ('auto', 'all'):
        raise ValueError('Choose auto-drain queues or all active queues')
    with open(path) as handle:
        config = json.load(handle)
    if payload.get('revision') != revision(config) or payload.get('defaults_revision') != revision(defaults):
        raise ValueError('Settings changed since this preview. Refresh and preview again.')
    if not defaults.get('worker_engine'):
        raise ValueError('Select and save a worker engine in Session defaults first.')
    changed = []
    for name, conf in config.items():
        if not isinstance(conf, dict) or conf.get('archived') or (payload['scope'] == 'auto' and not conf.get('auto_drain')):
            continue
        if any(conf.get(k) for k in ('engine', 'model', 'effort')):
            changed.append(name)
        for key in ('engine', 'model', 'effort'):
            conf.pop(key, None)
    fd, tmp = tempfile.mkstemp(prefix='queue-routing-', suffix='.tmp', dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(fd, 'w') as handle:
            json.dump(config, handle, indent=2)
        if revision(json.loads(path.read_text())) != payload.get('revision'):
            raise ValueError('Queue settings changed during apply. Refresh and preview again.')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return changed


_TRANSCRIPT_STARTS = {}


def transcript_starts(rows, now):
    """Read bounded transcript headers, cached by file identity and size.

    Never mistake last activity for the start time or the latest model for
    the initial model. Unknown initial models remain visibly unrecorded.
    """
    from pathlib import Path
    result = []
    seen = set()
    for row in rows:
        sid = row.get('session_id')
        filename = row.get('jsonl_path')
        engine = row.get('engine') or row.get('source')
        if not sid or sid in seen or not filename or engine not in ('claude', 'codex'):
            continue
        seen.add(sid)
        try:
            path = Path(filename)
            stat = path.stat()
            if stat.st_mtime < now - 7 * 86400:
                continue
            token = (str(path), stat.st_mtime_ns, stat.st_size)
            cached = _TRANSCRIPT_STARTS.get(sid)
            if cached and cached[0] == token:
                if cached[1]:
                    result.append(cached[1])
                continue
            stamp = None
            model = None
            with path.open('rb') as handle:
                for line in handle.read(512 * 1024).splitlines():
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    # Claude queue/file snapshots are not conversation starts.
                    kind = event.get('type')
                    if kind not in ('user', 'assistant', 'session_meta', 'turn_context'):
                        continue
                    payload = event.get('payload') or {}
                    if stamp is None:
                        stamp = event.get('timestamp') or payload.get('timestamp')
                    candidate = (event.get('message') or {}).get('model') if engine == 'claude' else payload.get('model')
                    if candidate and candidate != '<synthetic>':
                        model = candidate
                    if stamp and model:
                        break
            record = {'session_id': sid, 'spawned_at': stamp, 'engine': engine, 'model': model} if stamp else None
            _TRANSCRIPT_STARTS[sid] = (token, record)
            if record:
                result.append(record)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return result


_QUEUE_SETTINGS_LOCK = threading.RLock()


def serialize_queue_settings(method):
    """Serialize CCC queue writes and default changes against bulk previews.

    External WatchTower writers retain their existing concurrency contract;
    the revision recheck also detects their edits before publication.
    """
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        route = self.path.split('?', 1)[0]
        if route.startswith(('/api/queue/', '/api/wt/queue/', '/api/settings/queue-engines/')) or route == '/api/spawn-defaults':
            with _QUEUE_SETTINGS_LOCK:
                return method(self, *args, **kwargs)
        return method(self, *args, **kwargs)
    return wrapped
