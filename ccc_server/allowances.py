"""Account-scoped allowance ledger and isolated Codex quota reads.

Credentials remain in each CLI home; the ledger contains metadata only.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
import queue
import subprocess
import threading
import time
import uuid

_LOCK = threading.RLock()
_CACHE = {}


def _path():
    from ccc_server import core
    return core.COMMAND_CENTER_STATE_DIR / 'allowances.json'


def _load():
    try:
        data = json.loads(_path().read_text())
        if not isinstance(data, list):
            raise ValueError('Invalid allowance ledger')
        return data
    except FileNotFoundError:
        return [{'id': 'local-codex', 'account': 'Codex · current account',
                 'provider': 'Codex', 'kind': 'Coding', 'source': 'codex',
                 'codex_home': os.environ.get('CODEX_HOME', str(Path.home() / '.codex')),
                 'plan': '', 'notes': ''}]


def _number(value):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise ValueError('Amount must be a number')
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError('Amount must be a finite, nonnegative number')
    return result


def _epoch(value):
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError('Deadline must be an ISO timestamp')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Deadline must include a timezone')
    return stamp.timestamp()


def mutate(data):
    if not isinstance(data, dict):
        raise ValueError('Expected an object')
    with _LOCK:
        rows = _load()
        entry_id = data.get('id')
        old = next((r for r in rows if r['id'] == entry_id), None)
        if entry_id and old is None:
            raise ValueError('Entry no longer exists; refresh and try again')
        if data.get('delete'):
            rows = [r for r in rows if r['id'] != entry_id]
        else:
            row = {'id': entry_id or uuid.uuid4().hex}
            for key in ('account', 'provider', 'kind', 'plan', 'notes', 'unit', 'deadline_type'):
                value = data.get(key, '')
                if not isinstance(value, str) or len(value) > 1000:
                    raise ValueError('Text fields must be strings of at most 1000 characters')
                row[key] = value.strip()
            if not row['account'] or not row['provider']:
                raise ValueError('Account and provider are required')
            row['source'] = data.get('source', 'manual')
            if row['source'] not in ('manual', 'codex'):
                raise ValueError('Unknown reading source')
            if row['source'] == 'codex':
                home = data.get('codex_home', '')
                if not isinstance(home, str) or not home or len(home) > 4096:
                    raise ValueError('Codex home is required')
                home = Path(home).expanduser()
                if not home.is_absolute() or not home.is_dir():
                    raise ValueError('Codex home must be an existing absolute directory on this machine')
                if any(r['source'] == 'codex' and r['id'] != row['id'] and
                       Path(r['codex_home']).expanduser().resolve() == home.resolve() for r in rows):
                    raise ValueError('That Codex home is already connected')
                row['codex_home'] = str(home.resolve())
            else:
                row['remaining'] = _number(data.get('remaining'))
                row['deadline'] = data.get('deadline') or None
                _epoch(row['deadline'])
                if row['unit'] == '%' and row['remaining'] is not None and row['remaining'] > 100:
                    raise ValueError('Percentage must be between 0 and 100')
                if row['deadline_type'] not in ('reset', 'expiration'):
                    raise ValueError('Choose reset or expiration')
            row['updated_at'] = datetime.now(timezone.utc).isoformat()
            rows = [row if r['id'] == row['id'] else r for r in rows] if old else rows + [row]
            if len(rows) > 40:
                raise ValueError('At most 40 entries can be tracked')
        target = _path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix('.tmp')
        fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(rows, stream, indent=2)
        temp.replace(target)
        _CACHE.clear()
    return {'ok': True}


def _with_codex(home, action, timeout=15):
    """One bounded app-server process per home, never the fleet transport."""
    env = dict(os.environ, CODEX_HOME=home)
    # A global API key must not shadow a profile's ChatGPT login.
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
        env.pop(key, None)
    proc = subprocess.Popen(['codex', 'app-server'], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    messages = queue.Queue()

    def read():
        try:
            for line in proc.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    pass
        finally:
            messages.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    end = time.monotonic() + timeout

    def send(message):
        proc.stdin.write(json.dumps(message) + '\n')
        proc.stdin.flush()

    def request(ident, method, params):
        send({'id': ident, 'method': method, 'params': params})
        while True:
            message = messages.get(timeout=max(0, end - time.monotonic()))
            if message is None:
                raise ValueError('Codex reader stopped')
            if message.get('id') == ident:
                if 'error' in message:
                    raise ValueError('Codex quota read failed')
                return message

    try:
        request(1, 'initialize', {'clientInfo': {'name': 'ccc_allowances', 'version': '1.0'}})
        send({'method': 'initialized', 'params': {}})
        return action(request, messages)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        reader.join(timeout=1)
        proc.stdin.close()
        proc.stdout.close()


def _read_codex(home):
    def read(request, messages):
        account = request(2, 'account/read', {'refreshToken': False}).get('result', {}).get('account')
        if not isinstance(account, dict) or account.get('type') not in ('chatgpt', 'chatgptAuthTokens'):
            raise ValueError('ChatGPT login required for subscription quotas')
        response = request(3, 'account/rateLimits/read', {})
        from ccc_server.recall_usage import _codex_usage_from_account_rate_limits
        result = _codex_usage_from_account_rate_limits(response)
        if not result:
            raise ValueError('No Codex allowance returned')
        result['identity'] = account.get('email')
        return result
    return _with_codex(home, read)


def classify(row, now):
    """Expired reports never become renewed balances; unknowns cannot win."""
    deadline = _epoch(row.get('deadline'))
    remaining = row.get('remaining')
    if deadline is not None and deadline <= now:
        return 'needs_update' if row.get('deadline_type') == 'reset' else 'expired'
    if remaining is None:
        return 'unknown'
    if remaining <= 0:
        return 'empty'
    if deadline is None:
        return 'no_deadline'
    return 'available'


def payload(now=None):
    now = time.time() if now is None else now
    with _LOCK:
        entries = _load()
    rows = []
    for entry in entries:
        if entry['source'] == 'manual':
            rows.append(dict(entry))
            continue
        home = entry['codex_home']
        with _LOCK:
            cached = _CACHE.get(home)
            if cached is None or now - cached[0] >= 300:
                try:
                    cached = (now, _read_codex(home), None)
                except Exception:
                    cached = (now, None, 'No reading: check the Codex login in this home, then retry after five minutes.')
                _CACHE[home] = cached
        _, usage, error = cached
        if error:
            rows.append(dict(entry, remaining=None, error=error))
            continue
        for window in ('weekly', 'session'):
            block = usage.get(window)
            if block:
                rows.append(dict(entry, remaining=100 - block['pct'], unit='%',
                                 kind='Coding · ' + window, deadline=block['resets_at'],
                                 deadline_type='reset', observed_at=usage['fetched_at'],
                                 identity=usage.get('identity')))
    # A blocked short or weekly window blocks the whole account, even when
    # the other window still has room. Stale windows need a fresh read first.
    blocked = {r['id'] for r in rows if r['source'] == 'codex' and
               classify(r, now) in ('empty', 'needs_update')}
    for row in rows:
        row['status'] = classify(row, now)
        if row['id'] in blocked and row['status'] == 'available':
            row['status'] = 'blocked'
    rows.sort(key=lambda r: (r['status'] != 'available', _epoch(r.get('deadline')) or float('inf'), r['account']))
    best = next((r for r in rows if r['status'] == 'available'), None)
    return {'ok': True, 'entries': entries, 'rows': rows, 'recommended_id': best['id'] if best else None,
            'generated_at': datetime.fromtimestamp(now, timezone.utc).isoformat()}


_LOGINS = {}
_LOGIN_LOCK = threading.RLock()


def _login_snapshot(session):
    return {key: session[key] for key in (
        'session_id', 'status', 'verification_url', 'user_code', 'identity', 'error')}


def login_start(data):
    """Create an isolated CLI home; browser auth never replaces fleet auth."""
    if not isinstance(data, dict):
        raise ValueError('Expected an object')
    label = data.get('account')
    if not isinstance(label, str) or not label.strip() or len(label) > 120:
        raise ValueError('Enter an account label before signing in')
    if not isinstance(data.get('notes', ''), str) or len(data.get('notes', '')) > 1000:
        raise ValueError('Notes must be at most 1000 characters')
    entry_id = data.get('id')
    with _LOCK:
        old = next((r for r in _load() if r['id'] == entry_id), None)
    if entry_id and not old:
        raise ValueError('Entry no longer exists; refresh and try again')
    with _LOGIN_LOCK:
        if sum(s['status'] in ('starting', 'waiting') for s in _LOGINS.values()) >= 4:
            raise ValueError('Finish or cancel an existing sign-in first')
        # Keep a bounded history without removing active ceremonies.
        for key in list(_LOGINS):
            if len(_LOGINS) < 20:
                break
            if _LOGINS[key]['status'] not in ('starting', 'waiting'):
                del _LOGINS[key]
        ident = uuid.uuid4().hex
        session = dict(session_id=ident, status='starting', verification_url=None,
                       user_code=None, identity=None, error=None, cancel=threading.Event())
        _LOGINS[ident] = session
    from ccc_server import core
    home = core.COMMAND_CENTER_STATE_DIR / 'allowance-accounts' / ident
    try:
        home.mkdir(parents=True, mode=0o700)
        (home / 'config.toml').write_text('cli_auth_credentials_store = "file"\n')
    except OSError:
        with _LOGIN_LOCK:
            _LOGINS.pop(ident, None)
        raise

    def run():
        def authenticate(request, messages):
            result = request(2, 'account/login/start', {'type': 'chatgptDeviceCode'}).get('result', {})
            # Render only the documented OpenAI device URL, never arbitrary CLI links.
            url = result.get('verificationUrl')
            if url != 'https://auth.openai.com/codex/device' or not isinstance(result.get('userCode'), str):
                raise ValueError('Device sign-in unavailable')
            if session['cancel'].is_set():
                return False
            with _LOGIN_LOCK:
                session.update(status='waiting', verification_url=url, user_code=result['userCode'])
            end = time.monotonic() + 540
            while not session['cancel'].is_set() and time.monotonic() < end:
                try:
                    message = messages.get(timeout=1)
                except queue.Empty:
                    continue
                if message is None:
                    raise ValueError('Codex reader stopped')
                if message.get('method') != 'account/login/completed':
                    continue
                details = message.get('params') or {}
                if details.get('loginId') != result.get('loginId'):
                    continue
                if not details.get('success'):
                    raise ValueError('Sign-in did not complete')
                return True
            return False
        try:
            completed = _with_codex(str(home), authenticate)
            if not completed or session['cancel'].is_set():
                with _LOGIN_LOCK:
                    session.update(status='cancelled' if session['cancel'].is_set() else 'expired',
                                   user_code=None)
                return
            usage = _read_codex(str(home))
            # Preserve the existing manual report until authentication succeeds.
            entry = dict(old or {}, account=label.strip(), provider='Codex', kind='Coding',
                         source='codex', codex_home=str(home))
            entry['notes'] = data.get('notes', entry.get('notes', ''))
            entry['plan'] = str(data.get('plan') or entry.get('plan') or '')[:120]
            with _LOCK:
                if session['cancel'].is_set():
                    return
                current = next((r for r in _load() if r['id'] == entry_id), None)
                if old and current != old:
                    raise ValueError('Entry changed during sign-in')
                mutate(entry)
                _CACHE[str(home)] = (time.time(), usage, None)
            with _LOGIN_LOCK:
                session.update(status='connected', identity=usage.get('identity'), user_code=None)
        except Exception:
            with _LOGIN_LOCK:
                session.update(status='cancelled' if session['cancel'].is_set() else 'failed', user_code=None,
                               error='Sign-in could not complete. Enable device code login in ChatGPT Settings → Security, then retry. Existing entries were kept.')
    threading.Thread(target=run, daemon=True, name='allowance-sign-in').start()
    with _LOGIN_LOCK:
        return dict(ok=True, **_login_snapshot(session))


def login_status(data, cancel=False):
    if not isinstance(data, dict):
        raise ValueError('Expected an object')
    ident = data.get('session_id')
    if not isinstance(ident, str):
        raise ValueError('Sign-in session required')
    with _LOGIN_LOCK:
        session = _LOGINS.get(ident)
        if session is None:
            raise ValueError('Sign-in expired or dashboard restarted; start again')
        if cancel and session['status'] in ('starting', 'waiting'):
            session['cancel'].set()
            session.update(status='cancelled', user_code=None)
        return dict(ok=True, **_login_snapshot(session))
