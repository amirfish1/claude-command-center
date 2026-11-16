#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""No-retry RPC recorder for an E4-approved disposable fixture socket.

There is deliberately no connection opener or process launcher here. The
outer proof harness must establish isolation, exact owned daemon identity
and the fixture grant before passing in a connected socket. This client is
never a tool for attaching to existing user/worker conversations.
"""
from __future__ import annotations

import json
import socket
import threading
import time

from ccc_server.codex_managed_websocket import recv_text, send_text
from scripts.codex_restart_fixture import Stop, _json

METHODS = frozenset({'initialize', 'thread/start', 'thread/resume', 'thread/read',
    'thread/list', 'thread/loaded/list', 'thread/turns/list', 'thread/items/list', 'thread/unsubscribe',
    'turn/start', 'turn/steer', 'turn/interrupt', 'thread/queue/add', 'thread/queue/list',
    'thread/queue/remove', 'thread/goal/get', 'thread/goal/set', 'thread/goal/clear'})


class Client:
    def __init__(self, sock, *, label, record, max_messages=10000):
        if not isinstance(label, str) or not label or type(max_messages) is not int or max_messages < 1:
            raise Stop('invalid fixture client identity or receipt bound')
        self.sock, self.label, self.record = sock, label, record
        self.max_messages, self.received = max_messages, 0
        self.cv, self.write_lock = threading.Condition(), threading.Lock()
        self.next_id, self.pending, self.replies = 1, set(), {}
        self.notifications, self.server_requests = [], {}
        self.failure, self.closed = None, False
        self.reader = threading.Thread(target=self._read, name='fixture-rpc-' + label, daemon=True)
        self.reader.start()

    def _record(self, event, **fields):
        self.record(event, client=self.label, wall_ns=time.time_ns(),
                    monotonic_ns=time.monotonic_ns(), **fields)

    def _fail(self, reason):
        with self.cv:
            self.failure = self.failure or str(reason)
            self.cv.notify_all()

    def _read(self):
        try:
            while True:
                raw = recv_text(self.sock)
                message = _json(raw)
                self._record('rpc_receive', message=message, raw=raw)
                with self.cv:
                    self.received += 1
                    if self.received > self.max_messages:
                        raise Stop('RPC receipt bound exhausted')
                    if 'method' in message:
                        if not isinstance(message['method'], str) or not message['method']:
                            raise Stop('invalid server method')
                        if 'id' in message:
                            key = message['id']
                            if type(key) not in (int, str) or key in self.server_requests:
                                raise Stop('invalid or duplicate server request id')
                            self.server_requests[key] = message
                        else:
                            self.notifications.append(message)
                    else:
                        key = message.get('id')
                        if (type(key) is not int or key not in self.pending or key in self.replies
                                or ('result' in message) == ('error' in message)):
                            raise Stop('unmatched or malformed RPC response')
                        self.replies[key] = message
                    self.cv.notify_all()
        except Exception as exc:
            if not self.closed:
                self._fail(type(exc).__name__ + ': ' + str(exc))
                try:
                    self._record('rpc_failure', reason=self.failure)
                except Exception:
                    pass

    def _send(self, message):
        try:
            with self.write_lock:
                with self.cv:
                    if self.closed or self.failure:
                        raise Stop('fixture client closed or failed')
                self._record('rpc_send', message=message)
                send_text(self.sock, json.dumps(message, separators=(',', ':'), allow_nan=False))
        except Exception as exc:
            self._fail(type(exc).__name__ + ': ' + str(exc))
            raise Stop(self.failure) from exc

    def request(self, method, params, *, timeout=10, expected_error_codes=()):
        if method not in METHODS or type(params) is not dict:
            raise Stop('RPC outside disposable proof contract')
        if (not 0 < timeout <= 60 or not isinstance(expected_error_codes, tuple)
                or any(type(code) is not int for code in expected_error_codes)):
            raise Stop('invalid bounded RPC wait or negative control')
        with self.cv:
            key = self.next_id
            self.next_id += 1
            self.pending.add(key)
        try:
            self._send({'jsonrpc': '2.0', 'id': key, 'method': method, 'params': params})
            deadline = time.monotonic() + timeout
            with self.cv:
                while key not in self.replies:
                    if self.closed or self.failure:
                        raise Stop(self.failure or 'fixture client disconnected')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Stop('RPC timed out; result is unknown; do not retry')
                    self.cv.wait(remaining)
                reply = self.replies.pop(key)
                self.pending.remove(key)
            if 'error' in reply:
                error = reply['error']
                if (not isinstance(error, dict) or type(error.get('code')) is not int
                        or error['code'] not in expected_error_codes):
                    raise Stop('unexpected native RPC error: ' + json.dumps(error))
                return reply
            if expected_error_codes:
                raise Stop('negative control unexpectedly succeeded')
            return reply['result']
        except Exception:
            self.close()
            raise

    def initialize(self):
        result = self.request('initialize', {'clientInfo': {'name': 'restart_fixture', 'version': '1'},
            'capabilities': {'experimentalApi': True}}, timeout=5)
        self._send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
        return result

    def wait_notification(self, method, predicate=lambda value: True, *, since=0, timeout=10):
        if not 0 < timeout <= 60 or type(since) is not int or since < 0:
            raise Stop('invalid bounded notification wait')
        try:
            deadline = time.monotonic() + timeout
            with self.cv:
                while True:
                    for message in self.notifications[since:]:
                        if message['method'] == method and predicate(message.get('params', {})):
                            return message
                    if self.closed or self.failure:
                        raise Stop(self.failure or 'fixture client disconnected')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Stop('notification timed out; no implicit retry')
                    self.cv.wait(remaining)
        except Exception:
            self.close()
            raise

    def answer(self, key, result):
        with self.cv:
            if key not in self.server_requests:
                raise Stop('answer has no observed native request')
            message = self.server_requests.pop(key)
        self._record('rpc_answer', method=message['method'], request_id=key)
        self._send({'jsonrpc': '2.0', 'id': key, 'result': result})

    def close(self):
        with self.cv:
            if self.closed:
                return
            self.closed = True
            self.cv.notify_all()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        if threading.current_thread() is not self.reader:
            self.reader.join(timeout=2)
            if self.reader.is_alive():
                raise Stop('owned fixture RPC reader did not stop')
