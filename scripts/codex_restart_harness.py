#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Fixed entry for an independently accepted immutable disposable harness.

Direct checkout execution refuses. The trusted outer controller launches the
exact immutable image/config and owns all root acceptance and worker actions.
Its bounded stdin/stdout data channel carries no command, code or import name.
Neither source preparation nor a successful fixture result unlocks runtime.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

if __name__ == '__main__':
    if Path(__file__).resolve() != Path('/opt/ccc/scripts/codex_restart_harness.py'):
        raise SystemExit('STOP: launch requires the accepted immutable harness image')
    # Fixed measured source root; isolated Python never imports writable state.
    sys.path.insert(0, '/opt/ccc')

import argparse
import contextlib
import fcntl
import tempfile
import json
import select
import signal
import socket
import stat
import struct
import subprocess
import threading
import time

from ccc_server.execution_artifact import canonical, digest
from ccc_server import codex_managed_recovery as recovery
from ccc_server.codex_managed_websocket import connect, send_text, recv_text
from scripts.codex_restart_fixture import Budget, Fixture, Stop, _json
from scripts.codex_restart_isolation import structural_observation, receipt
from scripts.codex_restart_rpc import Client
from scripts.codex_restart_runner import Acceptance, Journal, Protocol, Workflow, need


def peer_identity(sock, socket_name, expected):
    """Harness peer can share our cgroup; worker peer checks stay stricter."""
    before = recovery.socket_path_identity(socket_name)
    pid, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    need(pid == expected['pid'] and uid == os.getuid(), 'private native peer uid/pid mismatch')
    observed = recovery.process_identity(pid)
    need(all(observed[k] == expected[k] for k in observed), 'native PID/start drift')
    need(digest(Path('/proc', str(pid), 'exe').read_bytes()) == expected['binary_sha256'], 'native peer binary drift')
    need(os.readlink('/proc/' + str(pid) + '/ns/net') == os.readlink('/proc/self/ns/net'), 'native peer namespace drift')
    need(recovery.socket_path_identity(socket_name) == before
         and all(expected[k] == before[k] for k in before), 'native peer socket drift')
    return {**observed, **before}


class OuterControl:
    """One request/response, never retry an uncertain root or worker action."""
    OPERATIONS = frozenset({'launch-acceptance', 'attest-closure', 'prepare-integration',
        'fixture-bootstrap', 'integration-snapshot', 'integration-action',
        'verify-outer-isolation', 'cleanup-outer', 'verify-cleanup', 'observe-worker', 'scenario-ready'})

    def __init__(self, incoming, outgoing, record):
        self.incoming, self.outgoing, self.record = incoming, outgoing, record
        self.sequence, self.failed, self.buffer = 0, False, b''

    def request(self, operation, data, timeout=60):
        need(not self.failed and operation in self.OPERATIONS, 'outer control failed or operation forbidden')
        self.sequence += 1
        message = {'id': self.sequence, 'operation': operation, 'data': data}
        raw = canonical(message) + b'\n'
        need(len(raw) <= 16 * 1024 * 1024, 'outer request exceeds fixed bound')
        self.record('outer_send', message=message)
        try:
            self.outgoing.write(raw)
            self.outgoing.flush()
            end = time.monotonic() + timeout
            while b'\n' not in self.buffer:
                remaining = end - time.monotonic()
                need(remaining > 0 and select.select([self.incoming], [], [], remaining)[0],
                     'outer reply unknown; no retry')
                chunk = os.read(self.incoming.fileno(), 65536)
                need(chunk, 'outer channel closed; outcome unknown')
                self.buffer += chunk
                need(len(self.buffer) <= 16 * 1024 * 1024, 'outer reply oversized')
            line, self.buffer = self.buffer.split(b'\n', 1)
            reply = _json(line)
            self.record('outer_receive', message=reply)
            need(set(reply) == {'id', 'result'} and type(reply['id']) is int
                 and reply['id'] == self.sequence and isinstance(reply['result'], dict),
                 'outer reply mismatch, refusal or unknown')
            return reply['result']
        except BaseException:
            self.failed = True
            raise


