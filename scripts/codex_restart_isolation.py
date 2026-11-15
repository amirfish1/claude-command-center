#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Fail-closed observations for an already isolated, disposable proof namespace.

Importing this file starts nothing. This does not create isolation or launch
Codex. The trusted outer launcher supplies host namespace identity and checks
Docker configuration separately. Structural checks precede network probes;
never point this helper at a shared worker or a host fixture.
"""
from __future__ import annotations

import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import sys
import time


class IsolationFailure(RuntimeError):
    pass


def _require(value, reason):
    if not value:
        raise IsolationFailure(reason)


def _unescape(value):
    # mountinfo escapes whitespace and backslashes as octal sequences.
    import re
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)


def mounts(raw):
    result = []
    for line in raw.splitlines():
        before, separator, after = line.partition(' - ')
        fields, tail = before.split(), after.split()
        _require(separator and len(fields) >= 6 and len(tail) >= 3, 'malformed mountinfo')
        result.append({'mount': _unescape(fields[4]), 'options': set(fields[5].split(',')),
                       'filesystem': tail[0], 'super_options': set(tail[2].split(','))})
    _require(result, 'empty mount table')
    return result


def writable_mounts_are_private(entries, private_root, *, device_root):
    """Reject writable ordinary mounts, including automatic Docker /etc files.

    Kernel proc/devpts/sysfs/cgroup views are not data mounts. The /dev tmpfs
    must be root-owned mode 0755: a non-root process cannot create files there.
    /dev/shm, mqueue, extra tmpfs and bind mounts are deliberately not exempt.
    """
    private_root = str(private_root)
    roots = [entry for entry in entries if entry['mount'] == '/']
    _require(len(roots) == 1 and 'ro' in roots[0]['options'], 'root filesystem is writable')
    private_mount_seen = False
    for entry in entries:
        if 'rw' not in entry['options']:
            continue
        name, kind = entry['mount'], entry['filesystem']
        if name == private_root or name.startswith(private_root + '/'):
            private_mount_seen = True
            continue
        if kind in ('proc', 'sysfs', 'devpts', 'cgroup2'):
            continue
        if name == '/dev' and kind == 'tmpfs':
            _require(device_root.st_uid == 0 and stat.S_IMODE(device_root.st_mode) == 0o755,
                     'device root is writable by the fixture uid')
            continue
        raise IsolationFailure('writable mount outside private root: ' + name)
    _require(private_mount_seen, 'private writable mount missing')
    return True


def routes_are_loopback(ipv4, ipv6):
    lines = ipv4.splitlines()
    _require(lines and lines[0].split()[0] == 'Iface', 'malformed IPv4 route table')
    for line in lines[1:]:
        fields = line.split()
        _require(len(fields) >= 8 and fields[0] == 'lo', 'non-loopback IPv4 route')
    for line in ipv6.splitlines():
        fields = line.split()
        _require(len(fields) == 10 and fields[-1] == 'lo', 'non-loopback IPv6 route')
    return True


def audit_sockets(private_root, allowed_sockets, *, root=Path('/'), limit=200000):
    """Walk ordinary filesystems without following directory symlinks.

    E5 allows only the exact owned private Codex socket; socket allowance is
    an exact path, never a directory-wide exemption. Kernel pseudo trees are
    excluded because their descriptors are inspected by the outer launcher.
    """
    private_root = Path(private_root)
    allowed = set()
    for item in allowed_sockets:
        candidate = Path(item)
        _require(candidate.is_absolute() and private_root in candidate.parents,
                 'socket exemption outside private root')
        allowed.add(str(candidate))
    count = 0
    skip = {root / name for name in ('proc', 'sys')}
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=lambda exc: (_ for _ in ()).throw(exc)):
        parent = Path(directory)
        dirs[:] = [name for name in dirs if parent / name not in skip]
        for name in dirs + files:
            candidate = parent / name
            count += 1
            _require(count <= limit, 'socket audit exceeded bounded walk')
            info = candidate.lstat()
            if stat.S_ISSOCK(info.st_mode):
                _require(str(candidate) in allowed, 'unexpected visible unix socket: ' + str(candidate))
                _require(info.st_uid == os.getuid(), 'private socket uid mismatch')
    return True


def structural_observation(host_netns, private_root, allowed_sockets):
    """No network operation occurs until every structural check has passed."""
    root = Path(private_root)
    _require(root.is_absolute() and str(root) != '/' and root.resolve() == root,
             'private root must be a canonical absolute path')
    info = root.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o700, 'private root is not owned mode0700')
    namespace = os.readlink('/proc/self/ns/net')
    _require(isinstance(host_netns, str) and host_netns.startswith('net:[')
             and host_netns.endswith(']') and namespace != host_netns,
             'network namespace did not differ from host')
    interfaces = sorted(item.name for item in Path('/sys/class/net').iterdir())
    net_dev = Path('/proc/net/dev').read_text()
    dev_interfaces = sorted(line.split(':', 1)[0].strip() for line in net_dev.splitlines() if ':' in line)
    _require(interfaces == ['lo'] and dev_interfaces == ['lo'], 'network interface other than loopback')
    status_raw = Path('/proc/self/status').read_text()
    status = dict(line.split(':', 1) for line in status_raw.splitlines() if ':' in line)
    _require(os.getuid() != 0 and os.geteuid() != 0, 'root uid forbidden')
    _require(int(status['CapEff'].strip(), 16) == 0, 'effective capabilities present')
    _require(status['NoNewPrivs'].strip() == '1', 'NoNewPrivs not set')
    ipv4 = Path('/proc/net/route').read_text()
    ipv6 = Path('/proc/net/ipv6_route').read_text()
    routes_are_loopback(ipv4, ipv6)
    raw_mounts = Path('/proc/self/mountinfo').read_text()
    writable_mounts_are_private(mounts(raw_mounts), root, device_root=Path('/dev').stat())
    audit_sockets(root, allowed_sockets)
    checks = dict.fromkeys(('different_host_netns', 'loopback_only', 'no_routes', 'nonroot',
                            'no_capabilities', 'no_new_privileges', 'readonly_root',
                            'private_writable_root_only', 'no_host_sockets'), True)
    return checks, {'pid': os.getpid(), 'ppid': os.getppid(), 'netns': namespace,
                    'uid': os.getuid(), 'interfaces': interfaces, 'status': status_raw,
                    'ipv4_routes': ipv4, 'ipv6_routes': ipv6, 'mountinfo': raw_mounts,
                    'net_dev': net_dev}


def network_observation(port):
    _require(type(port) is int and 0 < port < 65536, 'invalid local fixture port')
    checks, raw = {}, {'attempts': []}
    # Documentation-only literal destinations; a network-isolated namespace
    # must reject them at the kernel route lookup, before a packet can leave.
    for version, family, address in ((4, socket.AF_INET, '192.0.2.1'),
                                     (6, socket.AF_INET6, '2001:db8::1')):
        for kind, label in ((socket.SOCK_STREAM, 'tcp'), (socket.SOCK_DGRAM, 'udp')):
            with socket.socket(family, kind) as probe:
                probe.settimeout(0.5)
                try:
                    if kind == socket.SOCK_STREAM:
                        probe.connect((address, 9))
                    else:
                        probe.sendto(b'isolated-proof', (address, 9))
                except OSError as exc:
                    raw['attempts'].append({'transport': label, 'family': version,
                                            'destination': address, 'errno': exc.errno})
                    # Timeout/refusal alone is not enforced isolation.
                    _require(exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH,
                                           errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL),
                             'network probe did not prove route denial')
                else:
                    raise IsolationFailure('external network operation succeeded')
            checks[f'{label}{version}_denied'] = True
    # A separate bounded child avoids an unbounded libc resolver wait.
    try:
        result = subprocess.run([sys.executable, '-I', '-c',
            'import socket; socket.getaddrinfo("example.com",443)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=3, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        raw['dns'] = {'returncode': result.returncode, 'stderr': result.stderr.decode(errors='replace')[:4096]}
        _require(result.returncode != 0 and b'gaierror' in result.stderr,
                 'DNS lookup succeeded or failed unexpectedly')
    except subprocess.TimeoutExpired:
        # With no routes or other interfaces, a resolver timeout is denial;
        # its exact timeout is retained and the owned resolver child is reaped.
        raw['dns'] = {'timeout_seconds': 3}
    checks['dns_denied'] = True
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', '/health')
        response = connection.getresponse()
        body = response.read(4097)
        raw['fixture'] = {'status': response.status, 'body': body.decode(errors='replace')}
        _require(response.status == 200 and body == b'{"fixture":true}', 'local fixture unreachable')
    finally:
        connection.close()
    checks['fixture_reachable'] = True
    return checks, raw


def observe(host_netns, private_root, allowed_sockets, fixture_port):
    checks, raw = structural_observation(host_netns, private_root, allowed_sockets)
    network_checks, raw['network'] = network_observation(fixture_port)
    checks.update(network_checks)
    return {'checks': checks, 'raw': raw}


def double_fork_observe(host_netns, private_root, allowed_sockets, fixture_port):
    """Run the same probes after a double fork, retaining exact owned PIDs."""
    read_fd, write_fd = os.pipe()
    first = os.fork()
    if first == 0:
        os.close(read_fd)
        try:
            second = os.fork()
        except BaseException:
            os.close(write_fd)
            os._exit(1)
        if second != 0:
            os._exit(0)
        try:
            # Publish PID before probes, so timeout cleanup is exact.
            with os.fdopen(write_fd, 'w') as stream:
                stream.write(json.dumps({'pid': os.getpid()}) + '\n')
                stream.flush()
                try:
                    value = observe(host_netns, private_root, allowed_sockets, fixture_port)
                except Exception as exc:
                    value = {'error': type(exc).__name__ + ': ' + str(exc)}
                stream.write(json.dumps(value) + '\n')
                stream.flush()
        finally:
            os._exit(0)
    os.close(write_fd)
    os.waitpid(first, 0)
    data, descendant, pidfd = b'', None, None
    deadline = time.monotonic() + 12
    try:
        while True:
            remaining = deadline - time.monotonic()
            _require(remaining > 0, 'descendant observation timed out')
            ready, _, _ = select.select([read_fd], [], [], remaining)
            _require(ready, 'descendant observation timed out')
            chunk = os.read(read_fd, 65536)
            if not chunk:
                break
            data += chunk
            _require(len(data) <= 4 * 1024 * 1024, 'descendant receipt oversized')
            if descendant is None and b'\n' in data:
                descendant = json.loads(data.split(b'\n', 1)[0])['pid']
                _require(type(descendant) is int and descendant > 1, 'invalid owned descendant pid')
                try:
                    pidfd = os.pidfd_open(descendant)
                except ProcessLookupError:
                    pass
        rows = [json.loads(line) for line in data.splitlines()]
        _require(len(rows) == 2 and rows[0]['pid'] == rows[1].get('raw', {}).get('pid'),
                 'descendant failed: ' + str(rows[-1] if rows else 'no receipt'))
        return rows[1]
    except BaseException:
        if pidfd is not None:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise
    finally:
        os.close(read_fd)
        if pidfd is not None:
            os.close(pidfd)


def receipt(*, phase, run_id, host_netns, private_root, fixture_port, allowed_sockets=()):
    """One-shot E4/E5, no repair/retry. Caller persists failures and halts grant."""
    _require(phase in ('E4', 'E5') and isinstance(run_id, str) and run_id,
             'invalid isolation receipt identity')
    main = observe(host_netns, private_root, allowed_sockets, fixture_port)
    descendant = double_fork_observe(host_netns, private_root, allowed_sockets, fixture_port)
    raw = json.dumps({'main': main['raw'], 'descendant': descendant['raw']},
                     sort_keys=True, separators=(',', ':')).encode()
    result = {'format': 1, 'phase': phase, 'run_id': run_id, 'verdict': 'pass',
              'checks': main['checks'], 'descendant_checks': descendant['checks'],
              'raw_sha256': hashlib.sha256(raw).hexdigest()}
    return result, raw
