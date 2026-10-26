"""Decision-logic tests for scripts/uninstall.sh.

Drives the uninstaller against a fake HOME so we can assert it tears down the
leftovers a future brew/curl install must not leave behind: WatchTower's
LaunchAgent, a Homebrew-style (possibly broken) `ccc` symlink, CCC hook
entries, WatchTower skill symlinks, the managed WatchTower clone, and
--purge-data state dirs — without touching unrelated Boost hooks.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
UNINSTALL = PROJECT_ROOT / "scripts" / "uninstall.sh"


class UninstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.install = self.home / ".ccc" / "claude-command-center"
        self.watchtower = self.home / ".ccc" / "watchtower"
        self.data = self.home / ".claude" / "command-center"
        self.wt_state = self.home / ".watchtower"
        self.cli = self.home / ".local" / "bin" / "ccc"
        self.plist = (
            self.home / "Library" / "LaunchAgents" / "ai.watchtower.watcher.plist"
        )
        self.settings = self.home / ".claude" / "settings.json"
        self.claude_skills = self.home / ".claude" / "skills"
        self.codex_skills = self.home / ".codex" / "skills"
        self._seed()
        # Never let cleanup tests invoke host service/process commands. A fake
        # macOS surface exercises LaunchAgent cleanup on every test platform.
        self.command_bin = self.home / "test-bin"
        self.command_bin.mkdir()
        for name, body in {
            "uname": "echo Darwin",
            "launchctl": "exit 0",
            "wt": "exit 0",
            "pkill": "exit 0",
        }.items():
            command = self.command_bin / name
            command.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            command.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self):
        # Minimal CCC checkout with a run.sh that records --uninstall-service.
        self.install.mkdir(parents=True)
        (self.install / "server.py").write_text("# fake\n", encoding="utf-8")
        run_sh = self.install / "run.sh"
        run_sh.write_text(
            "#!/usr/bin/env bash\n"
            'echo "$*" >> "$HOME/.uninstall-runsh.log"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        run_sh.chmod(run_sh.stat().st_mode | stat.S_IXUSR)

        # Managed WatchTower clone.
        (self.watchtower / "watchtower").mkdir(parents=True)
        (self.watchtower / "pyproject.toml").write_text(
            "[project]\nname='watchtower'\n", encoding="utf-8"
        )
        skill_src = self.watchtower / "watchtower" / "skills" / "watchtower"
        skill_src.mkdir(parents=True)
        (skill_src / "SKILL.md").write_text("# wt\n", encoding="utf-8")

        # Homebrew-style broken ccc link (Cellar already gone).
        self.cli.parent.mkdir(parents=True)
        self.cli.symlink_to("/opt/homebrew/Cellar/ccc/5.39.0/libexec/ccc")

        # WatchTower LaunchAgent plist.
        self.plist.parent.mkdir(parents=True)
        self.plist.write_text(
            '<?xml version="1.0"?><plist><dict></dict></plist>\n',
            encoding="utf-8",
        )

        # Agent config: CCC Stop hook + unrelated Boost hook.
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": (
                                            "/usr/local/bin/python3 "
                                            "/root/.claude/command-center/hooks/stop.py"
                                        ),
                                    },
                                    {
                                        "type": "command",
                                        "command": str(
                                            self.home / ".claude/hooks/boost-sync.sh"
                                        ),
                                    },
                                ]
                            }
                        ]
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # Skill symlinks into the managed clone (Claude + Codex).
        for skills_dir in (self.claude_skills, self.codex_skills):
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "watchtower").symlink_to(skill_src)
            (skills_dir / "keep-me").mkdir()
            (skills_dir / "keep-me" / "SKILL.md").write_text("# keep\n", encoding="utf-8")

        # Runtime state dirs.
        self.data.mkdir(parents=True)
        (self.data / "port.txt").write_text("http://127.0.0.1:8090\n", encoding="utf-8")
        self.wt_state.mkdir(parents=True)
        (self.wt_state / "daemon.pid").write_text("1\n", encoding="utf-8")

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["PATH"] = str(self.command_bin) + os.pathsep + env.get("PATH", "")
        env["CCC_INSTALL_DIR"] = str(self.install)
        env["WATCHTOWER_INSTALL_DIR"] = str(self.watchtower)
        # Avoid inheriting a real CCC_* that could widen the blast radius.
        env.pop("CCC_BIND_HOST", None)
        return subprocess.run(
            ["bash", str(UNINSTALL), "--yes", *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_script_is_executable(self):
        self.assertTrue(UNINSTALL.is_file())
        self.assertTrue(
            UNINSTALL.stat().st_mode & stat.S_IXUSR,
            "scripts/uninstall.sh must have the executable bit set",
        )

    def test_full_uninstall_with_purge(self):
        result = self._run("--purge-data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # CCC service remover was invoked.
        log = (self.home / ".uninstall-runsh.log").read_text(encoding="utf-8")
        self.assertIn("--uninstall-service", log)

        # WatchTower LaunchAgent + Homebrew ccc link + managed clones gone.
        self.assertFalse(self.plist.exists())
        self.assertFalse(self.cli.exists())
        self.assertFalse(self.cli.is_symlink())
        self.assertFalse(self.watchtower.exists())
        self.assertFalse(self.install.exists())
        self.assertFalse((self.home / ".ccc").exists())

        # Purge removed runtime state.
        self.assertFalse(self.data.exists())
        self.assertFalse(self.wt_state.exists())

        # Skill symlinks into WatchTower removed; unrelated skill kept.
        self.assertFalse((self.claude_skills / "watchtower").exists())
        self.assertFalse((self.codex_skills / "watchtower").exists())
        self.assertTrue((self.claude_skills / "keep-me" / "SKILL.md").is_file())

        # CCC hook stripped; Boost hook kept.
        settings = json.loads(self.settings.read_text(encoding="utf-8"))
        stop = settings["hooks"]["Stop"][0]["hooks"]
        commands = [h["command"] for h in stop]
        self.assertEqual(len(commands), 1)
        self.assertIn("boost-sync.sh", commands[0])
        self.assertTrue(all("command-center/hooks/" not in c for c in commands))

    def test_without_purge_keeps_state_dirs(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.watchtower.exists())
        self.assertFalse(self.plist.exists())
        self.assertTrue(self.data.is_dir())
        self.assertTrue(self.wt_state.is_dir())

    def test_keeps_unrelated_cli_symlink(self):
        other = self.home / ".local" / "bin" / "ccc"
        other.unlink(missing_ok=True)
        other.symlink_to("/usr/local/bin/something-else")
        result = self._run("--purge-data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(other.is_symlink())
        self.assertEqual(os.readlink(other), "/usr/local/bin/something-else")


if __name__ == "__main__":
    unittest.main()
