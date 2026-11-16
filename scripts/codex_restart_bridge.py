#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Bounded, data-only relay between the harness and existing root authority.

The operator creates launches and provisions independent root acceptances.
This relay cannot supply acceptance in a request or certify its own facts.
It never starts native/fixture services, executes commands in workers, changes
the authority's capabilities/mounts, or provisions root files. Default CLI is
refusal; the parent explicitly supplies root-controlled contracts to serve.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from ccc_server.execution_artifact import canonical, digest, ProcessIdentity
from ccc_server.execution_authority import AttestationClient, AuthorityRefused, RootDocker, read_anchor, _profile, _streams
from scripts.codex_restart_fixture import Stop, _json
from scripts.codex_restart_runner import need


def root_contract(filename):
    filename = Path(filename).absolute()
    for p in (filename, *filename.parents):
        info = p.lstat()
        need(info.st_uid == 0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode),
             'controller contract is not exclusively root controlled')
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_size <= 16 * 1024 * 1024, 'invalid controller contract')
        raw = os.read(fd, before.st_size + 1)
        need(os.fstat(fd) == before and len(raw) == before.st_size, 'controller contract changed while reading')
    finally:
        os.close(fd)
    return _json(raw)


def pair(fact):
    return {'artifact_sha256': fact['executed_build_sha256'],
            'profile_sha256': fact['execution_profile_sha256']}


