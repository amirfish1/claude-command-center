"""Model policy: a machine-wide deny-list every spawn path honors.

Regression for 2026-09-05, when a 2.5x-priced Codex model was the default in
the engine CLI config, CCC's spawn defaults, and a queue's pinned model at the
same time and drained a weekly allowance in about thirty minutes. The policy
file is the single choke point: explicit requests are rejected, and inherited
defaults fall back to an allowed model.

2026-09-06: blocked models stopped being hidden from pickers -- they stay
listed (tagged policy_blocked) so a deliberate pick is still possible, but an
explicit spawn/queue-config save is rejected unless the request carries
confirm_blocked_model: true.
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
        self.codex_home = root / "codex_home"
        self._patches = [
            patch.object(server, "MODEL_POLICY_FILE", self.policy),
            patch.object(server, "SPAWN_DEFAULTS_FILE", self.defaults),
            patch.dict(os.environ, {}, clear=False),
        ]
        for p in self._patches:
            p.start()
        os.environ.pop("CCC_BLOCKED_MODELS", None)
        os.environ.pop("CCC_CODEX_MODEL", None)
        os.environ["CODEX_HOME"] = str(self.codex_home)
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

    def write_codex_config(self, model):
        self.codex_home.mkdir(parents=True, exist_ok=True)
        (self.codex_home / "config.toml").write_text(f'model = "{model}"\n')


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

    def test_catalog_still_lists_blocked_model_but_tags_it(self):
        # Ownership-only check: a blocked model still passes, so it stays
        # selectable -- deliberate, confirmed picks must still be possible.
        self.block("gpt-6-astra")
        self.assertTrue(server._model_catalog_allows_model("codex", "gpt-6-astra"))
        self.assertTrue(server._model_catalog_allows_model("codex", "gpt-5.6-sol"))
        catalog = {}
        server._model_catalog_add(catalog, "codex", "gpt-6-astra", source="curated")
        server._model_catalog_add(catalog, "codex", "gpt-5.6-sol", source="curated")
        by_id = {m["id"]: m for m in catalog["codex"]["models"]}
        self.assertTrue(by_id["gpt-6-astra"]["policy_blocked"])
        self.assertFalse(by_id["gpt-5.6-sol"]["policy_blocked"])

    def test_codex_default_falls_back_when_blocked(self):
        self.assertEqual(server._codex_default_model(), "gpt-5.6-terra")
        self.block("gpt-5.6-terra")
        fallback = server._codex_default_model()
        self.assertNotEqual(fallback, "gpt-5.6-terra")
        os.environ["CCC_CODEX_MODEL"] = "gpt-6-astra"
        server._MODEL_POLICY_CACHE["sig"] = None
        self.assertEqual(server._codex_default_model(), "gpt-6-astra")
        self.block("gpt-5.6-terra", "gpt-6-astra")
        self.assertNotIn(server._codex_default_model(), ("gpt-5.6-terra", "gpt-6-astra"))

    def test_inherited_spawn_default_is_substituted_not_rejected(self):
        self.write_defaults(codex="gpt-6-astra")
        self.assertEqual(server._spawn_model_for_engine("codex"), "gpt-6-astra")
        self.block("gpt-6-astra")
        self.assertEqual(server._spawn_model_for_engine("codex"), "gpt-5.6-terra")
        engine, model = server._spawn_request_engine_and_model({"engine": "codex"})
        self.assertEqual((engine, model), ("codex", "gpt-5.6-terra"))
        # An explicit ask still surfaces the blocked model so the validator
        # can reject it with the policy error.
        self.assertEqual(server._spawn_model_for_engine("codex", "gpt-6-astra"), "gpt-6-astra")

    def test_policy_error_is_engine_agnostic(self):
        self.block("claude-opus-5")
        self.assertIn("blocked", server._model_policy_error("claude-opus-5"))
        self.assertIsNone(server._model_policy_error("claude-sonnet-5"))

    def test_confirm_blocked_bypasses_codex_validation(self):
        self.block("gpt-6-astra")
        model, err = server._validate_codex_model("gpt-6-astra")
        self.assertIn("blocked by model policy", err)
        model, err = server._validate_codex_model("gpt-6-astra", confirm_blocked=True)
        self.assertIsNone(err)
        self.assertEqual(model, "gpt-6-astra")

    def test_confirmed_blocked_codex_model_allows_effort_only_update(self):
        """Changing effort keeps the confirmation for the unchanged model."""
        self.block("gpt-6-astra")
        with patch.object(server, "_detect_session_engine", return_value="codex"), \
             patch.object(
                 server,
                 "_get_session_override",
                 return_value={"model": "gpt-6-astra", "policy_confirmed": True},
             ), \
             patch.object(server, "_set_session_override") as set_override:
            result = server._set_session_model(
                "sid-1", "gpt-6-astra", False, "high", effort_only=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"], "queued")
        self.assertEqual(result["reasoning_effort"], "high")
        self.assertTrue(set_override.call_args.kwargs["policy_confirmed"])

    def test_active_blocked_codex_model_allows_effort_only_update(self):
        """An already-running native session needs no new policy confirmation."""
        self.block("gpt-6-astra")
        with patch.object(server, "_detect_session_engine", return_value="codex"), \
             patch.object(server, "_get_session_override", return_value={}), \
             patch.object(server, "_codex_thread_row", return_value={"model": "gpt-6-astra"}), \
             patch.object(server, "_set_session_override") as set_override:
            result = server._set_session_model(
                "sid-1", "gpt-6-astra", False, "high", effort_only=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"], "queued")
        self.assertTrue(set_override.call_args.kwargs["policy_confirmed"])

    def test_default_resolution_never_honors_confirm(self):
        # confirm_blocked_model is only meaningful for an explicit ask -- an
        # inherited/blank default must never resolve to a blocked model, human
        # confirmation or not, the same way it never did before this existed.
        self.write_defaults(codex="gpt-6-astra")
        self.block("gpt-6-astra")
        self.assertNotEqual(server._spawn_model_for_engine("codex"), "gpt-6-astra")


class TestModelPolicyHealth(_PolicyFixture):
    """MEMO-FIX-25: `ccc doctor` / /api/healthcheck must flag deny-list drift
    that the runtime gate itself can't see -- a missing/unparseable policy
    file, or a blocked model sitting as an engine's ambient default (codex
    config.toml, spawn-defaults.json models.*). Regression for the 2026-09-27
    recurrence of the 2026-09-05 incident: both drifted independently with
    nothing surfacing it until a human manually diffed state.
    """

    def test_ok_when_policy_present_and_ambient_defaults_clean(self):
        self.block("gpt-6-astra")
        self.write_defaults(codex="gpt-5.6-terra")
        self.write_codex_config("gpt-5.6-terra")

        report = server._model_policy_health()

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["policy_file"]["exists"])
        self.assertTrue(report["policy_file"]["parseable"])
        self.assertEqual(report["ambient_defaults"]["codex:config.toml"], "gpt-5.6-terra")
        self.assertEqual(report["ambient_defaults"]["spawn-defaults:codex"], "gpt-5.6-terra")

    def test_errors_when_policy_file_missing(self):
        self.assertFalse(self.policy.exists())

        report = server._model_policy_health()

        self.assertEqual(report["status"], "error")
        self.assertFalse(report["policy_file"]["exists"])
        self.assertTrue(any("missing" in e for e in report["errors"]))

    def test_errors_when_policy_file_unparseable(self):
        self.policy.write_text("not valid json")

        report = server._model_policy_health()

        self.assertEqual(report["status"], "error")
        self.assertFalse(report["policy_file"]["parseable"])
        self.assertTrue(any("unparseable" in e for e in report["errors"]))

    def test_errors_when_codex_config_toml_model_is_blocked(self):
        self.block("gpt-6-astra")
        self.write_codex_config("gpt-6-astra")

        report = server._model_policy_health()

        self.assertEqual(report["status"], "error")
        self.assertTrue(any("config.toml" in e and "gpt-6-astra" in e for e in report["errors"]))

    def test_errors_when_spawn_defaults_model_is_blocked(self):
        self.block("gpt-6-astra")
        self.write_defaults(codex="gpt-6-astra")

        report = server._model_policy_health()

        self.assertEqual(report["status"], "error")
        self.assertTrue(
            any("spawn-defaults.json models.codex" in e and "gpt-6-astra" in e for e in report["errors"])
        )

    def test_included_in_ccc_doctor(self):
        expected = {"status": "ok", "errors": [], "policy_file": {}, "ambient_defaults": {}}
        with patch.object(server, "_model_policy_health", return_value=expected):
            report = server.build_ccc_doctor()

        self.assertEqual(report["model_policy"], expected)

    def test_included_in_healthcheck(self):
        self.assertFalse(self.policy.exists())  # missing -> error, drives overall

        report = server._build_healthcheck()

        checks = {c["id"]: c for c in report["checks"]}
        self.assertIn("model_policy", checks)
        self.assertEqual(checks["model_policy"]["status"], "error")
        self.assertEqual(report["overall"], "error")

    def test_missing_policy_file_logs_loudly(self):
        # MEMO-FIX-25(c): a missing policy file must not be silently treated
        # as "nothing blocked" -- it needs to show up in the server log.
        with patch("sys.stderr") as mock_stderr:
            server._model_policy_blocked_models()

        logged = "".join(call.args[0] for call in mock_stderr.write.call_args_list if call.args)
        self.assertIn(str(self.policy), logged)
        self.assertIn("missing", logged)


class AstraGuardrailPromptTests(_PolicyFixture):
    """A blocked model spawned with confirm_blocked_model=true must not rely
    on the model choosing to load the astra-guardrail skill on its own -- the
    guardrail's own SKILL.md gets pushed into the prompt instead."""

    def setUp(self):
        super().setUp()
        self.block("gpt-6-astra")
        self._skill_tmp = tempfile.TemporaryDirectory()
        self.skill_file = pathlib.Path(self._skill_tmp.name) / "SKILL.md"
        self.skill_file.write_text("# Astra guardrail\n\nDo the thing.\n")
        self._skill_patch = patch.object(server, "ASTRA_GUARDRAIL_SKILL_FILE", self.skill_file)
        self._skill_patch.start()
        server._ASTRA_GUARDRAIL_CACHE["sig"] = None

    def tearDown(self):
        self._skill_patch.stop()
        server._ASTRA_GUARDRAIL_CACHE["sig"] = None
        self._skill_tmp.cleanup()
        super().tearDown()

    def test_injects_for_confirmed_blocked_codex_model(self):
        prefix = server._astra_guardrail_prompt_prefix("codex", "gpt-6-astra", True)
        self.assertIn("Do the thing.", prefix)
        self.assertIn("astra-guardrail", prefix)

    def test_no_injection_without_confirm(self):
        self.assertEqual(server._astra_guardrail_prompt_prefix("codex", "gpt-6-astra", False), "")

    def test_no_injection_for_allowed_model(self):
        self.assertEqual(server._astra_guardrail_prompt_prefix("codex", "gpt-5.6-terra", True), "")

    def test_no_injection_for_other_engines(self):
        self.assertEqual(server._astra_guardrail_prompt_prefix("claude", "gpt-6-astra", True), "")

    def test_rereads_after_skill_file_changes(self):
        first = server._astra_guardrail_prompt_prefix("codex", "gpt-6-astra", True)
        self.assertIn("Do the thing.", first)
        self.skill_file.write_text("# Astra guardrail\n\nDo the new thing.\n")
        second = server._astra_guardrail_prompt_prefix("codex", "gpt-6-astra", True)
        self.assertIn("Do the new thing.", second)
        self.assertNotIn("Do the thing.", second)


if __name__ == "__main__":
    unittest.main()


class SeedModelPolicyOnceTests(unittest.TestCase):
    """A fresh install gets an empty policy once; a later deletion stays loud."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.policy = self.root / "model-policy.json"
        self._patches = [
            patch.object(server, "MODEL_POLICY_FILE", self.policy),
            patch.object(server, "COMMAND_CENTER_STATE_DIR", self.root),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def test_fresh_install_seeds_empty_policy(self):
        server._seed_model_policy_once()
        self.assertEqual(json.loads(self.policy.read_text()), {"blocked_models": []})

    def test_existing_policy_untouched(self):
        self.policy.write_text(json.dumps({"blocked_models": ["gpt-6-astra"]}))
        server._seed_model_policy_once()
        self.assertEqual(json.loads(self.policy.read_text())["blocked_models"], ["gpt-6-astra"])

    def test_deleted_after_seed_is_not_reseeded(self):
        server._seed_model_policy_once()
        self.policy.unlink()
        server._seed_model_policy_once()
        self.assertFalse(self.policy.exists())
