import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ccc_server import headless_run, jobs_add


class HeadlessRunTests(unittest.TestCase):
    def test_resolve_session_defaults_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spawn-defaults.json"
            p.write_text(json.dumps({
                "engine": "codex",
                "models": {"codex": "gpt-6.1-sol", "claude": "sonnet-5-5"},
                "reasoning_effort": "high",
            }), encoding="utf-8")
            got = headless_run.resolve_session_defaults(server_url="http://127.0.0.1:1", defaults_file=p)
            self.assertEqual(got["engine"], "codex")
            self.assertEqual(got["model"], "gpt-6.1-sol")
            self.assertEqual(got["effort"], "high")
            self.assertEqual(got["source"], "file")

    def test_worker_defaults_and_cross_engine_fallback(self):
        defaults = {
            "engine": "claude",
            "worker_engine": "codex",
            "worker_model": "gpt-6.2-sol",
            "worker_reasoning_effort": "high",
            "worker_fallback": {"enabled": True, "models": [
                {"engine": "codex", "model": "gpt-6.1-terra", "effort": "medium"},
                {"engine": "claude", "model": "sonnet-5-5", "effort": "high"},
            ]},
        }
        schema = {"type": "object", "properties": {"note": {"type": "string"}}}
        primary = {"ok": False, "subtype": "error", "error": "You've hit your usage limit"}
        fallback = {"ok": True, "subtype": "success", "engine": "claude",
                    "model": "claude-sonnet-5-5", "effort": "high"}
        with patch.object(headless_run, "_load_defaults", return_value=(defaults, "file")), \
                patch.object(headless_run, "_run_codex", return_value=primary) as codex, \
                patch.object(headless_run, "_run_claude", return_value=fallback) as claude:
            result = headless_run.run_headless("synthetic", worker_defaults=True, json_schema=schema)
            self.assertEqual([route["engine"] for route in result["execution"]["attempts"]], ["codex", "claude"])
            self.assertEqual(codex.call_args.kwargs["model"], "gpt-6.2-sol")
            self.assertEqual(codex.call_args.kwargs["json_schema"]["required"], ["note"])
            self.assertEqual(claude.call_args.kwargs["model"], "claude-sonnet-5-5")
            self.assertEqual(claude.call_args.kwargs["json_schema"], schema)
            self.assertLess(claude.call_args.kwargs["timeout_s"], codex.call_args.kwargs["timeout_s"])

            claude.reset_mock()
            codex.return_value = {**primary, "engine": "codex", "model": "gpt-6.2-sol",
                                  "effort": "high", "usage": {"input_tokens": 5}}
            self.assertFalse(headless_run.run_headless("synthetic", worker_defaults=True)["ok"])
            claude.assert_not_called()

            codex.return_value = {"ok": True, "subtype": "success", "engine": "codex",
                                  "model": "gpt-6.2-sol", "effort": "high",
                                  "structured_output": {"note": None}}
            result = headless_run.run_headless("synthetic", worker_defaults=True, json_schema=schema)
            self.assertEqual(result["structured_output"], {})
            claude.assert_not_called()

    def test_rejected_worker_model_falls_through_to_the_next_route(self):
        """OPS-1068: a zero-token 'model is not supported' rejection suppressed both studio digests."""
        defaults = {"worker_engine": "codex", "worker_model": "gpt-6.2-sol",
                    "worker_fallback": {"enabled": True, "models": [{"engine": "claude", "model": "sonnet-5-5"}]}}
        rejected = {"ok": False, "subtype": "error", "engine": "codex", "model": "gpt-6.2-sol", "effort": "",
                    "usage": {}, "error": '{"type":"error","status":400,"error":{"message":"The '
                                         "'gpt-6.2-sol' model is not supported when using Codex with a "
                                         'ChatGPT account."}}'}
        fallback = {"ok": True, "subtype": "success", "engine": "claude", "model": "claude-sonnet-5-5", "effort": ""}
        with patch.object(headless_run, "_load_defaults", return_value=(defaults, "file")), \
                patch.object(headless_run, "_run_codex", return_value=rejected), \
                patch.object(headless_run, "_run_claude", return_value=fallback) as claude:
            result = headless_run.run_headless("synthetic", worker_defaults=True)
        self.assertTrue(result["ok"])
        self.assertEqual([a["engine"] for a in result["execution"]["attempts"]], ["codex", "claude"])
        claude.assert_called_once()

    def test_claude_dispatch_flags_and_envelope(self):
        captured = {}

        def fake_runner(argv, stdin_text, cwd, env, timeout_s, max_output_bytes):
            captured["argv"] = argv
            captured["stdin"] = stdin_text
            captured["env"] = env
            return 0, json.dumps({
                "subtype": "success",
                "is_error": False,
                "session_id": "sid-123",
                "result": '{"ok": true}',
                "structured_output": {"ok": True},
                "total_cost_usd": 0.02,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }), "", False, False

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spawn-defaults.json"
            p.write_text(json.dumps({
                "engine": "claude",
                "models": {"claude": "sonnet-5-5"},
                "reasoning_effort": "medium",
            }), encoding="utf-8")
            res = headless_run.run_headless(
                "hello",
                cwd=tmp,
                tools="none",
                json_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                subscription_only=True,
                server_url="http://127.0.0.1:1",
                defaults_file=p,
                base_env={"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-test-XXXX"},
                runner=fake_runner,
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["engine"], "claude")
            self.assertEqual(res["model"], "claude-sonnet-5-5")
            self.assertEqual(captured["argv"][captured["argv"].index("--model") + 1], "claude-sonnet-5-5")
            self.assertEqual(res["effort"], "medium")
            self.assertEqual(res["structured_output"], {"ok": True})
            self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])
            self.assertIn("--tools", captured["argv"])
            self.assertEqual(captured["argv"][captured["argv"].index("--tools") + 1], "")
            self.assertIn("--json-schema", captured["argv"])

    def test_claude_model_overrides_use_launch_ids(self):
        defaults = {"engine": "claude", "models": {"claude": "sonnet-5-5"}}
        with patch.object(headless_run, "_load_defaults", return_value=(defaults, "file")), \
                patch.object(headless_run, "_run_claude") as claude:
            for model, expected in (
                ("haiku-5-5", "claude-haiku-5-5"),
                ("claude-haiku-5-5", "claude-haiku-5-5"),
                ("sonnet", "sonnet"),
            ):
                with self.subTest(model=model):
                    headless_run.run_headless("synthetic", model=model)
                    self.assertEqual(claude.call_args.kwargs["model"], expected)

    def test_codex_dispatch_and_mcp_translation(self):
        captured = {}

        def fake_runner(argv, stdin_text, cwd, env, timeout_s, max_output_bytes):
            captured["argv"] = argv
            captured["stdin"] = stdin_text
            out_idx = argv.index("-o") + 1
            Path(argv[out_idx]).write_text('{"verdict": "pass"}', encoding="utf-8")
            jsonl = "\n".join([
                json.dumps({"type": "thread.started", "thread_id": "thr-1"}),
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"verdict": "pass"}'}}),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 12}}),
            ])
            return 0, jsonl, "", False, False

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spawn-defaults.json"
            p.write_text(json.dumps({
                "engine": "codex",
                "models": {"codex": "gpt-6.1-sol"},
                "reasoning_effort": "low",
            }), encoding="utf-8")
            mcp = Path(tmp) / "mcp.json"
            mcp.write_text(json.dumps({
                "mcpServers": {
                    "qa": {"command": "/usr/bin/python3", "args": ["srv.py"], "env": {"FOO": "bar"}}
                }
            }), encoding="utf-8")
            res = headless_run.run_headless(
                "check this",
                cwd=tmp,
                tools="none",
                mcp_config=str(mcp),
                strict_mcp_config=True,
                json_schema={"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]},
                server_url="http://127.0.0.1:1",
                defaults_file=p,
                runner=fake_runner,
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["engine"], "codex")
            self.assertEqual(res["session_id"], "thr-1")
            self.assertEqual(res["structured_output"], {"verdict": "pass"})
            joined = " ".join(captured["argv"])
            self.assertIn("--output-schema", joined)
            self.assertIn("mcp_servers.qa.command=", joined)
            self.assertIn("--ignore-user-config", joined)

    def test_codex_tools_none_rejects_command_execution(self):
        def fake_runner(argv, stdin_text, cwd, env, timeout_s, max_output_bytes):
            jsonl = "\n".join([
                json.dumps({"type": "thread.started", "thread_id": "thr-2"}),
                json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}}),
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}}),
            ])
            return 0, jsonl, "", False, False

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spawn-defaults.json"
            p.write_text(json.dumps({"engine": "codex", "models": {"codex": "gpt-6.1-sol"}}), encoding="utf-8")
            res = headless_run.run_headless(
                "test", cwd=tmp, tools="none", server_url="http://127.0.0.1:1", defaults_file=p, runner=fake_runner
            )
            self.assertFalse(res["ok"])
            self.assertEqual(res["subtype"], "policy_violation")

    def test_jobs_add_prompt_mentions_default_engine_commands(self):
        prompt = jobs_add.compose_job_prompt({
            "host": "hermes", "repo": "/home/user/Apps/demo", "what": "daily check", "when": "daily 8am", "name": "demo-check"
        })
        self.assertIn("ccc spawn --wait", prompt)
        self.assertIn("ccc run", prompt)


if __name__ == "__main__":
    unittest.main()
