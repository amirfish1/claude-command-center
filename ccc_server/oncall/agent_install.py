"""Agent entry points. No configuration changes at import time."""
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from ccc_server.paths import _is_pid_alive

REPO = Path(__file__).resolve().parents[2]


def local_url(base):
    try:
        parsed = urlsplit(base or '')
        return (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                and not parsed.username and not parsed.password and not parsed.query
                and not parsed.fragment and parsed.path in ('', '/'))
    except ValueError:
        return False


def ready(base):
    if not local_url(base):
        return False
    try:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(base.rstrip('/') + '/api/health', timeout=.3) as res:
            return res.status == 200
    except OSError:
        return False


def owned_service(state):
    """Reuse our recorded live process even when its HTTP thread is busy."""
    try:
        record = json.loads((state / 'oncall-agent-process.json').read_text())
        pid = int(record['pid'])
        if pid <= 1 or not local_url(record['url']):
            return None
        if not _is_pid_alive(pid):
            return None
        proc = Path('/proc') / str(pid) / 'cmdline'
        if proc.exists():
            argv = proc.read_bytes().decode().split('\0')
            if str(REPO / 'server.py') not in argv or '--port' not in argv:
                return None
            if argv[argv.index('--port') + 1] != str(urlsplit(record['url']).port):
                return None
        else:
            # macOS has no procfs. Inspect this one recorded pid, never kill it.
            command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='],
                                              text=True, timeout=2)
            if str(REPO / 'server.py') not in command or '--port ' + str(urlsplit(record['url']).port) not in command:
                return None
        return record['url']
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return None


def service(base=None):
    """Start the normal server without the browser-opening run.sh launcher."""
    import fcntl
    state = Path(os.environ.get('CCC_STATE_DIR') or Path.home() / '.claude/command-center')
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'oncall-agent.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        owned = owned_service(state)
        if owned and (not base or base == owned):
            return owned
        candidates = [base]
        for filename in (state / 'oncall-agent.url', state / 'port.txt'):
            try:
                candidates.append(filename.read_text().strip())
            except OSError:
                pass
        for candidate in candidates:
            if ready(candidate):
                return candidate
            if local_url(candidate):
                try:
                    with socket.create_connection(('127.0.0.1', urlsplit(candidate).port or 80), timeout=.3):
                        pass
                except OSError:
                    continue
                raise RuntimeError('A local listener is busy or is not CCC; no duplicate was started.')
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
                (state / 'oncall-agent-process.json').write_text(json.dumps({'pid': proc.pid, 'url': url}))
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
    script = home / 'ccc-oncall-hook.py'
    shutil.copyfile(REPO / 'plugins/oncall/scripts/hook.py', script)
    for event in ('SessionStart', 'Stop', 'PermissionRequest', 'PostToolUse', 'UserPromptSubmit', 'SessionEnd'):
        handler = {'hooks': [{'type': 'command', 'command':
            f'python3 {shlex.quote(str(script))} {event}', 'timeout': 600 if event in ('Stop', 'PermissionRequest') else 3}]}
        rows = hooks.setdefault(event, [])
        # Upgrade our old path-based entries, preserving unrelated handlers.
        for group in rows:
            group['hooks'] = [h for h in group.get('hooks', []) if not (
                'plugins/oncall/scripts/hook.py' in h.get('command', '') or
                'ccc-oncall-hook.py' in h.get('command', ''))]
        rows[:] = [group for group in rows if group.get('hooks')]
        rows.append(handler)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(doc, indent=2) + '\n')
    temporary.replace(target)
    print('Installed On-call hooks; review and trust them in Codex /hooks, then run ccc oncall setup.')
    return 0


def phone_wait(argv):
    import argparse
    from .agent_hook import state_root
    parser = argparse.ArgumentParser(prog='ccc oncall phone-wait')
    parser.add_argument('mode', choices=('on', 'off'))
    parser.add_argument('--seconds', type=int, default=180)
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 540:
        parser.error('--seconds must be between 1 and 540')
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'oncall-agent.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'wait': args.mode == 'on', 'seconds': args.seconds}) + '\n')
    temporary.replace(target)
    print(f'Phone wait {args.mode}; maximum {args.seconds} seconds per question or permission hook.')
    return 0


def cancel_wait(argv):
    import argparse
    from .agent_hook import request, server_url
    parser = argparse.ArgumentParser(prog='ccc oncall cancel-wait')
    parser.add_argument('session_id')
    args = parser.parse_args(argv)
    request(server_url(), 'hook-cancel', {'session_id': args.session_id})
    print('Canceled this session\'s phone wait; answer in the terminal.')
    return 0
