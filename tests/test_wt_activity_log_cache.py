"""Tests for WatchTower activity.log incremental cache and queue filtering."""
from __future__ import annotations

import importlib
import io
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def server():
    mod = importlib.import_module("server")
    mod._WT_ACTIVITY_LOG_CACHE.clear()
    return mod


def test_cold_read_with_and_without_queue_filter(server, tmp_path):
    log_file = tmp_path / "activity.log"
    line_ccc_1 = "2026-08-03 12:00:00 UTC  CCC             SPAWN    w-1"
    line_wt_1 = "2026-08-03 12:00:05 UTC  WT              CLAIM    WT-42"
    line_ccc_2 = "2026-08-03 12:00:10 UTC  CCC             RELEASE  w-1"
    log_file.write_text(f"{line_ccc_1}\n{line_wt_1}\n{line_ccc_2}\n", encoding="utf-8")

    all_lines = server._wt_activity_log_lines(log_file)
    assert all_lines == [line_ccc_1, line_wt_1, line_ccc_2]

    ccc_lines = server._wt_activity_log_lines(log_file, queue_filter="ccc")
    assert ccc_lines == [line_ccc_1, line_ccc_2]

    wt_lines = server._wt_activity_log_lines(log_file, queue_filter="WT")
    assert wt_lines == [line_wt_1]

    empty_lines = server._wt_activity_log_lines(log_file, queue_filter="MISSING")
    assert empty_lines == []


def test_incremental_append_seeks_instead_of_rereading_from_zero(server, tmp_path, monkeypatch):
    log_file = tmp_path / "activity.log"
    line_1 = "2026-08-03 12:00:00 UTC  CCC             SPAWN    w-1"
    line_2 = "2026-08-03 12:00:05 UTC  WT              CLAIM    WT-1"
    initial_payload = f"{line_1}\n{line_2}\n"
    log_file.write_text(initial_payload, encoding="utf-8")

    assert server._wt_activity_log_lines(log_file) == [line_1, line_2]

    line_3 = "2026-08-03 12:00:10 UTC  CCC             DISPATCH CCC-9"
    line_4 = "2026-08-03 12:00:15 UTC  RECONCILER      REAP     w-2"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{line_3}\n{line_4}\n")

    seek_offsets: list[int] = []
    real_open = open

    class _TrackingProxy:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def seek(self, pos, whence=0):
            seek_offsets.append(pos)
            return self._fh.seek(pos, whence)

        def __getattr__(self, name):
            return getattr(self._fh, name)

    def _tracking_open(file, mode="r", *args, **kwargs):
        fh = real_open(file, mode, *args, **kwargs)
        if pathlib.Path(file) == log_file and "b" in mode and "r" in mode:
            return _TrackingProxy(fh)
        return fh

    monkeypatch.setattr("builtins.open", _tracking_open)

    assert server._wt_activity_log_lines(log_file) == [line_1, line_2, line_3, line_4]
    assert seek_offsets == [len(initial_payload.encode("utf-8"))]
    assert server._wt_activity_log_lines(log_file, queue_filter="ccc") == [line_1, line_3]
    assert server._wt_activity_log_lines(log_file, queue_filter="reconciler") == [line_4]
    # Second call with no file changes hits cache without opening file again
    assert seek_offsets == [len(initial_payload.encode("utf-8"))]


def test_truncation_and_inode_replacement_reset_cache(server, tmp_path):
    log_file = tmp_path / "activity.log"
    line_1 = "2026-08-03 12:00:00 UTC  CCC             SPAWN    w-1"
    line_2 = "2026-08-03 12:00:05 UTC  CCC             CLAIM    CCC-1"
    line_3 = "2026-08-03 12:00:10 UTC  WT              STOP     w-9"
    log_file.write_text(f"{line_1}\n{line_2}\n{line_3}\n", encoding="utf-8")

    assert len(server._wt_activity_log_lines(log_file)) == 3
    assert len(server._wt_activity_log_lines(log_file, queue_filter="CCC")) == 2

    # Truncate in-place (smaller size, same path)
    short_line = "2026-08-03 13:00:00 UTC  WT              SPAWN    w-2"
    log_file.write_text(f"{short_line}\n", encoding="utf-8")
    assert server._wt_activity_log_lines(log_file) == [short_line]
    assert server._wt_activity_log_lines(log_file, queue_filter="CCC") == []
    assert server._wt_activity_log_lines(log_file, queue_filter="WT") == [short_line]

    # Replace via atomic rename (new inode, larger size)
    replacement = tmp_path / "activity.log.new"
    rep_1 = "2026-08-03 14:00:00 UTC  CCC             RUN      CCC-10"
    rep_2 = "2026-08-03 14:00:05 UTC  CCC             RELEASE  CCC-10"
    replacement.write_text(f"{rep_1}\n{rep_2}\n", encoding="utf-8")
    replacement.replace(log_file)

    assert server._wt_activity_log_lines(log_file) == [rep_1, rep_2]
    assert server._wt_activity_log_lines(log_file, queue_filter="WT") == []
    assert server._wt_activity_log_lines(log_file, queue_filter="CCC") == [rep_1, rep_2]


def test_missing_file_handling_and_diagnostic_events(server, tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist.log"
    assert server._wt_activity_log_lines(missing) == []
    assert server._wt_activity_log_lines(missing, queue_filter="CCC") == []
    with pytest.raises(FileNotFoundError):
        server._wt_activity_log_lines(missing, raise_missing=True)

    monkeypatch.setattr(server, "_WT_HOME", tmp_path)
    assert server._wt_diagnostic_events("CCC", now=1785758420) == []

    log_file = tmp_path / "activity.log"
    log_file.write_text(
        "2026-08-03 12:00:00 UTC  CCC             SPAWN    secret-worker\n"
        "2026-08-03 12:00:05 UTC  OTHER           CLAIM    other-ticket\n"
        "2026-08-03 12:00:10 UTC  CCC             RELEASE  secret-worker\n",
        encoding="utf-8",
    )
    events = server._wt_diagnostic_events("CCC", now=1785758420)
    assert events == [
        {"verb": "SPAWN", "seconds_ago": 20},
        {"verb": "RELEASE", "seconds_ago": 10},
    ]

    # Deleting a previously cached file clears the cache and returns []
    log_file.unlink()
    assert server._wt_activity_log_lines(log_file) == []
    assert server._wt_diagnostic_events("CCC", now=1785758420) == []
