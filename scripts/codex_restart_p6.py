# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Disposable P6 scenarios through real CCC worker RPC and native read APIs.

No adapter-provided success snapshots. The outer root observer supplies only
kernel launch facts; native history, CCC public status and durable delivery
receipts are read here. Importing starts no processes or connections.
"""
import json
import io
import email.message
import os
from pathlib import Path
import socket
import stat
import struct
import time

from ccc_server.execution_artifact import canonical, digest
from ccc_server import codex_managed_recovery as recovery
from scripts.codex_restart_fixture import Stop, _json
from scripts.codex_restart_runner import Protocol, need


class WorkerRPC:
    def __init__(self, identity, record, *, root=Path('/state')):
        self.identity, self.record, self.root = identity, record, root

    def call(self, method, params, *, discard_reply=False):
        token_path, socket_path = self.root / 'worker.token', self.root / 'worker.sock'
        token_info, socket_info = token_path.lstat(), socket_path.lstat()
        need(stat.S_ISREG(token_info.st_mode) and token_info.st_uid == os.getuid()
             and stat.S_IMODE(token_info.st_mode) == 0o600 and token_info.st_nlink == 1,
             'worker token is not private')
        need(stat.S_ISSOCK(socket_info.st_mode) and socket_info.st_uid == os.getuid()
             and not socket_info.st_mode & 0o077, 'worker socket is not private')
        fd = os.open(token_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            token = os.read(fd, 4097)
            need(0 < len(token) <= 4096 and os.fstat(fd) == token_info, 'worker token changed')
        finally:
            os.close(fd)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(25)
            conn.connect(str(socket_path))
            pid, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            need(pid == self.identity['pid'] and uid == os.getuid()
                 and recovery.process_identity(pid)['start_ticks'] == self.identity['start_ticks']
                 and socket_path.lstat() == socket_info, 'worker RPC peer identity drift')
            raw = canonical({'auth': token.decode().strip(), 'method': method, 'params': params}) + b'\n'
            need(len(raw) <= 1024 * 1024, 'worker RPC frame oversized')
            self.record('worker_send', method=method, params=params, worker=self.identity,
                        discard_reply=discard_reply)
            conn.sendall(raw)  # A failed/partial write is unknown; never retry.
            if discard_reply:
                return None
            with conn.makefile('rb') as incoming:
                raw = incoming.readline(4 * 1024 * 1024 + 1)
            need(raw.endswith(b'\n') and len(raw) <= 4 * 1024 * 1024, 'worker reply unknown or oversized')
            reply = _json(raw)
            self.record('worker_receive', method=method, reply=reply, worker=self.identity)
            need(reply.get('ok') is True, 'actual CCC handler refused: ' + str(reply.get('code', reply.get('error'))))
            return reply

    def native(self, method, params, *, discard_reply=False):
        result = self.call('engine.query', {'engine': 'codex', 'operation': 'rpc',
            'args': {'method': method, 'params': params, 'timeout': 20}}, discard_reply=discard_reply)
        if discard_reply:
            return None
        response = result['response']
        need('result' in response and 'error' not in response, 'CCC native RPC refused or unknown')
        return response['result']


class InputHandler:
    """Invoke the immutable HTTP handler without a second listening server.

    Only the fixed input route is available. It uses the real disposable
    worker, queues, receipts and activity logger. No local-engine fallback is
    permitted if that worker does not accept the request.
    """
    def __init__(self, harness, *, module=None):
        self.io = harness
        if module is None:
            import server as module
        self.module = module
        need(Path(module.COMMAND_CENTER_STATE_DIR).resolve() == harness.root.resolve(),
             'CCC input handler state escapes disposable root')
        original = module._control_plane_engine_call
        def strict(*args, **kwargs):
            result = original(*args, **kwargs)
            need(result is not None, 'disposable worker unavailable; local execution fallback refused')
            return result
        module._control_plane_engine_call = strict

    def send(self, worker, thread, key, *, discard_reply=False):
        # Check the exact RPC peer before entering the actual HTTP handler.
        worker.call('health', {})
        raw = canonical({'session_id': thread['id'], 'text': 'Disposable input ' + key,
            'idempotency_key': key, 'engine': 'codex', 'source': 'proof', 'skip_wt': True})
        handler = self.module.CommandCenterHandler.__new__(self.module.CommandCenterHandler)
        handler.path, handler.command = '/api/inject-input', 'POST'
        handler.headers = email.message.Message()
        for name, value in {'Content-Length': str(len(raw)), 'Content-Type': 'application/json',
                            'Origin': 'http://127.0.0.1:1', 'Host': '127.0.0.1:1'}.items():
            handler.headers[name] = value
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.client_address = ('127.0.0.1', 1)
        handler.server = SimpleServer()
        replies = []
        handler.send_json = lambda value, status=200, **kwargs: replies.append((status, value))
        handler.do_POST()
        need(len(replies) == 1, 'CCC input handler produced no unique response')
        status, result = replies[0]
        self.io.record('ccc_input_reply_discarded' if discard_reply else 'ccc_input_reply',
            status=status, result=result, delivery_id=key)
        need(status == 200 and result.get('ok') is True, 'actual CCC input handler refused')
        need(result.get('delivery_id') in (None, key), 'CCC changed the submitted delivery identity')
        return result


class SimpleServer:
    server_address = ('127.0.0.1', 1)


class Scenarios:
    def __init__(self, harness, *, rpc=WorkerRPC):
        self.io, self.rpc_factory = harness, rpc
        self.protocol = Protocol(harness, harness.budget, harness.record)
        self.current = None
        self.idle_thread = None
        self.input_handler = None

    def worker(self, role):
        observed = self.io.control.request('observe-worker', {'role': role,
            'native_identity': self.io.native_identity()})
        need(observed.get('scope') == 'proof-observation' and observed.get('runtime_authorized') is False
             and observed['fixture_identity']['daemon'] == self.io.native_identity(),
             'actual CCC worker/native binding unavailable')
        fact = observed['worker']
        return self.rpc_factory(fact['identity'], self.io.record), fact

    def prepare(self, case, path):
        need(self.current is None, 'prior P6 scenario not completed')
        # The operator provisions the next exact stopped replacement and root
        # contracts. A bounded wait never guesses an identity or accepts it.
        self.io.control.request('scenario-ready', {'case': case, 'path': path,
            'native_identity': self.io.native_identity()})
        worker, fact = self.worker('worker')
        c = self.protocol.client()
        if self.idle_thread is None:
            # Use an already persisted first-lifetime thread without spending
            # completion credits on idle setup. Its history is real disk data.
            rows = self.protocol.pages(c, 'thread/list', {'archived': False})
            candidates = [r for r in rows if r.get('path') and r.get('id')]
            need(candidates, 'P6 has no persisted idle thread')
            self.idle_thread = candidates[0]
        if case in ('idle', 'corrupt', 'C6'):
            t = dict(self.idle_thread)
            self.protocol.resume(c, t)
        else:
            t = worker.native('thread/start', {'model': 'fixture', 'modelProvider': 'fixture',
                'cwd': '/state/work', 'sandbox': 'workspace-write', 'approvalPolicy': 'on-request'})['thread']
        worker.native('thread/resume', {'threadId': t['id'], 'path': t.get('path')})
        row = {'case': case, 'path': path, 'thread': t, 'client': c, 'worker': worker,
               'fact': fact, 'keys': [], 'queued_keys': [], 'turn': None, 'before': None}
        self.current = row
        if case not in ('idle', 'corrupt', 'C6'):
            key = 'p6-' + case + '-' + path
            self.io.phase('tool-held' if case == 'approval' else 'held')
            if case == 'goal':
                self.io.bind_goal_thread(t['id'])
                worker.native('thread/goal/set', {'threadId': t['id'], 'origin': 'user',
                    'objective': 'Disposable bounded continuity goal', 'status': 'active', 'tokenBudget': 100})
            params = self.protocol.key_params('turn/start', t, key, None)
            if case == 'ack-loss':
                if self.input_handler is None:
                    self.input_handler = InputHandler(self.io)
                row['input_receipt'] = self.input_handler.send(worker, t, key, discard_reply=True)
                result = None
            else:
                result = worker.native('turn/start', params)
            self.io.wait_fixture_request()
            self.protocol.persisted(c, t)
            worker.native('thread/resume', {'threadId': t['id'], 'path': t['path']})
            turns = self.protocol.pages(c, 'thread/turns/list', {'threadId': t['id'], 'itemsView': 'full'})
            active = [turn for turn in turns if turn.get('status') == 'inProgress']
            need(len(active) == 1, 'P6 requires one observed active turn')
            row['turn'] = active[0]['id']
            if result:
                need(result['turn']['id'] == row['turn'], 'CCC/native turn identity differs')
            if case == 'ack-loss':
                row['keys'] = [key]
            if case == 'queue':
                queued = 'p6-queued-' + path
                if self.input_handler is None:
                    self.input_handler = InputHandler(self.io)
                receipt = self.input_handler.send(worker, t, queued)
                need(receipt.get('queued') is True, 'CCC queue case was not queued')
                row['input_receipt'] = receipt
                row['keys'], row['queued_keys'] = [queued], [queued]
            if case == 'approval':
                self.io.release_phase()
                self.wait_status(worker, t, lambda state: state.get('thread_needs_approval')
                                 or state.get('pending_approval_request'))
        return self.snapshot(False)

    def wait_status(self, worker, t, predicate):
        end = time.monotonic() + min(30, self.io.budget.remaining_seconds())
        while True:
            states = worker.call('engine.query', {'engine': 'codex', 'operation': 'thread_states', 'args': {}})['states']
            state = states.get(t['id'], {})
            if predicate(state):
                return state
            need(time.monotonic() < end, 'actual CCC terminal/approval status not observed')
            time.sleep(0.05)

    def finish(self):
        row = self.current
        need(row, 'P6 scenario not prepared')
        worker, fact = self.worker('replacement' if row['path'] != 'refusal' else 'worker')
        worker.native('thread/resume', {'threadId': row['thread']['id'], 'path': row['thread']['path']})
        row['worker'], row['fact'] = worker, fact
        if row['turn']:
            if row['case'] == 'goal':
                count = self.io.fixture_completions()
                self.io.release_phase(next_held=True)
                self.io.wait_fixture_reservations(count + 1)
                row['goal_after'] = worker.native('thread/goal/get', {'threadId': row['thread']['id']})['goal']
                row['goal_fixture_tokens'] = self.io.fixture_tokens(row['thread']['id'])
                worker.native('thread/goal/clear', {'threadId': row['thread']['id'], 'origin': 'user'})
            self.io.phase('text')
            if row['case'] == 'approval':
                approval = self.wait_status(worker, row['thread'], lambda s: s.get('pending_approval_request'))
                request = approval['pending_approval_request']
                worker.call('engine.query', {'engine': 'codex', 'operation': 'approval', 'args': {
                    'session_id': row['thread']['id'], 'decision': 'accept',
                    'expected_request_id': request.get('request_id', request.get('id')),
                    'expected_nonce': request.get('approval_nonce')}})
            else:
                self.io.release_phase()
            self.io.settle(row['client'], row['thread'])
            self.io.verify_rollout_terminal(row['thread'], row['turn'], 'completed')
            self.wait_status(worker, row['thread'], lambda s: not s.get('active_turn_id') and s.get('status') != 'active')
        result = self.snapshot(True)
        row['client'].close()
        self.io.goal_thread = None
        self.current = None
        return result

    def snapshot(self, after):
        row = self.current
        t, c, worker = row['thread'], row['client'], row['worker']
        native = self.protocol.snapshot(c, t)
        states = worker.call('engine.query', {'engine': 'codex', 'operation': 'thread_states', 'args': {}})['states']
        state = states.get(t['id'])
        need(isinstance(state, dict), 'CCC does not track the disposable native thread')
        status = worker.call('engine.query', {'engine': 'codex', 'operation': 'status',
            'args': {'session_id': t['id']}})['status']
        items = native['items']
        keys = [i.get('clientId') for i in items if i.get('type') == 'userMessage' and i.get('clientId') in row['keys']]
        turns = native['turns']
        current = next((turn for turn in turns if turn.get('id') == row['turn']), {})
        # Raw receipt file is evidence. An absent receipt never becomes success
        # by copying the keys we hoped to observe from native history.
        receipts_path = self.io.root / 'inject-receipts.json'
        receipts = _json(receipts_path.read_bytes()) if receipts_path.exists() else {}
        self.io.record('p6_snapshot', native=native, ccc_state=state, public_status=status, inject_receipts=receipts)
        landed = [r['inject_id'] for r in receipts.get('_landed', []) if r.get('sid') == t['id']
                  and r.get('inject_id') in row['keys']]
        snapshot = {'daemon': self.io.native_identity(), 'thread_id': t['id'],
            'worker_identity': row['fact']['identity'], 'history': [turn['id'] for turn in turns],
            'turn_id': row['turn'], 'turn_status': current.get('status'),
            'ui_terminal': 'completed' if current.get('status') == 'completed'
                and not state.get('active_turn_id') and state.get('status') != 'active'
                and not status.get('active_item') else None,
            'accepted_keys': row['keys'], 'queued_keys': row.get('queued_keys', []),
            'consumed_keys': keys, 'inject_receipt_keys': landed,
            'inject_outstanding_keys': [receipts[t['id']]['inject_id']] if t['id'] in receipts else [],
            'native': native, 'ccc_state': state, 'public_status': status}
        if row['case'] in ('ack-loss', 'queue'):
            activity = self.io.root / 'logs/activity.log'
            need(activity.exists() and activity.stat().st_size <= 8 * 1024 * 1024,
                 'CCC activity markers missing or exceed disposable bound')
            lines = [line for line in activity.read_text().splitlines() if t['id'] in line]
            snapshot['activity_markers'] = lines
            need(lines, 'CCC input has no activity marker')
        if row['case'] == 'goal':
            snapshot['goal'] = row.get('goal_after') or worker.native('thread/goal/get', {'threadId': t['id']})['goal']
            snapshot['fixture_tokens'] = row.get('goal_fixture_tokens', self.io.fixture_tokens(t['id']))
        return snapshot
