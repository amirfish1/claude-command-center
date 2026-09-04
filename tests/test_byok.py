"""BYOK (bring-your-own-key) profile storage, spawn-env injection, and the
usage/cost ledger: ccc_server/byok.py.

State is redirected to a tempdir per test (never ~/.claude/command-center)
and the macOS Keychain is forced off (`_keychain_available` patched to
False) so these run identically on CI Linux runners and on a real Mac —
covers the stdlib-only encrypted-file fallback path, which is exactly the
code path non-Darwin users actually hit in production.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server  # noqa: F401 -- required so ccc_server.core's _core proxy can
                # resolve COMMAND_CENTER_STATE_DIR at byok import time.
from ccc_server import byok


class BYOKTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        state_dir = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(byok, "_state_dir", return_value=state_dir),
            mock.patch.object(byok, "_keychain_available", return_value=False),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)


class TestProfileStorage(BYOKTestBase):
    def test_set_get_key_round_trip(self):
        self.assertTrue(byok.byok_set_key("work", "openrouter", "sk-or-abc"))
        self.assertEqual(byok.byok_get_key("work", "openrouter"), "sk-or-abc")

    def test_set_key_rejects_missing_fields(self):
        self.assertFalse(byok.byok_set_key("", "openrouter", "sk-or-abc"))
        self.assertFalse(byok.byok_set_key("work", "not-a-provider", "sk-or-abc"))
        self.assertFalse(byok.byok_set_key("work", "openrouter", ""))

    def test_list_profiles_reflects_index_only_no_secrets(self):
        byok.byok_set_key("work", "openrouter", "sk-or-abc")
        byok.byok_set_key("work", "anthropic", "sk-ant-xyz")
        profiles = byok.byok_list_profiles()
        self.assertEqual(profiles, [{"name": "work", "providers": ["anthropic", "openrouter"]}])

    def test_delete_key_removes_one_provider_keeps_profile(self):
        byok.byok_set_key("work", "openrouter", "sk-or-abc")
        byok.byok_set_key("work", "anthropic", "sk-ant-xyz")
        byok.byok_delete_key("work", "openrouter")
        self.assertIsNone(byok.byok_get_key("work", "openrouter"))
        self.assertEqual(byok.byok_get_key("work", "anthropic"), "sk-ant-xyz")
        self.assertEqual(byok.byok_list_profiles(), [{"name": "work", "providers": ["anthropic"]}])

    def test_delete_profile_removes_every_key(self):
        byok.byok_set_key("work", "openrouter", "sk-or-abc")
        byok.byok_set_key("work", "anthropic", "sk-ant-xyz")
        byok.byok_delete_profile("work")
        self.assertEqual(byok.byok_list_profiles(), [])
        self.assertIsNone(byok.byok_get_key("work", "openrouter"))

    def test_env_for_profile_maps_every_configured_provider(self):
        byok.byok_set_key("work", "openrouter", "sk-or-abc")
        byok.byok_set_key("work", "google", "AIza123")
        env = byok.byok_env_for_profile("work")
        self.assertEqual(env["OPENROUTER_API_KEY"], "sk-or-abc")
        self.assertEqual(env["GOOGLE_API_KEY"], "AIza123")
        self.assertEqual(env["GEMINI_API_KEY"], "AIza123")

    def test_env_for_profile_unknown_profile_is_empty(self):
        self.assertEqual(byok.byok_env_for_profile("no-such-profile"), {})


class TestVirtualProviderResolution(BYOKTestBase):
    def test_openrouter_prefix(self):
        self.assertEqual(
            byok.byok_resolve_virtual_provider("openrouter/anthropic/claude-sonnet-5"),
            "openrouter",
        )

    def test_tokenrouter_prefix(self):
        self.assertEqual(
            byok.byok_resolve_virtual_provider("tokenrouter/openai/gpt-5.4"),
            "tokenrouter",
        )

    def test_non_virtual_model_returns_none(self):
        self.assertIsNone(byok.byok_resolve_virtual_provider("claude-sonnet-5"))
        self.assertIsNone(byok.byok_resolve_virtual_provider(""))
        self.assertIsNone(byok.byok_resolve_virtual_provider(None))


class TestSpawnEnvInjection(BYOKTestBase):
    def setUp(self):
        super().setUp()
        byok.byok_set_key("work", "openrouter", "sk-or-abc")
        byok.byok_set_key("default", "openrouter", "sk-or-default")

    def test_no_profile_no_virtual_model_is_empty(self):
        self.assertEqual(byok.byok_spawn_env("opencode", "claude-sonnet-5", None), {})

    def test_direct_env_engine_with_explicit_profile(self):
        env = byok.byok_spawn_env("opencode", "claude-sonnet-5", "work")
        self.assertEqual(env, {"OPENROUTER_API_KEY": "sk-or-abc"})

    def test_non_direct_engine_ignored_even_with_profile(self):
        self.assertEqual(byok.byok_spawn_env("claude", "claude-sonnet-5", "work"), {})

    def test_virtual_model_falls_back_to_default_profile(self):
        env = byok.byok_spawn_env("opencode", "openrouter/anthropic/claude-sonnet-5", None)
        self.assertEqual(env, {"OPENROUTER_API_KEY": "sk-or-default"})

    def test_virtual_model_explicit_profile_wins_over_default(self):
        env = byok.byok_spawn_env("opencode", "openrouter/anthropic/claude-sonnet-5", "work")
        self.assertEqual(env, {"OPENROUTER_API_KEY": "sk-or-abc"})


class TestCostAndUsage(BYOKTestBase):
    def test_estimate_cost_known_model(self):
        cost = byok.byok_estimate_cost("openrouter/anthropic/claude-sonnet-5", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 3.0 + 15.0)

    def test_estimate_cost_unknown_model_is_none(self):
        self.assertIsNone(byok.byok_estimate_cost("claude-sonnet-5", 1000, 1000))

    def test_record_and_summarize_usage(self):
        byok.byok_record_usage(
            engine="opencode", model="openrouter/anthropic/claude-sonnet-5",
            key_profile="work", tokens_in=1_000_000, tokens_out=1_000_000,
        )
        byok.byok_record_usage(
            engine="opencode", model="openrouter/anthropic/claude-sonnet-5",
            key_profile="work", tokens_in=500_000, tokens_out=0,
        )
        byok.byok_record_usage(
            engine="opencode", model="openrouter/moonshotai/kimi-k3",
            key_profile="other", tokens_in=1_000_000, tokens_out=0,
        )
        summary = byok.byok_usage_summary(days=30)
        self.assertEqual(summary["spawns"], 3)
        self.assertAlmostEqual(summary["total_cost_usd"], 3.0 + 15.0 + 1.5 + 0.6)
        self.assertEqual(summary["by_profile"]["work"]["spawns"], 2)
        self.assertEqual(summary["by_profile"]["other"]["spawns"], 1)

    def test_summary_respects_days_cutoff(self):
        with mock.patch.object(byok.time, "time", return_value=1_000_000.0):
            byok.byok_record_usage(engine="opencode", model="openrouter/moonshotai/kimi-k3",
                                    tokens_in=1_000_000, tokens_out=0)
        with mock.patch.object(byok.time, "time", return_value=1_000_000.0 + 40 * 86400):
            summary = byok.byok_usage_summary(days=30)
        self.assertEqual(summary["spawns"], 0)
        self.assertEqual(summary["total_cost_usd"], 0)


class TestStorageBackend(BYOKTestBase):
    def test_backend_is_encrypted_file_when_keychain_unavailable(self):
        self.assertEqual(byok.byok_storage_backend(), "encrypted-file")


if __name__ == "__main__":
    unittest.main()
