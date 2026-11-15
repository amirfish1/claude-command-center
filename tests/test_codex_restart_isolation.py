"""Offline rejection checks, never an E4 receipt or a native lifetime."""
import importlib.util
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('codex_restart_isolation',
    Path(__file__).resolve().parents[1] / 'scripts' / 'codex_restart_isolation.py')
isolation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(isolation)


def table(extra=''):
    return isolation.mounts('10 1 0:1 / / ro - overlay overlay ro\n'
        '11 10 0:2 / /proof rw - tmpfs tmpfs rw\n'
        '12 10 0:3 / /dev rw - tmpfs tmpfs rw\n'
        '13 10 0:4 / /proc rw - proc proc rw\n' + extra)


def device(mode=0o40755):
    return SimpleNamespace(st_uid=0, st_mode=mode)


def test_only_explicit_private_writable_root_and_kernel_views_are_allowed():
    assert isolation.writable_mounts_are_private(table(), '/proof', device_root=device())


@pytest.mark.parametrize('mount,kind', [('/etc/hosts', 'ext4'), ('/dev/shm', 'tmpfs'),
    ('/tmp', 'tmpfs'), ('/run', 'tmpfs'), ('/dev/mqueue', 'mqueue'), ('/proof-other', 'tmpfs')])
def test_automatic_or_extra_writable_mount_is_not_an_exemption(mount, kind):
    entries = table(f'14 10 0:5 / {mount} rw - {kind} source rw\n')
    with pytest.raises(isolation.IsolationFailure, match='outside private root'):
        isolation.writable_mounts_are_private(entries, '/proof', device_root=device())


def test_writable_root_or_device_root_and_missing_private_mount_refuse():
    entries = table()
    entries[0]['options'] = {'rw'}
    with pytest.raises(isolation.IsolationFailure, match='root filesystem'):
        isolation.writable_mounts_are_private(entries, '/proof', device_root=device())
    with pytest.raises(isolation.IsolationFailure, match='device root'):
        isolation.writable_mounts_are_private(table(), '/proof', device_root=device(0o40777))
    with pytest.raises(isolation.IsolationFailure, match='private writable mount missing'):
        isolation.writable_mounts_are_private([entry for entry in table() if entry['mount'] != '/proof'],
                                               '/proof', device_root=device())


def test_mount_parser_handles_escaped_names_and_rejects_truncation():
    parsed = isolation.mounts(r'10 1 0:1 / /space\040name ro - tmpfs tmpfs ro')
    assert parsed[0]['mount'] == '/space name'
    for raw in ('', '10 1 0:1 / / rw', '10 - overlay'):
        with pytest.raises(isolation.IsolationFailure):
            isolation.mounts(raw)


def test_routes_reject_every_non_loopback_family():
    header = 'Iface Destination Gateway Flags RefCnt Use Metric Mask\n'
    assert isolation.routes_are_loopback(header, '')
    with pytest.raises(isolation.IsolationFailure):
        isolation.routes_are_loopback(header + 'eth0 0 0 0 0 0 0 0', '')
    with pytest.raises(isolation.IsolationFailure):
        isolation.routes_are_loopback(header, '0 0 0 0 0 0 0 0 0 eth0')
    with pytest.raises(isolation.IsolationFailure):
        isolation.routes_are_loopback('', '')


def test_visible_socket_requires_exact_owned_private_path(tmp_path):
    proof = tmp_path / 'proof'
    proof.mkdir()
    name = proof / 'owned.sock'
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(name))
        with pytest.raises(isolation.IsolationFailure, match='unexpected visible'):
            isolation.audit_sockets(proof, (), root=tmp_path)
        assert isolation.audit_sockets(proof, [name], root=tmp_path)
        with pytest.raises(isolation.IsolationFailure, match='outside private root'):
            isolation.audit_sockets(proof, [tmp_path / 'other.sock'], root=tmp_path)
        with pytest.raises(isolation.IsolationFailure, match='bounded walk'):
            isolation.audit_sockets(proof, [name], root=tmp_path, limit=1)


def test_structural_failure_prevents_any_network_attempt(monkeypatch):
    def fail(*args):
        raise isolation.IsolationFailure('host namespace')
    monkeypatch.setattr(isolation, 'structural_observation', fail)
    monkeypatch.setattr(isolation, 'network_observation', lambda *args: pytest.fail('network attempted'))
    with pytest.raises(isolation.IsolationFailure, match='host namespace'):
        isolation.observe('net:[1]', '/proof', (), 1)


def test_current_host_namespace_refuses_before_network(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(isolation, 'network_observation', lambda *args: pytest.fail('network attempted'))
    host = isolation.os.readlink('/proc/self/ns/net')
    with pytest.raises(isolation.IsolationFailure, match='did not differ'):
        isolation.observe(host, str(tmp_path), (), 1)


def test_double_fork_preserves_rejection_and_never_runs_network(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(isolation, 'network_observation', lambda *args: pytest.fail('network attempted'))
    host = isolation.os.readlink('/proc/self/ns/net')
    with pytest.raises(isolation.IsolationFailure, match='did not differ'):
        isolation.double_fork_observe(host, str(tmp_path), (), 1)


def test_successful_external_send_is_immediate_failure(monkeypatch):
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, timeout): pass
        def connect(self, address): pass
    monkeypatch.setattr(isolation.socket, 'socket', lambda *args: Socket())
    with pytest.raises(isolation.IsolationFailure, match='operation succeeded'):
        isolation.network_observation(1)


def test_timeout_is_not_mistaken_for_kernel_denial(monkeypatch):
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, timeout): pass
        def connect(self, address): raise TimeoutError('no reply')
    monkeypatch.setattr(isolation.socket, 'socket', lambda *args: Socket())
    with pytest.raises(isolation.IsolationFailure, match='route denial'):
        isolation.network_observation(1)
