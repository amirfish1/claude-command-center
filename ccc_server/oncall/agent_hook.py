"""CLI lifecycle adapter. Only opt-in Stop/PermissionRequest hooks can wait."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

import re

REPO = Path(__file__).resolve().parents[2]
MAX_WAIT = 540


def state_root():
    return Path(os.environ.get('CCC_STATE_DIR') or Path.home() / '.claude/command-center')


def settings():
    try:
        cfg = json.loads((state_root() / 'oncall-agent.json').read_text())
    except (OSError, ValueError):
        cfg = {}
    enabled = cfg.get('wait', False) is True
    if 'CCC_ONCALL_WAIT' in os.environ:
        enabled = os.environ['CCC_ONCALL_WAIT'] == '1'
    seconds = int(os.environ.get('CCC_ONCALL_WAIT_SECONDS', cfg.get('seconds', 180)))
    return enabled, max(1, min(MAX_WAIT, seconds))


def server_url():
    if os.environ.get('CCC_SERVER'):
        return os.environ['CCC_SERVER'].rstrip('/')
    for name in ('port.txt', 'oncall-agent.url'):
        try:
            return (state_root() / name).read_text().strip().rstrip('/')
        except OSError:
            pass
    return ''


def request(base, name, body, timeout=1):
    import urllib.request
    from urllib.parse import urlsplit
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    url = urlsplit(base)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or
            url.password or url.path or url.query or url.fragment):
        raise ValueError('Hook service must be on http://127.0.0.1.')
    req = urllib.request.Request(base + '/api/oncall/' + name,
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Origin': base})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=timeout) as response:
        result = json.loads(response.read(65536))
    if not result.get('ok'):
        raise ValueError('Hook request was refused.')
    return result


def load_hook(name):
    spec = importlib.util.spec_from_file_location('ccc_oncall_hook_impl', REPO / 'hooks' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.STATE_DIR = str(state_root())
    module.LIVE_STATE_DIR = str(state_root() / 'live-state')
    module.PORT_FILE = str(state_root() / 'port.txt')
    module.notify_session = lambda *a, **k: None
    module.request_auto_title = lambda *a, **k: None
    # Existing permission helper takes the path through live_dir().
    module.live_dir = lambda: str(state_root() / 'live-state')
    return module


def marker(sid, **values):
    target = state_root() / 'live-state' / (sid + '.json')
    try:
        data = json.loads(target.read_text())
    except (OSError, ValueError):
        data = {'session_id': sid}
    data.update(values)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.%d.tmp' % os.getpid())
    temporary.write_text(json.dumps(data))
    temporary.replace(target)


class Cache:
    def __init__(self):
        self.data = {}
    def kv_get(self, key):
        return self.data.get(key)
    def kv_set(self, key, value):
        self.data[key] = value


def wait_reply(event, data, text, seconds):
    import signal
    base, sid = server_url(), data['session_id']
    waiting = request(base, 'hook-register', {'session_id': sid, 'event': event,
                                            'text': text[:1800], 'seconds': seconds})
    body = {'session_id': sid, 'wait_id': waiting['wait_id'], 'token': waiting['token']}
    def interrupted(*_):
        raise InterruptedError('Hook canceled')
    prior = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        prior[signum] = signal.signal(signum, interrupted)
    try:
        reply = request(base, 'hook-wait', body, timeout=seconds + 2)
        answer = reply.get('answer')
        if answer is None:
            return
        if event == 'PermissionRequest':
            if answer not in ('allow', 'deny'):
                return
            decision = {'behavior': answer}
            if answer == 'deny':
                decision['message'] = 'Denied by the owner through CCC On-call.'
            print(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'decision': decision}}))
        else:
            print(json.dumps({'decision': 'block', 'reason': 'Owner reply from CCC On-call: ' + answer}))
    finally:
        for signum, handler in prior.items():
            signal.signal(signum, handler)
        try:
            request(base, 'hook-cancel', body, timeout=.5)
        except Exception:
            pass


def run(event, data, executable):
    sid = data.get('session_id', '')
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', sid):
        return
    if event == 'SessionStart':
        import subprocess
        subprocess.Popen([executable, 'oncall', 'agent-service'], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return
    if event in ('UserPromptSubmit', 'SessionEnd'):
        marker(sid, oncall_hook_wait=False, oncall_notify_idle=False, status='active')
        request(server_url(), 'hook-cancel', {'session_id': sid}, timeout=.5)
        return
    if event == 'Notification':
        kind = data.get('notification_type') or data.get('type')
        if kind == 'idle_prompt':
            event = 'Stop'
        elif kind != 'permission_prompt':
            return
        data['type'] = kind
        # Notification cannot return continuation/approval output.
        may_wait = False
    else:
        may_wait = True
    name = {'Stop': 'stop', 'PostToolUse': 'post-tool-use',
            'PermissionRequest': 'permission-request', 'Notification': 'notification'}.get(event)
    if not name:
        return
    module = load_hook(name)
    enabled, seconds = settings()
    if event == 'PermissionRequest':
        tool, tin = data.get('tool_name', ''), data.get('tool_input', {})
        if not isinstance(tool, str) or not tool or not isinstance(tin, dict):
            return
        if tool in module.SKIP_TOOLS:
            return  # interactive question tools need their own answer schema
        text = tool + ': ' + (module.describe_call(tool, tin) or 'Permission requested')
        if enabled and may_wait:
            wait_reply(event, data, text, seconds)
            return
        sys.path.insert(0, str(REPO / 'hooks'))
        import _asks
        _asks.record(str(state_root() / 'live-state'), sid, tool, tin, data.get('transcript_path', ''))
        module.raise_badge(sid, tool, tin)
        return
    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps(data))
        module.main()
    finally:
        sys.stdin = old_stdin
    if event == 'Stop':
        marker(sid, oncall_notify_idle=True, oncall_hook_wait=False)
        # Never loop on our own continuation. A new human turn resets this.
        if enabled and may_wait and not data.get('stop_hook_active'):
            from .session_signals import from_message, question
            text = data.get('last_assistant_message')
            found = (from_message(text) if isinstance(text, str) else
                     question(Cache(), sid, data.get('transcript_path')))
            if found:
                # Suppress the normal 120s observer for this already-handled turn.
                marker(sid, oncall_hook_wait=True)
                wait_reply(event, data, found['body'], seconds)
