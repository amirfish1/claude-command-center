"""Restart authority remains alive when a liveness probe cannot decide."""
import errno
import os

import pytest

from ccc_server import paths
from ccc_server import codex_restart_gate as gate


@pytest.mark.skipif(os.name == 'nt', reason='POSIX error semantics')
@pytest.mark.parametrize('error,conservative', [
    (ProcessLookupError(errno.ESRCH, 'gone'), False),
    (PermissionError(errno.EPERM, 'another user'), True),
    (OSError(errno.EIO, 'probe failed'), True),
])
def test_restart_probe_never_releases_authority_on_unknown(monkeypatch, error, conservative):
    def fail(*args):
        raise error
    monkeypatch.setattr(paths.os, 'kill', fail)
    assert paths._is_pid_alive(123) is False
    assert gate._pid_alive(123) is conservative
