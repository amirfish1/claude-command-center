"""The dashboard shortens the interpreter switch interval at startup.

With Python's 5 ms default, a request thread reading a database row by row
waits a whole interval per row whenever another thread is computing, which
made every open page wait seconds behind background rebuilds.
"""
import os
import sys
import unittest
from unittest import mock

import server


class ThreadSwitchIntervalTest(unittest.TestCase):
    def setUp(self):
        self._saved = sys.getswitchinterval()

    def tearDown(self):
        sys.setswitchinterval(self._saved)

    def _tune(self, value=None):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCC_THREAD_SWITCH_MS", None)
            if value is not None:
                os.environ["CCC_THREAD_SWITCH_MS"] = value
            return server._tune_thread_switch_interval()

    def test_default_is_half_a_millisecond(self):
        self.assertAlmostEqual(self._tune(), 0.0005)
        self.assertAlmostEqual(sys.getswitchinterval(), 0.0005)

    def test_override_in_milliseconds(self):
        self.assertAlmostEqual(self._tune("2"), 0.002)

    def test_zero_keeps_the_python_default(self):
        sys.setswitchinterval(0.005)
        self.assertAlmostEqual(self._tune("0"), 0.005)

    def test_garbage_falls_back_and_extremes_are_clamped(self):
        self.assertAlmostEqual(self._tune("fast"), 0.0005)
        self.assertAlmostEqual(self._tune("0.000001"), 0.00005)
        self.assertAlmostEqual(self._tune("99999"), 0.05)


if __name__ == "__main__":
    unittest.main()
