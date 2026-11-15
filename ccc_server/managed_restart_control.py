"""Fixed worker observation protocol for the immutable one-shot coordinator.

This channel cannot kill a process or accept a proof. It can only hold the
existing admission fence, report native/durable facts, and reconcile a released
replacement. The root authority owns proof selection and exact-container stop.
No handler here accepts code, commands, import paths, or a VerifiedLaunch.
"""
import copy
import json
import os
from pathlib import Path
import re
import sys
import time

from ccc_server.execution_artifact import Refused, canonical, digest

PREFIX = 'CCC-MANAGED-CONTROL/1 '
MAX_LINE = 65536
MAX_RECEIPT = 4 * 1024 * 1024
RECEIPT = 'managed-restart-control.json'


def native_snapshot(runtime):
    """Collect fresh reads and a final under-lock revision check, without sends."""
    from ccc_server import core, codex, codex_managed_recovery as recovery
    from ccc_server import codex_restart_gate as gate
    from ccc_server.codex_requests import CODEX_REQUESTS
    recovery.compatible_writers()
    with core._CODEX_APP_SERVER_LOCK:
        transport = core._CODEX_APP_SERVER_TRANSPORT
        if (transport is None or transport.kind != 'managed-unix' or not transport.alive()
                or getattr(transport, 'managed_recovering', True)):
            raise Refused('managed native connection is not ready')
        identity = recovery.observe_peer(transport.sock, transport.managed_socket_path)
        if identity != transport.managed_identity or recovery._sha(identity) != transport.managed_epoch:
            raise Refused('native daemon epoch changed')
        revisions = dict(transport.managed_revisions)
        pending = dict(codex._CODEX_APP_SERVER_MUTATING_PENDING)
        if pending:
            raise Refused('unanswered native mutation')
        if CODEX_REQUESTS.snapshot()['requests']:
            raise Refused('unanswered native server request')
        for state in core._CODEX_APP_SERVER_THREAD_STATE.values():
            if state.get('pending_approval_request') or state.get('thread_needs_approval'):
                raise Refused('unanswered native approval')
    with recovery._state() as state:
        saved = copy.deepcopy(state)
    if saved['daemon'] != identity or len(saved['threads']) > recovery.MAX_REATTACH:
        raise Refused('unknown or unbounded native continuity')
    rows, deadline = {}, time.monotonic() + 15
    def rpc(method, params, timeout):
        return core._codex_app_server_request_to_transport(transport, method, params, timeout=timeout)
    with gate.managed_observation():
        loaded = recovery._pages(rpc, 'thread/loaded/list', {}, deadline)
        if (any(not isinstance(sid, str) for sid in loaded)
                or len(set(loaded)) != len(loaded) or set(loaded) != set(saved['threads'])):
            raise Refused('loaded/tracked native inventory mismatch')
        for sid, record in saved['threads'].items():
            if (record['status'] != 'ready' or record['epoch'] != transport.managed_epoch
                    or sid not in transport.managed_subscriptions):
                raise Refused('native thread needs reconciliation')
            claims = []
            for _, payload in recovery._records(sid):
                claim = recovery.validate_handoff(payload['managed'], payload.get('pending_id') or payload.get('handoff_id'))
                if (claim['daemon'] != identity or claim['epoch'] != transport.managed_epoch
                        or claim['phase'] != 'acknowledged'):
                    raise Refused('outstanding durable native input')
                # Root needs exact receipt identity, never prompt/attachment
                # bytes from params in Docker's observation log.
                claims.append({key: claim[key] for key in
                    ('entry_id', 'phase', 'method', 'ledger_sequence', 'native_receipt', 'epoch')}
                    | {'claim_sha256': digest(canonical(claim))})
            turns = recovery._pages(rpc, 'thread/turns/list', {'threadId': sid}, deadline)
            active = []
            for turn in turns:
                if (not isinstance(turn, dict) or not isinstance(turn.get('id'), str)
                        or not turn['id'] or turn.get('status') not in
                        ('inProgress', 'completed', 'interrupted', 'failed')):
                    raise Refused('unknown native turn state')
                if turn['status'] == 'inProgress':
                    active.append(turn['id'])
            if len(active) > 1:
                raise Refused('ambiguous native active turn')
            queue = recovery._pages(rpc, 'thread/queue/list', {'threadId': sid}, deadline)
            if (any(not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] for row in queue)
                    or len({row['id'] for row in queue}) != len(queue)):
                raise Refused('unknown native queue state')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Refused('native observation deadline')
            result = rpc('thread/goal/get', {'threadId': sid}, min(3, remaining)).get('result')
            if not isinstance(result, dict) or 'goal' not in result:
                raise Refused('missing native goal observation')
            goal = result['goal']
            if goal is not None and (not isinstance(goal, dict) or not isinstance(goal.get('status'), str)):
                raise Refused('unknown native goal state')
            rows[sid] = {'active_turn_ids': active, 'queue_ids': [row['id'] for row in queue],
                         'goal_state': goal['status'] if goal is not None else None,
                         'claims': claims}
    def unchanged():
        with recovery._state() as current:
            same_saved = current == saved
        return (same_saved and core._CODEX_APP_SERVER_TRANSPORT is transport and transport.alive()
                and transport.managed_identity == identity and transport.managed_revisions == revisions
                and not codex._CODEX_APP_SERVER_MUTATING_PENDING and not CODEX_REQUESTS.snapshot()['requests']
                and recovery.observe_peer(transport.sock, transport.managed_socket_path) == identity)
    return {'worker': recovery.process_identity(), 'worker_epoch': runtime.epoch,
            'daemon': identity, 'daemon_epoch': transport.managed_epoch, 'threads': rows}, unchanged


