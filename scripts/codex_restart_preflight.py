#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Prepare the disposable proof packet without starting a service or process.

Run as ``python3 -m scripts.codex_restart_preflight prepare INPUT OUTPUT``.
Input contains paths to canonical image manifests and public launch policy.
Image labels and requested profile hashes are never actual process facts.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from dataclasses import replace
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from ccc_server.execution_artifact import canonical, digest, image_etc_mount, _resolve
from ccc_server.execution_worker import worker_profile
from ccc_server.codex_managed_recovery import SUPPORTED
from scripts.codex_restart_fixture import Stop, _json
from scripts.codex_restart_runner import CASES, need

FORMAT = 'ccc-disposable-proof-preparation-v1'
SOURCES = ('scripts/codex_restart_preflight.py', 'scripts/codex_restart_runner.py',
           'scripts/codex_restart_harness.py', 'scripts/codex_restart_raw_client.py', 'scripts/codex_restart_fixture.py',
           'scripts/codex_restart_isolation.py', 'scripts/codex_restart_rpc.py',
           'scripts/codex_restart_bridge.py', 'scripts/codex_restart_p6.py',
           'ccc_server/execution_authority.py', 'ccc_server/execution_proof_probe.py',
           'ccc_server/queue_events.py', 'ccc_server/codex_managed_recovery.py')


def sha(value):
    need(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'invalid SHA256')
    return value


def absolute(value):
    need(isinstance(value, str) and value.startswith('/') and '..' not in value.split('/')
         and str(PurePosixPath(value)) == value and value != '/', 'invalid exact absolute path')
    return value


def grant(value, *, preparation=False):
    need(isinstance(value, dict) and set(value) == {'reference', 'expires_at',
        'max_lifetimes', 'max_completions', 'max_lifetime_seconds'}, 'invalid bounded grant fields')
    need(isinstance(value['reference'], str) and value['reference'].strip(), 'missing grant reference')
    for name, cap in (('max_lifetimes', 2), ('max_completions', 48), ('max_lifetime_seconds', 1200)):
        need(type(value[name]) is int and 0 < value[name] <= cap, 'grant exceeds proof cap')
    if preparation and value['expires_at'] is None:
        return value  # Unknown expiry is preparation data, never an execution grant.
    try:
        expires = datetime.fromisoformat(value['expires_at'])
        need(expires.utcoffset() is not None, 'grant expiry lacks timezone')
    except (TypeError, ValueError) as exc:
        raise Stop('invalid grant expiry') from exc
    return value


def image(value, read=Path.read_bytes):
    need(set(value) == {'image_id', 'artifact_sha256', 'manifest'}, 'incomplete immutable image input')
    need(re.fullmatch('sha256:[0-9a-f]{64}', value['image_id']), 'image must have immutable ID')
    raw = read(Path(value['manifest']))
    need(digest(raw) == sha(value['artifact_sha256']), 'image manifest changed')
    manifest = _json(raw)
    need(manifest.get('format') == 'ccc-execution-rootfs-v1'
         and isinstance(manifest.get('entries'), list), 'invalid rootfs manifest')
    entries = {}
    for entry in manifest['entries']:
        name = entry.get('path')
        need(isinstance(name, str) and name and not name.startswith('/')
             and '..' not in name.split('/') and name not in entries, 'invalid/duplicate artifact entry')
        entries[name] = entry
    return {'image_id': value['image_id'], 'artifact_sha256': value['artifact_sha256']}, entries


def file_entry(entries, name, expected=None, executable=False):
    e = entries.get(name, {})
    need(e.get('type') == 'file' and isinstance(e.get('mode'), int), 'missing regular artifact file: ' + name)
    sha(e.get('sha256'))
    if expected:
        need(e['sha256'] == expected, 'artifact file drift: ' + name)
    if executable:
        need(e['mode'] & 0o111, 'artifact entry is not executable: ' + name)
    return e['sha256']


def native_config(image_id, state_root, user):
    """Immutable harness and native share a container, outside worker cgroups.

    Native is launched only by the fixed harness after full E4. This profile
    deliberately does not relax the guarded worker's kernel execution policy.
    A later exact outer launch supplies the private namespace anchor to workers.
    """
    return {'Image': image_id, 'User': user, 'WorkingDir': '/opt/ccc',
        'Entrypoint': ['/usr/local/bin/python3', '-I', '-S', '-B',
                       '/opt/ccc/scripts/codex_restart_harness.py'],
        'Cmd': ['--packet', '/state/packet.json'], 'OpenStdin': True, 'StdinOnce': False, 'Tty': False,
        'Env': ['HOME=/state/home', 'CODEX_HOME=/state/home/.codex', 'TMPDIR=/state/tmp',
                'LANG=C.UTF-8', 'CCC_STATE_DIR=/state'],
        'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
            'CapAdd': [], 'SecurityOpt': ['no-new-privileges'], 'IpcMode': 'none',
            'PidMode': 'host', 'CgroupnsMode': 'host', 'Runtime': 'runc', 'Init': False,
            'Mounts': [image_etc_mount(image_id), {'Type': 'bind', 'Source': state_root,
                                                'Target': '/state', 'ReadOnly': False}]}}


def bound_profiles(packet, harness_container_id):
    """Requested roles joined to an actual supplied container ID, data only.

    The ID remains a lookup hint. Kernel namespace and executed profile facts
    still require the independently accepted root observer.
    """
    need(isinstance(harness_container_id, str) and re.fullmatch('[0-9a-f]{64}', harness_container_id),
         'namespace anchor must be an exact container ID')
    profile = worker_profile(packet['state_root'], packet['user'], control_stdio=True,
                             managed_socket='/state/native/app.sock')
    for role in ('worker', 'replacement'):
        need(canonical(packet['requested_' + role + '_profile']) == canonical(profile.value()),
             'requested proof worker policy drift')
    bound = replace(profile, network='container:' + harness_container_id)
    return {role: bound for role in ('worker', 'replacement')}


