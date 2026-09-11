import json
from pathlib import Path
import tempfile
import unittest

import server


class TestRepoUsageSignals(unittest.TestCase):
    def test_missing_projects_root_returns_json_safe_zero_counts(self):
        original_root = server.PROJECTS_ROOT
        original_cache = dict(server._REPO_SIGNALS_CACHE)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "demo-repo"
                repo.mkdir()
                server.PROJECTS_ROOT = Path(tmp) / "missing-projects"
                server._REPO_SIGNALS_CACHE.update({"paths": (), "data": None, "ts": 0.0})

                result = server._compute_repo_usage_signals([str(repo)])

            json.dumps(result)
            windows = result[str(repo)]["signals"]
            self.assertEqual(windows["d7"]["sessions"], 0)
            self.assertEqual(windows["d30"]["sessions"], 0)
            self.assertEqual(windows["all"]["sessions"], 0)
        finally:
            server.PROJECTS_ROOT = original_root
            server._REPO_SIGNALS_CACHE.clear()
            server._REPO_SIGNALS_CACHE.update(original_cache)


if __name__ == "__main__":
    unittest.main()
