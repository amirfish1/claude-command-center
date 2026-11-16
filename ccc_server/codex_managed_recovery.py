"""Managed-daemon identity and durable handoff; never a restart permission.

Only explicit CCC sends/resumes establish thread ownership. A lost reply is
not a failed write. Native lookup/dedupe remains unproven: uncertain writes
hold their FIFO slot, including across a worker or daemon epoch change.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import tempfile
import threading
import time

from ccc_server import core as _core

CAPABILITY = "codex-managed-recovery-v1"
# Offline-generated protocol contract for this exact Linux executable. This
# pins syntax, not active-turn continuity or native idempotency semantics.
SUPPORTED = {
    "b452ca20f838a8855a88a41050ee4a694033fff4ea3ecdad5633e6e0e8d13f2b":
        ("0.169.0", "cfc29401a73d9a7bd1f53811c6ddb50e6d4d8d89c14b60cc72420cdb248fc8c4"),
}
INPUT_METHODS = frozenset(("turn/start", "turn/steer", "thread/queue/add"))
MAX_THREADS = 4096
MAX_PAGES = 8
MAX_REATTACH = 32
MAX_BYTES = 4 * 1024 * 1024
_local = threading.local()


class Refused(OSError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def process_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    raw = Path(f"/proc/{pid}/stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    if len(fields) <= 19 or fields[0] in ("Z", "X", "x"):
        raise Refused("process identity unavailable")
    return {"pid": pid, "start_ticks": int(fields[19]),
            "boot_id": Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def exact_process_dead(identity):
    """Unknown/permission failures are not death. A reused PID is not our PID."""
    if not isinstance(identity, dict) or set(identity) != {"pid", "start_ticks", "boot_id"}:
        return False
    if type(identity['pid']) is not int or identity['pid'] <= 0 or type(identity['start_ticks']) is not int:
        return False
    try:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if not boot or not identity['boot_id']:
            return False
        if boot != identity['boot_id']:
            return True
        return process_identity(identity['pid']) != identity
    except FileNotFoundError:
        return not Path(f"/proc/{identity['pid']}").exists()
    except (OSError, ValueError):
        return False


def _cgroups(pid):
    records = {}
    for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines():
        hierarchy, controllers, location = line.split(':', 2)
        if not location.startswith('/') or location == '/':
            raise Refused('unknown worker/daemon cgroup scope')
        records[(hierarchy, controllers)] = location.rstrip('/')
    if not records:
        raise Refused('missing worker/daemon cgroup scope')
    return records


def outside_worker_scope(peer_pid):
    worker = os.getpid()
    if os.getpgid(peer_pid) == os.getpgid(worker) or os.getsid(peer_pid) == os.getsid(worker):
        raise Refused('managed daemon shares worker process group/session')
    ours, theirs = _cgroups(worker), _cgroups(peer_pid)
    if set(ours) != set(theirs):
        raise Refused('incomparable worker/daemon cgroup scope')
    for controller, owner in ours.items():
        other = theirs[controller]
        if other == owner or other.startswith(owner + '/'):
            raise Refused('managed daemon belongs to worker cgroup')


def socket_path_identity(unix_path):
    target = Path(unix_path)
    parent = target.parent.lstat()
    current = target.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid() or not stat.S_ISSOCK(current.st_mode)
            or current.st_uid != os.getuid() or stat.S_IMODE(current.st_mode) & 0o077):
        raise Refused('managed socket requires private same-uid directory and socket')
    # No symlink in the supplied path, including its ancestors.
    if target.absolute() != target.resolve():
        raise Refused('managed socket path aliases another path')
    return {'socket_device': current.st_dev, 'socket_inode': current.st_ino,
            'directory_device': parent.st_dev, 'directory_inode': parent.st_ino}


def observe_peer(sock, unix_path):
    if not hasattr(socket, 'SO_PEERCRED'):
        raise Refused('managed peer credentials unsupported')
    before = socket_path_identity(unix_path)
    peer_pid, uid, _gid = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != os.getuid() or peer_pid <= 0:
        raise Refused('managed peer uid/pid mismatch')
    identity = process_identity(peer_pid)
    outside_worker_scope(peer_pid)
    executable = Path(f'/proc/{peer_pid}/exe')
    binary = hashlib.sha256(executable.read_bytes()).hexdigest()
    contract = SUPPORTED.get(binary)
    if contract is None:
        raise Refused('unrecognized managed daemon binary/protocol')
    if process_identity(peer_pid) != identity or socket_path_identity(unix_path) != before:
        raise Refused('managed identity changed during observation')
    return {**identity, **before, 'binary_sha256': binary,
            'version': contract[0], 'protocol_sha256': contract[1]}


def verify_initialize(identity, response):
    result = response.get('result')
    if not isinstance(result, dict):
        raise Refused('managed initialize failed')
    agent = result.get('userAgent')
    if not isinstance(agent, str) or not re.search(r'(?<![\d.])' + re.escape(identity['version']) + r'(?![\d.])', agent):
        raise Refused('managed initialize version mismatch')


def compatible_writers():
    """Legacy claim readers must never observe a new uncertain managed claim."""
    worker = {} if os.environ.get('CCC_WORKER_PROCESS') == '1' else _core._get_worker_compat_cache()
    if (os.environ.get('CCC_WORKER_PROCESS') != '1'
            and (worker.get('reachable') is not True or CAPABILITY not in (worker.get('capabilities') or []))):
        raise Refused('managed recovery requires a compatible control-plane worker')
    for peer in _core._read_registry_pruned():
        if peer.get('pid') != os.getpid() and peer.get('managed_recovery_protocol') != 1:
            raise Refused('managed recovery requires compatible dashboard peers')


def authenticated_delivery_transport(transport, socket_path):
    """Verify an initialized managed peer; socket configuration grants nothing.

    This does not start a process, alter ownership, or authorize a restart.
    before_send still checks admission and persists every durable input claim.
    """
    compatible_writers()
    with _core._CODEX_APP_SERVER_LOCK:
        if (transport is None or transport is not _core._CODEX_APP_SERVER_TRANSPORT
                or transport.kind != 'managed-unix' or not transport.alive()
                or not _core._CODEX_APP_SERVER_INITIALIZED
                or getattr(transport, 'managed_recovering', True)
                or str(getattr(transport, 'managed_socket_path', '')) != str(socket_path)):
            raise Refused('managed delivery connection is not authenticated and ready')
        identity = observe_peer(transport.sock, socket_path)
        if (not _valid_daemon(identity) or identity != transport.managed_identity
                or _sha(identity) != transport.managed_epoch):
            raise Refused('managed delivery daemon identity changed')
    return identity


def _valid_daemon(identity):
    fields = {'pid', 'start_ticks', 'boot_id', 'socket_device', 'socket_inode',
              'directory_device', 'directory_inode', 'binary_sha256', 'version', 'protocol_sha256'}
    if not isinstance(identity, dict) or set(identity) != fields:
        return False
    if any(type(identity[k]) is not int or identity[k] < 0 for k in (
            'pid', 'start_ticks', 'socket_device', 'socket_inode', 'directory_device', 'directory_inode')):
        return False
    if not identity['pid'] or not identity['start_ticks'] or not isinstance(identity['boot_id'], str) or not identity['boot_id']:
        return False
    contract = SUPPORTED.get(identity['binary_sha256']) if isinstance(identity['binary_sha256'], str) else None
    return contract is not None and (identity['version'], identity['protocol_sha256']) == contract


@contextmanager
def _state(write=False):
    target = _core.CODEX_APP_SERVER_STATE_FILE.with_name('codex-managed-recovery.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(target) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        value = {'version': 1, 'daemon': None, 'threads': {}}
        if target.exists() or target.is_symlink():
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > MAX_BYTES:
                raise Refused('unsafe managed recovery state')
            try:
                value = json.loads(target.read_bytes())
                if (set(value) != {'version', 'daemon', 'threads'} or value['version'] != 1
                        or not isinstance(value['threads'], dict) or len(value['threads']) > MAX_THREADS
                        or (value['daemon'] is not None and not _valid_daemon(value['daemon']))
                        or (value['threads'] and value['daemon'] is None)):
                    raise ValueError
                for sid, record in value['threads'].items():
                    if (not isinstance(sid, str) or not sid or not isinstance(record, dict)
                            or not isinstance(record.get('epoch'), str)
                            or not re.fullmatch('[a-f0-9]{64}', record['epoch'])
                            or record.get('status') not in ('ready', 'needs_reconciliation')
                            or (record.get('path') is not None and not isinstance(record['path'], str))
                            or (record.get('status') == 'ready' and record['epoch'] != _sha(value['daemon']))):
                        raise ValueError
            except (ValueError, TypeError, KeyError):
                raise Refused('corrupt managed recovery state') from None
        yield value
        if write:
            _atomic_json(target, value)
    finally:
        os.close(descriptor)


def _atomic_json(target, value):
    data = _canonical(value)
    if len(data) > MAX_BYTES:
        raise Refused('managed recovery state exceeds bound')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.managed-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(name, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def _legacy_thread(record):
    return (isinstance(record, dict) and record.get('transport') in ('managed', 'managed-unix')
            and record.get('transport_owner') == 'ccc-managed-app-server')


def _legacy_record(record):
    return {'epoch': '0' * 64, 'status': 'needs_reconciliation',
            'reason': 'legacy_managed_epoch_unknown',
            'path': record.get('path') or record.get('rollout_path')}


def known_thread(sid):
    with _state() as saved:
        record = saved['threads'].get(sid)
        if record:
            return dict(record)
    # The first transition has no new-format identity. Preserve known legacy
    # ownership conservatively; absence of new metadata is not absence of a
    # native writer. Registry lookups are cached and run only on explicit sends.
    record = _core._codex_thread_registry_entry(sid)
    return _legacy_record(record) if _legacy_thread(record) else {}


def protect_stdio(method, params):
    from ccc_server.codex_capabilities import codex_method_is_mutating
    if not codex_method_is_mutating(method):
        return
    sid = (params or {}).get('threadId')
    if sid and known_thread(sid):
        raise Refused('managed thread needs reconciliation; no second stdio writer')


def connected(transport, identity):
    epoch = _sha(identity)
    with _state(True) as saved:
        previous = saved['daemon']
        entries = _core._codex_thread_registry_entries()
        if len(entries) > MAX_THREADS:
            raise Refused('legacy tracked-thread inventory exceeds recovery bound')
        for sid, record in entries.items():
            if _legacy_thread(record) and sid not in saved['threads']:
                saved['threads'][sid] = _legacy_record(record)
        if len(saved['threads']) > MAX_THREADS:
            raise Refused('managed tracked-thread inventory exceeds recovery bound')
        saved['daemon'] = identity
        for record in saved['threads'].values():
            record.update(status='needs_reconciliation', reason=(
                'daemon_epoch_changed' if record['epoch'] != epoch else 'reconnecting'))
    transport.managed_identity = identity
    transport.managed_epoch = epoch
    transport.managed_recovering = True
    transport.managed_subscriptions = set()
    transport.managed_requests = {}
    transport.managed_tracked = set(saved['threads'])
    transport.managed_revisions = {}
    return previous


def track(transport, sid, path=None):
    if not isinstance(sid, str) or not sid or not getattr(transport, 'managed_epoch', None):
        raise Refused('unverified managed thread ownership')
    with _state(True) as saved:
        old = saved['threads'].get(sid)
        if old is None:
            if len(saved['threads']) >= MAX_THREADS:
                raise Refused('managed tracked-thread bound reached')
            saved['threads'][sid] = {'epoch': transport.managed_epoch, 'status': 'ready', 'path': path}
        else:
            if path:
                old['path'] = path
            if old.get('reason') == 'resume_intent' and old['epoch'] == transport.managed_epoch:
                old.update(status='ready', reason=None)
    transport.managed_subscriptions.add(sid)
    transport.managed_tracked.add(sid)


def _pages(rpc, method, params, deadline):
    rows, seen, cursor = [], set(), None
    for _ in range(MAX_PAGES):
        timeout = min(3, deadline - time.monotonic())
        if timeout <= 0:
            raise Refused('managed reconnect deadline')
        result = rpc(method, dict(params, **({'cursor': cursor} if cursor else {})), timeout).get('result')
        if not isinstance(result, dict) or not isinstance(result.get('data'), list):
            raise Refused('managed reconnect invalid ' + method)
        rows.extend(result['data'])
        if len(rows) > MAX_THREADS:
            raise Refused('managed reconnect row bound')
        cursor = result.get('nextCursor')
        if cursor is None:
            return rows
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise Refused('managed reconnect repeated/invalid cursor')
        seen.add(cursor)
    raise Refused('managed reconnect page cap')


def reattach(transport, rpc):
    """Bounded authoritative reads. Never discover/adopt a foreign thread."""
    deadline = time.monotonic() + 15
    raw_rpc = rpc
    def rpc(method, params, timeout):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Refused('managed reconnect deadline')
        return raw_rpc(method, params, min(timeout, remaining))
    with _state() as saved:
        tracked = dict(saved['threads'])
    try:
        if len(tracked) > MAX_REATTACH:
            raise Refused('managed reconnect tracked-thread cap')
        loaded = _pages(rpc, 'thread/loaded/list', {}, deadline)
        if any(not isinstance(sid, str) for sid in loaded) or len(set(loaded)) != len(loaded):
            raise Refused('managed reconnect malformed loaded threads')
        for index, (sid, previous) in enumerate(sorted(tracked.items())):
            if previous['epoch'] != transport.managed_epoch:
                continue  # A new daemon cannot silently authorize old-epoch input.
            if sid not in loaded or not previous.get('path'):
                _mark(sid, 'resume_unproven:not_loaded_or_no_rollout')
                continue
            # Limit recovery traffic to four subscriptions per second. This is
            # outside all app-server locks and never runs on a session-list row.
            if index:
                time.sleep(min(.25, max(0, deadline - time.monotonic())))
            try:
                result = rpc('thread/resume', {'threadId': sid, 'path': previous['path']}, 3)
                if not isinstance(result.get('result'), dict) or result['result'].get('thread', {}).get('id') != sid:
                    raise Refused('resume_unproven:' + str((result.get('error') or {}).get('code', 'unknown')))
                transport.managed_subscriptions.add(sid)
                revision = transport.managed_revisions.get(sid, 0)
                read = rpc('thread/read', {'threadId': sid, 'includeTurns': False}, 3).get('result')
                if not isinstance(read, dict) or not isinstance(read.get('thread'), dict) or read['thread'].get('id') != sid:
                    raise Refused('managed reconnect invalid thread/read')
                turns = _pages(rpc, 'thread/turns/list', {'threadId': sid}, deadline)
                if any(not isinstance(t, dict) or not t.get('id') or t.get('status') not in ('inProgress', 'completed', 'interrupted', 'failed') for t in turns):
                    raise Refused('managed reconnect unknown turn state')
                active = [t['id'] for t in turns if t['status'] == 'inProgress']
                if len(active) > 1:
                    raise Refused('managed reconnect ambiguous active turns')
                queue = _pages(rpc, 'thread/queue/list', {'threadId': sid}, deadline)
                if any(not isinstance(q, dict) or not q.get('id') for q in queue):
                    raise Refused('managed reconnect invalid queue state')
                goal = rpc('thread/goal/get', {'threadId': sid}, 3).get('result')
                if not isinstance(goal, dict) or 'goal' not in goal or (goal['goal'] is not None and not isinstance(goal['goal'], dict)):
                    raise Refused('managed reconnect invalid goal state')
                with _core._CODEX_APP_SERVER_LOCK:
                    if (_core._CODEX_APP_SERVER_TRANSPORT is not transport
                            or transport.managed_revisions.get(sid, 0) != revision):
                        raise Refused('managed reconnect snapshot changed during reads')
                    with _state(True) as saved:
                        if saved['threads'][sid]['epoch'] != transport.managed_epoch:
                            raise Refused('managed reconnect saved epoch changed')
                        saved['threads'][sid].update(status='ready', reason=None,
                            active_turn_id=active[0] if active else None,
                            last_completed_turn_id=next((t['id'] for t in turns if t['status'] == 'completed'), None),
                            queue_ids=[q['id'] for q in queue], goal_sha256=_sha(goal))
                    state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(sid, {})
                    state.update(active_turn_id=active[0] if active else None,
                                 managed_recovery='ready', managed_daemon_epoch=transport.managed_epoch)
                    # No fabricated aborted/completed event is emitted.
            except (OSError, ValueError, TypeError, KeyError) as error:
                _mark(sid, str(error))
    except (OSError, ValueError, TypeError) as error:
        for sid in tracked:
            _mark(sid, str(error))
    finally:
        transport.managed_recovering = False


def _mark(sid, reason):
    with _state(True) as saved:
        if sid in saved['threads']:
            saved['threads'][sid].update(status='needs_reconciliation', reason=reason)
    with _core._CODEX_APP_SERVER_LOCK:
        _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(sid, {}).update(
            managed_recovery='needs_reconciliation', managed_recovery_reason=reason)


def admission(transport, method, params):
    from ccc_server.codex_capabilities import codex_method_is_mutating
    if not codex_method_is_mutating(method) or method == 'initialize':
        return
    if not getattr(transport, 'managed_identity', None):
        raise Refused('unverified managed daemon')
    sid = (params or {}).get('threadId')
    if method == 'thread/unsubscribe' and sid not in transport.managed_subscriptions:
        raise Refused('managed unsubscribe requires current connection ownership')
    if getattr(transport, 'managed_recovering', False):
        if method != 'thread/resume' or not getattr(_local, 'recovery', False):
            raise Refused('managed reconnect still reconciling')
    elif sid:
        previous = known_thread(sid)
        if previous and previous['status'] != 'ready':
            raise Refused('managed thread needs reconciliation')


@contextmanager
def recovery_reads():
    old = getattr(_local, 'recovery', False)
    _local.recovery = True
    try:
        yield
    finally:
        _local.recovery = old


@contextmanager
def queued_claim(claim):
    previous = getattr(_local, 'claim', None)
    _local.claim = claim
    try:
        yield
    finally:
        _local.claim = previous


def validate_handoff(record, pending_id):
    fields = {'phase', 'worker', 'daemon', 'epoch', 'entry_id', 'method',
              'target_turn_id', 'last_turn_id', 'ledger_sequence', 'native_receipt', 'queue_action_id', 'params'}
    if (not isinstance(record, dict) or set(record) != fields
            or record['phase'] not in ('claimed', 'send_intent', 'acknowledged')
            or record['entry_id'] != pending_id or not pending_id
            or type(record['ledger_sequence']) is not int or record['ledger_sequence'] <= 0
            or not _valid_daemon(record['daemon']) or _sha(record['daemon']) != record['epoch']
            or not isinstance(record['worker'], dict)
            or set(record['worker']) != {'pid', 'start_ticks', 'boot_id'}
            or type(record['worker']['pid']) is not int or record['worker']['pid'] <= 0
            or type(record['worker']['start_ticks']) is not int or record['worker']['start_ticks'] <= 0
            or not isinstance(record['worker']['boot_id'], str) or not record['worker']['boot_id']
            or (record['phase'] == 'acknowledged' and (not isinstance(record['native_receipt'], str)
                                                      or not record['native_receipt']))
            or (record['phase'] != 'claimed' and (record['method'] not in INPUT_METHODS
                or not isinstance(record['params'], dict)
                or record['params'].get('clientUserMessageId') != pending_id))):
        raise Refused('corrupt managed handoff identity/state')
    return record


def _records(sid):
    directory = _core._pending_input_claim_journal_dir()
    if any(directory.glob('*.invalid')):
        raise Refused('quarantined claim requires reconciliation')
    paths = list(directory.glob('*.json'))
    if len(paths) > MAX_THREADS:
        raise Refused('managed handoff journal cap')
    rows = []
    for target in paths:
        if target.stat().st_size > MAX_BYTES:
            raise Refused('managed handoff exceeds bound')
        try:
            payload = json.loads(target.read_bytes())
            if payload.get('session_id') != sid:
                continue
            if payload.get('managed') is not None:
                if _core._validate_pending_claim_journal(target, payload) is None:
                    raise Refused('invalid managed claim journal')
                rows.append((target, payload))
        except (ValueError, TypeError, KeyError) as error:
            raise Refused('corrupt managed claim journal') from error
    return rows


def prepare_claim(claim, *, direct=False):
    """Write the claim BEFORE removing its exact row from the primary queue."""
    sid = claim['session_id']
    previous = known_thread(sid)
    if not previous:
        return
    if previous['status'] != 'ready' or any(p['managed']['phase'] != 'acknowledged' for _, p in _records(sid)):
        raise Refused('managed input holds its FIFO slot pending reconciliation')
    item = claim['item']
    entry_id = getattr(item, 'pending_id', None) or getattr(item, 'handoff_id', None)
    if not entry_id:
        raise Refused('managed queue row lacks stable delivery id')
    with _state() as saved:
        daemon = saved['daemon']
    if not daemon or _sha(daemon) != previous['epoch']:
        raise Refused('managed claim daemon epoch unknown')
    sequence = int(claim.get('claim_sequence') or time.monotonic_ns())
    claim['claim_sequence'] = sequence
    claim['managed'] = {'phase': 'send_intent' if direct else 'claimed', 'worker': process_identity(),
        'daemon': daemon, 'epoch': previous['epoch'], 'entry_id': entry_id,
        'method': claim.get('_native_method') if direct else None,
        'target_turn_id': claim.get('_native_target') if direct else None,
        'last_turn_id': previous.get('last_completed_turn_id'),
        'ledger_sequence': sequence, 'native_receipt': None,
        'queue_action_id': (getattr(_local, 'action_id', None)
                            if claim.get('_native_method') == 'thread/queue/add' else None),
        'params': claim.get('_native_params') if direct else None}
    if _core._write_pending_input_claim_journal(claim) != 'journaled':
        raise Refused('managed claim could not be fsynced before queue removal')


def _save_claim(claim):
    if _core._write_pending_input_claim_journal(claim) != 'journaled':
        raise Refused('managed handoff persistence failed; input was not authorized')


def before_send(transport, method, params, request_id):
    """Serialize journal check+intent across writers before ANY frame bytes."""
    if method in INPUT_METHODS:
        from ccc_server.pending_inputs import _pending_inputs_file_exclusive_lock
        with _pending_inputs_file_exclusive_lock():
            return _before_send_locked(transport, method, params, request_id)
    return _before_send_locked(transport, method, params, request_id)


def _before_send_locked(transport, method, params, request_id):
    admission(transport, method, params)
    if method not in INPUT_METHODS:
        if method == 'thread/resume' and not getattr(_local, 'recovery', False):
            sid = (params or {}).get('threadId')
            if not isinstance(sid, str) or not sid:
                raise Refused('managed resume lacks thread identity')
            if not known_thread(sid):
                track(transport, sid, params.get('path'))
                _mark(sid, 'resume_intent')
                transport.managed_subscriptions.discard(sid)
        return params
    if not isinstance(params, dict) or not params.get('threadId'):
        raise Refused('managed input lacks thread identity')
    from ccc_server import delivery
    if len(transport.managed_requests) >= 256:
        raise Refused('managed unanswered input request cap')
    claim = getattr(_local, 'claim', None)
    direct = claim is None
    supplied = params.get('clientUserMessageId')
    if claim is not None:
        if claim['session_id'] != params['threadId']:
            raise Refused('managed queued claim thread mismatch')
        entry = claim['item']
        key = getattr(entry, 'pending_id', None) or getattr(entry, 'handoff_id', None)
        if supplied and supplied != key:
            raise Refused('managed queued/native delivery id mismatch')
    else:
        key = supplied or delivery.bound_id()
        if not key:
            raise Refused('managed input requires a stable delivery id')
        from ccc_server.pending_inputs import _PendingInputEntry
        content = params.get('input') or []
        text = '\n'.join(str(i.get('text') or '') for i in content if isinstance(i, dict) and i.get('type') == 'text')
        # This is the existing claim journal, not another queue. Attachment-only
        # actions retain a non-content placeholder and require native ACK.
        claim = {'session_id': params['threadId'], 'queue_name': 'resume', 'index': 0,
                 'item': _PendingInputEntry(text or '[native input]', key),
                 'claim_sequence': time.monotonic_ns()}
    if not isinstance(key, str) or not key or len(key) > 256:
        raise Refused('invalid managed delivery id')
    rows = _records(params['threadId'])
    for _target, payload in rows:
        if payload['managed']['phase'] == 'acknowledged' and payload['managed']['entry_id'] != key:
            continue
        same_claim = payload.get('id') == claim.get('journal_id')
        if not same_claim or payload['managed']['phase'] != 'claimed':
            raise Refused('managed input is outstanding; no automatic resend or FIFO bypass',
                          uncertain=payload['managed']['phase'] != 'acknowledged')
    if claim.get('managed') is None:
        if not known_thread(params['threadId']):
            raise Refused('managed input requires explicit CCC resume/start ownership')
        claim.update(_native_method=method, _native_target=params.get('expectedTurnId'),
                     _native_params=dict(params, clientUserMessageId=key))
        prepare_claim(claim, direct=direct)
    managed = claim['managed']
    if managed['epoch'] != transport.managed_epoch or managed['worker'] != process_identity():
        raise Refused('managed send epoch/worker mismatch')
    managed.update(phase='send_intent', method=method, target_turn_id=params.get('expectedTurnId'),
                   params=dict(params, clientUserMessageId=key))
    _save_claim(claim)
    transport.managed_requests[request_id] = claim
    return dict(params, clientUserMessageId=key)


def response(transport, request_id, payload):
    """The matching RPC result is an ACK; text resemblance is never an ACK."""
    claim = getattr(transport, 'managed_requests', {}).pop(request_id, None)
    if claim is None:
        return
    if 'result' not in payload or 'error' in payload:
        return  # Conservative even for an error: no native dedupe is proven.
    result = payload['result']
    if not isinstance(result, dict):
        return
    method = claim['managed']['method']
    if method == 'turn/start':
        receipt = (result.get('turn') or {}).get('id')
    elif method == 'turn/steer':
        receipt = result.get('turnId')
    else:
        submission = result.get('queuedSubmission')
        if not isinstance(submission, dict):
            return
        receipt = submission.get('id')
        if submission.get('clientUserMessageId') != claim['managed']['entry_id']:
            return
    if not isinstance(receipt, str) or not receipt:
        return
    claim['managed'].update(phase='acknowledged', native_receipt=receipt)
    _save_claim(claim)


def journal_disposition(payload):
    """Return restore/consume/hold. Only exact old-worker death permits adoption."""
    record = payload.get('managed')
    if record is None:
        return 'restore'
    validate_handoff(record, payload.get('pending_id') or payload.get('handoff_id'))
    if not exact_process_dead(record['worker']):
        return 'hold'
    return {'claimed': 'restore', 'send_intent': 'hold', 'acknowledged': 'consume'}[record['phase']]


def finish_claim(claim, *, commit):
    record = claim.get('managed')
    if record is None:
        return True
    if (commit and record['phase'] != 'acknowledged') or (not commit and record['phase'] != 'claimed'):
        return False
    return True


def remove_claim_journal(claim):
    if claim.get('managed') is None:
        return
    ident = claim.get('journal_id')
    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{64}', ident):
        raise Refused('managed claim journal identity missing')
    target = _core._pending_input_claim_journal_dir() / (ident + '.json')
    target.unlink(missing_ok=True)
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def nonblocking_claim(payload):
    record = payload.get('managed') or {}
    current = getattr(_local, 'claim', None)
    return (record.get('phase') == 'acknowledged'
            or (current is not None and current.get('journal_id') == payload.get('id')
                and record.get('worker') == process_identity()))


def tracked_on_transport(sid):
    transport = getattr(_core, '_CODEX_APP_SERVER_TRANSPORT', None)
    return (transport is not None and transport.kind == 'managed-unix'
            and sid in getattr(transport, 'managed_tracked', set()))


@contextmanager
def native_action(action_id):
    previous = getattr(_local, 'action_id', None)
    _local.action_id = action_id
    try:
        yield
    finally:
        _local.action_id = previous


def notification(transport, payload):
    """No I/O on streamed events, and no foreign-thread ownership by event."""
    method = payload.get('method')
    if not isinstance(method, str):
        return
    if not (method.startswith(('turn/', 'thread/goal/', 'thread/queue/'))
            or method in ('thread/closed', 'thread/status/changed', 'item/started', 'item/completed')):
        return
    params = payload.get('params') or {}
    sid = _core._codex_notification_thread_id(method, params)
    if sid in getattr(transport, 'managed_tracked', set()):
        with _core._CODEX_APP_SERVER_LOCK:
            transport.managed_revisions[sid] = transport.managed_revisions.get(sid, 0) + 1


def disconnected(transport):
    with _core._CODEX_APP_SERVER_LOCK:
        current = _core._CODEX_APP_SERVER_TRANSPORT
        if current is not None and current is not transport:
            return
        _disconnected_locked(transport)


def _disconnected_locked(transport):
    try:
        with _state(True) as saved:
            for sid in transport.managed_tracked:
                if sid in saved['threads'] and saved['threads'][sid]['epoch'] == transport.managed_epoch:
                    saved['threads'][sid].update(status='needs_reconciliation', reason='managed_disconnected')
    except (OSError, ValueError):
        pass  # Corrupt saved state still refuses admission; cleanup must finish.
    finally:
        with _core._CODEX_APP_SERVER_LOCK:
            for sid in transport.managed_tracked:
                _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(sid, {}).update(
                    managed_recovery='needs_reconciliation', managed_recovery_reason='managed_disconnected')
