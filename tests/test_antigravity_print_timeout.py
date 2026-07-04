"""OPS-84 — AGY print-mode runs must outlive the CLI's 5m default timeout.

The AGY CLI kills `-p` runs after --print-timeout (CLI default 5m0s) with
"Error: timeout waiting for response", aborting healthy long spawns mid-task.
CCC must pass an explicit long --print-timeout on every spawn/resume, and the
resume-staleness window must exceed that timeout so a legit long turn is not
mistaken for a hung process (which would race a second agy on the same
conversation).
"""
import os
import sys
import uuid as uuid_mod
from unittest import mock

import pytest


@pytest.fixture()
def server_mod():
    sys.modules.pop("server", None)
    import server
    return server


class TestGoDurationParsing:
    def test_parses_common_forms(self, server_mod):
        parse = server_mod._parse_go_duration_seconds
        assert parse("2h") == 7200
        assert parse("5m0s") == 300
        assert parse("90s") == 90
        assert parse("1h30m") == 5400
        assert parse("500ms") == 0.5

    def test_rejects_garbage(self, server_mod):
        parse = server_mod._parse_go_duration_seconds
        assert parse("") is None
        assert parse("soon") is None
        assert parse("10") is None  # Go durations require a unit
        assert parse("5 m") is None

    def test_env_override_and_fallback(self, server_mod):
        with mock.patch.dict(os.environ, {"CCC_ANTIGRAVITY_PRINT_TIMEOUT": "45m"}):
            assert server_mod._antigravity_print_timeout() == "45m"
            assert server_mod._antigravity_print_timeout_seconds() == 45 * 60
        with mock.patch.dict(os.environ, {"CCC_ANTIGRAVITY_PRINT_TIMEOUT": "bogus"}):
            assert (
                server_mod._antigravity_print_timeout()
                == server_mod._ANTIGRAVITY_PRINT_TIMEOUT_DEFAULT
            )

    def test_stale_window_exceeds_print_timeout(self, server_mod):
        assert (
            server_mod._antigravity_resume_stale_seconds()
            > server_mod._antigravity_print_timeout_seconds()
        )


def _spawn_with_mocked_popen(server_mod, tmp_path, extra_env=None):
    proc = mock.Mock(pid=4321)
    proc.poll.return_value = None
    env = dict(extra_env or {})
    original_spawns = list(server_mod._spawned_sessions)
    server_mod._spawned_sessions.clear()
    try:
        with mock.patch.dict(os.environ, env), mock.patch.object(
            server_mod,
            "_resolve_antigravity_bin",
            return_value={"available": True, "bin": "/usr/bin/agy-test"},
        ), mock.patch.object(
            server_mod.subprocess, "Popen", return_value=proc
        ) as popen, mock.patch.object(server_mod, "_record_spawn_to_registry"):
            result = server_mod.spawn_session_antigravity(
                "long running task", name="agy timeout", repo_path=str(tmp_path),
            )
        return result, popen.call_args.args[0]
    finally:
        for entry in server_mod._spawned_sessions:
            fh = entry.get("log_fh")
            if fh:
                fh.close()
        server_mod._spawned_sessions.clear()
        server_mod._spawned_sessions.extend(original_spawns)


class TestSpawnCommand:
    def test_spawn_passes_print_timeout(self, server_mod, tmp_path):
        result, cmd = _spawn_with_mocked_popen(server_mod, tmp_path)
        assert result["ok"]
        assert "--print-timeout" in cmd
        value = cmd[cmd.index("--print-timeout") + 1]
        assert value == server_mod._ANTIGRAVITY_PRINT_TIMEOUT_DEFAULT
        # --print-timeout must precede the prompt so agy parses it as a flag
        assert cmd.index("--print-timeout") < cmd.index("-p")

    def test_spawn_respects_user_args_override(self, server_mod, tmp_path):
        result, cmd = _spawn_with_mocked_popen(
            server_mod, tmp_path,
            extra_env={"CCC_ANTIGRAVITY_ARGS": "--print-timeout 10m"},
        )
        assert result["ok"]
        assert cmd.count("--print-timeout") == 1
        assert cmd[cmd.index("--print-timeout") + 1] == "10m"


class TestResumeCommand:
    def test_resume_passes_print_timeout(self, server_mod, tmp_path):
        sid = str(uuid_mod.uuid4())
        conv = tmp_path / "conv.pb"
        conv.write_bytes(b"pb")
        proc = mock.Mock(pid=4322)
        proc.poll.return_value = None
        original_spawns = list(server_mod._spawned_sessions)
        server_mod._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server_mod,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(
                server_mod, "_antigravity_cli_conversation_path", return_value=conv,
            ), mock.patch.object(
                server_mod, "find_session_cwd", return_value=str(tmp_path),
            ), mock.patch.object(
                server_mod, "_git_toplevel_for_existing_dir", return_value=str(tmp_path),
            ), mock.patch.object(
                server_mod.subprocess, "Popen", return_value=proc,
            ) as popen, mock.patch.object(server_mod, "_record_spawn_to_registry"):
                result = server_mod.resume_session_antigravity(sid, "keep going")
        finally:
            for entry in server_mod._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server_mod._spawned_sessions.clear()
            server_mod._spawned_sessions.extend(original_spawns)

        assert result["ok"]
        cmd = popen.call_args.args[0]
        assert "--print-timeout" in cmd
        assert cmd.index("--print-timeout") < cmd.index("-p")
