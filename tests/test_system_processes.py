"""Scoring contract for GET /api/system/processes.

The audit runs one `ps`, one `lsof`, one `launchctl list` per snapshot and
scores every row from those three strings alone — never a subprocess per
row. These tests feed synthetic tables through `_sys_run` and pin the
signals that matter: a leaked dev-server tree lights up as one leak rooted at
its PPID-1 ancestor, launchd-supervised daemons never read as orphans, and
automation browsers lose the GUI shield only when their launcher is gone.
"""

import importlib
import os
import tempfile
import unittest
from unittest import mock


PS_FMT = "{pid} {ppid} {rss} {cpu} {etime} {cputime} {stat} {tty} {cmd}"


def ps_row(pid, ppid, cmd, cpu=0.0, etime="01:00:00", cputime="0:01.00", stat="S", tty="??", rss=10240):
    return PS_FMT.format(pid=pid, ppid=ppid, rss=rss, cpu=cpu, etime=etime, cputime=cputime, stat=stat, tty=tty, cmd=cmd)


def lsof_block(pid, cwd="/", fd0=None, fd0_type="CHR"):
    lines = ["p%d" % pid, "fcwd", "tDIR", "n%s" % cwd]
    if fd0:
        lines += ["f0", "t%s" % fd0_type, "n%s" % fd0]
    return "\n".join(lines)


class ProcessAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def _build(self, ps_rows, lsof_blocks=(), launchd=(), ccc_sessions=()):
        server = self.server
        ps_out = "\n".join(ps_rows) + "\n"
        lsof_out = "\n".join(lsof_blocks) + "\n"
        launchctl_out = "PID\tStatus\tLabel\n" + "".join("%d\t0\t%s\n" % (pid, label) for pid, label in launchd)

        def fake_run(cmd, timeout=3):
            base = os.path.basename(cmd[0])
            if base == "ps":
                return ps_out
            if base == "lsof":
                return lsof_out
            if base == "launchctl":
                return launchctl_out
            return ""

        with mock.patch.object(server, "_sys_run", side_effect=fake_run), \
             mock.patch.object(server, "_sys_ccc_session_pids", return_value=set(ccc_sessions)), \
             mock.patch.object(server.platform, "system", return_value="Darwin"), \
             mock.patch.object(server.os, "getpid", return_value=999901), \
             mock.patch.object(server.os, "getppid", return_value=999900):
            data = server._build_system_processes_uncached()
        return {p["pid"]: p for p in data["processes"]}, data

    # ── leaked dev-server tree ──────────────────────────────────────────

    def test_leaked_next_dev_tree_is_one_leak_rooted_at_the_orphan(self):
        rows = [
            ps_row(100, 1, "npm exec next dev --port 3007", etime="03:12:00"),
            ps_row(101, 100, "node /repo/node_modules/.bin/next dev --port 3007", etime="03:11:50"),
            ps_row(102, 101, "next-server (v16.3.1)", cpu=93.8, etime="03:11:40", cputime="150:28.57", stat="R"),
            ps_row(103, 102, "node /repo/.next/dev/build/chunks/pool_entry-[turbopack-node].js 63822", etime="03:10:00"),
        ]
        by, _ = self._build(rows)
        root, srv = by[100], by[102]
        self.assertEqual(root["risk"], "critical", root["reasons"])
        self.assertIn("Orphaned (PPID 1)", root["reasons"])
        self.assertIn("Dev server, launcher gone", root["reasons"])
        self.assertIn("Owns runaway/stuck child (pid 102)", root["reasons"])
        self.assertEqual(srv["risk"], "critical", srv["reasons"])
        self.assertEqual(srv["tree_root"], 100)
        self.assertTrue(srv["tree_orphan"])
        self.assertTrue(any(r.startswith("Sustained high CPU") for r in srv["reasons"]))
        self.assertTrue(any(r.startswith("Spinning orphan") for r in srv["reasons"]))
        self.assertIn("Parent tree orphaned (root pid 100)", by[103]["reasons"])

    def test_same_tree_under_a_tty_shell_is_not_a_leak(self):
        rows = [
            ps_row(90, 1, "-zsh", tty="ttys001", stat="Ss"),
            ps_row(100, 90, "npm exec next dev --port 3007", etime="03:12:00"),
            ps_row(102, 100, "next-server (v16.3.1)", cpu=93.8, etime="03:11:40", cputime="150:28.57", stat="R"),
        ]
        by, _ = self._build(rows)
        self.assertIsNone(by[102]["tree_root"])
        self.assertNotIn("Dev server under orphaned tree", by[102]["reasons"])
        self.assertLess(by[100]["score"], 4.0, by[100]["reasons"])

    # ── launchd-supervised is anchored, not orphaned ─────────────────────

    def test_ssh_agent_under_launchd_is_safe(self):
        rows = [ps_row(3720, 1, "/usr/bin/ssh-agent -l", etime="03-08:46:03")]
        by, _ = self._build(rows, launchd=[(3720, "com.openssh.ssh-agent")])
        p = by[3720]
        self.assertEqual(p["risk"], "safe", p["reasons"])
        self.assertNotIn("Orphaned (PPID 1)", p["reasons"])
        self.assertNotIn("Running >48h", p["reasons"])
        self.assertEqual(p["launchd_label"], "com.openssh.ssh-agent")
        self.assertIn("launchd job: com.openssh.ssh-agent", p["shields"])

    def test_user_launchagent_python_server_is_not_a_ccc_component_but_is_safe(self):
        rows = [ps_row(3338, 1, "/opt/homebrew/bin/python3 /home/dev/tools/image-scanner/server.py", etime="03-08:46:03")]
        by, _ = self._build(rows, launchd=[(3338, "com.example.image-scanner")])
        p = by[3338]
        self.assertTrue(p["can_kill"])
        self.assertNotIn("CCC component", p["shields"])
        self.assertEqual(p["risk"], "safe", p["reasons"])

    def test_unsupervised_daemon_with_a_known_basename_is_shielded(self):
        rows = [ps_row(500, 1, "/opt/homebrew/bin/tmux -L main new -d", etime="02-00:00:00")]
        by, _ = self._build(rows)
        self.assertEqual(by[500]["risk"], "safe", by[500]["reasons"])
        self.assertIn("Known dev daemon", by[500]["shields"])

    def test_ssh_tunnel_is_a_known_daemon_but_stuck_git_ssh_is_not(self):
        rows = [
            ps_row(600, 1, "ssh -N -L 5432:localhost:5432 db-host", etime="05:00:00"),
            ps_row(601, 1, "ssh -o SendEnv=GIT_PROTOCOL git@github.com git-upload-pack repo", etime="05:00:00"),
        ]
        by, _ = self._build(rows)
        self.assertIn("Known dev daemon", by[600]["shields"])
        self.assertIn("Stuck git transport (ssh, >30m, no TTY)", by[601]["reasons"])
        self.assertGreaterEqual(by[601]["score"], 4.0)

    # ── CCC shield is this clone, not any server.py ──────────────────────

    def test_ccc_shield_matches_this_clone_and_the_launchd_labels(self):
        server = self.server
        here = os.path.dirname(os.path.abspath(server.__file__))
        rows = [
            ps_row(700, 1, "python3 %s/server.py" % here, etime="05:00:00"),
            ps_row(701, 1, "python3 /elsewhere/ccc_worker.py", etime="05:00:00"),
            ps_row(702, 1, "python3 /elsewhere/server.py", etime="05:00:00"),
        ]
        by, _ = self._build(rows, launchd=[(701, "com.github.claude-command-center.worker")])
        self.assertFalse(by[700]["can_kill"])
        self.assertFalse(by[701]["can_kill"])
        self.assertTrue(by[702]["can_kill"])

    def test_ccc_spawned_session_reparented_by_a_worker_restart_anchors_its_tree(self):
        rows = [
            ps_row(2100, 1, "/home/dev/.local/bin/claude -p --verbose --input-format stream-json", etime="00:30:00", stat="Ss"),
            ps_row(2101, 2100, "node /x/puppeteer-launch.js", etime="00:01:00"),
            ps_row(2102, 2101, "/x/chrome-headless-shell --headless --remote-debugging-pipe --type=renderer", etime="00:01:00"),
        ]
        by, _ = self._build(rows, ccc_sessions=[2100])
        self.assertIn("CCC session", by[2100]["shields"])
        self.assertNotIn("Orphaned (PPID 1)", by[2100]["reasons"])
        self.assertTrue(by[2100]["can_kill"])
        self.assertIsNone(by[2102]["tree_root"])
        self.assertEqual(by[2102]["risk"], "safe", by[2102]["reasons"])
        # The same tree with no registry entry is a real leak.
        by2, _ = self._build(rows)
        self.assertEqual(by2[2102]["tree_root"], 2100)
        self.assertGreaterEqual(by2[2102]["score"], 4.0)

    # ── browsers ─────────────────────────────────────────────────────────

    def test_automation_chrome_with_live_launcher_is_quiet(self):
        rows = [
            ps_row(799, 1, "-zsh", tty="ttys003", stat="Ss"),
            ps_row(800, 799, "node /x/chrome-devtools-mcp/build/src/index.js", etime="02:00:00"),
            ps_row(801, 800, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-pipe --user-data-dir=/var/folders/x/puppeteer_dev_chrome_profile-1", etime="02:00:00"),
            ps_row(802, 801, "/Applications/Google Chrome.app/Contents/Frameworks/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper --type=renderer", etime="02:00:00"),
        ]
        by, _ = self._build(rows)
        self.assertEqual(by[801]["risk"], "safe", by[801]["reasons"])
        self.assertEqual(by[802]["risk"], "safe", by[802]["reasons"])

    def test_headless_browser_whose_launcher_died_is_critical_despite_gui_path(self):
        rows = [
            ps_row(801, 1, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless --remote-debugging-pipe --user-data-dir=/var/folders/x/puppeteer_dev_chrome_profile-1", etime="02:00:00"),
            ps_row(802, 801, "/Applications/Google Chrome.app/Contents/Frameworks/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper --type=renderer --user-data-dir=/var/folders/x/puppeteer_dev_chrome_profile-1", etime="02:00:00"),
        ]
        by, _ = self._build(rows)
        self.assertEqual(by[801]["risk"], "critical", by[801]["reasons"])
        self.assertIn("Leaked headless browser (launcher gone)", by[801]["reasons"])
        self.assertNotIn("GUI app", by[801]["shields"])
        self.assertGreaterEqual(by[802]["score"], 4.0, by[802]["reasons"])
        self.assertEqual(by[802]["tree_root"], 801)

    def test_desktop_chrome_keeps_its_gui_shield(self):
        rows = [ps_row(900, 1, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", etime="02-00:00:00")]
        by, _ = self._build(rows)
        self.assertEqual(by[900]["risk"], "safe", by[900]["reasons"])

    def test_orphaned_electron_helper_is_flagged(self):
        rows = [ps_row(910, 1, "/Applications/Some.app/Contents/Frameworks/Some Helper.app/Contents/MacOS/Some Helper --type=utility", etime="05:00:00")]
        by, _ = self._build(rows)
        self.assertIn("Orphaned app helper (main app gone)", by[910]["reasons"])
        self.assertGreaterEqual(by[910]["score"], 4.0)

    # ── language servers / MCP / pool workers ────────────────────────────

    def test_orphaned_language_server_is_suspicious(self):
        rows = [ps_row(1000, 1, "node /opt/homebrew/lib/node_modules/typescript-language-server/lib/cli.mjs --stdio", etime="04:00:00")]
        by, _ = self._build(rows)
        self.assertIn("Orphaned language server (editor gone)", by[1000]["reasons"])
        self.assertGreaterEqual(by[1000]["score"], 4.0)

    def test_language_server_under_a_live_editor_is_quiet(self):
        rows = [
            ps_row(1100, 1, "/Applications/Zed.app/Contents/MacOS/zed", etime="04:00:00"),
            ps_row(1101, 1100, "/home/dev/.local/share/zed/rust-analyzer", etime="04:00:00"),
        ]
        by, _ = self._build(rows)
        self.assertEqual(by[1101]["risk"], "safe", by[1101]["reasons"])

    def test_orphaned_mcp_server_and_dead_declared_parent(self):
        rows = [
            ps_row(1200, 1, "node /x/node_modules/chrome-devtools-mcp/build/src/index.js", etime="02:00:00"),
            ps_row(1201, 1200, "node /x/chrome-devtools-mcp/build/src/telemetry/watchdog/main.js --parent-pid=424242", etime="02:00:00"),
        ]
        by, _ = self._build(rows)
        self.assertIn("Orphaned MCP server (host gone)", by[1200]["reasons"])
        self.assertIn("Declared parent pid 424242 is gone", by[1201]["reasons"])
        self.assertGreaterEqual(by[1201]["score"], 4.0)

    def test_orphaned_multiprocessing_worker_is_critical(self):
        rows = [ps_row(1300, 1, "python3 -c from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=5, pipe_handle=7) --multiprocessing-fork", etime="00:40:00")]
        by, _ = self._build(rows)
        self.assertIn("Orphaned multiprocessing worker (coordinator gone)", by[1300]["reasons"])
        self.assertGreaterEqual(by[1300]["score"], 6.5, by[1300]["reasons"])

    # ── git / zombies / worktrees ────────────────────────────────────────

    def test_stuck_git_fetch_without_tty_is_suspicious_but_fsmonitor_is_not(self):
        rows = [
            ps_row(1400, 1, "git fetch origin", etime="02:00:00"),
            ps_row(1401, 1, "git fsmonitor--daemon run --detach", etime="02-00:00:00"),
        ]
        by, _ = self._build(rows)
        self.assertIn("Stuck git/gh op (>30m, no TTY)", by[1400]["reasons"])
        self.assertGreaterEqual(by[1400]["score"], 4.0)
        self.assertNotIn("Stuck git/gh op (>30m, no TTY)", by[1401]["reasons"])
        self.assertIn("Known dev daemon", by[1401]["shields"])

    def test_orphaned_bash_pipeline_with_stuck_grep_is_flagged_at_the_root(self):
        rows = [
            ps_row(2000, 1, "/bin/bash -c cd /repo && grep -R -l pattern . | head -30", etime="01-22:00:00"),
            ps_row(2001, 2000, "grep -R -l pattern .", etime="01-22:00:00"),
            ps_row(2002, 2000, "head -30", etime="01-22:00:00"),
        ]
        by, _ = self._build(rows)
        self.assertIn("Stuck orphan CLI", by[2001]["reasons"])
        self.assertGreaterEqual(by[2001]["score"], 4.0, by[2001]["reasons"])
        self.assertIn("Owns runaway/stuck child (pid 2001)", by[2000]["reasons"])
        self.assertGreaterEqual(by[2000]["score"], 4.0, by[2000]["reasons"])

    def test_zombie_names_the_parent_to_reap_it(self):
        rows = [ps_row(1500, 3347, "<defunct>", etime="04:00:00", stat="Z")]
        by, _ = self._build(rows)
        self.assertIn("Zombie process (reap: signal parent pid 3347)", by[1500]["reasons"])
        self.assertIn("3347", by[1500]["kill_hint"])

    def test_process_in_a_removed_worktree_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = os.path.join(tmp, "repo-wt-feature")
            os.makedirs(os.path.join(wt, "apps", "web"))
            with open(os.path.join(wt, ".git"), "w") as f:
                f.write("gitdir: %s\n" % os.path.join(tmp, "repo", ".git", "worktrees", "repo-wt-feature"))
            rows = [ps_row(1600, 4242, "node dev-server.js", etime="00:20:00"), ps_row(4242, 1, "-zsh", tty="ttys002", stat="Ss")]
            by, _ = self._build(rows, lsof_blocks=[lsof_block(1600, cwd=os.path.join(wt, "apps", "web"))])
        self.assertIn("Worktree removed (gitdir gone)", by[1600]["reasons"])

    def test_deleted_cwd_still_scores(self):
        rows = [ps_row(1700, 4242, "node server.js", etime="00:20:00"), ps_row(4242, 1, "-zsh", tty="ttys002", stat="Ss")]
        by, _ = self._build(rows, lsof_blocks=[lsof_block(1700, cwd="/nonexistent/path/for/ccc/test")])
        self.assertIn("Deleted CWD", by[1700]["reasons"])
        self.assertFalse(by[1700]["cwd_exists"])
        self.assertGreaterEqual(by[1700]["score"], 4.0)

    # ── helpers ──────────────────────────────────────────────────────────

    def test_cputime_parser(self):
        parse = self.server._sys_parse_cputime
        self.assertAlmostEqual(parse("150:28.57"), 150 + 28.57 / 60, places=3)
        self.assertAlmostEqual(parse("0:01.00"), 1 / 60, places=3)
        self.assertAlmostEqual(parse("1:02:03"), 62.05, places=3)
        self.assertAlmostEqual(parse("1-00:00:00"), 1440.0, places=3)
        self.assertEqual(parse("garbage"), 0.0)

    def test_response_keeps_the_original_fields(self):
        rows = [ps_row(1800, 1, "sleep 100000", etime="01:00:00")]
        by, data = self._build(rows)
        for key in ("pid", "ppid", "cmd", "cmd_short", "cpu", "rss_mb", "etime", "etime_min", "stat", "tty",
                    "has_tty", "cwd", "cwd_exists", "fd0", "fd0_type", "fd0_exists", "score", "risk",
                    "reasons", "can_kill"):
            self.assertIn(key, by[1800])
        for key in ("ts", "total_count", "high_risk_count", "medium_risk_count", "processes"):
            self.assertIn(key, data)
        self.assertIn("Stuck orphan CLI", by[1800]["reasons"])


if __name__ == "__main__":
    unittest.main()
