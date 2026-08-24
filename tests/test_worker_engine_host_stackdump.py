"""Regression coverage: the worker process must install the stack-dump handler.

server.py's main() calls _install_python_stack_dump_handler() so SIGUSR2
produces an all-thread traceback -- but ccc_worker.py never calls main(), it
only imports server as a library via EngineHost._legacy(). Codex app-server
spawning, liveness checks, and replacement all happen inside that worker
process (confirmed live: pid ownership traced the churning app-server child
to ccc_worker.py, not the dashboard). Without this, diagnostics like
_codex_app_server_dump_stacks_on_liveness_miss are silent no-ops in the one
process where they're actually needed.
"""

import importlib
import unittest
from unittest import mock

from worker_engines import EngineHost


class FakeRuntime:
    def __init__(self):
        self.ledger = mock.Mock()


class LegacyStackDumpInstallTests(unittest.TestCase):
    def test_legacy_installs_the_stack_dump_handler_for_the_worker_process(self):
        server = importlib.import_module("server")
        host = EngineHost(FakeRuntime())
        with mock.patch.object(server, "_install_python_stack_dump_handler") as install, \
             mock.patch.object(server, "_reattach_spawned_orphans"):
            host._legacy()
        install.assert_called_once_with()

    def test_legacy_only_installs_once_across_repeated_calls(self):
        server = importlib.import_module("server")
        host = EngineHost(FakeRuntime())
        with mock.patch.object(server, "_install_python_stack_dump_handler") as install, \
             mock.patch.object(server, "_reattach_spawned_orphans"):
            host._legacy()
            host._legacy()
        install.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