def prepare(inputs, *, source_root=None, read=Path.read_bytes):
    need(set(inputs) == {'format', 'grant', 'images', 'native', 'state_root', 'user'},
         'unknown or missing preparation inputs')
    need(inputs['format'] == FORMAT, 'unknown preparation format')
    limits = grant(inputs['grant'], preparation=True)
    state_root = absolute(inputs['state_root'])
    need(re.fullmatch('[1-9][0-9]*:[1-9][0-9]*', inputs['user']), 'proof uid/gid must be nonroot')
    need(set(inputs['images']) == {'worker', 'replacement', 'control', 'native'}, 'missing exact image roles')
    images, entries = {}, {}
    for role in ('worker', 'replacement', 'control', 'native'):
        images[role], entries[role] = image(inputs['images'][role], read)
        interpreter = _resolve(entries[role], 'usr/local/bin/python3')
        file_entry(entries[role], interpreter, executable=True)
    native = inputs['native']
    need(set(native) == {'version', 'binary_sha256', 'schema_sha256', 'schema_index'}, 'invalid native pin')
    need(SUPPORTED.get(sha(native['binary_sha256'])) == (native['version'], sha(native['schema_sha256'])),
         'unsupported official native binary/schema')
    file_entry(entries['native'], 'opt/ccc-native/bin/codex', native['binary_sha256'], True)
    raw = read(Path(native['schema_index']))
    need(digest(raw) == native['schema_sha256'], 'native schema index changed')
    try:
        index = json.loads(raw)
    except ValueError as exc:
        raise Stop('invalid schema index') from exc
    need(isinstance(index, list) and index, 'empty schema index')
    paths = set()
    for item in index:
        need(set(item) == {'path', 'sha256'} and item['path'] not in paths, 'invalid/duplicate schema entry')
        need(not item['path'].startswith('/') and '..' not in item['path'].split('/'), 'schema path traversal')
        paths.add(item['path'])
        file_entry(entries['native'], 'opt/ccc-native/schema/' + item['path'], sha(item['sha256']))
    file_entry(entries['native'], 'opt/ccc-native/schema-index.json', digest(raw))
    source_root = source_root or Path(__file__).resolve().parents[1]
    sources = {name: digest(read(source_root / name)) for name in SOURCES}
    rebuild = [name for name, value in sources.items() if any(
        entries[role].get('opt/ccc/' + name, {}).get('sha256') != value for role in entries)]
    worker = worker_profile(state_root, inputs['user'], control_stdio=True,
                            managed_socket='/state/native/app.sock')
    # Actual namespace and kernel-measured hashes must remain unfilled until
    # the independently authenticated stopped launches exist.
    policy = worker.value()
    return {'format': FORMAT, 'scope': 'disposable-proof-preparation',
        'runtime_authorized': False, 'execution_ready': False,
        'grant': limits, 'images': images, 'native': {k: native[k] for k in
            ('version', 'binary_sha256', 'schema_sha256')},
        'state_root': state_root, 'user': inputs['user'], 'source_sha256': sources,
        'rebuild_required': rebuild,
        'harness_launch': native_config(images['native']['image_id'], state_root, inputs['user']),
        'requested_worker_profile': policy, 'requested_replacement_profile': policy,
        'requested_profile_sha256': worker.sha256,
        'actual': {'host_netns': None, 'native_netns': None, 'native_identity': None,
                   'private_root': None, 'socket': None, 'worker': None, 'replacement': None,
                   'control': None, 'measured_profiles': None},
        'acceptance': {'execution': None, 'bootstrap': None, 'planned_P6': None, 'runtime': None},
        'schedule': [{'case': name, 'proof': proof, 'completion_ceiling': ceiling}
                     for name, proof, ceiling in CASES],
        'required_sequence': ['verify-closure', 'structural-before-listen', 'fixture-health',
            'E4-main-and-double-fork', 'reserve-lifetime-1', 'P1-P5', 'stop-native-1', 'E5',
            'E4-main-and-double-fork', 'reserve-lifetime-2', 'observe-new-exact-roles',
            'root-bootstrap-acceptance', 'root-planned-P6-acceptance', 'P6', 'stop-native-2',
            'E5', 'exact-owned-cleanup-and-absence'],
        'stop_rules': ['E4 failure: halt, no native startup, no repair or retry',
            'E5 or cleanup failure: invalidate all observations',
            'unexpected request or limit: durable shared grant halt',
            'unknown native key/capability: fail, no automatic resend',
            'lost root reply: unknown, no retry or fallback',
            'fixture receipt never unlocks runtime or first transition']}


def write_packet(output, packet):
    # Never overwrite a packet another process may already have accepted.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(canonical(packet) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', help='write an unaccepted data-only packet; launch nothing')
    prep.add_argument('input', type=Path)
    prep.add_argument('output', type=Path)
    # Explicit refusal is a useful actual CLI check, not an environment bypass.
    commands.add_parser('execute', help='refuse: execution belongs to the accepted immutable outer launcher')
    args = parser.parse_args(argv)
    try:
        need(args.command == 'prepare', 'execution requires independently accepted immutable harness launch')
        packet = prepare(_json(args.input.read_bytes()))
        write_packet(args.output, packet)
        print(json.dumps({'prepared': True, 'execution_ready': False,
            'packet_sha256': digest(canonical(packet)), 'rebuild_required': packet['rebuild_required']}))
        return 0
    except (Stop, ValueError, OSError, TypeError, KeyError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