class Bridge:
    def __init__(self, contracts, *, read=root_contract, client=AttestationClient, docker=None, draft=None):
        self.contracts, self.read, self.client = contracts, read, client
        self.docker, self.draft = docker or RootDocker(), draft or (lambda name, raw: None)
        self.failed, self.last_id, self.deleted = False, 0, set()
        self.fixed_packet, self.last_namespace, self.harness_identity = None, None, None

    def contract(self, *, cleanup=False):
        value = self.read(self.contracts)
        need(value.get('format') == 'ccc-disposable-proof-controller-v1', 'wrong controller scope')
        packet = value['packet']
        need(value.get('packet_sha256') == digest(canonical(packet))
             and packet.get('runtime_authorized') is False and packet.get('execution_ready') is True
             and re.fullmatch('[0-9a-f]{64}', value['harness_container_id'])
             and (cleanup or time.time() < datetime.fromisoformat(packet['grant']['expires_at']).timestamp()),
             'controller packet drift or expiry')
        if self.fixed_packet is None:
            self.fixed_packet = value['packet_sha256']
        need(value['packet_sha256'] == self.fixed_packet, 'controller execution packet changed mid-run')
        return value

    def observation(self, contract, worker=None, native=None):
        result = self.client(contract['proof_anchor_path']).observe_proof_harness(
            canonical(contract['packet']), contract['harness_container_id'], worker=worker, native=native)
        if result.get('harness'):
            need(self.harness_identity in (None, result['harness']), 'outer harness identity changed')
            self.harness_identity = result['harness']
        return result

    def handle(self, message):
        need(not self.failed and set(message) == {'id', 'operation', 'data'}
             and type(message['id']) is int and message['id'] == self.last_id + 1,
             'controller sequence/replay refused')
        self.last_id = message['id']
        try:
            result = self.dispatch(message['operation'], message['data'])
            return {'id': message['id'], 'result': result}
        except BaseException:
            self.failed = True  # Never resend a possibly executed root action.
            raise

    def dispatch(self, operation, data):
        c = self.contract(cleanup=operation in ('cleanup-outer', 'verify-cleanup'))
        packet = c['packet']
        if operation in ('launch-acceptance', 'attest-closure'):
            need(data == {'packet_sha256': c['packet_sha256']}, 'launch packet changed')
            observed = self.observation(c)
            self.last_namespace = observed['native_netns']
            if operation == 'attest-closure':
                return observed
            return {'packet_sha256': observed['accepted_packet_sha256'],
                    'reference': observed['reference'],
                    'expires_at': datetime.fromisoformat(packet['grant']['expires_at']).timestamp()}
        if operation == 'scenario-ready':
            name = data['case'] + '/' + data['path']
            need(name in c['scenarios'], 'exact root scenario contract missing')
            scenario = c['scenarios'][name]
            need(scenario['old'] == c['roles']['worker']
                 and scenario['replacement'] == c['roles']['replacement'], 'scenario role drift')
            old = self.observation(c, scenario['old'], data['native_identity'])
            new = self.observation(c, scenario['replacement'], data['native_identity'])
            return {'old': old['worker'], 'replacement': new['worker']}
        if operation == 'observe-worker':
            role = data['role']
            need(role in ('worker', 'replacement'), 'unknown exact worker role')
            binding = c['roles'][role]
            return self.observation(c, binding, data['native_identity'])
        if operation == 'prepare-integration':
            evidence = {key: base64.b64decode(raw, validate=True) for key, raw in data['evidence'].items()}
            need(all(digest(raw) == key for key, raw in evidence.items()), 'changed P1-P5 evidence')
            old = self.observation(c, c['roles']['worker'], data['native_identity'])
            new = self.observation(c, c['roles']['replacement'], data['native_identity'])
            need(old['fixture_identity'] == new['fixture_identity'], 'fixture role binding drift')
            control = c['fixture_control']
            transition = {'old_worker': pair(old['worker']), 'replacement': pair(new['worker']), 'control': control}
            manifest = {'format': 1, 'scope': 'fixture', 'purpose': 'planned-P6',
                'codex': packet['native'], 'transition': transition,
                'fixture_identity': old['fixture_identity'], 'proofs': data['proofs']}
            need('P6' not in manifest['proofs'], 'planned P6 cannot certify itself')
            bootstrap = {'format': 1, 'scope': 'fixture-bootstrap', 'worker': old['worker'],
                'profile': c['roles']['worker']['profile'], 'control': control,
                'fixture_identity': old['fixture_identity']}
            raw, boot = canonical(manifest), canonical(bootstrap)
            self.draft('planned-P6', raw)
            self.draft('bootstrap', boot)
            # Drafts contain fresh identities. The independently controlled
            # operator may accept these exact bytes within the bounded window.
            # Nothing writes an anchor or manufactures an acceptance here.
            deadline = min(time.monotonic() + c['acceptance_wait_seconds'],
                time.monotonic() + 60)
            while True:
                try:
                    anchor = read_anchor(c['fixture_anchor_path'])
                except FileNotFoundError:
                    anchor = {}
                accepted = (anchor.get('fixture_acceptance', {}).get('manifest_sha256') == digest(raw)
                    and anchor.get('bootstrap_acceptance', {}).get('manifest_sha256') == digest(boot)
                    and {'artifact_sha256': anchor.get('executed_build_sha256'),
                         'profile_sha256': anchor.get('execution_profile_sha256')} == control)
                if accepted:
                    break
                need(time.monotonic() < deadline, 'operator acceptance outstanding; drafts preserved, grant stops')
                need(self.contract()['packet_sha256'] == c['packet_sha256'], 'operator packet changed')
                time.sleep(0.25)
            return {'manifest': base64.b64encode(raw).decode(), 'acceptance': anchor['fixture_acceptance'],
                'codex': packet['native'], 'transition': transition, 'fixture_identity': old['fixture_identity'],
                'bootstrap': {'contract': base64.b64encode(boot).decode(),
                    'profile': c['roles']['worker']['profile'], 'identity': c['roles']['worker']['identity']}}
        if operation == 'fixture-bootstrap':
            # The request is independently root accepted and re-observed by the
            # existing API, including stopped guard, native listener and mount.
            return self.client(c['fixture_anchor_path']).fixture_bootstrap(data)
        if operation == 'integration-action':
            case, path = data['case'], data['path']
            need(case in ('idle', 'active', 'ack-loss', 'queue', 'goal', 'approval', 'corrupt', 'C6')
                 and path in ('planned', 'crash', 'refusal'), 'unplanned integration action')
            scenario = c['scenarios'][case + '/' + path]
            authority = self.client(c['fixture_anchor_path'])
            # Every action uses separately root-controlled case identities;
            # harness input cannot choose an arbitrary process or profile.
            root_query = scenario['request']
            if path == 'planned':
                receipt = authority.fixture_restart(root_query)
                return {'old_worker_absent': True, 'replacement_attested': True,
                        'authority_receipt': receipt}
            if path == 'crash':
                identity = ProcessIdentity(**scenario['old']['identity'])
                current = self.docker.inspect_container(identity.container_id)
                need(current['Id'] == identity.container_id
                     and current['State']['StartedAt'] == identity.started_at
                     and not current['State'].get('Running'), 'nonroot harness crash death is unknown')
                receipt = authority.fixture_bootstrap(scenario['replacement_bootstrap'])
                return {'old_worker_absent': True, 'replacement_attested': True,
                        'authority_receipt': receipt}
            # Negative cases must actually call the unchanged gate/handler.
            # A supplied flag cannot substitute for an observed refusal.
            try:
                authority.fixture_restart(root_query)
            except AuthorityRefused as exc:
                expected = scenario['expected_refusal']
                need(expected in ('corrupt managed recovery state',
                    'root has not accepted this exact runtime proof',
                    'root has not accepted this exact fixture plan', 'unaccepted fixture plan',
                    'fixture plan scope/binding mismatch')
                    and str(exc).rstrip().endswith(': ' + expected),
                    'authority error is not the expected measured refusal')
                observed = authority.observe(scenario['old']['identity']['container_id'],
                    _profile(scenario['old']['profile']), ProcessIdentity(**scenario['old']['identity']))
                need(vars(observed.identity) == scenario['old']['identity'], 'refusal worker identity changed')
                result = {'refused': True, 'killed': False, 'reason': str(exc)}
                if case == 'C6':
                    receipts = scenario['compatibility_receipts']
                    raw = canonical(receipts)
                    need(digest(raw) == scenario['compatibility_receipts_sha256']
                         and set(receipts) == {'stdio', 'kimi', 'other'}
                         and all(r.get('passed') is True and r.get('evidence_sha256')
                                 for r in receipts.values()), 'C6 unchanged receipt evidence absent')
                    evidence = {sha: base64.b64decode(data, validate=True)
                                for sha, data in scenario['compatibility_evidence'].items()}
                    need(all(digest(data) == sha and 0 < len(data) <= 4 * 1024 * 1024
                             for sha, data in evidence.items())
                         and all(r['evidence_sha256'] in evidence
                                 and r.get('source_sha256') == packet['source_sha256']
                                 for r in receipts.values()), 'C6 raw evidence/source closure missing')
                    result.update(compatibility=dict.fromkeys(receipts, True), receipts_sha256=digest(raw))
                return result
            raise Stop('negative control unexpectedly authorized a worker transition')
        if operation == 'verify-outer-isolation':
            observed = self.observation(c)
            need(self.last_namespace == observed['native_netns'], 'outer native namespace changed')
            return {**observed, 'same_namespace': self.last_namespace == observed['native_netns']}
        if operation == 'cleanup-outer':
            need(data == {'run_id': packet['run_id']}, 'cleanup run identity drift')
            result = self.client(c['proof_anchor_path']).cleanup_proof_workers(
                canonical(packet), c['harness_container_id'], c.get('owned_roles', []))
            wanted = {b['identity']['container_id'] for b in c.get('owned_roles', [])}
            need(set(result['removed']) == wanted and result.get('runtime_authorized') is False,
                 'root cleanup receipt scope differs')
            self.deleted.update(result['removed'])
            return {'owned_roles_stopped': True}
        if operation == 'verify-cleanup':
            need(data == {'run_id': packet['run_id']}, 'cleanup witness run drift')
            wanted = {b['identity']['container_id'] for b in c.get('owned_roles', [])}
            containers = self.docker.request('GET', '/containers/json?all=1')
            need(wanted <= self.deleted and not wanted.intersection(r['Id'] for r in containers),
                 'exact owned fixture containers still present')
            return {'owned_processes_absent': True, 'owned_containers_absent': True,
                    'harness_cleanup_after_exit': True}
        raise Stop('controller operation is not in the fixed proof protocol')


    def attach(self):
        """Relay bounded Docker log/stdin frames; create or start nothing."""
        c = self.contract()
        ident = c['harness_container_id']
        deadline = time.monotonic() + min(2520,
            datetime.fromisoformat(c['packet']['grant']['expires_at']).timestamp() - time.time())
        consumed, prefix, connection = 0, b'', None
        try:
            while True:
                need(time.monotonic() < deadline, 'outer proof deadline expired')
                container = self.docker.inspect_container(ident)
                raw = self.docker.request('GET', '/containers/' + ident + '/logs?stdout=1&stderr=0', raw=True)
                need(len(raw) <= 48 * 1024 * 1024, 'outer raw control log bound exhausted')
                output = bytes(_streams(raw)[1])
                need(output[:consumed] == prefix, 'outer control log changed or disappeared')
                end = output.rfind(b'\n') + 1
                for line in output[consumed:end].splitlines():
                    need(len(line) <= 16 * 1024 * 1024, 'outer control frame oversized')
                    result = self.handle(_json(line))
                    reply = canonical(result) + b'\n'
                    if connection is None:
                        connection = self.docker.input(ident, reply)
                    else:
                        connection.sock.sendall(reply)  # Never reconnect/replay a reply.
                consumed, prefix = end, output[:end]
                if not container['State'].get('Running'):
                    need(end == len(output), 'harness ended with a partial control frame')
                    return container['State']['ExitCode']
                time.sleep(0.05)
        finally:
            if connection:
                connection.close()

    def finish(self):
        c = self.contract(cleanup=True)
        need(self.harness_identity, 'harness identity unknown; no guessed cleanup target')
        result = self.client(c['proof_anchor_path']).finish_proof(canonical(c['packet']),
            c['harness_container_id'], self.harness_identity, c.get('owned_roles', []))
        need(result.get('owned_processes_absent') is True
             and result.get('owned_containers_absent') is True,
             'post-run exact cleanup did not complete')
        self.draft('post-run-cleanup', canonical(result))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contracts', type=Path)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--drafts', type=Path)
    parser.add_argument('--attach', action='store_true', help='relay the already launched exact harness; never launch it')
    args = parser.parse_args(argv)
    try:
        need(args.serve and args.contracts and args.drafts, 'preparation only; serving requires explicit root-controlled contracts')
        from scripts.codex_restart_preflight import write_packet
        info = args.drafts.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o700, 'draft directory must be private and owned')
        def draft(name, raw):
            write_packet(args.drafts / (name + '-' + digest(raw) + '.json'), _json(raw))
        bridge = Bridge(args.contracts, draft=draft)
        if args.attach:
            try:
                status = bridge.attach()
            finally:
                bridge.finish()
            need(status == 0, 'harness failed; no native proof accepted')
            return 0
        # This command consumes one bounded JSONL control stream, never a
        # daemon, queue or executable command/configuration channel.
        while True:
            raw = sys.stdin.buffer.readline(16 * 1024 * 1024 + 1)
            if not raw:
                break
            need(len(raw) <= 16 * 1024 * 1024, 'oversized controller frame')
            reply = bridge.handle(_json(raw))
            sys.stdout.buffer.write(canonical(reply) + b'\n')
            sys.stdout.buffer.flush()
        return 0
    except (Stop, ValueError, OSError, KeyError, TypeError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
