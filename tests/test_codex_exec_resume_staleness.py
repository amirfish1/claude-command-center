"""A hung `codex exec resume` fallback child must self-heal, not wedge forever.

That process is a one-shot CLI call talking to a single app-server socket; it
should finish in seconds to a few minutes. If the app-server it was talking to
died, the call blocks past its own RPC timeout and `proc.poll()` still reports
"running" — nothing else in CCC ever noticed, so every later send to that
thread queued forever behind the corpse (a real hour-long hang observed live).
"""
import time

import server

_codex_exec_resume_entry_is_stale = server._codex_exec_resume_entry_is_stale


def _entry(started_ago_s, log_mtime_ago_s, log_path):
    log_path.write_text("some output\n")
    now = time.time()
    mtime = now - log_mtime_ago_s
    import os
    os.utime(log_path, (mtime, mtime))
    started = time.strftime("%Y%m%dT%H%M%S", time.localtime(now - started_ago_s))
    return {"engine": "codex", "started": started, "log": str(log_path)}


def test_young_quiet_entry_is_not_stale(tmp_path):
    entry = _entry(started_ago_s=30, log_mtime_ago_s=30, log_path=tmp_path / "a.log")
    assert _codex_exec_resume_entry_is_stale(entry) is False


def test_old_but_actively_writing_entry_is_not_stale(tmp_path):
    entry = _entry(started_ago_s=3600, log_mtime_ago_s=5, log_path=tmp_path / "b.log")
    assert _codex_exec_resume_entry_is_stale(entry) is False


def test_old_and_silent_entry_is_stale(tmp_path):
    entry = _entry(started_ago_s=3700, log_mtime_ago_s=3650, log_path=tmp_path / "c.log")
    assert _codex_exec_resume_entry_is_stale(entry) is True


def test_missing_log_path_is_not_stale(tmp_path):
    entry = {"engine": "codex", "started": time.strftime("%Y%m%dT%H%M%S", time.localtime(time.time() - 3700))}
    assert _codex_exec_resume_entry_is_stale(entry) is False


def test_non_dict_entry_is_not_stale():
    assert _codex_exec_resume_entry_is_stale(None) is False
