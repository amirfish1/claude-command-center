"""Swallowed best-effort failures must leave exactly one greppable trace."""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ccc_server import errlog  # noqa: E402


class TestErrlog(unittest.TestCase):
    def setUp(self):
        errlog.reset_state()
        self._quiet = os.environ.pop("CCC_QUIET_ERRORS", None)
        self._debug = os.environ.pop("CCC_DEBUG", None)

    def tearDown(self):
        errlog.reset_state()
        for key, value in (("CCC_QUIET_ERRORS", self._quiet), ("CCC_DEBUG", self._debug)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _emit(self, context, exc):
        buf = io.StringIO()
        with redirect_stderr(buf):
            errlog.log_swallowed(context, exc)
        return buf.getvalue()

    def test_logs_context_and_exception(self):
        out = self._emit("persist pins", OSError("disk full"))
        self.assertIn("[ccc:swallowed]", out)
        self.assertIn("persist pins", out)
        self.assertIn("OSError: disk full", out)

    def test_picks_up_active_exception(self):
        buf = io.StringIO()
        try:
            raise ValueError("bad json")
        except ValueError:
            with redirect_stderr(buf):
                errlog.log_swallowed("parse state")
        self.assertIn("ValueError: bad json", buf.getvalue())

    def test_repeats_collapse_into_one_line(self):
        first = self._emit("persist pins", OSError("disk full"))
        self.assertTrue(first)
        for _ in range(5):
            self.assertEqual(self._emit("persist pins", OSError("disk full")), "")

    def test_suppressed_count_rides_along_after_cooldown(self):
        self._emit("persist pins", OSError("disk full"))
        for _ in range(3):
            self._emit("persist pins", OSError("disk full"))
        errlog._SEEN[("persist pins", "OSError")][0] -= errlog.COOLDOWN_S + 1
        out = self._emit("persist pins", OSError("disk full"))
        self.assertIn("+3 suppressed", out)

    def test_distinct_contexts_and_types_are_not_collapsed(self):
        self.assertTrue(self._emit("persist pins", OSError("x")))
        self.assertTrue(self._emit("persist lanes", OSError("x")))
        self.assertTrue(self._emit("persist pins", ValueError("x")))

    def test_quiet_env_mutes(self):
        os.environ["CCC_QUIET_ERRORS"] = "1"
        self.assertEqual(self._emit("persist pins", OSError("x")), "")

    def test_swallow_logs_expected_and_reraises_the_rest(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with errlog.swallow("write cache", OSError):
                raise OSError("read-only fs")
        self.assertIn("write cache", buf.getvalue())

        with self.assertRaises(ValueError):
            with errlog.swallow("write cache", OSError):
                raise ValueError("not expected here")


if __name__ == "__main__":
    unittest.main()
