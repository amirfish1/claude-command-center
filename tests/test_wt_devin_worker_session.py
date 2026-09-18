"""Devin WatchTower worker -> session_id resolution regression coverage."""
import importlib
import json
import pathlib
import sys
from unittest import mock

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def server():
    mod = importlib.import_module("server")
    mod._WT_DEVIN_SID_BY_PID.clear()
    yield mod
    mod._WT_DEVIN_SID_BY_PID.clear()


def _write_workers(tmp_path, workers):
    workers_path = tmp_path / "workers.json"
    workers_path.write_text(
        json.dumps({"workers": workers}), encoding="utf-8")
    return workers_path


def _read(server, workers_path, **kw):
    with mock.patch.object(server, "_wt_workers_path", return_value=workers_path), \
         mock.patch.object(server.os, "kill", return_value=None):
        return server._wt_read_workers(**kw)


def test_devin_worker_without_session_id_resolves_by_pid(server, tmp_path):
    """A devin worker's session slug never reaches workers.json, so the
    pending row could never match its real conversation (CCC-1162). The
    reader resolves it from the session-lock pid, in-memory only."""
    path = _write_workers(tmp_path, [
        {"worker_id": "w-devin", "pid": 4242, "queue": "CCC",
         "engine": "devin", "session_id": None},
    ])
    with mock.patch.object(
            server, "_devin_cli_raw_id_for_pid", return_value="blue-heron") as raw:
        rows = _read(server, path, include_activity=False)

    raw.assert_called_once_with(4242)
    assert rows[0]["session_id"] == "devincli-blue-heron"


def test_existing_session_id_is_not_overwritten(server, tmp_path):
    path = _write_workers(tmp_path, [
        {"worker_id": "w-devin", "pid": 4242, "queue": "CCC",
         "engine": "devin", "session_id": "already-there"},
    ])
    with mock.patch.object(server, "_devin_cli_raw_id_for_pid") as raw:
        rows = _read(server, path)

    raw.assert_not_called()
    assert rows[0]["session_id"] == "already-there"


def test_non_devin_engines_skip_pid_resolution(server, tmp_path):
    path = _write_workers(tmp_path, [
        {"worker_id": "w-claude", "pid": 4242, "queue": "CCC",
         "engine": "claude", "session_id": None},
    ])
    with mock.patch.object(server, "_devin_cli_raw_id_for_pid") as raw:
        rows = _read(server, path)

    raw.assert_not_called()
    assert rows[0]["session_id"] is None


def test_resolution_failure_leaves_session_id_unset(server, tmp_path):
    path = _write_workers(tmp_path, [
        {"worker_id": "w-devin", "pid": 4242, "queue": "CCC",
         "engine": "devin", "session_id": None},
    ])
    with mock.patch.object(
            server, "_devin_cli_raw_id_for_pid", side_effect=OSError("ps boom")):
        rows = _read(server, path)

    assert rows[0]["session_id"] is None


def test_pid_resolution_is_memoised(server, tmp_path):
    """The resolver can shell out to ps; it must not run once per poll."""
    path = _write_workers(tmp_path, [
        {"worker_id": "w-devin", "pid": 4242, "queue": "CCC",
         "engine": "devin", "session_id": None},
    ])
    with mock.patch.object(
            server, "_devin_cli_raw_id_for_pid", return_value="blue-heron") as raw:
        assert _read(server, path)[0]["session_id"] == "devincli-blue-heron"
        assert _read(server, path)[0]["session_id"] == "devincli-blue-heron"

    assert raw.call_count == 1


def test_miss_is_negative_cached(server, tmp_path):
    path = _write_workers(tmp_path, [
        {"worker_id": "w-devin", "pid": 4242, "queue": "CCC",
         "engine": "devin", "session_id": None},
    ])
    with mock.patch.object(
            server, "_devin_cli_raw_id_for_pid", return_value=None) as raw:
        assert _read(server, path)[0]["session_id"] is None
        assert _read(server, path)[0]["session_id"] is None

    assert raw.call_count == 1
