#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Offline-only building blocks for the managed-restart proof harness.

Importing this file starts nothing. The deterministic Responses fixture has
no outbound client and binds loopback only. It is NOT egress isolation: its
caller must first pass the separately recorded kernel-isolation preflight.
A durable grant counter must be shared by both native lifetimes. Failure
permanently halts that grant; there is deliberately no reset/retry command.
"""
from __future__ import annotations

import contextlib
from datetime import datetime
import fcntl
import hashlib
import http.server
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import threading
import time


class Stop(RuntimeError):
    pass


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Stop('duplicate JSON key')
            result[key] = value
        return result
    def nonfinite(value):
        raise Stop('nonfinite JSON number')
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise Stop('nonfinite JSON number')
        return result
    try:
        result = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite,
                            parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Stop('invalid JSON') from exc
    if not isinstance(result, dict):
        raise Stop('expected JSON object')
    return result


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _private_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise Stop('budget file is not private, regular and exclusively linked')


class Budget:
    """Persistent hard cap; callers cannot reset it by constructing another object."""
    def __init__(self, directory, grant):
        self.directory = Path(directory)
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise Stop('budget directory must be private and owned')
        if not isinstance(grant, dict) or not isinstance(grant.get('reference'), str) or not grant['reference'].strip():
            raise Stop('explicit bounded grant reference required')
        limits = {'max_lifetimes': 2, 'max_completions': 48, 'max_lifetime_seconds': 1200}
        for key, cap in limits.items():
            value = grant.get(key)
            if type(value) is not int or not 0 < value <= cap:
                raise Stop('grant exceeds fixed proof cap')
        try:
            expiry = datetime.fromisoformat(grant['expires_at'])
            if expiry.utcoffset() is None:
                raise ValueError('expiry lacks timezone')
            self.expiry = expiry.timestamp()
        except (KeyError, TypeError, ValueError) as exc:
            raise Stop('explicit expiry with timezone required') from exc
        # Retain immutable canonical bytes, not a caller-mutable grant object.
        self.grant_bytes = _canonical(grant)
        self.grant = _json(self.grant_bytes)
        self.grant_hash = hashlib.sha256(self.grant_bytes).hexdigest()
        self.state_file = self.directory / 'budget.json'
        with self._locked() as state:
            pass

    def _save(self, state):
        descriptor, temporary = tempfile.mkstemp(prefix='.budget-', dir=self.directory)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(_canonical(state))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_file)
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextlib.contextmanager
    def _locked(self):
        try:
            descriptor = os.open(self.directory / 'budget.lock',
                                 os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            new_lock = True
        except FileExistsError:
            descriptor = os.open(self.directory / 'budget.lock', os.O_RDWR | os.O_NOFOLLOW)
            new_lock = False
        try:
            _private_file(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                state_fd = os.open(self.state_file, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                if not new_lock:
                    raise Stop('budget state disappeared; cannot reset grant')
                state = {'format': 1, 'grant_sha256': self.grant_hash,
                         'lifetimes': 0, 'completions': 0, 'last_wall': time.time(),
                         'active_until': 0, 'halted': None}
            else:
                with os.fdopen(state_fd, 'rb') as stream:
                    _private_file(stream.fileno())
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise Stop('oversized budget state')
                state = _json(raw)
            if (state.get('format') != 1 or type(state.get('format')) is not int
                    or state.get('grant_sha256') != self.grant_hash):
                raise Stop('budget state or grant changed')
            for key, cap in (('lifetimes', self.grant['max_lifetimes']),
                             ('completions', self.grant['max_completions'])):
                if type(state.get(key)) is not int or not 0 <= state[key] <= cap:
                    raise Stop('corrupt budget counter')
            for key in ('last_wall', 'active_until'):
                if (type(state.get(key)) not in (int, float)
                        or not math.isfinite(state[key])):
                    raise Stop('corrupt budget time')
            if state.get('halted') is not None:
                raise Stop('grant halted: ' + str(state['halted'])[:200])
            now = time.time()
            if now < state['last_wall'] or now >= self.expiry:
                state['halted'] = 'clock regressed or grant expired'
                self._save(state)
                raise Stop(state['halted'])
            state['last_wall'] = now
            try:
                yield state
            except Stop as exc:
                state['halted'] = str(exc)[:200]
                self._save(state)
                raise
            else:
                self._save(state)
        finally:
            os.close(descriptor)

    def begin_lifetime(self):
        """Call once AFTER E4 passes, BEFORE executing the native binary."""
        with self._locked() as state:
            if state['lifetimes'] >= self.grant['max_lifetimes']:
                raise Stop('native lifetime cap exhausted')
            if state['active_until'] != 0:
                raise Stop('previous lifetime has not ended')
            state['lifetimes'] += 1
            state['active_until'] = min(self.expiry, time.time() + self.grant['max_lifetime_seconds'])
            return state['lifetimes']

    def remaining_seconds(self):
        with self._locked() as state:
            remaining = state['active_until'] - time.time()
            if not state['lifetimes'] or remaining <= 0:
                raise Stop('no active bounded lifetime')
            return remaining

    def completion(self):
        """Reserve before responding, including failed/disconnected requests."""
        with self._locked() as state:
            if not state['lifetimes'] or time.time() >= state['active_until']:
                raise Stop('no active bounded lifetime')
            if state['completions'] >= self.grant['max_completions']:
                raise Stop('fixture completion cap exhausted')
            state['completions'] += 1
            return state['completions']

    def end_lifetime(self):
        """Does not restore the consumed lifetime or any completion credits."""
        with self._locked() as state:
            state['active_until'] = 0

    def halt(self, reason):
        with self._locked() as state:
            raise Stop(str(reason))


def response_events(sequence, *, text='fixture reply', tool=None):
    """SSE frames generated entirely from deterministic local fixture data."""
    response_id = f'resp_fixture_{sequence}'
    item = ({'type': 'function_call', 'id': f'fc_fixture_{sequence}',
             'call_id': f'call_fixture_{sequence}', 'name': tool['name'],
             'arguments': tool['arguments'], 'status': 'completed'} if tool else
            {'id': f'msg_fixture_{sequence}', 'type': 'message', 'role': 'assistant',
             'status': 'completed', 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]})
    response = {'id': response_id, 'object': 'response', 'created_at': 0,
                'model': 'fixture', 'status': 'in_progress', 'output': []}
    events = [{'type': 'response.created', 'response': response},
              {'type': 'response.in_progress', 'response': response},
              {'type': 'response.output_item.added', 'output_index': 0,
               'item': dict(item, status='in_progress', **({'arguments': ''} if tool else {'content': []}))}]
    if tool:
        events += [{'type': 'response.function_call_arguments.delta', 'output_index': 0,
                    'item_id': item['id'], 'delta': tool['arguments']},
                   {'type': 'response.function_call_arguments.done', 'output_index': 0,
                    'item_id': item['id'], 'arguments': tool['arguments']}]
    else:
        part = {'type': 'output_text', 'text': '', 'annotations': []}
        events += [{'type': 'response.content_part.added', 'item_id': item['id'],
                    'output_index': 0, 'content_index': 0, 'part': part},
                   {'type': 'response.output_text.delta', 'item_id': item['id'],
                    'output_index': 0, 'content_index': 0, 'delta': text},
                   {'type': 'response.output_text.done', 'item_id': item['id'],
                    'output_index': 0, 'content_index': 0, 'text': text},
                   {'type': 'response.content_part.done', 'item_id': item['id'],
                    'output_index': 0, 'content_index': 0, 'part': item['content'][0]}]
    events += [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
               {'type': 'response.completed', 'response': dict(response, status='completed',
                output=[item], usage={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15,
                                     'input_tokens_details': {'cached_tokens': 0},
                                     'output_tokens_details': {'reasoning_tokens': 0}})}]
    return [('event: ' + event['type'] + '\ndata: ' + json.dumps(dict(event, sequence_number=index))
             + '\n\n').encode() for index, event in enumerate(events)]


class Fixture(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, budget, record):
        self.budget, self.record = budget, record
        self._phase_lock = threading.Lock()
        self._phase = {'text': 'fixture reply', 'tool': None, 'release': None}
        super().__init__(('127.0.0.1', 0), Handler)

    def phase(self, *, text='fixture reply', tool=None, release=None):
        """Harness-only control; never exposed as an HTTP mutation endpoint."""
        if tool is not None and (set(tool) != {'name', 'arguments'}
                                 or not all(isinstance(value, str) for value in tool.values())):
            raise Stop('invalid deterministic tool fixture')
        if not isinstance(text, str) or (release is not None and not isinstance(release, threading.Event)):
            raise Stop('invalid deterministic response phase')
        with self._phase_lock:
            self._phase = {'text': text, 'tool': dict(tool) if tool else None, 'release': release}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def _stop(self, reason):
        try:
            self.server.budget.halt(reason)
        except Stop:
            pass
        self.server.record('fixture_stop', reason=reason)
        self.close_connection = True

    def do_GET(self):
        if self.path != '/health':
            self._stop('unsupported fixture route')
            self.send_error(404)
            return
        body = b'{"fixture":true}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/v1/responses':
            self._stop('unsupported fixture route')
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16 * 1024 * 1024:
                raise Stop('invalid fixture request size')
            request = _json(self.rfile.read(length))
            sequence = self.server.budget.completion()
            with self.server._phase_lock:
                phase = dict(self.server._phase)
            self.server.record('fixture_request', sequence=sequence,
                               body_sha256=hashlib.sha256(_canonical(request)).hexdigest())
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            for frame in response_events(sequence, text=phase['text'], tool=phase['tool']):
                # Hold after native acknowledgement but before the first item.
                if b'event: response.output_item.added\n' in frame and phase['release'] is not None:
                    if not phase['release'].wait(min(60, self.server.budget.remaining_seconds())):
                        self.server.budget.halt('held fixture exceeded bounded wait')
                self.wfile.write(frame)
                self.wfile.flush()
            self.close_connection = True
            self.server.record('fixture_completed', sequence=sequence)
        except Stop as exc:
            self._stop(str(exc))
        except (OSError, ValueError) as exc:
            # Reservation remains consumed even when the socket was closed.
            self._stop('unexpected fixture I/O failure: ' + type(exc).__name__)
