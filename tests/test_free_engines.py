"""Free-engine config (L20): ccc_server/free_engines.py.

Spawn env for aider/opencode, marker-delimited codex config.toml merge, and
router resolution. State dirs and the managed-router state file are
redirected to tempdirs; router detection is stubbed — nothing touches the
user's real ~/.codex, ~/.ccc, or ~/.config/opencode.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server  # noqa: F401 -- registers "server" for ccc_server.core lookups
from ccc_server import free_engines
from ccc_server import router_detect


class FreeEnginesBase(unittest.TestCase):
    ROUTER = {
        "base_url": "http://127.0.0.1:3017",
        "openai_base_url": "http://127.0.0.1:3017/v1",
        "anthropic_base_url": "http://127.0.0.1:3017",
        "api_key": "fl-test-key",
        "name": "FreeLLMAPI",
        "keyless": False,
        "source": "ccc",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "ccc-state"
        self._patches = [
            mock.patch.object(free_engines, "FREE_ROUTER_STATE_FILE", self.tmp / "free-router.json"),
            mock.patch.object(
                free_engines._core, "COMMAND_CENTER_STATE_DIR",
                self.state_dir, create=True,
            ),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)


class TestRouterResolution(FreeEnginesBase):
    def test_explicit_base_url_wins(self):
        r = free_engines.resolve_free_router("http://127.0.0.1:4000/", "k")
        self.assertEqual(r["base_url"], "http://127.0.0.1:4000")
        self.assertEqual(r["openai_base_url"], "http://127.0.0.1:4000/v1")
        self.assertEqual(r["source"], "explicit")

    def test_ccc_state_file_read(self):
        self.tmp.joinpath("free-router.json").write_text(json.dumps({
            "port": 3017, "unified_key": "fl-abc",
        }))
        r = free_engines.resolve_free_router()
        self.assertEqual(r["base_url"], "http://127.0.0.1:3017")
        self.assertEqual(r["api_key"], "fl-abc")

    def test_preferred_external_router(self):
        rec = {
            "id": "omniroute", "base_url": "http://127.0.0.1:20128",
            "openai_base_url": "http://127.0.0.1:20128/v1",
            "anthropic_base_url": "http://127.0.0.1:20128",
            "keyless": True, "name": "OmniRoute",
        }
        with mock.patch.object(router_detect, "chosen_router", return_value=rec), \
             mock.patch.object(router_detect, "_choice_path",
                               return_value=self.tmp / "nope.json"):
            r = free_engines.resolve_free_router()
        self.assertEqual(r["source"], "omniroute")
        self.assertTrue(r["keyless"])

    def test_nothing_configured_returns_none(self):
        with mock.patch.object(router_detect, "chosen_router", return_value=None):
            self.assertIsNone(free_engines.resolve_free_router())


class TestSpawnEnv(FreeEnginesBase):
    def test_aider_env_and_model(self):
        out = free_engines.spawn_env("aider", "qwen3-coder", router=dict(self.ROUTER))
        self.assertEqual(out["env"]["OPENAI_API_BASE"], "http://127.0.0.1:3017/v1")
        self.assertEqual(out["env"]["OPENAI_API_KEY"], "fl-test-key")
        self.assertEqual(out["model"], "openai/qwen3-coder")

    def test_aider_default_model_auto(self):
        out = free_engines.spawn_env("aider", None, router=dict(self.ROUTER))
        self.assertEqual(out["model"], "openai/auto")

    def test_claude_env(self):
        out = free_engines.spawn_env("claude", None, router=dict(self.ROUTER))
        self.assertEqual(out["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:3017")
        self.assertEqual(out["env"]["ANTHROPIC_AUTH_TOKEN"], "fl-test-key")

    def test_keyless_router_uses_placeholder_key(self):
        r = dict(self.ROUTER); r["api_key"] = ""; r["keyless"] = True
        out = free_engines.spawn_env("aider", None, router=r)
        self.assertEqual(out["env"]["OPENAI_API_KEY"], "free")

    def test_opencode_writes_config_and_env(self):
        out = free_engines.spawn_env(
            "opencode", None, router=dict(self.ROUTER), api_key="fl-test-key",
        )
        cfg_path = Path(out["env"]["OPENCODE_CONFIG"])
        self.assertTrue(cfg_path.is_file())
        cfg = json.loads(cfg_path.read_text())
        prov = cfg["provider"]["cccfree"]
        self.assertEqual(prov["options"]["baseURL"], "http://127.0.0.1:3017/v1")
        self.assertEqual(prov["options"]["apiKey"], "{env:CCC_FREE_ROUTER_KEY}")
        self.assertTrue(out["model"].startswith("cccfree/"))
        self.assertIn(out["model"].split("/", 1)[1], prov["models"])

    def test_codex_spawn_env_is_none(self):
        self.assertIsNone(
            free_engines.spawn_env("codex", None, router=dict(self.ROUTER))
        )

    def test_unsupported_engine(self):
        self.assertIsNone(free_engines.spawn_env("gemini", None, router=dict(self.ROUTER)))

    def test_payload_gate(self):
        self.assertIsNone(free_engines.spawn_env_for_payload("aider", None, {}))
        self.assertIsNone(
            free_engines.spawn_env_for_payload("aider", None, {"runtime": "paid"})
        )
        out = free_engines.spawn_env_for_payload("aider", None, {
            "runtime": "free",
            "free_base_url": "http://127.0.0.1:9999",
            "free_api_key": "k",
        })
        self.assertEqual(out["env"]["OPENAI_API_BASE"], "http://127.0.0.1:9999/v1")


class TestCodexConfig(FreeEnginesBase):
    def test_provider_block_shape(self):
        block = free_engines.codex_provider_block("http://127.0.0.1:3017", "qwen3-coder")
        self.assertIn('[model_providers.ccc_free]', block)
        self.assertIn('wire_api = "responses"', block)
        self.assertIn('base_url = "http://127.0.0.1:3017/v1"', block)
        self.assertIn('env_key = "CCC_FREE_ROUTER_KEY"', block)
        self.assertIn("[profiles.ccc-free]", block)
        self.assertIn('model = "qwen3-coder"', block)
        self.assertIn("# ccc-free:start", block)
        self.assertIn("# ccc-free:end", block)

    def test_install_writes_marked_block(self):
        home = self.tmp / "home"
        res = free_engines.install_codex_provider(
            "http://127.0.0.1:3017", model="auto", home=home,
        )
        self.assertTrue(res["ok"])
        text = (home / ".codex" / "config.toml").read_text()
        self.assertIn("[model_providers.ccc_free]", text)

    def test_install_is_idempotent(self):
        home = self.tmp / "home"
        free_engines.install_codex_provider("http://127.0.0.1:3017", home=home)
        res = free_engines.install_codex_provider("http://127.0.0.1:3017", home=home)
        self.assertTrue(res["ok"])
        self.assertFalse(res["changed"])

    def test_install_preserves_existing_config(self):
        home = self.tmp / "home"
        cfg = home / ".codex" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('model = "gpt-5"\n\n[model_providers.other]\nname = "Other"\n')
        free_engines.install_codex_provider("http://127.0.0.1:3017", home=home)
        text = cfg.read_text()
        self.assertIn('model = "gpt-5"', text)          # user's own line kept
        self.assertIn('[model_providers.other]', text)  # user's providers kept
        self.assertIn("[model_providers.ccc_free]", text)
        # A backup was written before the merge.
        self.assertTrue(list(cfg.parent.glob("config.toml.ccc-backup-*")))

    def test_reinstall_replaces_old_block_only(self):
        home = self.tmp / "home"
        cfg = home / ".codex" / "config.toml"
        free_engines.install_codex_provider("http://127.0.0.1:3017", home=home)
        cfg.write_text(cfg.read_text() + 'approval_policy = "never"\n')
        res = free_engines.install_codex_provider("http://127.0.0.1:3017", home=home)
        self.assertTrue(res["changed"])
        text = cfg.read_text()
        self.assertEqual(text.count("[model_providers.ccc_free]"), 1)
        self.assertIn('approval_policy = "never"', text)


class TestEngineSetup(FreeEnginesBase):
    def test_opencode_setup_writes_ccc_config(self):
        res = free_engines.engine_setup(
            "opencode", base_url="http://127.0.0.1:3017", api_key="k",
        )
        self.assertTrue(res["ok"])
        self.assertTrue(Path(res["path"]).is_file())

    def test_codex_setup_returns_profile_run_line(self):
        with mock.patch.object(free_engines, "_codex_config_path",
                               return_value=self.tmp / "codex" / "config.toml"):
            res = free_engines.engine_setup(
                "codex", base_url="http://127.0.0.1:3017",
            )
        self.assertTrue(res["ok"])
        self.assertEqual(res["profile"], "ccc-free")
        self.assertIn("--profile ccc-free", res["run"])

    def test_preview_never_contains_key(self):
        res = free_engines.engine_config_preview(
            "codex", base_url="http://127.0.0.1:3017",
        )
        self.assertTrue(res["ok"])
        self.assertIn("wire_api", res["snippet"])

    def test_preview_unknown_engine(self):
        res = free_engines.engine_config_preview("gemini", base_url="http://x")
        self.assertFalse(res["ok"])
        self.assertIn("supported", res)


if __name__ == "__main__":
    unittest.main()
