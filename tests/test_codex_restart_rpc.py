"""Socket-pair RPC checks; no native Codex or fixture completion executes."""
import importlib.util
import json
from pathlib import Path
import socket
import threading

import pytest

from ccc_server import codex_managed_websocket as ws

SPEC = importlib.util.spec_from_file_location('codex_restart_rpc',
    Path(__file__).resolve().parents[1] / 'scripts' / 'codex_restart_rpc.py')
rpc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rpc)


def server_read(sock):
    first, second = ws.read_exact(sock, 2)
    assert first == 0x81 and second & 0x80
    size = second & 127
    if size in (126, 127):
        size = int.from_bytes(ws.read_exact(sock, 2 if size == 126 else 8), 'big')
    key = ws.read_exact(sock, 4)
    raw = ws.read_exact(sock, size)
    return json.loads(bytes(value ^ key[index % 4] for index, value in enumerate(raw)))


def server_send(sock, message):
    ws.send_frame(sock, 1, json.dumps(message).encode(), mask=False)


@pytest.fixture
def connection():
    left, right = socket.socketpair()
    right.settimeout(2)
    records = []
    client = rpc.Client(ws.BufferedSocket(left, b''), label='offline',
                        record=lambda event, **fields: records.append((event, fields)))
    yield client, right, records
    client.close()
    right.close()


def run_peer(callback):
    failures = []
    def run():
        try:
            callback()
        except Exception as exc:
            failures.append(exc)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, failures


def finish(peer):
    thread, failures = peer
    thread.join(timeout=2)
    assert not thread.is_alive() and not failures


def test_initialization_demultiplexes_interleaved_notifications_and_server_requests(connection):
    client, sock, records = connection
    def respond():
        request = server_read(sock)
        assert request['method'] == 'initialize'
        assert request['params']['capabilities'] == {'experimentalApi': True}
        server_send(sock, {'method': 'thread/started', 'params': {'threadId': 'fixture'}})
        server_send(sock, {'method': 'item/commandExecution/requestApproval', 'id': 'approval', 'params': {}})
        server_send(sock, {'id': request['id'], 'result': {'userAgent': 'offline'}})
        assert server_read(sock)['method'] == 'initialized'
        assert server_read(sock) == {'jsonrpc': '2.0', 'id': 'approval', 'result': {'decision': 'decline'}}
    peer = run_peer(respond)
    assert client.initialize() == {'userAgent': 'offline'}
    assert client.wait_notification('thread/started')['params']['threadId'] == 'fixture'
    client.answer('approval', {'decision': 'decline'})
    with pytest.raises(rpc.Stop):
        client.answer('approval', {})
    finish(peer)
    assert all('wall_ns' in fields and 'monotonic_ns' in fields for _, fields in records)


def test_uncertain_timeout_never_retries_and_wakes_reader(connection):
    client, sock, _ = connection
    with pytest.raises(rpc.Stop, match='unknown; do not retry'):
        client.request('turn/start', {'threadId': 'fixture', 'input': []}, timeout=0.02)
    assert server_read(sock)['method'] == 'turn/start'
    assert sock.recv(1) == b''
    assert client.closed and not client.reader.is_alive()


@pytest.mark.parametrize('reply,expected,success', [({'error': {'code': -32600}}, (-32600,), True),
    ({'error': {'code': -32600}}, (), False), ({'error': {'code': -32603}}, (-32600,), False),
    ({'result': {}}, (-32600,), False)])
def test_only_named_negative_control_errors_are_accepted(connection, reply, expected, success):
    client, sock, _ = connection
    def respond():
        key = server_read(sock)['id']
        server_send(sock, dict(reply, id=key))
    peer = run_peer(respond)
    if success:
        assert client.request('thread/resume', {}, expected_error_codes=expected)['error']['code'] == -32600
    else:
        with pytest.raises(rpc.Stop):
            client.request('thread/resume', {}, expected_error_codes=expected)
        assert client.closed
    finish(peer)


@pytest.mark.parametrize('message', [{'id': 987, 'result': {}}, {'id': True, 'result': {}},
    {'id': 1, 'result': {}, 'error': {}}, {'method': ''}])
def test_unmatched_or_malformed_response_is_failure(connection, message):
    client, sock, _ = connection
    def respond():
        server_read(sock)
        server_send(sock, message)
    peer = run_peer(respond)
    with pytest.raises(rpc.Stop):
        client.request('thread/read', {}, timeout=1)
    finish(peer)


def test_forbidden_rpc_is_rejected_before_writing(connection):
    client, sock, records = connection
    with pytest.raises(rpc.Stop, match='outside disposable'):
        client.request('account/login/start', {})
    assert not records


def test_notification_wait_has_a_bound_and_disconnect_unblocks(connection):
    client, sock, _ = connection
    with pytest.raises(rpc.Stop, match='no implicit retry'):
        client.wait_notification('thread/closed', timeout=0.01)
    client.close()
    with pytest.raises(rpc.Stop, match='disconnected'):
        client.wait_notification('thread/closed')


def test_concurrent_requests_accept_out_of_order_replies(connection):
    from concurrent.futures import ThreadPoolExecutor
    client, sock, _ = connection
    def respond():
        requests = [server_read(sock) for _ in range(3)]
        for request in reversed(requests):
            server_send(sock, {'id': request['id'], 'result': request['params']})
    peer = run_peer(respond)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(client.request, 'thread/read', {'threadId': str(index)}) for index in range(3)]
        assert [future.result(timeout=2) for future in futures] == [{'threadId': str(index)} for index in range(3)]
    finish(peer)


def test_duplicate_response_fails_connection_even_after_first_reply(connection):
    client, sock, _ = connection
    def respond():
        request = server_read(sock)
        duplicate = {'id': request['id'], 'result': {}}
        server_send(sock, duplicate)
        server_send(sock, duplicate)
    peer = run_peer(respond)
    try:
        client.request('thread/read', {}, timeout=1)
    except rpc.Stop:
        pass
    finish(peer)
    with pytest.raises(rpc.Stop):
        client.wait_notification('never', timeout=0.1)
    assert client.failure and client.closed
