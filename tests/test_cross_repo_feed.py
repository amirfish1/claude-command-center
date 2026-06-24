import importlib
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class CrossRepoFeedTests(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.server._CROSS_REPO_ISSUES_CACHE.clear()
        self.server._OPEN_PRS_CACHE.clear()

    def test_cross_repo_feeds_ignore_recent_repos_not_in_known_repos(self):
        """Recent repos only order the picker; they must not expand cross-repo feeds."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            known = root / "known"
            stale_recent = root / "stale-recent"
            for repo in (known, stale_recent):
                repo.mkdir()
                (repo / ".git").mkdir()

            def fake_run(cmd, **kwargs):
                cwd = pathlib.Path(kwargs["cwd"])
                if cmd[1:3] == ["issue", "list"]:
                    return self.server.subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
                if cmd[1:3] == ["pr", "list"]:
                    return self.server.subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
                raise AssertionError(f"unexpected command: {cmd}")

            with mock.patch.object(
                self.server, "_load_recent_repos", return_value=[str(stale_recent), str(known)]
            ), mock.patch.object(
                self.server, "_load_custom_repos", return_value=[str(known)]
            ), mock.patch.object(
                self.server, "load_known_repos", return_value=[{"path": str(known), "label": "known"}]
            ), mock.patch.object(
                self.server.subprocess, "run", side_effect=fake_run
            ) as run:
                self.server.fetch_cross_repo_issues()
                self.server.fetch_cross_repo_prs()

            cwd_calls = [pathlib.Path(call.kwargs["cwd"]) for call in run.call_args_list]
            self.assertTrue(cwd_calls)
            self.assertEqual(set(cwd_calls), {known})


if __name__ == "__main__":
    unittest.main()