def _allowed_goals(observation, states):
    for row in observation['threads'].values():
        if row['goal_state'] is not None and row['goal_state'] not in states:
            raise Refused('native goal changed to an unproven class')


class WorkerControl:
    """Per-worker finite protocol. Durable receipt survives ambiguous replies."""
    def __init__(self, runtime):
        self.runtime = runtime

    def _path(self):
        from control_plane import state_dir
        return state_dir() / RECEIPT

    def _save(self, value):
        from ccc_server.codex_managed_recovery import _atomic_json
        _atomic_json(self._path(), value)

    def _load(self):
        target = self._path()
        info = target.lstat()
        import stat
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > MAX_RECEIPT):
            raise Refused('unsafe managed control receipt')
        value = json.loads(target.read_bytes())
        fields = {'format', 'transition_id', 'manifest_sha256', 'worker', 'lease',
                  'stage', 'drain', 'observation', 'goal_states'}
        if (not isinstance(value, dict) or set(value) != fields or value['format'] != 1
                or value['stage'] not in ('captured', 'sealed', 'resumed', 'aborted')
                or not isinstance(value['goal_states'], list)):
            raise Refused('corrupt managed control receipt')
        return value

    def dispatch(self, request):
        from ccc_server import codex_restart_gate as gate
        from ccc_server import codex_managed_recovery as recovery
        from ccc_server.codex_managed_contract import GOAL_STATES
        if (not isinstance(request, dict) or set(request) != {'operation', 'transition_id', 'manifest_sha256', 'owner_pid', 'goal_states', 'receipt_sha256'}
                or not isinstance(request['transition_id'], str)
                or not re.fullmatch(r'[0-9a-f]{32}', request['transition_id'])
                or not isinstance(request['manifest_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', request['manifest_sha256'])
                or type(request['owner_pid']) is not int or request['owner_pid'] <= 0
                or not isinstance(request['goal_states'], list)
                or any(not isinstance(x, str) or x not in GOAL_STATES for x in request['goal_states'])
                or len(set(request['goal_states'])) != len(request['goal_states'])):
            raise Refused('invalid fixed managed control request')
        operation = request['operation']
        if operation not in ('begin', 'final', 'abort', 'ready', 'resume'):
            raise Refused('unsupported fixed managed operation')
        if operation == 'begin':
            if request['receipt_sha256'] is not None:
                raise Refused('begin cannot adopt a prior receipt')
            if self._path().exists():
                prior = self._load()
                if prior['stage'] not in ('resumed', 'aborted'):
                    raise Refused('previous managed transition requires reconciliation')
            before = self.runtime.ledger.drain_state()
            if before.get('enabled'):
                raise Refused('another owner holds worker drain')
            drain = self.runtime.ledger.set_drain(True, 'managed-restart:' + request['transition_id'])
            try:
                capture = gate.capture_managed(lambda: native_snapshot(self.runtime))
                receipt = {'format': 1, 'transition_id': request['transition_id'],
                           'manifest_sha256': request['manifest_sha256'],
                           'worker': recovery.process_identity(), 'lease': capture['lease'],
                           'stage': 'captured', 'drain': drain, 'observation': capture['observation'], 'goal_states': []}
                self._save(receipt)
            except BaseException:
                if 'capture' in locals():
                    gate.release(capture['lease'])
                self.runtime.ledger.release_drain(drain, 'managed observation refused')
                raise
            return receipt
        receipt = self._load()
        if digest(canonical(receipt)) != request['receipt_sha256']:
            raise Refused('durable managed receipt changed')
        if any(receipt[key] != request[key] for key in ('transition_id', 'manifest_sha256')):
            raise Refused('managed receipt belongs to a different transition')
        if operation == 'final':
            if receipt['stage'] != 'captured' or receipt['worker'] != recovery.process_identity():
                raise Refused('managed seal replay or changed worker')
            def collect():
                observation, check = native_snapshot(self.runtime)
                _allowed_goals(observation, request['goal_states'])
                return observation, check
            capture = gate.capture_managed(collect,
                token=receipt['lease'], seal=True, owner_pid=request['owner_pid'])
            receipt.update(stage='sealed', observation=capture['observation'], goal_states=request['goal_states'])
            self._save(receipt)
            return receipt
        if operation == 'abort':
            if receipt['stage'] != 'captured' or receipt['worker'] != recovery.process_identity():
                raise Refused('sealed or foreign managed freeze cannot be aborted')
            gate.release(receipt['lease'])
            self.runtime.ledger.release_drain(receipt['drain'], 'managed observation abandoned')
            receipt['stage'] = 'aborted'
            self._save(receipt)
            return receipt
        if operation in ('ready', 'resume'):
            if request['goal_states'] != receipt['goal_states']:
                raise Refused('accepted native classes changed')
            if receipt['stage'] != 'sealed' or not recovery.exact_process_dead(receipt['worker']):
                raise Refused('replacement requires sealed receipt and exact prior-worker death')
            # Root only releases the replacement admission stop after accepting
            # proofs/final facts. Drain remains held during re-subscription.
            if not self.runtime.ledger.drain_state().get('enabled'):
                raise Refused('replacement lost its drain')
            with gate.managed_observation(recovery=True):
                from ccc_server import core
                if core._ensure_codex_app_server(allow_stdio=False) is None:
                    raise Refused('replacement native transport unavailable')
            observation, unchanged = native_snapshot(self.runtime)
            with core._CODEX_APP_SERVER_LOCK:
                if not unchanged():
                    raise Refused('replacement observation changed')
                _allowed_goals(observation, receipt['goal_states'])
            if observation['daemon'] != receipt['observation']['daemon']:
                raise Refused('replacement connected to a different native daemon')
            if operation == 'resume':
                # Only this exact persisted fence can be completed. A lost
                # reply never retries a send: all journals remain authoritative.
                path = gate.done_path(receipt['lease'])
                if not path:
                    raise Refused('replacement has no inherited fence')
                with open(path, 'w', encoding='ascii') as out:
                    out.write('killed\n'); out.flush(); os.fsync(out.fileno())
                if not gate.recover():
                    raise Refused('replacement fence remains held')
                receipt['stage'] = 'resumed'
                self._save(receipt)
                if not self.runtime.ledger.release_drain(receipt['drain'], 'managed replacement verified'):
                    raise Refused('replacement drain owner changed')
            return dict(receipt, observation=observation)
        raise Refused('unsupported fixed managed operation')


def serve_stdio(runtime, input_stream=None, output_stream=None):
    """One bounded JSON line in/out; no generic worker dispatch or code eval."""
    source, target = input_stream or sys.stdin, output_stream or sys.stdout
    control = WorkerControl(runtime)
    while True:
        line = source.readline(MAX_LINE + 1)
        if not line:
            return
        if len(line) > MAX_LINE or not line.endswith('\n'):
            return  # Never parse the remainder of a truncated request.
        ident = None
        try:
            envelope = json.loads(line)
            if (not isinstance(envelope, dict) or set(envelope) != {'id', 'request'}
                    or not isinstance(envelope['id'], str) or not re.fullmatch(r'[0-9a-f]{32}', envelope['id'])):
                raise Refused('invalid managed request envelope')
            ident = envelope['id']
            response = {'id': ident, 'ok': True, 'result': control.dispatch(envelope['request'])}
        except Exception as exc:
            response = {'id': ident, 'ok': False, 'error': str(exc)[:200]}
        target.write(PREFIX + canonical(response).decode() + '\n')
        target.flush()
