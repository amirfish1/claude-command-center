"""Windows regressions exercised without a Windows host or agent processes."""

import builtins
import ctypes
from ctypes import wintypes
import os
from pathlib import PureWindowsPath
import signal
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

import server
from ccc_server import compact, config_consent, engines, paths


@pytest.mark.parametrize("wait_result, alive", [(258, True), (0, False), (0xFFFFFFFF, False)])
def test_windows_liveness_preserves_pointer_sized_handles(wait_result, alive):
    handle = 0x1234567887654321
    kernel = mock.Mock()
    kernel.OpenProcess.return_value = handle
    kernel.WaitForSingleObject.return_value = wait_result
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch.object(ctypes, "windll", SimpleNamespace(kernel32=kernel), create=True), \
         mock.patch.object(os, "kill") as kill:
        assert paths._is_pid_alive(12345) is alive
    kill.assert_not_called()
    assert kernel.OpenProcess.restype is wintypes.HANDLE
    assert kernel.OpenProcess.argtypes == [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    assert kernel.WaitForSingleObject.argtypes == [wintypes.HANDLE, wintypes.DWORD]
    assert kernel.CloseHandle.argtypes == [wintypes.HANDLE]
    kernel.WaitForSingleObject.assert_called_once_with(handle, 0)
    kernel.CloseHandle.assert_called_once_with(handle)


def test_windows_liveness_closes_handle_when_wait_fails():
    kernel = mock.Mock()
    kernel.OpenProcess.return_value = 123
    kernel.WaitForSingleObject.side_effect = OSError("wait failed")
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch.object(ctypes, "windll", SimpleNamespace(kernel32=kernel), create=True), \
         mock.patch.object(os, "kill") as kill:
        assert paths._is_pid_alive(12345) is False
    kernel.CloseHandle.assert_called_once_with(123)
    kill.assert_not_called()


@pytest.mark.parametrize("platform_name, missing_module", [
    ("win32", None), ("linux", "pty"), ("linux", "termios"),
])
def test_hidden_pty_unavailable_does_not_retire_session(platform_name, missing_module):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == missing_module:
            raise ImportError(f"No module named {name}")
        return real_import(name, *args, **kwargs)

    # A strict proxy catches any session lookup, retirement or CLI resolution
    # before the platform capability has been checked.
    with mock.patch.object(sys, "platform", platform_name), \
         mock.patch.object(builtins, "__import__", side_effect=guarded_import), \
         mock.patch.object(compact, "_core", SimpleNamespace()), \
         mock.patch.object(compact.subprocess, "Popen") as spawn:
        result = compact._compact_via_hidden_pty("test-session", None)
    assert result["ok"] is False
    assert result["via"] == "hidden-pty"
    assert "unavailable" in result["error"].lower()
    spawn.assert_not_called()


def test_windows_fifo_write_refuses_blocking_descriptor():
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch.object(engines.fcntl, "fcntl") as fcntl_call, \
         mock.patch.object(os, "write") as write:
        assert engines._write_fd_nonblocking(123, b"test\n") is False
    fcntl_call.assert_not_called()
    write.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="real POSIX pipe regression")
def test_posix_fifo_write_sets_nonblocking_and_delivers_bytes():
    reader, writer = os.pipe()
    try:
        assert engines._write_fd_nonblocking(writer, b"test\n") is True
        assert os.get_blocking(writer) is False
        assert os.read(reader, 5) == b"test\n"
    finally:
        os.close(reader)
        os.close(writer)


def test_windows_retire_terminates_pid_and_cleans_entry():
    entry = {"pid": 12345, "engine": "claude"}
    tracked = [entry]
    core = SimpleNamespace(
        _resume_ledger_append=mock.Mock(), _log_activity=mock.Mock(),
        _record_kill_event=mock.Mock(), _remove_spawn_from_registry=mock.Mock(),
        _spawned_sessions=tracked,
    )
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch.object(engines, "_core", core), \
         mock.patch.object(os, "kill") as kill, \
         mock.patch.object(os, "killpg", create=True) as killpg:
        engines._retire_unresponsive_spawn_entry(entry, terminate=True)
    kill.assert_called_once_with(12345, signal.SIGTERM)
    killpg.assert_not_called()
    core._remove_spawn_from_registry.assert_called_once_with(12345)
    assert tracked == []


def test_force_stop_without_sigkill_preserves_pid_protections():
    with mock.patch.object(server, "signal", SimpleNamespace(SIGTERM=signal.SIGTERM)), \
         mock.patch.object(os, "getpid", return_value=100), \
         mock.patch.object(os, "getppid", return_value=99), \
         mock.patch.object(os, "kill") as kill, \
         mock.patch.object(server, "_resume_ledger_append"), \
         mock.patch.object(server, "_log_activity"):
        result = server.system_process_kill([0, 1, 99, 100, 12345], force=True)
    assert result["killed"] == [12345]
    assert result["blocked"] == [0, 1, 99, 100]
    kill.assert_called_once_with(12345, signal.SIGTERM)


@pytest.mark.parametrize("alias", [
    r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\wt.exe",
    "C:/Users/test/AppData/Local/Microsoft/WindowsApps/wt.exe",
    "C:/Users/test/AppData/Local/Microsoft/WINDOWSAPPS\\wt.exe",
])
def test_consent_rejects_terminal_alias_with_either_separator(alias):
    scripts = r"C:\Python\Scripts"
    expected = PureWindowsPath(scripts) / "wt.exe"
    windows_os = SimpleNamespace(name="nt", X_OK=os.X_OK, access=lambda p, mode: p == expected)
    with mock.patch.object(config_consent, "os", windows_os), \
         mock.patch.object(config_consent, "Path", PureWindowsPath), \
         mock.patch.object(config_consent.shutil, "which", return_value=alias), \
         mock.patch.object(config_consent.sysconfig, "get_path", return_value=scripts):
        assert config_consent._find_wt() == str(expected)


def test_windows_desktop_links_remain_enabled():
    with mock.patch.object(server.platform, "system", return_value="Windows"), \
         mock.patch.object(server, "_linux_folder_picker_cmd", return_value=None):
        assert server._platform_capabilities()["desktopDeepLinks"] is True
