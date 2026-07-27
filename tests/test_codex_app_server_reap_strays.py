"""Regression coverage for the codex app-server stray-child backstop.

See _codex_app_server_reap_stray_children: _CodexAppServerTransport.close()
can give up silently if a process refuses to die within its wait timeouts,
leaving an untracked orphan behind. This backstop is the last line of
defense against that orphan accumulating across replacements.
"""

import importlib
import unittest
from unittest import mock


class ReapStrayCodexAppServerChildrenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def _ps_output(self, my_pid, rows):
        header = "  PID  PPID COMMAND\n"
        lines = [
            f"{pid} {ppid} {cmd}" for pid, ppid, cmd in rows
        ]
        return header + "\n".join(lines) + ("\n" if lines else "")

    def test_kills_untracked_app_server_child_of_this_process(self):
        server = self.server
        my_pid = 4242
        rows = [
            (5001, my_pid, "/usr/local/bin/codex -c x app-server --listen stdio://"),
        ]
        with mock.patch.object(server.os, "getpid", return_value=my_pid), \
             mock.patch.object(server, "_CODEX_APP_SERVER_PROC", None), \
             mock.patch.object(server.subprocess, "check_output",
                               return_value=self._ps_output(my_pid, rows)), \
             mock.patch.object(server.os, "killpg") as killpg, \
             mock.patch.object(server, "_log_activity") as log_activity:
            server._codex_app_server_reap_stray_children()
        killpg.assert_called_once_with(5001, server.signal.SIGTERM)
        log_activity.assert_called_once()
        self.assertEqual(log_activity.call_args.args[0], "app-server")
        self.assertEqual(log_activity.call_args.args[1], "REAP")
        self.assertIn("5001", log_activity.call_args.args[2])

    def test_leaves_the_currently_tracked_pid_alone(self):
        server = self.server
        my_pid = 4242
        tracked_proc = mock.Mock(pid=5001)
        rows = [
            (5001, my_pid, "/usr/local/bin/codex -c x app-server --listen stdio://"),
        ]
        with mock.patch.object(server.os, "getpid", return_value=my_pid), \
             mock.patch.object(server, "_CODEX_APP_SERVER_PROC", tracked_proc), \
             mock.patch.object(server.subprocess, "check_output",
                               return_value=self._ps_output(my_pid, rows)), \
             mock.patch.object(server.os, "killpg") as killpg:
            server._codex_app_server_reap_stray_children()
        killpg.assert_not_called()

    def test_ignores_children_that_are_not_the_app_server(self):
        server = self.server
        my_pid = 4242
        rows = [
            (6001, my_pid, "npm exec mcp-remote@latest https://mcp.posthog.com/mcp"),
        ]
        with mock.patch.object(server.os, "getpid", return_value=my_pid), \
             mock.patch.object(server, "_CODEX_APP_SERVER_PROC", None), \
             mock.patch.object(server.subprocess, "check_output",
                               return_value=self._ps_output(my_pid, rows)), \
             mock.patch.object(server.os, "killpg") as killpg:
            server._codex_app_server_reap_stray_children()
        killpg.assert_not_called()

    def test_ignores_matching_command_owned_by_someone_else(self):
        server = self.server
        my_pid = 4242
        rows = [
            (7001, 1, "/usr/local/bin/codex -c x app-server --listen stdio://"),
        ]
        with mock.patch.object(server.os, "getpid", return_value=my_pid), \
             mock.patch.object(server, "_CODEX_APP_SERVER_PROC", None), \
             mock.patch.object(server.subprocess, "check_output",
                               return_value=self._ps_output(my_pid, rows)), \
             mock.patch.object(server.os, "killpg") as killpg:
            server._codex_app_server_reap_stray_children()
        killpg.assert_not_called()

    def test_swallows_ps_failure(self):
        server = self.server
        with mock.patch.object(server.subprocess, "check_output",
                               side_effect=OSError("no ps")), \
             mock.patch.object(server.os, "killpg") as killpg:
            server._codex_app_server_reap_stray_children()
        killpg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