class LocalHarness:
    """Real nonroot namespace operations; no Docker or host service socket."""
    def __init__(self, packet, control, record):
        self.packet, self.control, self.record = packet, control, record
        self.root = Path('/state')
        self.socket = self.root / 'native/app.sock'
        self.sockets = ('/state/native/app.sock', '/state/worker.sock')
        self.host_netns = packet['actual']['host_netns']
        self.fixture = self.thread = self.native = self.pidfd = None
        self.clients, self.release, self.request_cv = [], None, threading.Condition()
        self.requests, self.completed, self.turn_tokens = 0, 0, {}
        self.case_ceiling = None
        self.case_start = 0
        self.uncertain_native = False
        self.goal_thread = None
        self.watchdog = None
        self.output_thread = None
        self.scenarios = None
        self.scenario_phase = 'before'

    def verify_closure(self, packet):
        need(os.getuid() != 0 and os.geteuid() != 0, 'harness must be nonroot')
        for name, expected in packet['source_sha256'].items():
            need(digest(Path('/opt/ccc', name).read_bytes()) == expected, 'immutable source mismatch')
        binary = Path('/opt/ccc-native/bin/codex')
        need(digest(binary.read_bytes()) == packet['native']['binary_sha256'], 'native binary drift')
        index = Path('/opt/ccc-native/schema-index.json').read_bytes()
        need(digest(index) == packet['native']['schema_sha256'], 'native schema index drift')
        for row in json.loads(index):
            name = Path(row['path'])
            need(not name.is_absolute() and '..' not in name.parts, 'schema path traversal')
            need(digest(Path('/opt/ccc-native/schema', name).read_bytes()) == row['sha256'], 'native schema drift')
        observed = self.control.request('attest-closure', {'packet_sha256': digest(canonical(packet))})
        need(observed.get('images') == packet['images'] and observed.get('native_netns') == os.readlink('/proc/self/ns/net')
             and observed.get('source_sha256') == packet['source_sha256']
             and observed.get('accepted_packet_sha256') == digest(canonical(packet)),
             'outer authenticated launch closure mismatch')

    def structural(self):
        checks, raw = structural_observation(self.host_netns, self.root, self.sockets)
        self.record('structural_before_listen', checks=checks, raw=raw)

    def start_fixture(self, budget, record):
        need(self.fixture is None, 'fixture already started')
        self.budget = budget
        owner = self

        class CaseBudget:
            def completion(self):
                with owner.request_cv:
                    need(owner.case_ceiling is not None
                         and owner.fixture_completions() - owner.case_start < owner.case_ceiling,
                         'unexpected fixture request or case ceiling exhausted')
                    return budget.completion()

            def remaining_seconds(self):
                return budget.remaining_seconds()

            def halt(self, reason):
                return budget.halt(reason)

        self.fixture = Fixture(CaseBudget(), self.fixture_record)
        self.thread = threading.Thread(target=self.fixture.serve_forever, daemon=True)
        self.thread.start()

    def fixture_record(self, event, **fields):
        self.record(event, **fields)
        with self.request_cv:
            if event == 'fixture_request':
                self.requests += 1
            elif event == 'fixture_completed':
                self.completed += 1
                if self.goal_thread is not None:
                    self.turn_tokens[self.goal_thread] = self.turn_tokens.get(self.goal_thread, 0) + 15
            elif event == 'fixture_stop':
                self.uncertain_native = True
            self.request_cv.notify_all()

    def begin_case(self, name, ceiling):
        self.case_start = self.fixture_completions()
        self.case_ceiling = ceiling
        self.record('case_start', name=name, completion_ceiling=ceiling, reserved=self.case_start)

    def isolation(self, phase, run_id):
        return receipt(phase=phase, run_id=run_id, host_netns=self.host_netns,
            private_root='/state', fixture_port=self.fixture.server_port, allowed_sockets=self.sockets)

    def new_run_id(self, lifetime):
        return self.packet['run_id'] + '-' + str(lifetime)

    def start_native(self, lifetime, remaining):
        need(self.native is None and not self.socket.exists(), 'native handle/socket already present')
        # Directories are disposable data, never executable/config mounts.
        for name in ('home', 'home/.codex', 'tmp', 'work', 'native'):
            p = self.root / name
            p.mkdir(mode=0o700, exist_ok=True)
            info = p.lstat()
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                 and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe native data directory')
        need(not (self.root / 'home/.codex/auth.json').exists()
             and not (self.root / 'home/.codex/config.toml').exists(), 'native auth/config is not disposable')
        argv = ['/opt/ccc-native/bin/codex', 'app-server', '--listen', 'unix://' + str(self.socket)]
        settings = {'model': 'fixture', 'model_provider': 'fixture',
            'model_providers.fixture.name': 'Local deterministic proof fixture',
            'model_providers.fixture.base_url': 'http://127.0.0.1:' + str(self.fixture.server_port) + '/v1',
            'model_providers.fixture.wire_api': 'responses',
            'model_providers.fixture.requires_openai_auth': False,
            'model_providers.fixture.supports_websockets': False,
            'model_providers.fixture.request_max_retries': 0,
            'model_providers.fixture.stream_max_retries': 0,
            'analytics.enabled': False, 'feedback.enabled': False, 'check_for_update_on_startup': False}
        for key, value in settings.items():
            argv.extend(['-c', key + '=' + json.dumps(value)])
        self.stderr = (self.root / ('native-' + str(lifetime) + '.stderr')).open('xb')
        self.native = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd='/state/work', start_new_session=True,
            env={'HOME': '/state/home', 'CODEX_HOME': '/state/home/.codex', 'TMPDIR': '/state/tmp',
                 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        self.pidfd = os.pidfd_open(self.native.pid)
        self.identity = recovery.process_identity(self.native.pid)
        self.output_thread = threading.Thread(target=self.capture_native_output, daemon=True)
        self.output_thread.start()
        self.watchdog = threading.Timer(remaining, self.expire)
        self.watchdog.daemon = True
        self.watchdog.start()
        end = time.monotonic() + min(10, remaining)
        while not self.socket.exists():
            need(self.native.poll() is None and time.monotonic() < end, 'native failed to create private socket')
            time.sleep(0.02)
        before = self.socket.lstat()
        need(stat.S_ISSOCK(before.st_mode) and before.st_uid == os.getuid(), 'native created an unsafe socket')
        self.socket.chmod(0o600)
        need(self.socket.lstat().st_ino == before.st_ino, 'native socket changed during private admission')
        self.socket_identity = recovery.socket_path_identity(self.socket)
        need(digest(Path('/proc', str(self.native.pid), 'exe').read_bytes()) == self.packet['native']['binary_sha256'],
             'actual native executable drift')
        self.record('native_started', identity=self.native_identity(), argv=argv)

    def capture_native_output(self):
        total, limit = 0, 4 * 1024 * 1024
        try:
            while True:
                raw = self.native.stdout.read(65536)
                if not raw:
                    break
                remaining = limit - total
                self.stderr.write(raw[:remaining])
                self.stderr.flush()
                total += len(raw)
                need(total <= limit, 'native output exceeded fixed byte limit')
        except BaseException as exc:
            self.uncertain_native = True
            self.expire(str(exc))

    def native_identity(self):
        need(self.native is not None and self.native.poll() is None
             and recovery.process_identity(self.native.pid) == self.identity, 'native process identity changed')
        return {**self.identity, **recovery.socket_path_identity(self.socket),
                'binary_sha256': self.packet['native']['binary_sha256'],
                'version': self.packet['native']['version'],
                'protocol_sha256': self.packet['native']['schema_sha256']}

    def expire(self, reason='native lifetime deadline expired'):
        try:
            # The pidfd pins the owned process even if its numerical PID is reused.
            if self.pidfd is not None:
                signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
            self.budget.halt(reason)
        except (Stop, OSError):
            pass

    def client(self):
        need(self.native_identity(), 'missing native')
        sock = connect(str(self.socket))
        try:
            peer_identity(sock, str(self.socket), self.native_identity())
        except BaseException:
            sock.close()
            raise
        client = Client(sock, label='client-' + str(len(self.clients)), record=self.record)
        self.clients.append(client)
        return client

    def phase(self, kind):
        need(kind in ('text', 'held', 'tool', 'tool-held'), 'unknown fixture phase')
        if kind.endswith('held') or kind == 'held':
            self.release = threading.Event()
        tool = {'name': '$approval-command', 'arguments': '{}'} if kind.startswith('tool') else None
        self.fixture.phase(text='Deterministic local fixture reply', tool=tool,
                           release=self.release if 'held' in kind else None)

    def release_phase(self, *, next_held=False):
        need(self.release is not None, 'no owned held fixture phase')
        previous = self.release
        if next_held:
            self.phase('held')
        previous.set()

    def wait(self, seconds):
        self.budget.remaining_seconds()
        time.sleep(seconds)
        self.budget.remaining_seconds()

    def fixture_completions(self):
        with self.budget._locked() as state:
            return state['completions']

    def wait_condition(self, predicate, timeout=30):
        end = time.monotonic() + min(timeout, self.budget.remaining_seconds())
        with self.request_cv:
            while not predicate():
                need(not self.uncertain_native, 'fixture failed')
                remaining = end - time.monotonic()
                need(remaining > 0, 'fixture observation timed out')
                self.request_cv.wait(min(remaining, 0.1))

    def wait_fixture_request(self):
        self.wait_condition(lambda: self.requests > self.completed)

    def wait_fixture_completions(self, count):
        self.wait_condition(lambda: self.completed >= count)

    def wait_fixture_reservations(self, count):
        self.wait_condition(lambda: self.requests >= count)

    def hold_for_method(self, c, t, method):
        if method == 'turn/start':
            self.phase('text')
            return None
        self.phase('held')
        turn = Protocol(self, self.budget, self.record).start(c, t, 'holding-' + str(self.requests))
        self.wait_fixture_request()
        return turn

    def settle(self, c, t):
        if self.release and not self.release.is_set():
            self.phase('text')
            self.release_phase()
        end = time.monotonic() + min(60, self.budget.remaining_seconds())
        while True:
            value = c.request('thread/read', {'threadId': t['id'], 'includeTurns': False})['thread']
            if value.get('status', {}).get('type') == 'idle':
                return
            need(time.monotonic() < end, 'native did not reach known idle')
            time.sleep(0.05)

    def wait_server_request(self, c):
        end = time.monotonic() + min(30, self.budget.remaining_seconds())
        with c.cv:
            while not c.server_requests:
                need(not c.failure and time.monotonic() < end, 'pending approval was not transferred')
                c.cv.wait(min(0.1, max(0, end - time.monotonic())))
            need(len(c.server_requests) == 1, 'ambiguous server approval')
            return next(iter(c.server_requests.values()))

    def answer_pending(self, c):
        request = self.wait_server_request(c)
        need(request['method'] == 'item/commandExecution/requestApproval', 'unexpected server request class')
        c.answer(request['id'], {'decision': 'accept'})

    def ack_loss(self, frame):
        # A raw socket has no receiving reader. Parent records exact child death
        # rather than calling Client.request then pretending its ack was lost.
        raw = canonical({'socket': str(self.socket), 'frame': frame, 'native': self.native_identity()})
        child = subprocess.Popen(['/usr/local/bin/python3', '-I', '-S', '-B',
            '/opt/ccc/scripts/codex_restart_raw_client.py'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd='/opt/ccc', env={'LANG': 'C.UTF-8'})
        descriptor = os.pidfd_open(child.pid)
        identity = recovery.process_identity(child.pid)
        try:
            child.stdin.write(raw + b'\n')
            child.stdin.flush()
            need(select.select([child.stdout], [], [], min(8, self.budget.remaining_seconds()))[0],
                 'raw mutation write unknown; no retry')
            need(child.stdout.readline(64) == b'FRAME_WRITTEN\n', 'raw mutation write was not confirmed')
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            child.wait(timeout=3)
            self.record('raw_client_killed', identity=identity, returncode=child.returncode, frame=frame)
            need(child.returncode == -signal.SIGKILL, 'exact raw client death not proved')
        finally:
            if child.poll() is None:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                child.wait(timeout=3)
            os.close(descriptor)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

    def verify_rollout_terminal(self, t, turn, status):
        target = Path(t['path'])
        need(self.root in target.parents and target.is_file(), 'rollout escaped private root')
        terminal = []
        with target.open() as stream:
            for line in stream:
                row = _json(line)
                payload = row.get('payload', {})
                if payload.get('turn_id') == turn and payload.get('type') in ('task_complete', 'turn_aborted'):
                    terminal.append(payload['type'])
        need(status == 'completed' and terminal == ['task_complete'], 'native rollout terminal is missing or aborted')

    def fixture_tokens(self, thread_id):
        # Native goal accounting is compared with deterministic fixture usage,
        # restricted to the goal's freshly created thread and observed requests.
        return self.turn_tokens.get(thread_id, 0)

    def bind_goal_thread(self, thread_id):
        need(self.goal_thread is None, 'goal accounting thread already bound')
        self.goal_thread = thread_id
        self.turn_tokens[thread_id] = 0

    def prepare_integration(self, proofs, evidence):
        import base64
        proposal = self.control.request('prepare-integration', {'proofs': proofs,
            'evidence': {key: base64.b64encode(raw).decode() for key, raw in evidence.items()},
            'native_identity': self.native_identity()}, timeout=60)
        proposal['manifest'] = base64.b64decode(proposal['manifest'], validate=True)
        return proposal

    def fixture_bootstrap(self, query):
        result = self.control.request('fixture-bootstrap', query)
        need(result.get('ok') is True and result.get('scope') == 'fixture-bootstrap'
             and result.get('runtime_authorized') is False, 'fixture bootstrap refused or wrong scope')

    def integration_snapshot(self, case, path):
        from scripts.codex_restart_p6 import Scenarios
        if self.scenarios is None:
            self.scenarios = Scenarios(self)
        if self.scenario_phase == 'before':
            self.scenario_phase = 'after'
            return self.scenarios.prepare(case, path)
        self.scenario_phase = 'before'
        return self.scenarios.finish()

    def kill_worker(self, identity):
        """The specified harness crash, using nonroot exact-owned pidfds."""
        need(os.geteuid() != 0, 'fixture crash must remain nonroot')
        native = self.native_identity()
        targets = [(identity['pid'], identity['start_ticks']),
                   (identity['launcher_pid'], identity['launcher_start_ticks'])]
        need(len({pid for pid, _ in targets}) == 2 and native['pid'] not in {pid for pid, _ in targets},
             'crash target aliases native or guardian')
        descriptors = []
        try:
            for pid, ticks in targets:
                fd = os.pidfd_open(pid)
                descriptors.append(fd)
                observed = recovery.process_identity(pid)
                security = dict(line.split(':', 1) for line in Path('/proc', str(pid), 'status').read_text().splitlines() if ':' in line)
                need(observed['start_ticks'] == ticks and observed['boot_id'] == identity['boot_id']
                     and all(int(v) == os.getuid() for v in security['Uid'].split()), 'fixture crash identity/uid drift')
            for fd in descriptors:
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # The pinned guardian may exit after reaping its child.
            end = time.monotonic() + 5
            while not all(recovery.exact_process_dead({'pid': pid, 'start_ticks': ticks,
                    'boot_id': identity['boot_id']}) for pid, ticks in targets):
                need(time.monotonic() < end, 'exact crashed worker/guardian death unknown')
                time.sleep(0.02)
            need(self.native_identity() == native, 'crash changed native daemon')
            self.record('worker_crashed', identity=identity, native=native, signal='SIGKILL')
        finally:
            for fd in descriptors:
                os.close(fd)

    @contextlib.contextmanager
    def corrupt_disposable_state(self):
        target = self.root / 'codex-managed-recovery.json'
        info = target.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 4 * 1024 * 1024,
             'corruption target is not exact disposable state')
        original = target.read_bytes()
        lock = os.open(str(target) + '.lock', os.O_RDWR | os.O_NOFOLLOW)
        def replace(raw):
            fd, name = tempfile.mkstemp(prefix='.proof-state-', dir=self.root)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            need(target.read_bytes() == original, 'disposable state changed before corruption')
            replace(b'{')
            fcntl.flock(lock, fcntl.LOCK_UN)
            self.record('disposable_corruption', path=str(target), original_sha256=digest(original))
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                need(target.read_bytes() == b'{', 'refused transition unexpectedly rewrote corrupt state')
                replace(original)
                self.record('disposable_state_restored', sha256=digest(original))
        finally:
            os.close(lock)

    def integration_action(self, case, path, proposal):
        import base64
        if path == 'crash':
            _, fact = self.scenarios.worker('worker')
            self.kill_worker(fact['identity'])
        request = {**proposal, 'manifest': base64.b64encode(proposal['manifest']).decode()}
        runtime_refusal = None
        if case == 'C6':
            from ccc_server.codex_managed_proof import validate, Unproven
            try:
                validate(proposal['manifest'], acceptance=proposal['acceptance'], expected_scope='runtime',
                    codex=proposal['codex'], transition=proposal['transition'], evidence={},
                    fixture_identity=proposal['fixture_identity'])
            except Unproven as exc:
                need(str(exc) == 'wrong proof scope', 'unexpected runtime validator refusal')
                runtime_refusal = str(exc)
            need(runtime_refusal, 'fixture plan unexpectedly accepted for runtime')
            self.record('runtime_scope_refused', reason=runtime_refusal,
                        manifest_sha256=digest(proposal['manifest']))
        with self.corrupt_disposable_state() if case == 'corrupt' else contextlib.nullcontext():
            result = self.control.request('integration-action', {'case': case, 'path': path,
                'proposal': request}, timeout=min(300, self.budget.remaining_seconds()))
        if case == 'C6':
            result['runtime_scope_refusal'] = runtime_refusal
        return result

    def verify_outer_isolation(self):
        result = self.control.request('verify-outer-isolation', {'native_identity': self.identity})
        need(result.get('network_none') is True and result.get('no_added_capabilities') is True
             and result.get('same_namespace') is True and result.get('sole_private_writable_mount') is True,
             'outer E5 launch configuration drift')

    def stop_native(self):
        if self.watchdog:
            self.watchdog.cancel()
            self.watchdog.join(timeout=2)
            need(not self.watchdog.is_alive(), 'native watchdog did not stop')
            self.watchdog = None
        for client in self.clients:
            client.close()
        self.clients = []
        if self.native is not None:
            if self.native.poll() is None:
                need(recovery.process_identity(self.native.pid) == self.identity, 'cleanup process identity drift')
                signal.pidfd_send_signal(self.pidfd, signal.SIGTERM)
                try:
                    self.native.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                    self.native.wait(timeout=3)
            if self.output_thread:
                self.output_thread.join(timeout=3)
                need(not self.output_thread.is_alive(), 'native output capture did not stop')
                self.output_thread = None
            self.native.stdout.close()
            os.close(self.pidfd)
            self.native = self.pidfd = None
            self.stderr.close()
            # The native parent may leave its own exact private socket inode.
            if self.socket.exists():
                info = self.socket.lstat()
                need(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(), 'cleanup socket changed')
                need(recovery.socket_path_identity(self.socket) == self.socket_identity,
                     'cleanup refuses a replaced socket inode')
                self.socket.unlink()

    def cleanup(self):
        if self.release:
            self.release.set()
        self.stop_native()
        if self.fixture:
            self.fixture.shutdown()
            self.fixture.server_close()
            self.thread.join(timeout=3)
            need(not self.thread.is_alive(), 'fixture did not stop')
        if not self.control.failed:
            self.control.request('cleanup-outer', {'run_id': self.packet['run_id']}, timeout=300)

    def verify_cleanup(self):
        need(self.native is None and not self.socket.exists(), 'owned native is not absent')
        result = self.control.request('verify-cleanup', {'run_id': self.packet['run_id']})
        need(result.get('owned_processes_absent') is True and result.get('owned_containers_absent') is True,
             'owned descendant/container absence unknown')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        packet = _json(args.packet.read_bytes())
        # Refuse before opening a journal, budget, fixture or native process.
        need(packet.get('execution_ready') is True and packet.get('rebuild_required') == [],
             'packet is preparation-only; exact closure/acceptance outstanding')
        with Path('/state/runner.jsonl').open('xb') as stream:
            record = Journal(stream)
            outer = OuterControl(sys.stdin.buffer, sys.stdout.buffer, record)
            accepted = outer.request('launch-acceptance', {'packet_sha256': digest(canonical(packet))})
            acceptance = Acceptance(**accepted)
            acceptance.check(packet)
            budget_dir = Path('/state/budget')
            budget_dir.mkdir(mode=0o700, exist_ok=True)
            budget = Budget(budget_dir, packet['grant'])
            io = LocalHarness(packet, outer, record)
            result = Workflow(packet, acceptance, budget, io, record).run()
            # Evidence bytes already persisted in bounded journal; no JSON byte coercion.
            result.pop('evidence')
            Path('/state/result.json').write_bytes(canonical(result))
        return 0
    except (Stop, ValueError, OSError, TypeError, KeyError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
