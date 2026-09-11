"""Check import-time log routing without importing server or touching live state."""

import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


class TestDiagnosticLogIsolation(unittest.TestCase):
    def test_test_runners_write_diagnostics_outside_runtime_state(self):
        source = Path(__file__).resolve().parents[1] / "server.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {"ACTIVITY_LOG_FILE", "_RESUME_LEDGER_FILE", "_RESUME_LEDGER_BACKUP"}
        # Execute only the real path declarations, never server imports,
        # startup hooks, session scans, or delivery code.
        declarations = [
            node for node in tree.body
            if isinstance(node, ast.If) and any(
                isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                and child.id in names for child in ast.walk(node)
            )
        ]
        code = compile(ast.Module(body=declarations, type_ignores=[]), str(source), "exec")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_state = root / "runtime"
            expected_names = {
                "ACTIVITY_LOG_FILE": "ccc-test-activity.log",
                "_RESUME_LEDGER_FILE": "ccc-test-resume-ledger.jsonl",
                "_RESUME_LEDGER_BACKUP": "ccc-test-resume-ledger.1.jsonl",
            }
            for runner in ("pytest", "unittest", None):
                with self.subTest(runner=runner):
                    namespace = {
                        "Path": Path,
                        "sys": SimpleNamespace(modules={runner: object()} if runner else {}),
                        "tempfile": SimpleNamespace(gettempdir=lambda: str(root / "tests")),
                        "COMMAND_CENTER_STATE_DIR": runtime_state,
                    }
                    exec(code, namespace)
                    for name in names:
                        destination = namespace[name]
                        if runner:
                            self.assertEqual(destination, root / "tests" / expected_names[name])
                        else:
                            self.assertTrue(destination.is_relative_to(runtime_state))


if __name__ == "__main__":
    unittest.main()
