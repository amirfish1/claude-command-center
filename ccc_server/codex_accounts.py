"""Route Codex sessions to persistent, account-scoped engine processes.

Only metadata crosses this boundary. Each process opens its own CLI home and
owns its transport, approval IDs, live event store, and native process handles.
"""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

_LOCK = threading.RLock()
_NEGATIVE = {}
_PROCESSES = {}


def _root():
    from ccc_server import core
    return Path(os.environ.get('CCC_CODEX_ACCOUNTS_DIR', str(core.COMMAND_CENTER_STATE_DIR / 'codex-accounts')))


def _db():
    root = _root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    con = sqlite3.connect(root / 'bindings.sqlite3', timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE IF NOT EXISTS accounts (key TEXT PRIMARY KEY, home TEXT UNIQUE, label TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, account_key TEXT NOT NULL)')
    return con


def default_home():
    override = os.environ.get('CCC_CODEX_DEFAULT_HOME')
    if not override:
        row = next((r for r in configured() if r.get('id') == 'local-codex'), None)
        override = row.get('codex_home') if row else os.environ.get('CODEX_HOME')
    return Path(override or str(Path.home() / '.codex')).expanduser().resolve()


def configured():
    from ccc_server import allowances
    with allowances._LOCK:
        return [dict(row) for row in allowances._load() if row.get('source') == 'codex']


def homes():
    """Include saved bindings even if an allowance is subsequently removed."""
    result = {str(default_home())}
    result.update(str(Path(row['codex_home']).expanduser().resolve()) for row in configured())
    result.update(row['home'] for row in _profiles())
    return [Path(home) for home in sorted(result)]


def _profiles():
    try:
        return json.loads((_root() / 'profiles.json').read_text())
    except FileNotFoundError:
        return []


def _register(home, label):
    home = str(Path(home).expanduser().resolve())
    key = hashlib.sha256(home.encode()).hexdigest()[:20]
    root = _root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _LOCK, (root / 'profiles.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with _db() as con:
            con.execute('INSERT INTO accounts VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET label=excluded.label',
                        (key, home, label))
            rows = [dict(row) for row in con.execute('SELECT * FROM accounts')]
        temp = root / 'profiles.json.tmp'
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(rows, stream)
        temp.replace(root / 'profiles.json')
    return {'key': key, 'home': home, 'label': label}


def bind(sid, account):
    if not sid:
        return
    with _db() as con:
        existing = con.execute('SELECT account_key FROM threads WHERE id=?', (sid,)).fetchone()
        if existing and existing['account_key'] != account['key']:
            raise ValueError('This session is already bound to a different Codex account')
        con.execute('INSERT OR IGNORE INTO threads VALUES (?, ?)', (sid, account['key']))
    _NEGATIVE.pop(sid, None)


def session_account(sid):
    if not isinstance(sid, str) or not sid:
        return None
    with _db() as con:
        row = con.execute('SELECT a.* FROM accounts a JOIN threads t ON t.account_key=a.key WHERE t.id=?', (sid,)).fetchone()
    if row:
        return dict(row)
    if time.monotonic() - _NEGATIVE.get(sid, -100) < 10:
        return None
    # Native agent children and threads created through the capabilities UI
    # may precede the spawn response. The CLI index establishes their owner.
    for home in homes():
        if home == default_home():
            continue
        for db in sorted(home.glob('state*.sqlite'), reverse=True):
            try:
                with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=.25) as con:
                    found = con.execute('SELECT 1 FROM threads WHERE id=?', (sid,)).fetchone()
            except sqlite3.Error:
                continue
            if found:
                label = next((r['account'] for r in configured() if Path(r['codex_home']).resolve() == home), 'Codex account')
                account = _register(home, label)
                bind(sid, account)
                return account
    if len(_NEGATIVE) > 4096:
        _NEGATIVE.clear()
    _NEGATIVE[sid] = time.monotonic()
    return None


def public_payload(sid=''):
    account = session_account(sid) if sid else None
    rows = [{'id': row['id'], 'label': row['account'],
             'current': Path(row['codex_home']).expanduser().resolve() == default_home()}
            for row in configured()]
    return {'ok': True, 'accounts': rows,
            'session_account': account['label'] if account else 'Codex · current account',
            'isolated': bool(account)}


def _client(account, start=True):
    from control_plane import ControlPlaneClient
    runtime = _root() / account['key']
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Short socket filenames stay below sockaddr_un's limit on macOS/Linux.
    client = ControlPlaneClient(path=runtime / 'w.sock', token_file=runtime / 'worker.token', timeout=90)
    probe = ControlPlaneClient(path=client.path, token_file=client.token_file, timeout=2)
    health = probe.request('health')
    from ccc_server import core
    wanted_hash = getattr(core, '_ccc_content_hash', None)
    loaded_hash = (health.get('worker') or {}).get('server_content_hash')
    stale = bool(start and wanted_hash and loaded_hash and wanted_hash != loaded_hash)
    if health.get('ok') and not stale:
        return client
    if not start:
        return None
    if not Path(account['home']).is_dir():
        raise ValueError('The selected Codex account home is unavailable; reconnect it in Allowances')
    with _LOCK, (runtime / 'start.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        health = probe.request('health')
        if health.get('ok'):
            loaded_hash = (health.get('worker') or {}).get('server_content_hash')
            if not (wanted_hash and loaded_hash and wanted_hash != loaded_hash):
                return client
            retired = probe.request('account.retire')
            if not retired.get('ok'):
                # Active turns and outstanding requests keep their owner.
                return client
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and probe.request('health').get('ok'):
                time.sleep(.05)
            if probe.request('health').get('ok'):
                raise ValueError('The isolated account backend is still restarting; retry shortly')
        env = dict(os.environ, CODEX_HOME=account['home'],
                   CCC_STATE_DIR=str(runtime), CCC_WORKER_SOCKET=str(client.path),
                   CCC_WORK_LEDGER=str(runtime / 'work.sqlite3'),
                   CCC_CODEX_ACCOUNT_KEY=account['key'], CCC_CODEX_RUNTIME_DIR=str(runtime),
                   CCC_CODEX_DEFAULT_HOME=str(default_home()),
                   CCC_CODEX_MANAGED_APP_SERVER='0')
        for name in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
            env.pop(name, None)
        repo = Path(__file__).resolve().parent.parent
        with (runtime / 'worker.log').open('ab') as log:
            proc = subprocess.Popen([sys.executable, str(repo / 'ccc_worker.py')],
                                    cwd=str(repo), env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, start_new_session=True)
        _PROCESSES[account['key']] = proc
        threading.Thread(target=proc.wait, daemon=True).start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if probe.request('health').get('ok'):
                return client
            if proc.poll() is not None:
                break
            time.sleep(.1)
        raise ValueError('The selected Codex account backend could not start')


def query_account(account, operation, args):
    # Root worker owns the durable work item; this worker owns only execution.
    result = _client(account).request('engine.query', {
        'engine': 'codex', 'operation': operation, 'args': args})
    if not result.get('available', True):
        return {'ok': False, 'code': 'codex_account_unavailable',
                'error': 'The selected Codex account backend is unavailable; the request was not retried on another account',
                'ambiguous': result.get('ambiguous', False)}
    return result


def request_session_id(operation, args):
    nested = args.get('data') if operation == 'client' else args.get('params')
    nested = nested if isinstance(nested, dict) else {}
    context = nested.get('context') if isinstance(nested.get('context'), dict) else {}
    return args.get('session_id') or args.get('sid') or nested.get('threadId') or context.get('thread_id')


def requires_isolation(operation, args):
    if args.get('codex_account_id'):
        return True
    sid = args.get('parent_session_id') if operation == 'spawn' else request_session_id(operation, args)
    return bool(session_account(sid))


def spawn_account(args):
    selected = args.get('codex_account_id')
    if selected == '__current__':
        return None
    if selected:
        row = next((r for r in configured() if r['id'] == selected), None)
        if not row:
            raise ValueError('That Codex account is no longer connected')
        home = Path(row['codex_home']).expanduser().resolve()
        return _register(home, row['account']) if home != default_home() else None
    return session_account(args.get('parent_session_id'))


def dedupe_matches(args, sid):
    requested = spawn_account(args)
    existing = session_account(sid)
    return (requested or {}).get('key') == (existing or {}).get('key')


def route(operation, args):
    """None means use the original worker; errors never mean fallback."""
    if os.environ.get('CCC_CODEX_ACCOUNT_KEY'):
        return None
    try:
        sid = request_session_id(operation, args)
        account = spawn_account(args) if operation == 'spawn' else session_account(sid)
        if not account:
            return None
        forwarded = {k: v for k, v in args.items() if k != 'codex_account_id'}
        if operation in ('spawn', 'resume', 'inject') and 'delivery_id' not in forwarded:
            from ccc_server import delivery
            bound = delivery.bound_id()
            if bound:
                forwarded['delivery_id'] = bound
        if operation == 'spawn':
            identity = query_account(account, 'rpc', {'method': 'account/read', 'params': {}})
            native = (identity.get('response') or {}).get('result') or {}
            if not identity.get('ok') or (native.get('account') or {}).get('type') != 'chatgpt':
                raise ValueError('Sign in to the selected ChatGPT account again in Allowances before starting a session')
        if operation == 'client' and args.get('action') == 'handover':
            raise ValueError('This session uses an isolated account; keep it in CCC rather than handing it to the shared desktop login')
        result = query_account(account, operation, forwarded)
        if result.get('ok') and operation == 'spawn' and result.get('session_id'):
            bind(result['session_id'], account)
        return dict(result, codex_account=account['label'])
    except (ValueError, OSError, sqlite3.Error) as exc:
        return {'ok': False, 'code': 'codex_account_unavailable', 'error': str(exc)}


def extra_states():
    if os.environ.get('CCC_CODEX_ACCOUNT_KEY'):
        return {}
    result = {}
    for account in _profiles():
        client = _client(account, start=False)
        if client:
            reply = client.request('engine.query', {'engine': 'codex', 'operation': 'thread_states', 'args': {}})
            result.update(reply.get('states') or {})
    return result
