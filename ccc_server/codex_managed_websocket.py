# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Bounded stdlib WebSocket transport for the local managed Codex socket.

Transport framing establishes no daemon ownership or restart authority.
Process identity and proof admission are checked by their separate gates.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import threading
import time

MAX_MESSAGE = 64 * 1024 * 1024
MAX_HEADER = 16 * 1024
_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


class BufferedSocket:
    """Preserve frames coalesced with HTTP Upgrade; delegate socket lifecycle."""
    def __init__(self, sock, prefix):
        self._socket, self._prefix = sock, bytearray(prefix)
        self._frame_lock = threading.Lock()

    def recv(self, length):
        if self._prefix:
            result = bytes(self._prefix[:length])
            del self._prefix[:length]
            return result
        return self._socket.recv(length)

    def sendall(self, raw):
        # Reader-generated pong and application JSON must never interleave.
        with self._frame_lock:
            return self._socket.sendall(raw)

    def close(self):
        # Closing an fd from another thread need not wake its blocked recv.
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()

    def __getattr__(self, name):
        return getattr(self._socket, name)


def read_exact(sock, length):
    chunks = []
    while length:
        chunk = sock.recv(length)
        if not chunk:
            raise OSError('managed WebSocket EOF')
        chunks.append(chunk)
        length -= len(chunk)
    return b''.join(chunks)


def send_frame(sock, opcode, payload=b'', *, mask=True):
    payload = payload or b''
    if opcode not in (0, 1, 2, 8, 9, 10) or len(payload) > MAX_MESSAGE:
        raise OSError('invalid WebSocket output frame')
    if opcode >= 8 and len(payload) > 125:
        raise OSError('oversized WebSocket control frame')
    size = len(payload)
    header = bytes([0x80 | opcode, (0x80 if mask else 0) | (size if size < 126 else 126 if size <= 65535 else 127)])
    if size >= 126:
        header += size.to_bytes(2 if size <= 65535 else 8, 'big')
    if mask:
        key = os.urandom(4)
        payload = bytes(value ^ key[index % 4] for index, value in enumerate(payload))
        header += key
    sock.sendall(header + payload)


def send_text(sock, text):
    send_frame(sock, 1, str(text).encode('utf-8'))


def recv_text(sock):
    fragments, total, started = [], 0, False
    while True:
        first, second = read_exact(sock, 2)
        fin, opcode, size = bool(first & 0x80), first & 15, second & 127
        if first & 0x70 or second & 0x80:
            raise OSError('unsupported WebSocket extension or masked server frame')
        if size == 126:
            size = int.from_bytes(read_exact(sock, 2), 'big')
            if size < 126:
                raise OSError('noncanonical WebSocket frame length')
        elif size == 127:
            size = int.from_bytes(read_exact(sock, 8), 'big')
            if size < 65536 or size >> 63:
                raise OSError('noncanonical WebSocket frame length')
        if size > MAX_MESSAGE or (opcode >= 8 and (not fin or size > 125)):
            raise OSError('oversized or fragmented WebSocket control/message')
        if opcode not in (0, 1, 8, 9, 10):
            raise OSError('unsupported WebSocket opcode')
        if opcode == 0 and not started:
            raise OSError('unexpected WebSocket continuation')
        if opcode == 1 and started:
            raise OSError('new WebSocket message before fragmented message ended')
        if opcode in (0, 1):
            total += size
            if total > MAX_MESSAGE:
                raise OSError('WebSocket message exceeded bound')
        payload = read_exact(sock, size)
        if opcode == 8:
            if size == 1:
                raise OSError('invalid WebSocket close payload')
            raise OSError('managed WebSocket closed')
        if opcode == 9:
            send_frame(sock, 10, payload)
            continue
        if opcode == 10:
            continue
        started = True
        fragments.append(payload)
        if fin:
            try:
                return b''.join(fragments).decode('utf-8')
            except UnicodeDecodeError as exc:
                raise OSError('invalid WebSocket UTF-8') from exc


def connect(unix_path, *, timeout=10):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    deadline = time.monotonic() + timeout
    try:
        sock.settimeout(timeout)
        sock.connect(str(unix_path))
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        sock.sendall(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                      'Connection: Upgrade\r\nSec-WebSocket-Key: ' + key +
                      '\r\nSec-WebSocket-Version: 13\r\n\r\n').encode('ascii'))
        raw = b''
        while b'\r\n\r\n' not in raw:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(raw) >= MAX_HEADER:
                raise OSError('managed WebSocket upgrade exceeded bound')
            sock.settimeout(remaining)
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError('managed WebSocket upgrade EOF')
            raw += chunk
        header, tail = raw.split(b'\r\n\r\n', 1)
        if len(header) >= MAX_HEADER:
            raise OSError('oversized WebSocket upgrade header')
        lines = header.decode('iso-8859-1').split('\r\n')
        status = lines[0].split(' ', 2)
        if len(status) < 2 or status[0] != 'HTTP/1.1' or status[1] != '101':
            raise OSError('managed app-server did not accept WebSocket upgrade')
        headers = {}
        for line in lines[1:]:
            name, colon, value = line.partition(':')
            name = name.lower()
            if not colon or not name or name in headers:
                raise OSError('invalid or duplicate WebSocket upgrade header')
            headers[name] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + _GUID).encode('ascii')).digest()).decode('ascii')
        if (headers.get('sec-websocket-accept') != expected
                or headers.get('upgrade', '').lower() != 'websocket'
                or 'upgrade' not in {value.strip().lower() for value in headers.get('connection', '').split(',')}):
            raise OSError('invalid WebSocket upgrade proof')
        sock.settimeout(None)
        return BufferedSocket(sock, tail)
    except BaseException:
        sock.close()
        raise
