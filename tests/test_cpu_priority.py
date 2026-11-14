"""Maintenance yields without lowering foreground work or raising priority."""
from unittest import mock

import pytest

from ccc_server import cpu_priority


@pytest.mark.parametrize("role", ["main", "archive"])
def test_maintenance_lowers_priority(role, monkeypatch):
    monkeypatch.delenv("CCC_BACKGROUND_NICE", raising=False)
    with mock.patch.object(cpu_priority.os, "getpriority", return_value=0), \
            mock.patch.object(cpu_priority.os, "setpriority") as apply:
        assert cpu_priority.set_background_priority(role)
    apply.assert_called_once_with(cpu_priority.os.PRIO_PROCESS, 0, 10)


@pytest.mark.parametrize("role", ["live", "dashboard", "voice"])
def test_interactive_work_keeps_inherited_priority(role):
    with mock.patch.object(cpu_priority.os, "setpriority") as apply:
        assert not cpu_priority.set_background_priority(role)
    apply.assert_not_called()


def test_repeated_application_never_raises_existing_priority(monkeypatch):
    monkeypatch.delenv("CCC_BACKGROUND_NICE", raising=False)
    with mock.patch.object(cpu_priority.os, "getpriority", return_value=15), \
            mock.patch.object(cpu_priority.os, "setpriority") as apply:
        assert cpu_priority.set_background_priority("archive")
    apply.assert_not_called()


def test_launcher_can_disable_or_override_background_priority(monkeypatch):
    with mock.patch.object(cpu_priority.os, "getpriority", return_value=0), \
            mock.patch.object(cpu_priority.os, "setpriority") as apply:
        monkeypatch.setenv("CCC_BACKGROUND_NICE", "0")
        assert not cpu_priority.set_background_priority("main")
        apply.assert_not_called()
        monkeypatch.setenv("CCC_BACKGROUND_NICE", "12")
        assert cpu_priority.set_background_priority("main")
        apply.assert_called_once_with(cpu_priority.os.PRIO_PROCESS, 0, 12)


def test_restricted_platform_does_not_break_startup(monkeypatch):
    monkeypatch.delenv("CCC_BACKGROUND_NICE", raising=False)
    with mock.patch.object(cpu_priority.os, "getpriority", side_effect=OSError):
        assert not cpu_priority.set_background_priority("main")
