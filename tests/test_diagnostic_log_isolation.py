"""Check import-time log routing without importing server or touching live state."""

import ast
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _spawn_child_state_paths(result_queue):
    """Spawn-child target: re-imports server in a fresh interpreter and
    reports where its mutable state paths resolved."""
    import server as child_server

    result_queue.put(
        {
            "pid": os.getpid(),
            "pending": str(child_server.PENDING_INPUTS_FILE),
            "budget": str(child_server.INJECT_BUDGET_FILE),
            "activity": str(child_server.ACTIVITY_LOG_FILE),
        }
    )


class TestDiagnosticLogIsolation(unittest.TestCase):
    def test_test_runners_write_diagnostics_outside_runtime_state(self):
        source = REPO_ROOT / "server.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        # ACTIVITY_LOG_FILE moved to ccc_server/activity_log.py (slice 4);
        # scan its top-level declarations alongside server.py's.
        tree.body += ast.parse(
            (REPO_ROOT / "ccc_server" / "activity_log.py").read_text(encoding="utf-8")
        ).body
        names = {
            "ACTIVITY_LOG_FILE",
            "_RESUME_LEDGER_FILE",
            "_RESUME_LEDGER_BACKUP",
            "PENDING_INPUTS_FILE",
            "PENDING_INPUT_HANDOFF_DIR",
            "_ARCHIVE_RESPONSE_CACHE_FILE",
        }
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
                "PENDING_INPUTS_FILE": "ccc-test-pending-inputs-4242.json",
                "PENDING_INPUT_HANDOFF_DIR": "ccc-test-pending-handoffs-4242",
                "_ARCHIVE_RESPONSE_CACHE_FILE": "ccc-test-archive-cache-4242.json",
            }
            for isolated in (True, False):
                with self.subTest(isolated=isolated):
                    namespace = {
                        "Path": Path,
                        "test_isolation_active": lambda: isolated,
                        "tempfile": SimpleNamespace(gettempdir=lambda: str(root / "tests")),
                        "os": SimpleNamespace(getpid=lambda: 4242),
                        "COMMAND_CENTER_STATE_DIR": runtime_state,
                    }
                    exec(code, namespace)
                    for name in names:
                        destination = namespace[name]
                        if isolated:
                            self.assertEqual(destination, root / "tests" / expected_names[name])
                        else:
                            self.assertTrue(destination.is_relative_to(runtime_state))

    def test_isolation_marker_env_redirects_fresh_interpreters(self):
        # A `python -c "import server"` subprocess has neither pytest nor
        # unittest in sys.modules — the same position multiprocessing
        # "spawn" children are in. The CCC_TEST_ISOLATION marker the parent
        # test process stamped is the only signal that survives (CCC-1165).
        script = (
            "import json, server; print(json.dumps({"
            "'pending': str(server.PENDING_INPUTS_FILE), "
            "'budget': str(server.INJECT_BUDGET_FILE), "
            "'activity': str(server.ACTIVITY_LOG_FILE)}))"
        )
        env = dict(os.environ)
        env.pop("CCC_TEST_ISOLATION", None)
        env["PYTHONPATH"] = (
            str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        )

        plain = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(plain.returncode, 0, plain.stderr[-2000:])
        real_state = str(Path.home() / ".claude" / "command-center")
        plain_paths = json.loads(plain.stdout.strip().splitlines()[-1])
        self.assertEqual(
            plain_paths["pending"], real_state + "/pending-inputs.json"
        )
        self.assertEqual(
            plain_paths["activity"], real_state + "/logs/activity.log"
        )

        env["CCC_TEST_ISOLATION"] = "1"
        marked = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(marked.returncode, 0, marked.stderr[-2000:])
        marked_paths = json.loads(marked.stdout.strip().splitlines()[-1])
        for key in ("pending", "budget", "activity"):
            self.assertTrue(
                marked_paths[key].startswith(tempfile.gettempdir()),
                f"{key} did not resolve under the temp dir: {marked_paths[key]}",
            )
            self.assertIn("ccc-test-", Path(marked_paths[key]).name)

    def test_multiprocessing_spawn_child_is_isolated(self):
        # The exact CCC-1165 vector: a multiprocessing "spawn" child re-imports
        # server with no test runner in sys.modules. It must still resolve
        # test-isolated paths via the inherited CCC_TEST_ISOLATION marker.
        import server  # noqa: F401 — importing stamps the env marker

        self.assertEqual(os.environ.get("CCC_TEST_ISOLATION"), "1")
        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue()
        proc = ctx.Process(target=_spawn_child_state_paths, args=(result_queue,))
        proc.start()
        proc.join(60)
        try:
            self.assertEqual(proc.exitcode, 0)
            child = result_queue.get(timeout=10)
        finally:
            if proc.is_alive():
                proc.terminate()
        self.assertNotEqual(child["pid"], os.getpid())
        for key in ("pending", "budget", "activity"):
            self.assertTrue(
                child[key].startswith(tempfile.gettempdir()),
                f"{key} did not resolve under the temp dir: {child[key]}",
            )
            self.assertIn("ccc-test-", Path(child[key]).name)


if __name__ == "__main__":
    unittest.main()
