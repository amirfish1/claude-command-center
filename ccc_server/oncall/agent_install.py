"""Agent entry points. No configuration changes at import time."""
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]


def local_url(base):
    parsed = urlsplit(base or '')
    return (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
            and not parsed.username and not parsed.password and parsed.path in ('', '/'))


def ready(base):
    if not local_url(base):
        return False
    try:
        with urllib.request.urlopen(base + '/api/health', timeout=.3) as res:
            return res.status == 200
    except OSError:
        return False


def service(base=None):
    """Start the normal server without the browser-opening run.sh launcher."""
    import fcntl
    state = Path(os.environ.get('CCC_STATE_DIR') or Path.home() / '.claude/command-center')
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'oncall-agent.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        candidates = [base]
        for filename in (state / 'oncall-agent.url', Path.home() / '.claude/command-center/port.txt'):
            try:
                candidates.append(filename.read_text().strip())
            except OSError:
                pass
        for candidate in candidates:
            if ready(candidate):
                return candidate
        if base and not local_url(base):
            raise ValueError('Agent service requires an http://127.0.0.1 address.')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        env = dict(os.environ, CCC_BIND_HOST='127.0.0.1')
        with (state / 'oncall-agent.log').open('ab') as log:
            proc = subprocess.Popen([sys.executable, str(REPO / 'server.py'), '--host',
                                     '127.0.0.1', '--port', str(port)],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    env=env, start_new_session=True)
        # One bounded startup observer, never inside a synchronous hook.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready(url):
                (state / 'oncall-agent.url').write_text(url)
                return url
            if proc.poll() is not None:
                break
            time.sleep(.2)
        proc.terminate()
        proc.wait(timeout=5)
        raise RuntimeError('CCC did not start; inspect oncall-agent.log in CCC state.')


def install_codex():
    """Merge lifecycle hooks without touching config.toml or existing handlers."""
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    home.mkdir(parents=True, exist_ok=True)
    target = home / 'hooks.json'
    doc = json.loads(target.read_text()) if target.exists() else {}
    hooks = doc.setdefault('hooks', {})
    script = REPO / 'plugins/oncall/scripts/hook.py'
    for event in ('SessionStart', 'Stop', 'PermissionRequest', 'PostToolUse'):
        handler = {'hooks': [{'type': 'command', 'command':
            f'{shlex.quote(sys.executable)} {shlex.quote(str(script))} {event}', 'timeout': 3}]}
        rows = hooks.setdefault(event, [])
        if handler not in rows:
            rows.append(handler)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(doc, indent=2) + '\n')
    temporary.replace(target)
    print('Installed On-call hooks; review and trust them in Codex /hooks, then run ccc oncall setup.')
    return 0
