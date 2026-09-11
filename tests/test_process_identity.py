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
