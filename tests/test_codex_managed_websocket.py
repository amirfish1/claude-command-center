"""Real local AF_UNIX framing checks; no Codex process, credentials or turns."""
import base64
import hashlib
import socket
import threading

import pytest

from ccc_server import codex_managed_websocket as ws


def frame(payload, opcode=1, *, fin=True):
    length = len(payload)
    header = bytes([(0x80 if fin else 0) | opcode, length if length < 126 else 126 if length <= 65535 else 127])
    if length >= 126:
        header += length.to_bytes(2 if length <= 65535 else 8, 'big')
    return header + payload


@pytest.fixture
def peer(tmp_path):
    listener = socket.socket(socket.AF_UNIX)
    name = tmp_path / 'mock.sock'
    listener.bind(str(name))
    listener.listen(1)
    listener.settimeout(2)
    threads, failures = [], []
    def serve(callback):
        def run():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(2)
                    raw = b''
                    while b'\r\n\r\n' not in raw:
                        raw += connection.recv(4096)
                    key = dict(line.split(': ', 1) for line in raw.decode().split('\r\n')[1:] if ': ' in line)['Sec-WebSocket-Key']
                    accept = base64.b64encode(hashlib.sha1((key + ws._GUID).encode()).digest()).decode()
                    header = ('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                              'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n').encode()
                    callback(connection, header)
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        threads.append(thread)
        thread.start()
        return name
    yield serve
    listener.close()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert not failures


def test_coalesced_first_notification_and_masked_client_request(peer):
    observed = []
    def respond(connection, header):
        connection.sendall(header + frame(b'{"method":"ready"}'))
        first, second = ws.read_exact(connection, 2)
        assert first == 0x81 and second & 0x80
        key = ws.read_exact(connection, 4)
        raw = ws.read_exact(connection, second & 127)
        observed.append(bytes(value ^ key[index % 4] for index, value in enumerate(raw)))
        connection.sendall(frame(b'{"id":1,"result":{}}'))
    client = ws.connect(peer(respond))
    try:
        assert ws.recv_text(client) == '{"method":"ready"}'
        ws.send_text(client, '{"id":1}')
        assert ws.recv_text(client) == '{"id":1,"result":{}}'
        assert observed == [b'{"id":1}']
    finally:
        client.close()


@pytest.mark.parametrize('mutation', ['missing_accept', 'bad_accept', 'no_upgrade', 'wrong_connection',
    'duplicate_accept', 'wrong_status', 'unterminated'])
def test_invalid_upgrade_fails_closed(peer, mutation):
    def respond(connection, header):
        lines = header.split(b'\r\n')
        if mutation == 'missing_accept': lines = [line for line in lines if not line.startswith(b'Sec-WebSocket-Accept:')]
        if mutation == 'bad_accept': lines[3] = b'Sec-WebSocket-Accept: wrong'
        if mutation == 'no_upgrade': lines[1] = b'Upgrade: other'
        if mutation == 'wrong_connection': lines[2] = b'Connection: close'
        if mutation == 'duplicate_accept': lines.insert(4, lines[3])
        if mutation == 'wrong_status': lines[0] = b'HTTP/1.1 200 OK'
        result = b'\r\n'.join(lines)
        if mutation == 'unterminated': result = result.rstrip(b'\r\n')
        connection.sendall(result)
    with pytest.raises(OSError):
        ws.connect(peer(respond), timeout=1)


@pytest.mark.parametrize('size', [0, 125, 126, 65535, 65536, 70000])
def test_server_frame_lengths(size):
    receiver, sender = socket.socketpair()
    writer = threading.Thread(target=lambda: (sender.sendall(frame(b'x' * size)), sender.close()), daemon=True)
    writer.start()
    try:
        assert ws.recv_text(receiver) == 'x' * size
    finally:
        receiver.close()
        writer.join(timeout=2)
        assert not writer.is_alive()


def test_fragmented_utf8_with_interleaved_ping_and_pong():
    receiver, sender = socket.socketpair()
    try:
        payload = 'hi \u2603'.encode()
        sender.sendall(frame(payload[:4], fin=False) + frame(b'ping', opcode=9)
                       + frame(b'pong', opcode=10) + frame(payload[4:], opcode=0))
        assert ws.recv_text(receiver) == 'hi \u2603'
        first, second = ws.read_exact(sender, 2)
        assert first == 0x8a and second == 0x84
        key = ws.read_exact(sender, 4)
        data = ws.read_exact(sender, 4)
        assert bytes(value ^ key[index % 4] for index, value in enumerate(data)) == b'ping'
    finally:
        receiver.close()
        sender.close()


@pytest.mark.parametrize('raw', [b'\x80\x00', b'\x82\x00', b'\xc1\x00', b'\x81\x80',
    b'\x09\x00', b'\x89\x7e\x00\x7e', b'\x81\x7e\x00\x01',
    b'\x81\x7f\x80\x00\x00\x00\x00\x00\x00\x00',
    b'\x81\x7f\x00\x00\x00\x00\x04\x00\x00\x01',
    frame(b'\xff'), frame(b'x', opcode=8), frame(b'')[:1],
    frame(b'a', fin=False) + frame(b'b')])
def test_invalid_fragment_control_utf8_size_and_eof_fail_without_waiting(raw):
    receiver, sender = socket.socketpair()
    receiver.settimeout(1)
    try:
        sender.sendall(raw)
        sender.shutdown(socket.SHUT_WR)
        with pytest.raises(OSError):
            ws.recv_text(receiver)
    finally:
        receiver.close()
        sender.close()


def test_actual_managed_transport_wrappers_preserve_upgrade_frame(peer, monkeypatch):
    from ccc_server import codex, codex_managed_recovery as recovery
    # Framing-only fixture is ordinary Python in this process, not a pinned
    # native daemon. Identity rejection is covered by managed-recovery tests.
    monkeypatch.setattr(recovery, 'compatible_writers', lambda: None)
    monkeypatch.setattr(recovery, 'socket_path_identity', lambda _: {})
    monkeypatch.setattr(recovery, 'observe_peer', lambda *_: {})
    def respond(connection, header):
        connection.sendall(header + frame(b'{"method":"first"}'))
    transport = codex._connect_codex_managed_app_server(peer(respond))
    try:
        assert transport.kind == 'managed-unix'
        assert codex._websocket_recv_text(transport.sock) == '{"method":"first"}'
    finally:
        transport.close()


@pytest.mark.parametrize('size', [125, 126, 65535, 65536])
def test_client_extended_lengths_and_mask(size):
    class Capture:
        def sendall(self, raw): self.raw = raw
    capture = Capture()
    ws.send_text(capture, 'y' * size)
    first, second = capture.raw[:2]
    assert first == 0x81 and second & 0x80
    offset = 2
    length = second & 127
    if length in (126, 127):
        count = 2 if length == 126 else 8
        length = int.from_bytes(capture.raw[offset:offset + count], 'big')
        offset += count
    assert length == size
    key = capture.raw[offset:offset + 4]
    payload = capture.raw[offset + 4:]
    assert bytes(value ^ key[index % 4] for index, value in enumerate(payload)) == b'y' * size


def test_cumulative_fragment_bound_is_checked_before_read(monkeypatch):
    receiver, sender = socket.socketpair()
    try:
        monkeypatch.setattr(ws, 'MAX_MESSAGE', 4)
        sender.sendall(frame(b'abc', fin=False) + bytes([0x80, 3]))
        with pytest.raises(OSError, match='message exceeded bound'):
            ws.recv_text(receiver)
    finally:
        receiver.close()
        sender.close()


def test_disconnect_wakes_reader_without_waiting_for_server_data():
    receiver, sender = socket.socketpair()
    wrapped = ws.BufferedSocket(receiver, b'')
    results = []
    def read():
        try:
            ws.recv_text(wrapped)
        except OSError:
            results.append('disconnected')
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        wrapped.close()
        reader.join(timeout=1)
        assert not reader.is_alive() and results == ['disconnected']
    finally:
        sender.close()
