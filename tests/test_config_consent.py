"""Consent gate for CCC's writes to agent config (ccc_server/config_consent.py).

CCC must not touch ~/.claude/settings.json, ~/.codex/hooks.json, or the
skills folders until the user approves, must keep the user's formatting and
other entries, and must be able to take everything back out cleanly.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import config_consent as cc


USER_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/my-guard"}]}
        ]
    },
    "statusLine": {"type": "command", "command": "echo hi"},
}


class ConsentTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.ccc_root = root / "ccc"
        (self.ccc_root / "skills").mkdir(parents=True)
        (self.ccc_root / "skills" / "ccc-orchestration.md").write_text("---\nname: ccc-orchestration\n---\nv1\n")
        (self.ccc_root / "skills" / "fleet-verify.md").write_text("---\nname: fleet-verify\n---\nv1\n")
        self.scripts = self.home / ".claude" / "command-center" / "hooks"
        self.ctx = cc.Ctx(
            home=self.home,
            state_dir=self.home / ".claude" / "command-center",
            ccc_root=self.ccc_root,
            hook_scripts_dir=self.scripts,
            codex_present=False,
            wt_bin="",
            hook_command=lambda name: f"/usr/bin/python3 {self.scripts / name}",
        )
        self.settings = self.home / ".claude" / "settings.json"
        env = mock.patch.dict(os.environ, {"CCC_SKIP_SKILL_INSTALL": ""})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

    def write_settings(self, data, indent=2, newline=True):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=indent) + ("\n" if newline else "")
        self.settings.write_text(text)
        return text

    def ccc_commands(self):
        data = json.loads(self.settings.read_text())
        return [h["command"] for entries in data.get("hooks", {}).values()
                for e in entries for h in e.get("hooks", [])
                if cc.HOOK_MARKER in h.get("command", "")]

    def item(self, view, item_id):
        return next(i for i in view["items"] if i["id"] == item_id)

    def skill_file(self, name):
        return self.home / ".claude" / "skills" / name / "SKILL.md"


class NoWritesBeforeConsent(ConsentTestBase):
    def test_startup_writes_nothing_outside_ccc_dir(self):
        original = self.write_settings(USER_SETTINGS)
        summary = cc.startup(self.ctx, log=None)
        self.assertEqual(self.settings.read_text(), original)
        self.assertFalse((self.home / ".claude" / "skills").exists())
        self.assertFalse((self.home / ".codex").exists())
        self.assertIn("claude-hooks", summary["pending"])
        self.assertIn("skill:ccc-orchestration", summary["pending"])
        self.assertEqual(summary["applied"], [])

    def test_overview_lists_pending_items_with_diffs_and_no_notice(self):
        self.write_settings(USER_SETTINGS)
        view = cc.overview(self.ctx)
        hooks = self.item(view, "claude-hooks")
        self.assertEqual(hooks["status"], "pending")
        self.assertTrue(hooks["needs_review"])
        self.assertIn("pre-tool-use.py", hooks["changes"][0]["diff"])
        self.assertEqual(hooks["targets"], ["~/.claude/settings.json"])
        self.assertFalse(view["notice"]["pending"])
        # Codex not installed -> no Codex item; no wt -> no WatchTower item.
        ids = {i["id"] for i in view["items"]}
        self.assertNotIn("codex-hooks", ids)
        self.assertNotIn("watchtower-skills", ids)
        self.assertEqual(self.settings.read_text(), json.dumps(USER_SETTINGS, indent=2) + "\n")

    def test_no_settings_file_is_not_created(self):
        cc.startup(self.ctx, log=None)
        cc.overview(self.ctx)
        self.assertFalse(self.settings.exists())


class Approve(ConsentTestBase):
    def test_approve_adds_hooks_and_keeps_everything_else(self):
        self.write_settings(USER_SETTINGS, indent=4, newline=False)
        res = cc.decide({"claude-hooks": "approve"}, ctx=self.ctx)
        self.assertTrue(res["ok"], res)
        text = self.settings.read_text()
        data = json.loads(text)
        self.assertEqual(len(self.ccc_commands()), 9)
        self.assertEqual(data["hooks"]["PreToolUse"][0], USER_SETTINGS["hooks"]["PreToolUse"][0])
        self.assertEqual(list(data)[:2], ["model", "permissions"])
        self.assertEqual(data["statusLine"], USER_SETTINGS["statusLine"])
        # Formatting kept: 4-space indent, no trailing newline.
        self.assertIn('\n    "model"', text)
        self.assertFalse(text.endswith("\n"))
        pre = [h for e in data["hooks"]["PreToolUse"] for h in e["hooks"] if "pre-tool-use.py" in h["command"]]
        self.assertEqual(pre[0]["timeout"], cc.PRETOOLUSE_HOOK_TIMEOUT)
        # The previous file was backed up first.
        backups = list((self.ctx.state_dir / cc.BACKUP_DIR_NAME).rglob("settings.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), USER_SETTINGS)

    def test_approved_item_is_reapplied_idempotently_on_startup(self):
        self.write_settings(USER_SETTINGS)
        cc.decide({"claude-hooks": "approve"}, ctx=self.ctx)
        after = self.settings.read_text()
        summary = cc.startup(self.ctx, log=None)
        self.assertIn("claude-hooks", summary["applied"])
        self.assertEqual(self.settings.read_text(), after)
        self.assertEqual(self.item(cc.overview(self.ctx), "claude-hooks")["status"], "enabled")

    def test_approve_skill_installs_it(self):
        res = cc.decide({"skill:fleet-verify": "approve"}, ctx=self.ctx)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.skill_file("fleet-verify").read_text(), "---\nname: fleet-verify\n---\nv1\n")
        self.assertFalse(self.skill_file("ccc-orchestration").exists())

    def test_symlinked_settings_keeps_the_link(self):
        real = self.home / "dotfiles" / "claude-settings.json"
        real.parent.mkdir()
        real.write_text(json.dumps(USER_SETTINGS, indent=2) + "\n")
        self.settings.parent.mkdir(parents=True)
        self.settings.symlink_to(real)
        cc.decide({"claude-hooks": "approve"}, ctx=self.ctx)
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(len(self.ccc_commands()), 9)
        self.assertIn(cc.HOOK_MARKER, real.read_text())

    def test_invalid_json_is_never_touched(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{ not json")
        res = cc.decide({"claude-hooks": "approve"}, ctx=self.ctx)
        self.assertFalse(res["ok"])
        self.assertEqual(self.settings.read_text(), "{ not json")

    def test_skill_folder_owned_by_another_tool_is_skipped(self):
        other = self.home / "other-tool" / "ccc-orchestration"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("theirs\n")
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        (skills / "ccc-orchestration").symlink_to(other)
        cc.decide({"skill:ccc-orchestration": "approve"}, ctx=self.ctx)
        self.assertEqual((other / "SKILL.md").read_text(), "theirs\n")
        cc.revoke(["skill:ccc-orchestration"], ctx=self.ctx)
        self.assertEqual((other / "SKILL.md").read_text(), "theirs\n")


class OwnedByAnotherTool(ConsentTestBase):
    def test_skill_linked_everywhere_by_another_tool_is_not_offered(self):
        other = self.home / "wt" / "fleet-verify"
        other.mkdir(parents=True)
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        (skills / "fleet-verify").symlink_to(other)
        ids = {i["id"] for i in cc.overview(self.ctx)["items"]}
        self.assertNotIn("skill:fleet-verify", ids)
        self.assertIn("skill:ccc-orchestration", ids)


class Decline(ConsentTestBase):
    def test_declined_item_is_never_written(self):
        original = self.write_settings(USER_SETTINGS)
        res = cc.decide({"claude-hooks": "decline", "skill:fleet-verify": "decline"}, ctx=self.ctx)
        self.assertTrue(res["ok"], res)
        cc.startup(self.ctx, log=None)
        self.assertEqual(self.settings.read_text(), original)
        self.assertFalse(self.skill_file("fleet-verify").exists())
        view = cc.overview(self.ctx)
        self.assertEqual(self.item(view, "claude-hooks")["status"], "declined")
        self.assertFalse(self.item(view, "claude-hooks")["needs_review"])
        self.assertFalse(cc.is_enabled("claude-hooks", ctx=self.ctx))

    def test_declined_stays_declined_after_content_change(self):
        cc.decide({"skill:fleet-verify": "decline"}, ctx=self.ctx)
        (self.ccc_root / "skills" / "fleet-verify.md").write_text("v2\n")
        view = cc.overview(self.ctx)
        self.assertEqual(self.item(view, "skill:fleet-verify")["status"], "declined")
        self.assertFalse(self.item(view, "skill:fleet-verify")["needs_review"])

    def test_skip_env_keeps_skills_out_and_unprompted(self):
        with mock.patch.dict(os.environ, {"CCC_SKIP_SKILL_INSTALL": "1"}):
            view = cc.overview(self.ctx)
            skill = self.item(view, "skill:fleet-verify")
            self.assertTrue(skill["skipped_by_env"])
            self.assertFalse(skill["needs_review"])
            res = cc.decide({"skill:fleet-verify": "approve"}, ctx=self.ctx)
            self.assertFalse(res["ok"])
        self.assertFalse(self.skill_file("fleet-verify").exists())


class ContentChangeKeepsApproval(ConsentTestBase):
    def test_changed_skill_is_applied_without_reprompt(self):
        cc.decide({"skill:fleet-verify": "approve"}, ctx=self.ctx)
        (self.ccc_root / "skills" / "fleet-verify.md").write_text("---\nname: fleet-verify\n---\nv2\n")
        item = self.item(cc.overview(self.ctx), "skill:fleet-verify")
        self.assertEqual(item["status"], "enabled")
        self.assertFalse(item["needs_review"])
        summary = cc.startup(self.ctx, log=None)
        self.assertIn("skill:fleet-verify", summary["applied"])
        self.assertTrue(self.skill_file("fleet-verify").read_text().endswith("v2\n"))

    def test_changed_hook_command_keeps_approval(self):
        self.write_settings(USER_SETTINGS)
        cc.decide({"claude-hooks": "approve"}, ctx=self.ctx)
        self.ctx.hook_command = lambda name: f"/opt/py3 {self.scripts / name}"
        self.assertEqual(self.item(cc.overview(self.ctx), "claude-hooks")["status"], "enabled")


class Revoke(ConsentTestBase):
    def test_revoke_restores_settings_byte_for_byte(self):
        original = self.write_settings(USER_SETTINGS)
        cc.decide({"claude-hooks": "approve", "skill:fleet-verify": "approve"}, ctx=self.ctx)
        self.assertNotEqual(self.settings.read_text(), original)
        res = cc.revoke(ctx=self.ctx)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.settings.read_text(), original)
        self.assertFalse(self.skill_file("fleet-verify").exists())
        self.assertFalse(self.skill_file("fleet-verify").parent.exists())
        view = cc.overview(self.ctx)
        self.assertEqual(self.item(view, "claude-hooks")["status"], "declined")
        cc.startup(self.ctx, log=None)
        self.assertEqual(self.settings.read_text(), original)

    def test_revoke_without_prior_hooks_key_removes_it(self):
        data = {"model": "opus"}
        original = self.write_settings(data)
        cc.decide({"claude-hooks": "approve"}, ctx=self.ctx)
        cc.revoke(["claude-hooks"], ctx=self.ctx)
        self.assertEqual(self.settings.read_text(), original)

    def test_revoke_keeps_user_hook_sharing_a_matcher_group(self):
        mixed = {"hooks": {"Stop": [{"matcher": "", "hooks": [
            {"type": "command", "command": "/usr/bin/python3 /x/.claude/command-center/hooks/stop.py"},
            {"type": "command", "command": "say done"},
        ]}]}}
        self.write_settings(mixed)
        cc.revoke(["claude-hooks"], ctx=self.ctx)
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"], [{"type": "command", "command": "say done"}])

    def test_codex_hooks_revoke_keeps_other_tools(self):
        self.ctx.codex_present = True
        hooks = self.home / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        other = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "other.sh"}]}]}}
        hooks.write_text(json.dumps(other, indent=2) + "\n")
        cc.decide({"codex-hooks": "approve"}, ctx=self.ctx)
        self.assertIn("post-compact-codex.py", hooks.read_text())
        cc.revoke(["codex-hooks"], ctx=self.ctx)
        self.assertEqual(json.loads(hooks.read_text()), other)


class CleanUninstall(ConsentTestBase):
    def test_one_line_json_stays_one_line(self):
        self.ctx.codex_present = True
        hooks = self.home / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        original = '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"other.sh"}]}]}}\n'
        hooks.write_text(original)
        cc.decide({"codex-hooks": "approve"}, ctx=self.ctx)
        self.assertEqual(hooks.read_text().count("\n"), 1)
        cc.revoke(["codex-hooks"], ctx=self.ctx)
        self.assertEqual(hooks.read_text(), original)

    def test_files_and_dirs_ccc_created_are_removed_on_revoke(self):
        self.ctx.codex_present = True
        (self.home / ".codex").mkdir()
        cc.decide({"claude-hooks": "approve", "codex-hooks": "approve",
                   "skill:fleet-verify": "approve"}, ctx=self.ctx)
        self.assertTrue(self.settings.exists())
        self.assertTrue((self.home / ".codex" / "skills" / "fleet-verify" / "SKILL.md").exists())
        cc.revoke(ctx=self.ctx)
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.home / ".codex" / "hooks.json").exists())
        self.assertFalse((self.home / ".claude" / "skills").exists())
        self.assertFalse((self.home / ".codex" / "skills").exists())
        self.assertTrue((self.home / ".codex").is_dir())  # the user's, not ours

    def test_users_own_empty_skills_dir_and_edited_file_are_kept(self):
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        cc.decide({"skill:fleet-verify": "approve", "claude-hooks": "approve"}, ctx=self.ctx)
        data = json.loads(self.settings.read_text())
        data["model"] = "sonnet"  # user edits the file CCC created
        self.settings.write_text(json.dumps(data, indent=2) + "\n")
        cc.revoke(ctx=self.ctx)
        self.assertTrue(skills.is_dir())
        self.assertEqual(json.loads(self.settings.read_text()), {"model": "sonnet"})


class WatchTowerScope(ConsentTestBase):
    def test_revoke_all_leaves_watchtower_alone_unless_approved_via_ccc(self):
        calls = []
        self.ctx.wt_bin = "/bin/wt-fake"
        def fake_run(self_, ctx, *args, timeout=20):
            calls.append(args)
            return ("", None)
        with mock.patch.object(cc.WatchTowerSkills, "_run", fake_run):
            cc.revoke(ctx=self.ctx)
            self.assertNotIn(("remove",), calls)
            cc.decide({"watchtower-skills": "approve"}, ctx=self.ctx)
            self.assertIn(("sync",), calls)
            cc.revoke(ctx=self.ctx)
            self.assertIn(("remove",), calls)


class ExistingInstalls(ConsentTestBase):
    def seed_existing_install(self):
        existing = json.loads(json.dumps(USER_SETTINGS))
        existing["hooks"]["Stop"] = [{"matcher": "", "hooks": [
            {"type": "command", "command": f"python3 {self.scripts / 'stop.py'}"}]}]
        self.write_settings(existing)
        self.skill_file("ccc-orchestration").parent.mkdir(parents=True)
        self.skill_file("ccc-orchestration").write_text("old\n")

    def test_existing_install_keeps_working_and_shows_notice_once(self):
        self.seed_existing_install()
        summary = cc.startup(self.ctx, log=None)
        self.assertIn("claude-hooks", summary["applied"])
        self.assertIn("skill:ccc-orchestration", summary["applied"])
        self.assertEqual(len(self.ccc_commands()), 9)
        view = cc.overview(self.ctx)
        self.assertTrue(view["notice"]["pending"])
        self.assertEqual({i["id"] for i in view["notice"]["items"]},
                         {"claude-hooks", "skill:ccc-orchestration"})
        self.assertEqual(self.item(view, "claude-hooks")["decided_via"], "existing-install")
        # Not-yet-installed items still ask.
        self.assertEqual(self.item(view, "skill:fleet-verify")["status"], "pending")
        cc.ack_notice(ctx=self.ctx)
        self.assertFalse(cc.overview(self.ctx)["notice"]["pending"])

    def test_notice_remove_takes_existing_install_out(self):
        self.seed_existing_install()
        cc.startup(self.ctx, log=None)
        cc.revoke(["claude-hooks", "skill:ccc-orchestration"], ctx=self.ctx)
        self.assertEqual(self.ccc_commands(), [])
        self.assertEqual(json.loads(self.settings.read_text())["hooks"]["PreToolUse"],
                         USER_SETTINGS["hooks"]["PreToolUse"])
        self.assertFalse(self.skill_file("ccc-orchestration").exists())


class InstalledEngines(ConsentTestBase):
    def test_auto_review_only_for_installed_engines(self):
        self.ctx.claude_present = False
        self.ctx.codex_present = True
        ov = cc.overview(self.ctx)
        by = {i["id"]: i for i in ov["items"]}
        self.assertFalse(by["claude-hooks"]["auto_review"])
        self.assertTrue(by["claude-hooks"]["needs_review"])
        self.assertTrue(by["codex-hooks"]["auto_review"])
        self.assertEqual(by["claude-hooks"]["hook_count"], 9)
        self.assertEqual(ov["engines_present"], {"claude": False, "codex": True})

    def test_nothing_installed_means_no_auto_review(self):
        self.ctx.claude_present = False
        self.ctx.codex_present = False
        ov = cc.overview(self.ctx)
        self.assertEqual(ov["auto_review"], 0)
        self.assertGreater(ov["needs_review"], 0)


class Formatting(unittest.TestCase):
    def test_detect_indent(self):
        self.assertEqual(cc._detect_indent('{\n  "a": 1\n}', 4), 2)
        self.assertEqual(cc._detect_indent('{\n\t"a": 1\n}', 4), "\t")
        self.assertEqual(cc._detect_indent('{"a": 1}', 4), 4)
        self.assertEqual(cc._detect_indent(None, 2), 2)

    def test_non_ascii_is_kept_literal(self):
        out = cc.render_json({"name": "café"}, '{\n  "name": "café"\n}\n', 2)
        self.assertIn("café", out)


if __name__ == "__main__":
    unittest.main()
