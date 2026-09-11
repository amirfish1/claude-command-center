# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""AGY spawn identity must survive a log that outgrew the 64 KB tail window.

`Created conversation <uuid>`, the workspace line and the model label are all
written in the first ~2 KB of an AGY CLI log. A chatty run pushes them past the
tail read, and the row then resolves with no session_id — the spawn card spins
forever ("Gemini 3.8 Flash is not spawning", 2026-09-02).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


HEAD = (
    'I0902 16:24:55.686016 1 server.go:289] Creating CLI server backend\n'
    'I0902 16:24:55.689758 1 manager.go:401] Initializing CLI store manager '
    'for workspace /Users/amirfish/Apps/BYM\n'
    'I0902 16:24:56.985617 1 model_config_manager.go:311] Propagating selected '
    'model override to backend: label="Gemini 3.8 Flash (High)"\n'
    'I0902 16:24:58.882654 1 server.go:1142] Created conversation '
    '3443e3e2-744d-41a0-bc10-574764139191\n'
)


class AntigravityLogMetaHeadTest(unittest.TestCase):
    def setUp(self):
        server._antigravity_cli_log_meta_cache.clear()

    def _write(self, tmpdir, filler_bytes):
        path = Path(tmpdir) / "spawn-antigravity-x.log.agy.log"
        path.write_text(HEAD + ("I0902 noisy tool output line\n" * filler_bytes))
        return path

    def test_meta_found_when_header_is_inside_tail_window(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 10)
            meta = server._antigravity_cli_log_meta(str(path))
            self.assertEqual(meta["session_id"], "3443e3e2-744d-41a0-bc10-574764139191")
            self.assertEqual(meta["cwd"], "/Users/amirfish/Apps/BYM")
            self.assertEqual(meta["model"], "Gemini 3.8 Flash (High)")

    def test_meta_found_when_header_scrolled_out_of_tail_window(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 8000)  # ~230 KB, well past the 64 KB tail
            self.assertGreater(path.stat().st_size, 200_000)
            self.assertNotIn(
                "Created conversation",
                server._antigravity_read_log_tail(str(path)),
            )
            meta = server._antigravity_cli_log_meta(str(path))
            self.assertEqual(meta["session_id"], "3443e3e2-744d-41a0-bc10-574764139191")
            self.assertEqual(meta["cwd"], "/Users/amirfish/Apps/BYM")
            self.assertEqual(meta["model"], "Gemini 3.8 Flash (High)")


if __name__ == "__main__":
    unittest.main()
