"""Model policy: a machine-wide deny-list every spawn path honors.

Regression for 2026-09-05, when a 2.5x-priced Codex model was the default in
the engine CLI config, CCC's spawn defaults, and a queue's pinned model at the
same time and drained a weekly allowance in about thirty minutes. The policy
file is the single choke point: pickers hide a blocked model, explicit
requests are rejected, and inherited defaults fall back to an allowed model.
"""

import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import server


class _PolicyFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self.policy = root / "model-policy.json"
        self.defaults = root / "spawn-defaults.json"
        self._patches = [
            patch.object(server, "MODEL_POLICY_FILE", self.policy),
            patch.object(server, "SPAWN_DEFAULTS_FILE", self.defaults),
            patch.dict(os.environ, {}, clear=False),
        ]
        for p in self._patches:
            p.start()
        os.environ.pop("CCC_BLOCKED_MODELS", None)
        os.environ.pop("CCC_CODEX_MODEL", None)
        server._MODEL_POLICY_CACHE["sig"] = None

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        server._MODEL_POLICY_CACHE["sig"] = None
        self._tmp.cleanup()

    def block(self, *models):
        self.policy.write_text(json.dumps({"blocked_models": list(models)}))

    def write_defaults(self, **models):
        base = server._factory_spawn_defaults()
        base["models"].update(models)
        self.defaults.write_text(json.dumps(base))


class TestModelPolicy(_PolicyFixture):
    def test_no_policy_file_blocks_nothing(self):
        self.assertFalse(server._model_policy_blocks("gpt-6-astra"))
        self.assertEqual(server._validate_codex_model("gpt-6-astra"), ("gpt-6-astra", None))

    def test_policy_file_blocks_explicit_codex_request(self):
        self.block("gpt-6-astra")
        model, err = server._validate_codex_model("GPT-6-Astra")
        self.assertEqual(model, "GPT-6-Astra")
        self.assertIn("blocked by model policy", err)
        self.assertIn("model-policy.json", err)

    def test_env_var_unions_with_file(self):
        self.block("gpt-6-astra")
        os.environ["CCC_BLOCKED_MODELS"] = "gpt-5.5, Claude-Opus-5"
        self.assertTrue(server._model_policy_blocks("gpt-5.5"))
        self.assertTrue(server._model_policy_blocks("claude-opus-5"))
        self.assertTrue(server._model_policy_blocks("gpt-6-astra"))
        self.assertFalse(server._model_policy_blocks("gpt-5.6-sol"))

    def test_policy_file_edit_is_picked_up_without_restart(self):
        self.block("gpt-6-astra")
        self.assertTrue(server._model_policy_blocks("gpt-6-astra"))
        self.policy.write_text(json.dumps({"blocked_models": []}))
        os.utime(self.policy, ns=(1, 1))  # force a distinct mtime signature
        self.assertFalse(server._model_policy_blocks("gpt-6-astra"))

    def test_catalog_hides_blocked_model(self):
        self.block("gpt-6-astra")
        self.assertFalse(server._model_catalog_allows_model("codex", "gpt-6-astra"))
        self.assertTrue(server._model_catalog_allows_model("codex", "gpt-5.6-sol"))

    def test_codex_default_falls_back_when_blocked(self):
        self.assertEqual(server._codex_default_model(), "gpt-6-astra")
        self.block("gpt-6-astra")
        fallback = server._codex_default_model()
        self.assertNotEqual(fallback, "gpt-6-astra")
        self.assertEqual(fallback, "gpt-5.5")
        os.environ["CCC_CODEX_MODEL"] = "gpt-6-astra"
        server._MODEL_POLICY_CACHE["sig"] = None
        self.assertEqual(server._codex_default_model(), "gpt-5.5")

    def test_inherited_spawn_default_is_substituted_not_rejected(self):
        self.write_defaults(codex="gpt-6-astra")
        self.assertEqual(server._spawn_model_for_engine("codex"), "gpt-6-astra")
        self.block("gpt-6-astra")
        self.assertEqual(server._spawn_model_for_engine("codex"), "gpt-5.5")
        engine, model = server._spawn_request_engine_and_model({"engine": "codex"})
        self.assertEqual((engine, model), ("codex", "gpt-5.5"))
        # An explicit ask still surfaces the blocked model so the validator
        # can reject it with the policy error.
        self.assertEqual(server._spawn_model_for_engine("codex", "gpt-6-astra"), "gpt-6-astra")

    def test_policy_error_is_engine_agnostic(self):
        self.block("claude-opus-5")
        self.assertIn("blocked", server._model_policy_error("claude-opus-5"))
        self.assertIsNone(server._model_policy_error("claude-sonnet-5"))


if __name__ == "__main__":
    unittest.main()
