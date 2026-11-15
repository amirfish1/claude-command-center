"""Offline identity, real RPC handler and existing queue-ledger recovery.

No native daemon, agent, model/provider, fixture server or service is started.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
from types import SimpleNamespace

import pytest
import server
from ccc_server import codex as codex_mod
from ccc_server import codex_managed_recovery as recovery
from ccc_server import codex_restart_gate as gate
from ccc_server import pending_inputs


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'CODEX_APP_SERVER_STATE_FILE', tmp_path / 'runtime.json')
    monkeypatch.setattr(server, 'PENDING_INPUTS_FILE', tmp_path / 'pending.json')
    monkeypatch.setattr(server, 'PENDING_INPUT_HANDOFF_DIR', tmp_path / 'handoffs')
    monkeypatch.setattr(server, '_pending_resume_queue', {})
    monkeypatch.setattr(server, '_pending_terminal_input_queue', {})
    monkeypatch.setattr(server, '_pending_terminal_handoff_ids', {})
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_THREAD_STATE', {})
    monkeypatch.setattr(server, '_app_server_trace', lambda *a, **k: None)
    monkeypatch.setattr(server, '_log_activity', lambda *a, **k: None)
    monkeypatch.setattr(server, '_save_codex_app_server_state_unlocked', lambda: True)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_RESPONSES', {})
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_TRANSPORT', None)
    monkeypatch.setattr(server, '_pending_writer_compatibility_status', lambda: {'ok': True})
    monkeypatch.setattr(server, '_codex_thread_registry_entry', lambda _sid: {})
    monkeypatch.setattr(server, '_codex_thread_registry_entries', lambda: {})
    monkeypatch.setattr(gate, '_frozen_until', 0)
    monkeypatch.setattr(gate, '_committed', False)
    monkeypatch.setattr(gate, '_fence_path', tmp_path / 'fence.json')
    monkeypatch.setattr(codex_mod, '_CODEX_APP_SERVER_MUTATING_PENDING', {})
    monkeypatch.setattr(codex_mod, '_CODEX_APP_SERVER_LAST_MSG_AT', 0)
    monkeypatch.setattr(pending_inputs, '_pending_input_recovery_registry', {})
    monkeypatch.setattr(pending_inputs, '_pending_primary_conflicts', {})
    monkeypatch.setattr(pending_inputs, '_pending_inputs_file_token', None, raising=False)
    recovery._local.claim = None


def identity():
    return dict(recovery.process_identity(), socket_device=1, socket_inode=2,
                directory_device=1, directory_inode=3,
                binary_sha256=next(iter(recovery.SUPPORTED)), version='0.169.0',
                protocol_sha256=next(iter(recovery.SUPPORTED.values()))[1])


class Native:
    kind = 'managed-unix'
    proc = None
    reader_exited = False
    started_at = 0

    def __init__(self):
        self.sent = []
        self.reply = True
        self.at_wire = None
        recovery.connected(self, identity())
        self.managed_recovering = False
        recovery.track(self, 'thread-a', '/state/rollout.jsonl')

    def send_json(self, payload):
        if self.at_wire:
            self.at_wire(payload)
        self.sent.append(payload)
        if self.reply:
            method = payload['method']
            key = (payload.get('params') or {}).get('clientUserMessageId')
            result = ({'turnId': 'turn-a'} if method == 'turn/steer' else
                      {'queuedSubmission': {'id': 'queue-a', 'clientUserMessageId': key}} if method == 'thread/queue/add' else
                      {'turn': {'id': 'turn-a'}})
            server._codex_app_server_handle_message({'id': payload['id'], 'result': result}, source_transport=self)

    def alive(self):
        return True


@pytest.fixture
def native(monkeypatch):
    transport = Native()
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_TRANSPORT', transport)
    return transport


def send(native, key='entry-a', method='turn/start', timeout=.01):
    return server._codex_app_server_request_to_transport(native, method,
        {'threadId': 'thread-a', 'clientUserMessageId': key,
         'input': [{'type': 'text', 'text': 'same text'}]}, timeout=timeout)


def records():
    return list(server._pending_input_claim_journal_dir().glob('*.json'))


@pytest.mark.parametrize('mode', [0o755, 0o710, 0o777])
def test_socket_parent_must_be_private(tmp_path, mode):
    root = tmp_path / 'socket'; root.mkdir(mode=mode); root.chmod(mode)
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(root / 'daemon.sock')); (root / 'daemon.sock').chmod(0o600)
        with pytest.raises(recovery.Refused, match='private'):
            recovery.socket_path_identity(root / 'daemon.sock')
    finally:
        sock.close()


def test_socket_inode_mode_and_alias(tmp_path):
    tmp_path.chmod(0o700)
    target = tmp_path / 'daemon.sock'
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(target)); target.chmod(0o600)
        assert recovery.socket_path_identity(target)['socket_inode'] == target.stat().st_ino
        alias = tmp_path / 'alias'; alias.symlink_to(target)
        with pytest.raises(recovery.Refused):
            recovery.socket_path_identity(alias)
        target.chmod(0o660)
        with pytest.raises(recovery.Refused):
            recovery.socket_path_identity(target)
    finally:
        sock.close()


def test_real_peer_credentials_are_checked(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    listener = socket.socket(socket.AF_UNIX)
    address = tmp_path / 'daemon.sock'
    listener.bind(str(address)); address.chmod(0o600); listener.listen()
    client = socket.socket(socket.AF_UNIX); client.connect(str(address))
    peer, _ = listener.accept()
    try:
        # This is our own ordinary Python process, so the real scope check
        # MUST refuse. The positive subcase substitutes scope policy only;
        # kernel peer credentials/start/executable/path reads remain real.
        with pytest.raises(recovery.Refused, match='process group/session'):
            recovery.observe_peer(client, address)
        binary = hashlib.sha256(Path('/proc/self/exe').read_bytes()).hexdigest()
        monkeypatch.setattr(recovery, 'SUPPORTED', {binary: ('test', 'schema')})
        monkeypatch.setattr(recovery, 'outside_worker_scope', lambda pid: None)
        fact = recovery.observe_peer(client, address)
        assert fact['pid'] == os.getpid() and fact['binary_sha256'] == binary
    finally:
        client.close(); peer.close(); listener.close()


@pytest.mark.parametrize('other', ['/worker', '/worker/child'])
def test_cgroup_ancestry_refuses(monkeypatch, other):
    monkeypatch.setattr(recovery.os, 'getpgid', lambda pid: pid)
    monkeypatch.setattr(recovery.os, 'getsid', lambda pid: pid)
    monkeypatch.setattr(recovery, '_cgroups', lambda pid: {('0', ''): '/worker' if pid == os.getpid() else other})
    with pytest.raises(recovery.Refused, match='cgroup'):
        recovery.outside_worker_scope(os.getpid() + 1)


def test_independent_scope_and_unknown_scope(monkeypatch):
    monkeypatch.setattr(recovery.os, 'getpgid', lambda pid: pid)
    monkeypatch.setattr(recovery.os, 'getsid', lambda pid: pid)
    monkeypatch.setattr(recovery, '_cgroups', lambda pid: {('0', ''): '/worker' if pid == os.getpid() else '/daemon'})
    recovery.outside_worker_scope(os.getpid() + 1)
    monkeypatch.setattr(recovery, '_cgroups', lambda pid: {('0', ''): '/'} if pid == os.getpid() else {})
    with pytest.raises(recovery.Refused):
        recovery.outside_worker_scope(os.getpid() + 1)


def test_legacy_writers_and_version_refuse(monkeypatch):
    monkeypatch.delenv('CCC_WORKER_PROCESS', raising=False)
    monkeypatch.setattr(server, '_get_worker_compat_cache', lambda: {'reachable': True, 'capabilities': []})
    with pytest.raises(recovery.Refused, match='control-plane'):
        recovery.compatible_writers()
    monkeypatch.setattr(server, '_get_worker_compat_cache', lambda: {'reachable': True, 'capabilities': [recovery.CAPABILITY]})
    monkeypatch.setattr(server, '_read_registry_pruned', lambda: [{'pid': -1}])
    with pytest.raises(recovery.Refused, match='dashboard'):
        recovery.compatible_writers()
    monkeypatch.setattr(server, '_read_registry_pruned', lambda: [{'pid': -1, 'managed_recovery_protocol': 1}])
    recovery.compatible_writers()
    recovery.verify_initialize(identity(), {'result': {'userAgent': 'codex_cli_rs/0.169.0 (Linux)'}})
    with pytest.raises(recovery.Refused, match='version'):
        recovery.verify_initialize(identity(), {'result': {'userAgent': 'codex_cli_rs/0.169.01'}})


@pytest.mark.parametrize('method', list(recovery.INPUT_METHODS))
def test_real_request_handler_fsyncs_identity_before_frame_and_records_ack(native, method):
    phases = []
    def at_wire(payload):
        rows = [json.loads(p.read_text()) for p in records()]
        assert len(rows) == 1
        row = rows[0]
        phases.append(row['managed']['phase'])
        assert row['managed']['entry_id'] == payload['params']['clientUserMessageId'] == 'entry-a'
        assert row['managed']['worker'] == recovery.process_identity()
        assert row['managed']['daemon'] == native.managed_identity
        assert row['managed_sha256'] == recovery._sha(row['managed'])
    native.at_wire = at_wire
    assert 'result' in send(native, method=method)
    assert phases == ['send_intent']
    saved = json.loads(records()[0].read_text())
    assert saved['managed']['phase'] == 'acknowledged'
    assert saved['managed']['native_receipt']
    # A second equal-text input has a distinct stable id and remains a send.
    native.at_wire = None
    assert 'result' in send(native, key='entry-b', method=method)
    assert len(native.sent) == 2
    # An acknowledged id must not be sent twice after a caller loses its reply.
    assert send(native, key='entry-a', method=method)['sent'] is False
    assert len(native.sent) == 2


def test_lost_reply_holds_same_key_cross_method_and_later_fifo_inputs(native, monkeypatch):
    native.reply = False
    result = send(native)
    assert result['ambiguous']
    assert json.loads(records()[0].read_text())['managed']['phase'] == 'send_intent'
    for method, key in [('turn/start', 'entry-a'), ('turn/steer', 'entry-a'), ('turn/start', 'entry-b')]:
        assert send(native, key, method)['sent'] is False
    assert len(native.sent) == 1
    monkeypatch.setattr(recovery, 'exact_process_dead', lambda _: True)
    server._recover_pending_input_claim_journals()
    assert not server._pending_resume_queue
    assert server._pending_input_recovery_blocked('thread-a')
    # An exact late RPC ACK resolves this request without resending it.
    server._codex_app_server_handle_message({'id': native.sent[0]['id'], 'result': {'turn': {'id': 'turn-a'}}}, source_transport=native)
    assert json.loads(records()[0].read_text())['managed']['phase'] == 'acknowledged'


def test_durable_claim_exists_before_primary_pop_and_unsent_recovers_once(native, monkeypatch):
    item = server._PendingInputEntry('same text', 'entry-a')
    next_item = server._PendingInputEntry('same text', 'entry-b')
    server._pending_resume_queue['thread-a'] = [item, next_item]
    assert server._save_pending_inputs({'thread-a'})
    claim = server._claim_matching_pending_input('thread-a', 'same text', pending_id='entry-a')
    assert claim['managed']['phase'] == 'claimed' and records()
    assert [r.pending_id for r in server._pending_resume_queue['thread-a']] == ['entry-b']
    server._recover_pending_input_claim_journals()
    assert [r.pending_id for r in server._pending_resume_queue['thread-a']] == ['entry-b']
    monkeypatch.setattr(recovery, 'exact_process_dead', lambda _: True)
    server._recover_pending_input_claim_journals()
    server._recover_pending_input_claim_journals()
    assert [r.pending_id for r in server._pending_resume_queue['thread-a']] == ['entry-a', 'entry-b']
    assert not records()


def test_real_claim_send_and_commit_or_unknown_rollback(native):
    server._pending_resume_queue['thread-a'] = [server._PendingInputEntry('same text', 'entry-a')]
    assert server._save_pending_inputs({'thread-a'})
    claim = server._claim_matching_pending_input('thread-a', 'same text', pending_id='entry-a')
    with recovery.queued_claim(claim):
        assert server._retry_pending_input_recovery('thread-a')
        assert 'result' in send(native)
    assert claim['managed']['phase'] == 'acknowledged'
    assert server._commit_pending_input_claim(claim)
    assert not records()
    server._pending_resume_queue['thread-a'] = [server._PendingInputEntry('same text', 'entry-b')]
    assert server._save_pending_inputs({'thread-a'})
    claim = server._claim_matching_pending_input('thread-a', 'same text', pending_id='entry-b')
    native.reply = False
    with recovery.queued_claim(claim):
        assert send(native, 'entry-b')['ambiguous']
    assert not server._restore_pending_input_claim(claim)
    assert not server._commit_pending_input_claim(claim)
    assert len(records()) == 1


def test_fsync_failure_prevents_native_frame(native, monkeypatch):
    monkeypatch.setattr(server, '_write_pending_input_claim_journal', lambda _: 'failed')
    assert send(native)['sent'] is False
    assert not native.sent


def test_corrupt_handoff_quarantines_and_blocks_managed_only(native):
    native.reply = False
    send(native)
    target = records()[0]; saved = json.loads(target.read_text())
    saved['managed']['phase'] = 'claimed'  # leave its checksum invalid
    target.write_text(json.dumps(saved))
    assert server._validate_pending_claim_journal(target, saved) is None
    assert list(target.parent.glob('*.invalid'))
    assert server._pending_input_recovery_blocked('thread-a')
    assert not server._pending_input_recovery_blocked('unrelated-stdio')
    assert send(native, 'entry-b')['sent'] is False


def test_managed_thread_cannot_fall_back_to_stdio(native):
    stdio = SimpleNamespace(kind='stdio', proc=None, sent=[])
    stdio.send_json = stdio.sent.append
    response = server._codex_app_server_request_to_transport(stdio, 'thread/resume', {'threadId': 'thread-a'}, timeout=.01)
    assert response['sent'] is False and not stdio.sent


def test_changed_epoch_and_corruption_never_unlock_input(native):
    other = dict(native.managed_identity, start_ticks=native.managed_identity['start_ticks'] + 1)
    recovery.connected(native, other)
    recovery.reattach(native, lambda *args: {'result': {'data': [], 'nextCursor': None}})
    assert recovery.known_thread('thread-a')['reason'] == 'daemon_epoch_changed'
    assert send(native)['sent'] is False
    target = server.CODEX_APP_SERVER_STATE_FILE.with_name('codex-managed-recovery.json')
    target.write_text('{broken')
    with pytest.raises(recovery.Refused, match='corrupt'):
        recovery.protect_stdio('turn/start', {'threadId': 'thread-a'})


def test_exact_death_never_equates_live_pid_with_dead(monkeypatch):
    own = recovery.process_identity()
    assert not recovery.exact_process_dead(own)
    assert recovery.exact_process_dead(dict(own, start_ticks=own['start_ticks'] - 1))
    monkeypatch.setattr(recovery, 'process_identity', lambda pid: (_ for _ in ()).throw(PermissionError()))
    assert not recovery.exact_process_dead(own)
    assert not recovery.exact_process_dead({})


def test_reattach_only_owned_threads_reads_authority_and_keeps_missing_resume_unproven(native, monkeypatch):
    from time import monotonic
    calls = []
    def rpc(method, params, timeout):
        calls.append((method, params))
        if method == 'thread/loaded/list':
            return {'result': {'data': ['foreign', 'thread-a'], 'nextCursor': None}}
        assert params['threadId'] == 'thread-a'
        if method == 'thread/resume':
            return {'result': {'thread': {'id': 'thread-a'}}}
        if method == 'thread/read':
            return {'result': {'thread': {'id': 'thread-a', 'status': {'type': 'active'}}}}
        if method == 'thread/turns/list':
            return {'result': {'data': [{'id': 'turn-a', 'status': 'inProgress'}], 'nextCursor': None}}
        if method == 'thread/queue/list':
            return {'result': {'data': [{'id': 'queue-a'}], 'nextCursor': None}}
        if method == 'thread/goal/get':
            return {'result': {'goal': {'status': 'active'}}}
        raise AssertionError(method)
    recovery.connected(native, identity())
    with recovery.recovery_reads():
        recovery.reattach(native, rpc)
    assert recovery.known_thread('thread-a')['active_turn_id'] == 'turn-a'
    assert server._CODEX_APP_SERVER_THREAD_STATE['thread-a']['active_turn_id'] == 'turn-a'
    assert native.managed_subscriptions == {'thread-a'}
    assert not any('unsubscribe' in m for m, _ in calls)
    assert server._codex_unsubscribe_idle_thread('thread-a') is False
    recovery.connected(native, identity())
    recovery.reattach(native, lambda method, *_: {'result': {'data': ['thread-a']}} if method == 'thread/loaded/list' else {'error': {'code': -32600}})
    assert recovery.known_thread('thread-a')['reason'] == 'resume_unproven:-32600'
    assert recovery.known_thread('thread-a')['status'] == 'needs_reconciliation'


def test_page_caps_and_duplicate_snapshots_refuse(native):
    recovery.connected(native, identity())
    recovery.reattach(native, lambda *args: {'result': {'data': ['thread-a'], 'nextCursor': 'same'}})
    assert recovery.known_thread('thread-a')['status'] == 'needs_reconciliation'
    assert 'cursor' in recovery.known_thread('thread-a')['reason']


def test_socketpair_real_masked_frame_reader_and_durable_rpc_ack(monkeypatch):
    import threading
    from ccc_server import codex_managed_websocket as wire
    left, right = socket.socketpair()
    right.settimeout(2)
    transport = server._CodexAppServerTransport('managed-unix', sock=wire.BufferedSocket(left, b''))
    recovery.connected(transport, identity()); transport.managed_recovering = False
    recovery.track(transport, 'thread-a', '/state/rollout.jsonl')
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_TRANSPORT', transport)
    output = {}
    sender = threading.Thread(target=lambda: output.update(send(transport, timeout=2)))
    reader = threading.Thread(target=server._codex_app_server_reader, args=(transport,))
    try:
        reader.start(); sender.start()
        header = wire.read_exact(right, 2)
        assert header[0] == 0x81 and header[1] & 0x80
        length = header[1] & 0x7f
        if length == 126:
            length = struct.unpack('!H', wire.read_exact(right, 2))[0]
        elif length == 127:
            length = struct.unpack('!Q', wire.read_exact(right, 8))[0]
        mask = wire.read_exact(right, 4)
        encoded = wire.read_exact(right, length)
        request = json.loads(bytes(byte ^ mask[i % 4] for i, byte in enumerate(encoded)))
        # Real bytes are on the socket only after durable send_intent exists.
        saved = json.loads(records()[0].read_text())
        assert saved['managed']['phase'] == 'send_intent'
        assert saved['managed']['params'] == request['params']
        wire.send_frame(right, 1, json.dumps({'id': request['id'], 'result': {'turn': {'id': 'real-wire-turn'}}}).encode(), mask=False)
        sender.join(3)
        assert not sender.is_alive() and 'result' in output
        assert json.loads(records()[0].read_text())['managed']['native_receipt'] == 'real-wire-turn'
    finally:
        transport.close(); right.close(); reader.join(3); sender.join(3)
    assert not reader.is_alive() and transport.reader_exited
    assert recovery.known_thread('thread-a')['reason'] == 'managed_disconnected'


def test_claim_before_primary_pop_crash_does_not_duplicate_existing_row(native, monkeypatch):
    item = server._PendingInputEntry('same text', 'entry-a')
    server._pending_resume_queue['thread-a'] = [item]
    assert server._save_pending_inputs({'thread-a'})
    claim = {'session_id': 'thread-a', 'queue_name': 'resume', 'index': 0,
             'item': item, 'claim_sequence': 1}
    recovery.prepare_claim(claim)  # crash before primary pop/persist
    monkeypatch.setattr(recovery, 'exact_process_dead', lambda _: True)
    server._recover_pending_input_claim_journals()
    assert [x.pending_id for x in server._pending_resume_queue['thread-a']] == ['entry-a']
    assert not records()


def test_native_queue_ack_recovery_closes_exact_action_without_copying_queue(native, monkeypatch):
    from ccc_server import codex_queue_owner as owner
    owner.begin_native_queue_action('thread-a', 'native-action-a')
    with recovery.native_action('native-action-a'):
        assert 'result' in send(native, 'entry-a', 'thread/queue/add')
    saved = json.loads(records()[0].read_text())
    assert saved['managed']['queue_action_id'] == 'native-action-a'
    assert owner._inflight_file('thread-a').exists()
    monkeypatch.setattr(recovery, 'exact_process_dead', lambda _: True)
    server._recover_pending_input_claim_journals()
    assert not records() and not owner._inflight_file('thread-a').exists()
    assert owner.native_queue_owned('thread-a')
    assert not server._pending_resume_queue


def test_uncertain_native_input_holds_ownership_even_if_action_list_missing(native):
    from ccc_server import codex_queue_owner as owner
    owner.begin_native_queue_action('thread-a', 'native-action-a')
    native.reply = False
    with recovery.native_action('native-action-a'):
        assert send(native, 'entry-a', 'thread/queue/add')['ambiguous']
    # Damage/missing old action bookkeeping does not turn a missing reply
    # into evidence that the queue was empty and safe to transfer.
    owner._inflight_file('thread-a').unlink()
    with pytest.raises(ValueError, match='uncertain managed'):
        owner.release_native_queue('thread-a', lambda: {'ok': True, 'result': {'data': []}})
    result = send(native, 'entry-a', 'thread/queue/add')
    assert result['sent'] is False and result['ambiguous'] is True
    assert owner.native_queue_owned('thread-a')


def test_resume_intent_survives_lost_reply_and_prevents_second_writer(native):
    native.reply = False
    result = server._codex_app_server_request_to_transport(native, 'thread/resume',
        {'threadId': 'thread-new', 'path': '/state/new-rollout.jsonl'}, timeout=.01)
    assert result['ambiguous']
    assert recovery.known_thread('thread-new')['reason'] == 'resume_intent'
    assert 'thread-new' not in native.managed_subscriptions
    with pytest.raises(recovery.Refused, match='second stdio'):
        recovery.protect_stdio('turn/start', {'threadId': 'thread-new'})


def test_recovery_notification_race_refuses_stale_snapshot(native):
    recovery.connected(native, identity())
    def rpc(method, params, timeout):
        if method == 'thread/loaded/list':
            return {'result': {'data': ['thread-a']}}
        if method == 'thread/resume':
            return {'result': {'thread': {'id': 'thread-a'}}}
        if method == 'thread/read':
            return {'result': {'thread': {'id': 'thread-a'}}}
        if method in ('thread/turns/list', 'thread/queue/list'):
            return {'result': {'data': []}}
        if method == 'thread/goal/get':
            recovery.notification(native, {'method': 'thread/queue/changed', 'params': {'threadId': 'thread-a'}})
            return {'result': {'goal': None}}
        raise AssertionError(method)
    recovery.reattach(native, rpc)
    assert recovery.known_thread('thread-a')['status'] == 'needs_reconciliation'
    assert 'snapshot changed' in recovery.known_thread('thread-a')['reason']


def test_notifications_are_io_free_and_never_adopt_foreign_threads(native, monkeypatch):
    monkeypatch.setattr(recovery, '_state', lambda *a: (_ for _ in ()).throw(AssertionError('notification I/O')))
    for _ in range(50):
        recovery.notification(native, {'method': 'item/agentMessage/delta', 'params': {'threadId': 'thread-a', 'delta': 'x'}})
        recovery.notification(native, {'method': 'turn/started', 'params': {'threadId': 'foreign'}})
    for _ in range(2):
        recovery.notification(native, {'method': 'turn/started', 'params': {'threadId': 'thread-a'}})
    assert native.managed_revisions == {'thread-a': 2}
    assert native.managed_tracked == {'thread-a'}


def test_retired_reader_cannot_mark_new_connection_disconnected(native, monkeypatch):
    replacement = Native()
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_TRANSPORT', replacement)
    replacement.managed_recovering = False
    with recovery._state(True) as state:
        state['threads']['thread-a'].update(status='ready', reason=None)
    recovery.disconnected(native)
    assert recovery.known_thread('thread-a')['status'] == 'ready'


def test_successful_queue_transaction_uses_exact_id_at_real_rpc_handler(native, monkeypatch):
    item = server._PendingInputEntry('same text', 'entry-a')
    server._pending_resume_queue['thread-a'] = [item, server._PendingInputEntry('same text', 'entry-b')]
    assert server._save_pending_inputs({'thread-a'})
    monkeypatch.setattr(server, '_deliver_with_auto_resume_barrier', lambda _sid, _text, fn: fn())
    def deliver(sid, text, **kwargs):
        # Exercise the real transaction context; no test-supplied native key.
        result = server._codex_app_server_request_to_transport(native, 'turn/start',
            {'threadId': sid, 'input': [{'type': 'text', 'text': text}]}, timeout=.01)
        return {'ok': 'result' in result, 'turn_id': 'turn-a'}
    monkeypatch.setattr(server, '_resume_session_codex_native_delivery', deliver)
    result = server._codex_queued_delivery_transaction('thread-a')
    assert result['delivered']
    assert native.sent[0]['params']['clientUserMessageId'] == 'entry-a'
    assert [x.pending_id for x in server._pending_resume_queue['thread-a']] == ['entry-b']
    assert not records()


def test_legacy_managed_registry_never_becomes_stdio_or_new_epoch_approval(native, monkeypatch):
    legacy = {'transport': 'managed', 'transport_owner': 'ccc-managed-app-server'}
    monkeypatch.setattr(server, '_codex_thread_registry_entry', lambda sid: legacy if sid == 'legacy' else {})
    monkeypatch.setattr(server, '_codex_thread_registry_entries', lambda: {'legacy': legacy,
        'old-stdio': dict(legacy, transport='stdio'), 'foreign': {'transport': 'managed'}})
    with pytest.raises(recovery.Refused, match='second stdio'):
        recovery.protect_stdio('thread/resume', {'threadId': 'legacy'})
    recovery.connected(native, identity())
    assert native.managed_tracked == {'legacy', 'thread-a'}
    assert 'old-stdio' not in native.managed_tracked and 'foreign' not in native.managed_tracked
    recovery.reattach(native, lambda *args: {'result': {'data': ['legacy', 'thread-a']}})
    assert recovery.known_thread('legacy')['status'] == 'needs_reconciliation'


def test_real_ensure_initializes_before_recovery_and_rechecks_identity(monkeypatch, tmp_path):
    endpoint = tmp_path / 'daemon.sock'; endpoint.touch()
    transport = SimpleNamespace(kind='managed-unix', proc=None, sock=object(),
        started_at=0, reader_exited=False, managed_candidate_identity=identity())
    transport.alive = lambda: True
    sent = []
    def send_json(payload):
        sent.append(payload['method'])
        if 'id' not in payload:
            return
        result = {'userAgent': 'codex_cli_rs/0.169.0'} if payload['method'] == 'initialize' else {'data': []}
        server._codex_app_server_handle_message({'id': payload['id'], 'result': result}, source_transport=transport)
    transport.send_json = send_json
    transport.close = lambda: None
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_INITIALIZING', False)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_INITIALIZED', False)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_PROC', None)
    monkeypatch.setattr(server, '_codex_managed_app_server_enabled', lambda: True)
    monkeypatch.setattr(server, '_codex_managed_app_server_socket_path', lambda: endpoint)
    monkeypatch.setattr(server, '_connect_codex_managed_app_server', lambda _: transport)
    monkeypatch.setattr(codex_mod, '_codex_managed_in_cooldown', lambda: False)
    monkeypatch.setattr(codex_mod, '_codex_app_server_reader', lambda _: None)
    observations = []
    def observe(*args):
        observations.append(1)
        return identity()
    monkeypatch.setattr(recovery, 'observe_peer', observe)
    assert server._ensure_codex_app_server(allow_stdio=False) is transport
    assert sent == ['initialize', 'initialized', 'thread/loaded/list']
    assert len(observations) == 2  # connect's own check is separately covered.
    assert transport.managed_recovering is False


def test_ack_recovery_retains_journal_when_primary_save_fails(native, monkeypatch):
    assert 'result' in send(native)
    monkeypatch.setattr(recovery, 'exact_process_dead', lambda _: True)
    real = server._apply_pending_input_operations
    monkeypatch.setattr(server, '_apply_pending_input_operations', lambda *a, **k: {'ok': False})
    server._recover_pending_input_claim_journals()
    assert len(records()) == 1
    assert json.loads(records()[0].read_text())['managed']['phase'] == 'acknowledged'
    monkeypatch.setattr(server, '_apply_pending_input_operations', real)
    server._recover_pending_input_claim_journals()
    assert not records()


def test_concurrent_intent_checks_hold_one_writer(native, monkeypatch):
    import threading
    import time
    real = recovery._records
    def slow_records(sid):
        result = real(sid)
        time.sleep(.03)
        return result
    monkeypatch.setattr(recovery, '_records', slow_records)
    barrier = threading.Barrier(2)
    outcomes = []
    def attempt(key):
        barrier.wait(timeout=2)
        try:
            recovery.before_send(native, 'turn/start', {'threadId': 'thread-a',
                'clientUserMessageId': key, 'input': [{'type': 'text', 'text': 'same'}]}, key)
            outcomes.append('intent')
        except recovery.Refused:
            outcomes.append('held')
    threads = [threading.Thread(target=attempt, args=(key,)) for key in ('a', 'b')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert sorted(outcomes) == ['held', 'intent']
    assert len(records()) == 1


def test_unknown_worker_and_unpinned_queue_reply_fail_closed(native, monkeypatch):
    monkeypatch.delenv('CCC_WORKER_PROCESS', raising=False)
    monkeypatch.setattr(server, '_get_worker_compat_cache', lambda: {'reachable': False})
    with pytest.raises(recovery.Refused, match='control-plane'):
        recovery.compatible_writers()
    native.reply = False
    send(native, method='thread/queue/add')
    request_id = native.sent[-1]['id']
    server._codex_app_server_handle_message({'id': request_id,
        'result': {'submission': {'id': 'foreign', 'clientUserMessageId': 'entry-a'}}}, source_transport=native)
    assert json.loads(records()[0].read_text())['managed']['phase'] == 'send_intent'


def test_managed_subagent_error_never_resumes_an_unowned_parent(native, monkeypatch):
    error = {'error': {'message': 'sub-agent: resume the parent first'}}
    monkeypatch.setattr(codex_mod, '_codex_app_server_request_once', lambda *a, **k: error)
    monkeypatch.setattr(codex_mod, '_codex_resume_subagent_via_parent',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('foreign parent adoption')))
    assert server._codex_app_server_request('thread/resume', {'threadId': 'thread-a'}) == error


def test_malformed_saved_daemon_and_empty_ack_refuse(native):
    assert 'result' in send(native)
    record = json.loads(records()[0].read_text())['managed']
    record['native_receipt'] = ''
    with pytest.raises(recovery.Refused, match='corrupt'):
        recovery.validate_handoff(record, 'entry-a')
    saved = server.CODEX_APP_SERVER_STATE_FILE.with_name('codex-managed-recovery.json')
    payload = json.loads(saved.read_text())
    payload['daemon']['pid'] = 'not-a-pid'
    saved.write_text(json.dumps(payload))
    with pytest.raises(recovery.Refused, match='corrupt'):
        recovery.known_thread('thread-a')
