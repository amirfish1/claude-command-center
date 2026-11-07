"""An interrupted hook must not leave an unanswerable approval badge."""

import os

import server


def test_dead_hook_does_not_block(monkeypatch):
    def dead(pid, signal):
        raise ProcessLookupError

    monkeypatch.setattr(server.os, "kill", dead)
    assert not server._notification_is_blocking({
        "source": "permission-request", "pid": 12345,
        "message": "Claude needs your permission to use Bash",
    })


def test_live_hook_still_blocks():
    assert server._notification_is_blocking({
        "source": "permission-request", "pid": os.getpid(),
        "message": "Claude needs your permission to use Bash",
    })


def test_terminal_notification_is_not_a_hook():
    assert server._notification_is_blocking({
        "source": "notification", "pid": 12345,
        "message": "Claude needs your permission to use Bash",
    })


def test_unreadable_process_still_blocks(monkeypatch):
    def unreadable(pid, signal):
        raise PermissionError

    monkeypatch.setattr(server.os, "kill", unreadable)
    assert server._notification_is_blocking({
        "source": "permission-request", "pid": 12345,
        "message": "Claude needs your permission to use Bash",
    })
