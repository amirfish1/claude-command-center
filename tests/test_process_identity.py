"""Contracts for bounded process launch identity reads."""

import os
import struct
import unittest
from unittest import mock

from ccc_server import process_identity


class ProcessIdentityTests(unittest.TestCase):
    def test_snapshot_uses_one_batched_ps_call_and_skips_bad_rows(self):
        completed = mock.Mock(
            returncode=0,
            stdout="  41     1 Mon Sep  7 12:34:56 2026\ninvalid row\n",
        )
        with mock.patch.object(process_identity.subprocess, "run", return_value=completed) as run:
            snapshot = process_identity.process_snapshot()

        self.assertEqual(
            snapshot,
            {41: {"ppid": 1, "birth": "Mon Sep 7 12:34:56 2026"}},
        )
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["ps", "-axo", "pid=,ppid=,lstart="])

    def test_linux_parser_returns_argv_and_only_allowlisted_environment(self):
        identity = process_identity._parse_linux_identity(
            b"claude\0-p\0--model\0sonnet\0",
            b"PATH=/private/bin\0CCC_PARENT_SESSION_ID=session-123\0"
            b"CODEX_THREAD_ID=thread-456\0SECRET=do-not-return\0",
        )

        self.assertEqual(identity["argv"], ["claude", "-p", "--model", "sonnet"])
        self.assertEqual(
            identity["env"],
            {"CCC_PARENT_SESSION_ID": "session-123", "CODEX_THREAD_ID": "thread-456"},
        )

    def test_linux_parser_denies_unterminated_records(self):
        self.assertIsNone(process_identity._parse_linux_identity(b"claude\0-p", b""))

    def test_procargs_parser_counts_argv_before_reading_environment(self):
        # The prompt can contain text that looks like an environment variable.
        # It must remain argv, rather than being reclassified from a raw blob.
        record = (
            struct.pack("=i", 3)
            + b"/usr/local/bin/claude\0\0"
            + b"claude\0-p\0CCC_PARENT_SESSION_ID=spoofed prompt\0"
            + b"CCC_PARENT_SESSION_ID=real-session\0SECRET=hidden\0"
        )
        identity = process_identity._parse_procargs2(record)

        self.assertEqual(
            identity["argv"],
            ["claude", "-p", "CCC_PARENT_SESSION_ID=spoofed prompt"],
        )
        self.assertEqual(identity["env"], {"CCC_PARENT_SESSION_ID": "real-session"})

    def test_procargs_parser_denies_short_or_malformed_records(self):
        self.assertIsNone(process_identity._parse_procargs2(b"\x01\x00"))
        self.assertIsNone(
            process_identity._parse_procargs2(struct.pack("=i", 2) + b"/bin/x\0\0one\0")
        )

    def test_current_process_identity_is_safe_to_read(self):
        identity = process_identity.read_launch_identity(os.getpid())

        self.assertIsInstance(identity, dict)
        self.assertIsInstance(identity["argv"], list)
        self.assertLessEqual(set(identity["env"]), {"CCC_PARENT_SESSION_ID", "CODEX_THREAD_ID"})


def test_darwin_rejects_unterminated_environment():
    record = struct.pack('=i', 2) + b'/bin/claude\0\0claude\0-p\0CODEX_THREAD_ID=unterminated'
    assert process_identity._parse_procargs2(record) is None


def test_linux_requires_a_held_exclusive_writer_lock(tmp_path):
    import sys
    import pytest
    if not sys.platform.startswith('linux'):
        pytest.skip('procfs contract')
    import fcntl
    sid = '11111111-1111-4111-8111-111111111111'
    locks = tmp_path / 'thread-writer-locks'
    locks.mkdir()
    with (locks / (sid + '.lock')).open('w') as handle:
        assert process_identity.codex_writer_sessions([os.getpid()], [tmp_path]) == {}
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert process_identity.codex_writer_sessions([os.getpid()], [tmp_path]) == {os.getpid(): {sid}}
        assert process_identity.codex_writer_sessions([os.getpid()], [tmp_path / 'another-home']) == {}
        fcntl.flock(handle, fcntl.LOCK_UN)
        assert process_identity.codex_writer_sessions([os.getpid()], [tmp_path]) == {}


def test_lsof_writer_records_keep_pid_file_and_lock_boundaries(tmp_path):
    sid = '11111111-1111-4111-8111-111111111111'
    locks = tmp_path / 'thread-writer-locks'
    filename = str(locks / (sid + '.lock'))
    output = f'p10\nf3\nlW\nn{filename}\nf4\nn{filename}\np20\nf3\nlR\nn{filename}\n'
    assert process_identity._parse_writer_lsof(output, {10, 20}, {locks}) == {10: {sid}}
    assert process_identity._parse_writer_lsof(output, {20}, {locks}) == {}
    assert process_identity._parse_writer_lsof(output, {10}, {tmp_path}) == {}
