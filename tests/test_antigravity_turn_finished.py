"""A finished Antigravity turn must not keep the conversation busy."""
import importlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest import mock

import pytest


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
