"""Version-one message attribution. Stdlib only; shared verbatim with CCC.

Evidence describes attribution, never authentication. Reading another local
application's journal is read-only. Monthly segments are never auto-pruned.
"""
from __future__ import annotations

import base64
import copy
import contextvars
try:
    import fcntl
except ImportError:  # Windows: append remains available without flock.
    fcntl = None
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import time
import uuid

from ccc_server.paths import _is_pid_alive

ENV = 'WT_MESSAGE_ENVELOPE'
HEADER = '[Message attribution v1: '
MAX_BYTES = 32768
MAX_HOPS = 20
BASES = ('runtime', 'peer_cred', 'claimed', 'unknown')
KINDS = ('session', 'service', 'human', 'unknown')
_INHERITED = (ENV, 'CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID', 'WT_SESSION_ID',
              'WT_WORKER', 'WT_WORKER_ID', 'WT_PARENT_SESSION_ID')
_cache = {}
_warned = set()
_current = contextvars.ContextVar("message_attribution", default=None)

def current():
    return copy.deepcopy(_current.get())

def during(attr, fn):
    token = _current.set(attr)
    try:
        return fn()
    finally:
        _current.reset(token)


def child_env(env=None):
    out = dict(os.environ if env is None else env)
    for key in _INHERITED:
        out.pop(key, None)
    return out


def enabled(app):
    return os.environ.get(app.upper() + '_ATTRIBUTION_EMIT', '1').lower() not in ('0', 'off', 'false')


def _ccc_state():
    return Path(os.environ.get('CCC_STATE_DIR') or Path.home() / '.claude/command-center')


def _wt_state():
    return Path(os.environ.get('WATCHTOWER_STATE_DIR') or Path.home() / '.watchtower')


