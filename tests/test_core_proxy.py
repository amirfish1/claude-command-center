"""Regression tests for ccc_server.core (_CoreProxy) fallback resolution.

The smoke suite pops and re-imports the ``server`` module per test. During
the fresh import there is a wide window where names owned by ccc_server.*
modules are not yet adopted onto server (``_adopt_ccc_module`` runs ~20k
lines into server.py). Background threads from the previous server instance
(queue pumps, rollout watchers) that touch ``_core.X`` in that window used
to die on AttributeError: module 'server' has no attribute
'_pending_resume_lock' / '_ACP_TERMINALS_LOCK' / '_codex_rollout_stat'
(OPS-855). The proxy now falls back to the already-imported submodule that
owns the name.
"""

import sys
import unittest

import server  # noqa: F401  baseline: server imported once for the suite
from ccc_server import acp, core, pending_inputs


class TestCoreProxyFallback(unittest.TestCase):

    def test_fallback_to_submodule_when_server_popped(self):
        saved = sys.modules.pop("server", None)
        try:
            self.assertIs(
                core._pending_resume_lock, pending_inputs._pending_resume_lock
            )
        finally:
            if saved is not None:
                sys.modules["server"] = saved

    def test_fallback_when_server_lacks_name_mid_import(self):
        # Simulate the mid-import window: server exists but the adoption has
        # not rebound the name yet.
        saved = server._ACP_TERMINALS_LOCK
        delattr(server, "_ACP_TERMINALS_LOCK")
        try:
            self.assertIs(core._ACP_TERMINALS_LOCK, acp._ACP_TERMINALS_LOCK)
        finally:
            server._ACP_TERMINALS_LOCK = saved

    def test_server_value_wins_when_present(self):
        # Monkeypatched/adopted attributes on server keep precedence.
        self.assertIs(core._pending_resume_lock, server._pending_resume_lock)

    def test_missing_name_still_raises(self):
        with self.assertRaises(AttributeError):
            core._definitely_not_a_real_name_xyz_123


if __name__ == "__main__":
    unittest.main()
