#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Exact disposable client: write one mutating frame and exit without its ACK.

Only the immutable harness calls this in the same isolated namespace. No
thread discovery, retry or attach to a user-supplied host socket is supported.
"""
import os
from pathlib import Path
import sys

if __name__ == '__main__':
    if Path(__file__).resolve() != Path('/opt/ccc/scripts/codex_restart_raw_client.py'):
        raise SystemExit('STOP: raw client requires immutable disposable harness')
    sys.path.insert(0, '/opt/ccc')

from ccc_server.codex_managed_websocket import connect, send_text, recv_text
from ccc_server.execution_artifact import canonical
from scripts.codex_restart_fixture import _json, Stop


def write_once(packet):
    if set(packet) != {'socket', 'frame', 'native'} or packet['socket'] != '/state/native/app.sock':
        raise Stop('raw client has no exact private socket contract')
    frame = packet['frame']
    if (set(frame) != {'jsonrpc', 'id', 'method', 'params'} or frame['id'] != 1
            or frame['method'] not in ('turn/start', 'turn/steer', 'thread/queue/add')):
        raise Stop('raw client request outside bounded key experiment')
    from scripts.codex_restart_harness import peer_identity
    sock = connect(packet['socket'], timeout=3)
    try:
        peer_identity(sock, packet['socket'], packet['native'])
        send_text(sock, canonical({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'disposable_ack_loss', 'version': '1'},
            'capabilities': {'experimentalApi': True}}}).decode())
        sock.settimeout(3)
        reply = _json(recv_text(sock))
        if reply.get('id') != 0 or 'result' not in reply:
            raise Stop('raw client initialization failed')
        send_text(sock, '{"jsonrpc":"2.0","method":"initialized","params":{}}')
        send_text(sock, canonical(frame).decode())
        sys.stdout.buffer.write(b'FRAME_WRITTEN\n')
        sys.stdout.buffer.flush()
        # Parent kills this exact child before it can read the mutation ACK.
        # Waiting on stdin is only an owned-process handshake, not a retry.
        os.read(sys.stdin.fileno(), 1)
        # No read occurs after the mutating frame. Parent waits this exact
        # process and records its exit before the new client's key search.
    finally:
        sock.close()


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.readline(65537)
        if len(raw) > 65536:
            raise Stop('raw client input oversized')
        write_once(_json(raw))
    except Exception as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