def node_identity():
    """Prefer CCC's existing id; retain WT's earlier id as a local alias."""
    ccc = _read(_ccc_state() / 'node.json')
    path = _wt_state() / 'node.json'
    wt = _read(path)
    if ccc.get('node_id'):
        return str(ccc['node_id']), [str(wt['node_id'])] if wt.get('node_id') else []
    if wt.get('node_id'):
        return str(wt['node_id']), []
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete file atomically; first-time senders must not observe
    # another writer's empty file between exclusive open and JSON write.
    temporary = path.with_name('node.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps({'node_id': str(uuid.uuid4())}))
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return str(_read(path)['node_id']), []


def _read(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def local_node():
    try:
        return node_identity()[0]
    except (OSError, KeyError, ValueError):
        return ''


def author(kind='unknown', ident='', name='', engine='', node=''):
    return dict(kind=kind, id=str(ident), name_at_send=str(name), engine=str(engine), node=node or local_node())


def make(recipient='', *, who=None, basis='unknown', evidence=None, message_id=None):
    return dict(v=1, message_id=str(message_id or uuid.uuid4()),
                author=who or author(), basis=basis, evidence=dict(evidence or {}),
                intended_recipient={'id': str(recipient), 'node': local_node()},
                delivered_recipient=None, reply_to=None, relay=[],
                created_at=time.time(), transport_ids=[])


def service(ident, recipient='', name=''):
    return make(recipient, who=author('service', ident, name or ident), basis='runtime',
                evidence={'source': 'service_callsite'})


def validate(value):
    if not isinstance(value, dict) or value.get('v') != 1:
        raise ValueError('unsupported attribution envelope version')
    out = copy.deepcopy(value)
    if not isinstance(out.get('author'), dict) or out['author'].get('kind') not in KINDS:
        raise ValueError('invalid attribution author')
    if out.get('basis') not in BASES:
        raise ValueError('invalid attribution basis')
    try:
        uuid.UUID(str(out['message_id']))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError('invalid attribution message_id') from exc
    for key in ('evidence', 'intended_recipient'):
        if not isinstance(out.get(key), dict):
            raise ValueError('invalid attribution ' + key)
    for key in ('relay', 'transport_ids'):
        if not isinstance(out.get(key), list):
            raise ValueError('invalid attribution ' + key)
    for key in ('id', 'node', 'name_at_send', 'engine'):
        if not isinstance(out['author'].get(key, ''), str):
            raise ValueError('invalid author ' + key)
    if not all(isinstance(hop, dict) for hop in out['relay']):
        raise ValueError('invalid relay hop')
    if not all(isinstance(ident, str) for ident in out['transport_ids']):
        raise ValueError('invalid transport id')
    out['relay'] = out['relay'][-MAX_HOPS:]
    if len(json.dumps(out, ensure_ascii=True).encode()) > MAX_BYTES:
        raise ValueError('attribution envelope exceeds 32 KiB')
    return out


def consume_env():
    """CLI boundary only: remove the one-shot carrier before any subprocess."""
    raw = os.environ.pop(ENV, '')
    if not raw:
        return None
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError('attribution envelope exceeds 32 KiB')
    return validate(json.loads(raw))


def _nearest_engine():
    pid = os.getppid()
    for _ in range(64):
        try:
            if sys.platform.startswith('linux'):
                data = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
                cmd = Path('/proc/%d/cmdline' % pid).read_bytes().replace(b'\0', b' ').decode(errors='replace')
                parent = int(data[1])
            else:
                import subprocess
                row = subprocess.check_output(['ps', '-p', str(pid), '-o', 'ppid=,comm='], text=True).strip().split(None, 1)
                parent, cmd = int(row[0]), row[1]
            executable = cmd.split()[0].rsplit('/', 1)[-1]
            if executable.startswith('claude'):
                return 'claude'
            if executable.startswith('codex') or '/codex/' in cmd:
                return 'codex'
            if parent <= 1 or parent == pid:
                break
            pid = parent
        except (OSError, ValueError, IndexError):
            break
    return ''


def process_start_matches(pid, token):
    """Compare Linux clock ticks or the legacy UTC ps timestamp."""
    try:
        if token.isdigit() and sys.platform.startswith('linux'):
            return Path('/proc/%d/stat' % pid).read_text().rsplit(')',1)[1].split()[19] == token
        env = dict(os.environ, TZ='UTC')
        current = subprocess.check_output(['ps','-p',str(pid),'-o','lstart='],env=env,text=True,timeout=2)
        return ' '.join(current.split()) == ' '.join(token.split())
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False


def capture_cli(recipient='', *, claimed=''):
    """Only interactive CLI entry points may inspect inherited session ids."""
    if claimed:
        return make(recipient, who=author('session', claimed, claimed), basis='claimed', evidence={'source': 'argument'})
    candidates = {'claude': os.environ.get('CLAUDE_CODE_SESSION_ID', ''),
                  'codex': os.environ.get('CODEX_THREAD_ID', '')}
    nearest = _nearest_engine()
    populated = [(engine, sid) for engine, sid in candidates.items() if sid]
    engine = nearest if candidates.get(nearest) else (populated[0][0] if not nearest and len(populated) == 1 else '')
    sid = candidates.get(engine, '')
    if not sid:
        sid = os.environ.get('WT_SESSION_ID', '')
    if not sid:
        return make(recipient)
    env_name = {'claude': 'CLAUDE_CODE_SESSION_ID', 'codex': 'CODEX_THREAD_ID'}.get(engine, 'WT_SESSION_ID')
    result = make(recipient, who=author('session', sid, sid[:8], engine), basis='claimed', evidence={'source': 'env', 'variable': env_name, 'ancestor_engine': nearest})
    registry = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'sessions'
    for path in registry.glob('*.json'):
        row = _read(path)
        if row.get('sessionId') != sid:
            continue
        try:
            pid = int(row.get('pid') or 0)
            if not pid or path.stat().st_uid != os.getuid():
                continue
            if not _is_pid_alive(pid):
                continue
        except (OSError, ValueError):
            continue
        token = str(row.get('procStart') or '').strip()
        if token and not process_start_matches(pid, token):
            continue
        result['basis'] = 'runtime'
        result['evidence']['procStart'] = token
        result['author']['name_at_send'] = str(row.get('name') or sid[:8])
        result['evidence']['registry_pid'] = pid
        if row.get('messagingSocketPath'):
            result['reply_to'] = {'addr': 'uds:' + row['messagingSocketPath'], 'node': local_node()}
        break
    if result['basis'] == 'claimed':
        paths = [Path(os.environ.get('WATCHTOWER_AGENTS_FILE') or _wt_state() / 'agents.json'),
                 Path(os.environ.get('WATCHTOWER_WORKERS_FILE') or _wt_state() / 'workers.json'),
                 _ccc_state() / 'spawned-pids.json'] + list((_ccc_state() / 'spawn-markers').glob('*.json'))
        for path in paths:
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                raw = json.loads(path.read_text())
                if isinstance(raw, dict):
                    rows = [raw] if raw.get('session_id') else raw.get('workers') or raw.get('sessions') or list(raw.values())
                else:
                    rows = raw
                for row in rows if isinstance(rows, list) else []:
                    if not isinstance(row, dict) or row.get('session_id') != sid:
                        continue
                    pid = int(row.get('pid') or 0)
                    if pid:
                        if not _is_pid_alive(pid):
                            continue
                    elif path.name != 'agents.json':
                        continue
                    if pid and row.get('procStart') and not process_start_matches(pid, str(row['procStart'])):
                        continue
                    result['basis'] = 'runtime'
                    result['author']['name_at_send'] = str(row.get('name') or row.get('worker_id') or sid[:8])
                    result['author']['engine'] = str(row.get('engine') or result['author']['engine'])
                    result['evidence']['registry'] = path.name
                    break
            except (OSError, ValueError, TypeError):
                continue
    return result


def relay(attr, via, *, attested_mode='', delivered=None):
    out = validate(attr)
    hop = {'via': via, 'node': local_node(), 'at': time.time()}
    if attested_mode:
        hop['attested_mode'] = attested_mode
    out['relay'] = (out['relay'] + [hop])[-MAX_HOPS:]
    if delivered:
        out['delivered_recipient'] = {'id': str(delivered), 'node': local_node()}
    return out


def from_text(text):
    for line in str(text or '').splitlines()[:5]:
        if line.startswith(HEADER) and line.endswith(']'):
            try:
                raw = line[len(HEADER):-1]
                if raw.startswith('{'):
                    return validate(json.loads(raw))
                return validate(json.loads(base64.urlsafe_b64decode(raw).decode()))
            except (ValueError, TypeError, UnicodeError):
                return None
    return None


def strip_header(text):
    lines = str(text or '').split('\n')
    for index, line in enumerate(lines[:5]):
        if line.startswith(HEADER) and from_text(line):
            del lines[index]
            break
    return '\n'.join(lines)


def carry(text, attr, *, app):
    if not enabled(app) or re.match(r'^\s*/', str(text)):
        return str(text)
    text = strip_header(text)
    raw = json.dumps(validate(attr), separators=(',', ':'), ensure_ascii=True).encode()
    header = HEADER + raw.decode() + ']'
    lines = text.split('\n')
    at = 1 if lines and lines[0].startswith('orig_msg_id:') else 0
    lines.insert(at, header)
    return '\n'.join(lines)


def journal(attr, *, app, event='send', **details):
    """Persist before transport; delivery stays available on write failure."""
    try:
        attr = validate(attr)
        directory = (_wt_state() if app == 'wt' else _ccc_state()) / 'attribution'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (time.strftime('%Y-%m', time.gmtime()) + '.jsonl')
        row = dict(attr=attr, event=event, node=local_node(), at=time.time(), **details)
        with path.open('a', encoding='utf-8') as f:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(row, ensure_ascii=True, separators=(',', ':')) + '\n')
            f.flush()
            os.fsync(f.fileno())
        return True
    except (OSError, ValueError, TypeError) as exc:
        key = (app, str(exc))
        if key not in _warned:
            print('[attribution] journal write failed: ' + str(exc), file=sys.stderr)
            _warned.add(key)
        return False


def lookup(*, message_id='', transport_id='', text='', recipient=''):
    """Index same-node journals; never create or mutate the other app's files."""
    embedded = from_text(text)
    keys = {str(x) for x in (message_id, transport_id, (embedded or {}).get('message_id')) if x}
    keys.update(re.findall(r'⟨wt:([A-Za-z0-9_-]{4,64})⟩', str(text)))
    if not keys:
        return None
    ccc_id = _read(_ccc_state() / 'node.json').get('node_id')
    wt_id = _read(_wt_state() / 'node.json').get('node_id')
    node, aliases = ccc_id or wt_id or '', [wt_id] if wt_id else []
    for base in (_wt_state(), _ccc_state()):
        for path in sorted((base / 'attribution').glob('*.jsonl'), reverse=True):
            try:
                st = path.stat()
                signature = (st.st_mtime_ns, st.st_size)
                cached = _cache.get(str(path))
                if not cached or cached[0] != signature:
                    index = {}
                    snapshots = {}
                    for line in path.read_text().splitlines():
                        try:
                            row = json.loads(line)
                            a = validate(row['attr'])
                            if row.get('node') not in [node] + aliases:
                                continue
                            previous = snapshots.get(a['message_id'])
                            if previous is None or len(a.get('relay', [])) >= len(previous.get('relay', [])):
                                snapshots[a['message_id']] = a
                            ids = [a['message_id']] + list(a.get('transport_ids', []))
                            ids += [row.get(k) for k in ('transport_id', 'nonce', 'receipt_id', 'delivery_id')]
                            for key in ids:
                                if key:
                                    index[str(key).removeprefix('⟨wt:').removesuffix('⟩')] = a
                        except (ValueError, TypeError, KeyError):
                            continue
                    # Receipts can carry an earlier snapshot than the adapter
                    # that delivered them. Keep every alias, while resolving
                    # all of them to the fullest locally recorded relay chain.
                    index = {key: snapshots[a['message_id']] for key, a in index.items()}
                    _cache[str(path)] = (signature, index)
                index = _cache[str(path)][1]
                for key in keys:
                    a = index.get(key)
                    if a:
                        destinations = (a.get('intended_recipient') or {}, a.get('delivered_recipient') or {})
                        if recipient and not any(d.get('id') == recipient for d in destinations):
                            continue
                        return copy.deepcopy(a)
            except OSError:
                continue
    if embedded:
        embedded['basis'] = 'claimed'
        embedded['evidence'] = {'source': 'unmatched_header'}
        return embedded
    return None


def request_attr(payload, recipient, *, browser_composer=False):
    """HTTP claims never become runtime evidence just by naming a source."""
    incoming = payload.get('attr')
    if incoming is not None:
        attr = validate(incoming)
        recorded = lookup(message_id=attr['message_id'], recipient=str(recipient))
        if recorded and recorded['author'] == attr['author']:
            return recorded
        attr['basis'] = 'claimed'
        attr['evidence'] = {'source': 'request', 'remote_node': attr['author'].get('node', '')}
        return attr
    if browser_composer:
        return make(recipient, who=author('human', '', 'You (dashboard)'), basis='claimed', evidence={'source': 'dashboard'})
    sender = next((payload.get(k) for k in ('peer_sender_sid', 'sender_session_id', 'from_session_id', 'sender_id', 'announced_from') if payload.get(k)), '')
    if sender:
        return make(recipient, who=author('session', sender, sender), basis='claimed', evidence={'source': 'request'})
    return make(recipient)


def decorate_event(event, origin=None):
    """Attach UI attribution and hide the carrier without modifying history."""
    if not isinstance(event, dict) or event.get('type') != 'user_text':
        return event
    origin = origin or {}
    text = str(event.get('text') or '')
    peer = event.get('peer') or {}
    attr = lookup(message_id=origin.get('msg_id') or peer.get('msg_id', ''), text=text)
    if attr:
        event['attribution'] = attr
        event['text'] = strip_header(text)
        node = attr['author'].get('node')
        event['attribution_local'] = node in {
            _read(_ccc_state() / 'node.json').get('node_id'),
            _read(_wt_state() / 'node.json').get('node_id')}
        ident = attr['author'].get('id', '')
        if event['attribution_local'] and attr['basis'] in ('runtime', 'peer_cred') and attr['author']['kind'] == 'session':
            registry = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'sessions'
            if any(_read(p).get('sessionId') == ident for p in registry.glob('*.json')):
                event['attribution_session_id'] = ident
        if attr['author'].get('kind') != 'human':
            event.setdefault('peer', {'name': attr['author'].get('name_at_send', ''), 'from': '', 'from_mode': '', 'msg_id': attr['message_id']})
    return event


def main():
    """Explicit manual retention operation; normal operation never prunes."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prune = sub.add_parser('prune')
    prune.add_argument('--before', required=True, help='Delete own monthly segments older than YYYY-MM')
    prune.add_argument('--app', choices=('wt','ccc'), required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', args.before):
        parser.error('--before must be YYYY-MM')
    base = _wt_state() if args.app == 'wt' else _ccc_state()
    for path in sorted((base / 'attribution').glob('????-??.jsonl')):
        if path.stem < args.before:
            path.unlink()
            print(path.name)


if __name__ == '__main__':
    main()
