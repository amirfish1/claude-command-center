"""ccc doctor's code-freshness check: does the running server's build stamp
(/api/version's code_rev, frozen at that process's start) match disk HEAD.

A committed fix that never reaches a restarted process is invisible from the
CLI today -- `ccc doctor` only reported per-engine CLI/auth health, nothing
about whether the server itself is running current code. These cover the
decision logic in isolation (no live server, no git checkout needed).
"""

import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest import mock


def _load_ccc_cli():
    ccc_path = Path(__file__).resolve().parent.parent / "ccc"
    loader = importlib.machinery.SourceFileLoader("ccc_cli_freshness", str(ccc_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CodeFreshnessVerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ccc = _load_ccc_cli()

    def test_match_when_revs_are_equal(self):
        self.assertEqual(
            self.ccc._code_freshness_verdict("abc123", "abc123"), "match",
        )

    def test_stale_when_revs_differ(self):
        self.assertEqual(
            self.ccc._code_freshness_verdict("abc123", "def456"), "stale",
        )

    def test_unknown_when_running_rev_missing(self):
        """A server predating the code_rev field, or a non-git install."""
        self.assertEqual(self.ccc._code_freshness_verdict("", "def456"), "unknown")

    def test_unknown_when_local_rev_missing(self):
        """The ccc CLI itself isn't a git checkout (zip/Homebrew keg)."""
        self.assertEqual(self.ccc._code_freshness_verdict("abc123", ""), "unknown")

    def test_unknown_when_both_missing(self):
        self.assertEqual(self.ccc._code_freshness_verdict("", ""), "unknown")


class DoctorReportsStaleCodeTests(unittest.TestCase):
    """cmd_doctor must surface a stale server as an actionable, non-zero
    exit -- not just print it and return 0 like a healthy run."""

    @classmethod
    def setUpClass(cls):
        cls.ccc = _load_ccc_cli()

    def _run_doctor(self, running_rev, local_rev, engine_arg=None):
        ccc = self.ccc

        class Args:
            server = "http://127.0.0.1:8099"
            engine = engine_arg
            json = False

        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
             mock.patch.object(ccc, "_get_json") as get_json, \
             mock.patch.object(ccc, "_local_head_rev", return_value=local_rev or None):
            def fake_get_json(base, path, timeout=10):
                if path == "/api/engines/doctor":
                    return {"engines": {"claude": {"cli_present": True, "auth_present": True}}}
                if path == "/api/version":
                    return {"code_rev": running_rev}
                raise AssertionError(f"unexpected path {path!r}")
            get_json.side_effect = fake_get_json
            return ccc.cmd_doctor(Args())

    def test_matching_code_exits_zero(self):
        rc = self._run_doctor("abc123", "abc123")
        self.assertEqual(rc, 0)

    def test_stale_code_exits_nonzero_even_without_engine_filter(self):
        rc = self._run_doctor("abc123", "def456")
        self.assertEqual(rc, 1)

    def test_unknown_freshness_does_not_fail_the_run(self):
        """Not being a git install is not itself unhealthy."""
        rc = self._run_doctor("", "")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
