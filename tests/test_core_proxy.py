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
import types

from ccc_server import acp, codex, core, pending_inputs
import ccc_server


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

    def test_codex_atexit_shutdown_ignores_unloaded_server_state(self):
        """The atexit cleanup must not warn after test teardown removes server."""
        saved = sys.modules.pop("server", None)
        try:
            codex._codex_app_server_shutdown()
        finally:
            if saved is not None:
                sys.modules["server"] = saved


class TestCoreProxyRegistry(unittest.TestCase):
    """Slice 1: registry-backed resolution for a server-less process."""

    def setUp(self):
        self._saved_server = sys.modules.pop("server", None)
        self._saved_reg = dict(ccc_server._registry)
        self.fake = types.ModuleType("ccc_server._fake_owner")
        self.fake._fake_value = 1
        ccc_server.register(self.fake)

    def tearDown(self):
        ccc_server._registry.clear()
        ccc_server._registry.update(self._saved_reg)
        if self._saved_server is not None:
            sys.modules["server"] = self._saved_server

    def test_read_resolves_via_registry_without_scan(self):
        # Not in sys.modules, so only the registry can find it.
        self.assertNotIn("ccc_server._fake_owner", sys.modules)
        self.assertEqual(core._fake_value, 1)

    def test_lookup_is_live_not_snapshot(self):
        self.fake._fake_value = 2
        self.assertEqual(core._fake_value, 2)

    def test_write_routes_to_owner_when_server_absent(self):
        core._fake_value = 7
        self.assertEqual(self.fake._fake_value, 7)

    def test_write_unknown_name_raises_without_server(self):
        with self.assertRaises(AttributeError):
            core._never_registered_xyz = 1

    def test_later_registration_wins(self):
        other = types.ModuleType("ccc_server._fake_owner2")
        other._fake_value = 99
        ccc_server.register(other)
        self.assertEqual(core._fake_value, 99)

    def test_registry_hit_skips_module_scan(self):
        class _Boom(dict):
            def items(self):
                raise AssertionError("scanned sys.modules on a registry hit")

        real = ccc_server._sys.modules
        try:
            ccc_server._sys.modules = _Boom(real)
            self.assertEqual(core._fake_value, 1)
        finally:
            ccc_server._sys.modules = real

    def test_server_adoption_populates_registry(self):
        self.assertIs(ccc_server._registry["_pending_resume_lock"], pending_inputs)


if __name__ == "__main__":
    unittest.main()
