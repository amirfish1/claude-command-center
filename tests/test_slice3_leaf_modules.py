# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Targeted unit tests for leaf modules ccc_server.paths and ccc_server.textutil (Slice 3)."""

from pathlib import Path
import subprocess
import sys
import unittest

import server
from ccc_server import core, paths, textutil


class TestSlice3LeafModules(unittest.TestCase):
    def test_paths_module_imports_without_server_or_core(self):
        code = (
            "import sys; "
            "import ccc_server.paths; "
            "assert 'server' not in sys.modules, 'server was imported'; "
            "assert 'ccc_server.core' not in sys.modules, 'ccc_server.core was imported'"
        )
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_textutil_module_imports_without_server_or_core(self):
        code = (
            "import sys; "
            "import ccc_server.textutil; "
            "assert 'server' not in sys.modules, 'server was imported'; "
            "assert 'ccc_server.core' not in sys.modules, 'ccc_server.core was imported'"
        )
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_server_reexports_all_moved_symbols(self):
        symbols = [
            "COMMAND_CENTER_STATE_DIR",
            "COMMAND_CENTER_PASTED_IMAGES_DIR",
            "COMMAND_CENTER_ATTACHMENTS_DIR",
            "PYTHON_STACK_DUMP_LOG",
            "LOG_VIEWER_STATE_DIR",
            "PINNED_CONVERSATIONS_FILE",
            "SPAWN_DEFAULTS_FILE",
            "SPAWNED_PIDS_FILE",
            "USAGE_LIMIT_RESUME_FILE",
            "CODEX_THREAD_REGISTRY_FILE",
            "SESSION_OVERRIDES_FILE",
            "CODEX_GOALS_DB_CANDIDATES",
            "CODEX_SESSIONS_ROOT",
            "CODEX_APP_SERVER_STATE_FILE",
            "CODEX_TELEMETRY_FILE",
            "_SPAWN_TIMELINE_FILE",
            "_which",
            "_iter_common_cli_candidates",
            "_path_is_within",
            "_resolve_sys_tool",
            "_SYS_PS",
            "_SYS_LSOF",
            "_SYS_SYSCTL",
            "_SYS_VM_STAT",
            "_SYS_OSASCRIPT",
            "_LONE_SURROGATE_RE",
            "_strip_lone_surrogates",
            "_strip_spawn_payload_surrogates",
            "_slugify",
            "_QUEUE_LABEL_RESERVED",
            "_validate_queue_label",
            "_validate_auto_compact_k",
        ]
        for sym in symbols:
            self.assertTrue(hasattr(server, sym), f"server missing symbol: {sym}")

    def test_slugify(self):
        self.assertEqual(textutil._slugify("Hello, World!"), "hello-world")
        self.assertEqual(textutil._slugify("---abc---"), "abc")
        self.assertEqual(textutil._slugify("a" * 60, max_len=10), "a" * 10)
        self.assertEqual(server._slugify("Hello, World!"), "hello-world")

    def test_strip_lone_surrogates(self):
        lone_high = chr(0xD800)
        lone_low = chr(0xDC00)
        astral = "😀"  # U+1F600
        self.assertEqual(textutil._strip_lone_surrogates(f"foo{lone_high}bar"), "foobar")
        self.assertEqual(textutil._strip_lone_surrogates(f"foo{lone_low}bar"), "foobar")
        self.assertEqual(textutil._strip_lone_surrogates(f"foo{astral}bar"), f"foo{astral}bar")
        self.assertEqual(server._strip_lone_surrogates(f"foo{lone_high}bar"), "foobar")

    def test_strip_spawn_payload_surrogates(self):
        lone_high = chr(0xD800)
        payload = {
            "name": f"task-{lone_high}",
            "nested": [f"item-{lone_high}", {"deep": f"val-{lone_high}"}],
            "int_val": 42,
        }
        cleaned = textutil._strip_spawn_payload_surrogates(payload)
        self.assertEqual(cleaned["name"], "task-")
        self.assertEqual(cleaned["nested"][0], "item-")
        self.assertEqual(cleaned["nested"][1]["deep"], "val-")
        self.assertEqual(cleaned["int_val"], 42)

    def test_validate_auto_compact_k(self):
        self.assertEqual(textutil._validate_auto_compact_k(250), 250)
        self.assertEqual(textutil._validate_auto_compact_k("100"), 100)
        self.assertEqual(textutil._validate_auto_compact_k("invalid", default=250), 250)
        self.assertEqual(textutil._validate_auto_compact_k(10), 50)  # clamped min 50
        self.assertEqual(textutil._validate_auto_compact_k(2000), 1000)  # clamped max 1000
        self.assertEqual(server._validate_auto_compact_k(300), 300)

    def test_validate_queue_label(self):
        # Valid label
        textutil._validate_queue_label("valid-label")
        server._validate_queue_label("valid-label")

        # Invalid labels
        with self.assertRaises(ValueError):
            textutil._validate_queue_label("has,comma")
        with self.assertRaises(ValueError):
            textutil._validate_queue_label("has\nnewline")
        with self.assertRaises(ValueError):
            textutil._validate_queue_label("x" * 51)
        with self.assertRaises(ValueError):
            textutil._validate_queue_label("watchtower:in-progress")

    def test_path_is_within(self):
        home = Path.home()
        self.assertTrue(paths._path_is_within(home / "foo" / "bar", home))
        self.assertTrue(paths._path_is_within(home, home))
        self.assertFalse(paths._path_is_within(home, home / "foo"))
        self.assertTrue(server._path_is_within(home / "foo", home))

    def test_iter_common_cli_candidates(self):
        cands = list(paths._iter_common_cli_candidates("python3"))
        self.assertIsInstance(cands, list)
        self.assertTrue(any("bin" in str(p) for p in cands))

    def test_core_proxy_fallback_when_server_popped(self):
        saved = sys.modules.pop("server", None)
        try:
            self.assertEqual(core._slugify("test"), "test")
            self.assertEqual(core._validate_auto_compact_k(250), 250)
            self.assertEqual(core.COMMAND_CENTER_STATE_DIR, paths.COMMAND_CENTER_STATE_DIR)
        finally:
            if saved is not None:
                sys.modules["server"] = saved


if __name__ == "__main__":
    unittest.main()
