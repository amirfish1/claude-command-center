import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import mock_open, patch
from pathlib import Path

import server

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "session-start.py"


class SpawnMarkerCallerTests(unittest.TestCase):
    def test_caller_only_marker_has_no_lane(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps({"caller": "grade-findings.ts", "parent_session_id": "aaaaaaaa-1111"}))
            m = server._decode_spawn_marker_file(p)
        self.assertEqual(m, {"caller": "grade-findings.ts", "parent_session_id": "aaaaaaaa-1111"})

    def test_apply_stamps_caller_and_parent_without_lane(self):
        rows = [{"session_id": "child-0001"}, {"session_id": "kid-00002", "parent_session_id": "keep-me-1"}]
        markers = {
            "child-0001": {"caller": "x.ts", "parent_session_id": "parent-01"},
            "kid-00002": {"caller": "y.ts", "parent_session_id": "other-002"},
        }
        server._apply_spawn_markers(rows, markers)
        self.assertEqual(rows[0]["spawn_caller"], "x.ts")
        self.assertEqual(rows[0]["parent_session_id"], "parent-01")
        self.assertNotIn("spawned_lane", rows[0])
        self.assertEqual(rows[1]["parent_session_id"], "keep-me-1")


class HookLabelTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ccc_session_start_hook", HOOK)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_labels(self):
        L = self.mod._label
        self.assertEqual(L("node /x/node_modules/.bin/tsx scripts/wren-replay/grade-findings.ts --a"), "grade-findings.ts")
        self.assertEqual(L("/bin/zsh -c foo"), "")
        self.assertEqual(L("/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"), "Terminal")
        self.assertEqual(L("/usr/bin/python3 /a/b/run_eval.py"), "run_eval.py")
        self.assertEqual(
            L("/opt/homebrew/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python my-eval-script.py"),
            "my-eval-script.py")

    def test_linux_parent_environment(self):
        env = b"OTHER=value\0CLAUDE_CODE_SESSION_ID=parent-0001\0CCC_CALLER_NAME=worker.sh\0"
        with patch("builtins.open", mock_open(read_data=env)), patch.object(self.mod.subprocess, "run") as ps:
            result = self.mod._claude_env(123)
        self.assertEqual(self.mod.ENV_SID_RE.search(result).group(1), "parent-0001")
        self.assertEqual(self.mod.ENV_CALLER_RE.search(result).group(1), "worker.sh")
        ps.assert_not_called()

    def test_environment_falls_back_without_procfs(self):
        with patch("builtins.open", side_effect=FileNotFoundError), patch.object(self.mod.subprocess, "run") as ps:
            ps.return_value.stdout = "claude CCC_PARENT_SESSION_ID=parent-0002"
            result = self.mod._claude_env(123)
        self.assertEqual(self.mod.ENV_PARENT_RE.search(result).group(1), "parent-0002")

    def test_start_records_linux_parent(self):
        with tempfile.TemporaryDirectory() as d, patch.object(self.mod, "MARKERS_DIR", d), \
                patch.object(self.mod.sys, "stdin", io.StringIO('{"session_id":"child-0001"}')), \
                patch.object(self.mod.os, "getppid", return_value=123), \
                patch.object(self.mod, "_ps_table", return_value={123: (1, "claude -p task")}), \
                patch.object(self.mod, "_claude_env", return_value="CLAUDE_CODE_SESSION_ID=parent-0001 CCC_CALLER_NAME=worker.sh"):
            self.mod.main()
            marker = json.loads((Path(d) / "child-0001.json").read_text())
        self.assertEqual(marker, {"caller": "worker.sh", "parent_session_id": "parent-0001"})

    def test_bad_payload_is_silent(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_start_records_codex_parent_and_preserves_nearest_parent(self):
        for environment, expected in [
            ("CODEX_THREAD_ID=codex-parent-0001", "codex-parent-0001"),
            ("CODEX_THREAD_ID=codex-parent-0001 CLAUDE_CODE_SESSION_ID=claude-parent-0001", "claude-parent-0001"),
            ("CODEX_THREAD_ID=codex-parent-0001 CCC_PARENT_SESSION_ID=explicit-parent-0001", "explicit-parent-0001"),
            ("CODEX_THREAD_ID=child-0001", None),
            ("CODEX_THREAD_ID=bad/id", None),
        ]:
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as d, \
                    patch.object(self.mod, "MARKERS_DIR", d), \
                    patch.object(self.mod.sys, "stdin", io.StringIO('{"session_id":"child-0001"}')), \
                    patch.object(self.mod.os, "getppid", return_value=123), \
                    patch.object(self.mod, "_ps_table", return_value={123: (1, "claude -p task")}), \
                    patch.object(self.mod, "_claude_env", return_value=environment):
                self.mod.main()
                marker_path = Path(d) / "child-0001.json"
                marker = json.loads(marker_path.read_text()) if marker_path.exists() else {}
                self.assertEqual(marker.get("parent_session_id"), expected)

    def test_resume_does_not_reattribute_to_resuming_codex(self):
        with tempfile.TemporaryDirectory() as d, patch.object(self.mod, "MARKERS_DIR", d), \
                patch.object(self.mod.sys, "stdin", io.StringIO('{"session_id":"child-0001"}')), \
                patch.object(self.mod, "_ps_table") as scan, \
                patch.object(self.mod, "_claude_env", return_value="CODEX_THREAD_ID=unrelated-parent"):
            marker_path = Path(d) / "child-0001.json"
            original = {"caller": "helper.py", "parent_session_id": "original-parent"}
            marker_path.write_text(json.dumps(original))
            self.mod.main()
            self.assertEqual(json.loads(marker_path.read_text()), original)
            scan.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "procfs launch contract")
    def test_codex_parent_survives_real_short_lived_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            launcher = Path(d) / "claude"
            launcher.symlink_to(sys.executable)
            hook_code = (
                "import runpy; "
                f"m = runpy.run_path({str(HOOK)!r}); "
                f"m['main'].__globals__['MARKERS_DIR'] = {d!r}; m['main']()"
            )
            launcher_code = (
                "import subprocess, sys; "
                f"subprocess.run([sys.executable, '-c', {hook_code!r}], "
                f"input={json.dumps({'session_id': 'child-0001'})!r}, text=True, check=True)"
            )
            environment = dict(os.environ, CODEX_THREAD_ID="codex-parent-0001")
            for key in ("CCC_PARENT_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CCC_SESSION_RUNTIME"):
                environment.pop(key, None)
            result = subprocess.run([str(launcher), "-c", launcher_code], env=environment,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            marker = json.loads((Path(d) / "child-0001.json").read_text())
            self.assertEqual(marker["parent_session_id"], "codex-parent-0001")


if __name__ == "__main__":
    unittest.main()
