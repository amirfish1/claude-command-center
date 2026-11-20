# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""AGY spawn identity must survive a log that outgrew the 64 KB tail window.

`Created conversation <uuid>`, the workspace line and the model label are all
written in the first ~2 KB of an AGY CLI log. A chatty run pushes them past the
tail read, and the row then resolves with no session_id — the spawn card spins
forever ("Gemini 3.8 Flash is not spawning", 2026-09-02).
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


HEAD = (
    'I0902 16:24:55.686016 1 server.go:289] Creating CLI server backend\n'
    'I0902 16:24:55.689758 1 manager.go:401] Initializing CLI store manager '
    'for workspace /Users/dev/Apps/Acme\n'
    'I0902 16:24:56.985617 1 model_config_manager.go:311] Propagating selected '
    'model override to backend: label="Gemini 3.8 Flash (High)"\n'
    'I0902 16:24:58.882654 1 server.go:1142] Created conversation '
    '3443e3e2-744d-41a0-bc10-574764139191\n'
)


def test_late_native_id_preserves_startup_marks(tmp_path, monkeypatch):
    import pytest
    from ccc_server import engines

    sid = "3443e3e2-744d-41a0-bc10-574764139191"
    log = tmp_path / "spawn-antigravity-x.log"
    debug = log.with_suffix(".log.agy.log")
    native_store = tmp_path / "native.db"
    request_epoch = time.time() - 90
    entry = {
        "engine": "antigravity",
        "log": str(log),
        "_antigravity_spawn_timing": (
            request_epoch, (request_epoch - 0.25) * 1000,
            request_epoch + 0.15, request_epoch + 0.9,
        ),
    }
    monkeypatch.setattr(server, "_SPAWN_TIMELINE", {})
    monkeypatch.setattr(server, "_SPAWN_TIMELINE_FILE", tmp_path / "timeline.json")
    monkeypatch.setattr(server, "_SPAWN_TIMELINE_FILE_SIG", None)
    monkeypatch.setattr(server, "_is_antigravity_session", lambda _: native_store.exists())
    monkeypatch.setattr(server, "_update_spawn_session_id_in_registry", lambda *args: None)
    monkeypatch.setattr(engines._antigravity_accounts, "finalize_pending", lambda *args: None)
    assert server._antigravity_spawn_session_status(entry) == {"session_id": None, "landed": False}

    debug.write_text(HEAD)
    assert server._antigravity_spawn_session_status(entry) == {"session_id": sid, "landed": False}
    native_store.touch()
    assert server._antigravity_spawn_session_status(entry) == {"session_id": sid, "landed": True}
    timeline = server._spawn_timeline_get(sid)
    assert timeline["engine"] == "antigravity"
    assert timeline["marks"]["process_started"] == pytest.approx(400, abs=0.1)
    assert timeline["marks"]["spawn_response_sent"] == pytest.approx(1150, abs=0.1)
    assert timeline["marks"]["antigravity_id_observed"] >= 90_000
    assert server._spawn_timeline_mark(sid, "client_first_visible_activity", 91_000)
    server._antigravity_spawn_session_status(entry)
    assert server._spawn_timeline_get(sid)["marks"]["client_first_visible_activity"] == 91_000


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
            self.assertEqual(meta["cwd"], "/Users/dev/Apps/Acme")
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
            self.assertEqual(meta["cwd"], "/Users/dev/Apps/Acme")
            self.assertEqual(meta["model"], "Gemini 3.8 Flash (High)")


if __name__ == "__main__":
    unittest.main()
