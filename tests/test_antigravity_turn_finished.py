"""A finished Antigravity turn must not keep the conversation busy."""
import importlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest import mock

import pytest
import server  # noqa: F401 — initializes the module registry


def _write_transcript(root, sid, rows, age_s):
    logs = Path(root) / sid / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    path = logs / "transcript_full.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    old = time.time() - age_s
    os.utime(path, (old, old))


FINAL = {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "done"}
TOOL = {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
        "tool_calls": [{"name": "run_command"}]}
USER = {"source": "USER", "type": "USER_INPUT", "status": "DONE"}


@pytest.mark.parametrize("rows,age,expected", [
    ([USER, {"type": "CHECKPOINT", "status": "DONE",
              "content": "# Resuming from a compaction\n\nSummary."}], 300, True),
    ([USER, FINAL], 300, True),            # final answer, idle
    ([USER, FINAL], 5, False),             # still being written
    ([USER, TOOL], 300, False),            # last step is a tool call
    ([FINAL, USER], 300, False),           # a new user turn began
    ([USER, dict(FINAL, status="RUNNING")], 300, False),
])
def test_turn_finished_reads_the_last_transcript_row(tmp_path, rows, age, expected):
    server = importlib.import_module("server")
    sid = str(uuid.uuid4())
    _write_transcript(tmp_path, sid, rows, age)
    with mock.patch.object(server, "ANTIGRAVITY_BRAIN", tmp_path), \
         mock.patch.object(server, "ANTIGRAVITY_CLI_BRAIN", tmp_path / "none"):
        assert server._antigravity_turn_finished(sid) is expected


def test_freshly_started_process_is_never_finished(tmp_path):
    """The transcript still shows the previous answer right after a resume starts."""
    server = importlib.import_module("server")
    sid = str(uuid.uuid4())
    _write_transcript(tmp_path, sid, [USER, FINAL], 300)
    with mock.patch.object(server, "ANTIGRAVITY_BRAIN", tmp_path), \
         mock.patch.object(server, "ANTIGRAVITY_CLI_BRAIN", tmp_path / "none"), \
         mock.patch("ccc_server.engines._pid_age_seconds", return_value=3.0):
        assert server._antigravity_turn_finished(sid, pid=123) is False


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS Python rewrites argv[0] after exec")
def test_reap_only_touches_that_conversations_agy():
    server = importlib.import_module("server")
    sid = str(uuid.uuid4())
    code = "import time; time.sleep(60)"
    proc = subprocess.Popen(["agy", "-c", code, "--conversation", sid],
                            executable=sys.executable)
    other = subprocess.Popen(["agy", "-c", code, "--conversation", str(uuid.uuid4())],
                             executable=sys.executable)
    try:
        assert server._antigravity_reap_finished_process("not-this-one", proc.pid) is False
        assert proc.poll() is None
        with mock.patch("ccc_server.engines._core._log_activity"):
            assert server._antigravity_reap_finished_process(sid, proc.pid) is True
        proc.wait(timeout=5)
        assert other.poll() is None
    finally:
        other.kill()
        proc.kill()


@pytest.mark.parametrize('elapsed,expected', [('02:03', 123), ('01:02:03', 3723), ('2-01:02:03', 176523), ('bad', None)])
def test_process_age_uses_portable_elapsed_time(elapsed, expected):
    from ccc_server import engines
    def ps(cmd, **kwargs):
        assert 'etime=' in cmd
        return subprocess.CompletedProcess(cmd, 0, elapsed)
    with mock.patch.object(engines.subprocess, 'run', side_effect=ps):
        assert engines._pid_age_seconds(123) == expected


def test_process_age_can_read_this_process():
    from ccc_server import engines
    assert engines._pid_age_seconds(os.getpid()) is not None


def test_reaper_does_not_treat_missing_proc_filesystem_as_process_exit():
    from ccc_server import engines
    with mock.patch.object(engines.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'unrelated --conversation test-session')), \
         mock.patch.object(engines.os, 'kill') as kill:
        assert engines._antigravity_reap_finished_process('test-session', 123) is False
        kill.assert_not_called()


def test_reaper_accepts_initial_spawn_only_with_matching_log_identity():
    from ccc_server import engines
    argv='agy --log-file /tmp/example-agy.log -p example'
    with mock.patch.object(engines.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, argv)), \
         mock.patch.object(engines._core, '_antigravity_cli_log_meta', return_value={'session_id': 'test-session'}), \
         mock.patch.object(engines, '_paths_is_pid_alive', side_effect=[True, False, False, False]), \
         mock.patch.object(engines.os, 'kill') as kill, \
         mock.patch.object(engines.os, 'waitpid', side_effect=ChildProcessError), \
         mock.patch.object(engines._core, '_log_activity'):
        assert engines._antigravity_reap_finished_process('test-session', 123) is True
        kill.assert_called_once_with(123, signal.SIGTERM)


@pytest.mark.parametrize('argv,log_sid', [
    ('agy --conversation other-session -p example', 'test-session'),
    ('agy --log-file /tmp/example-agy.log -p example', 'other-session'),
    ('agy -p "--conversation test-session"', None),
])
def test_reaper_refuses_another_conversation(argv, log_sid):
    from ccc_server import engines
    with mock.patch.object(engines.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, argv)), \
         mock.patch.object(engines._core, '_antigravity_cli_log_meta', return_value={'session_id': log_sid}), \
         mock.patch.object(engines.os, 'kill') as kill:
        assert engines._antigravity_reap_finished_process('test-session', 123) is False
        kill.assert_not_called()


def test_initial_spawn_reaper_preserves_spaces_and_quotes_in_log_path():
    from ccc_server import engines
    log="/tmp/example repo/owner's agy.log"
    command=f'agy --settings {{"model":"example"}} --log-file {log} --print-timeout 2h -p example'
    with mock.patch.object(engines.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, command)), \
         mock.patch.object(engines._core, '_antigravity_cli_log_meta', return_value={'session_id': 'test-session'}) as meta, \
         mock.patch.object(engines, '_paths_is_pid_alive', side_effect=[True, False, False, False]), \
         mock.patch.object(engines.os, 'kill'), \
         mock.patch.object(engines.os, 'waitpid', side_effect=ChildProcessError), \
         mock.patch.object(engines._core, '_log_activity'):
        assert engines._antigravity_reap_finished_process('test-session', 123) is True
        meta.assert_called_once_with(log)
