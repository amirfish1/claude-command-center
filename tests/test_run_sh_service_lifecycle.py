"""run.sh launchd lifecycle: label migration, --start-service, --stop-service.

Runs the real run.sh against a temp HOME with fake `launchctl` and `uname`
on PATH, so nothing touches the machine's real LaunchAgents.
"""

import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUN_SH = REPO / "run.sh"
LEGACY = "com.github.claude-command-center"
SERVER = "com.github.claude-command-center.server"
WORKER = "com.github.claude-command-center.worker"

FAKE_LAUNCHCTL = r"""#!/bin/bash
echo "$*" >> "$FAKE_STATE/log"
case "$1" in
  help) echo "bootstrap bootout kickstart print enable" ;;
  print) [ -f "$FAKE_STATE/loaded.$(basename "$2")" ] ;;
  bootstrap)
    label="$(basename "$3" .plist)"
    touch "$FAKE_STATE/loaded.$label"
    if [ "$label" = "${FAKE_LISTEN_LABEL:-}" ]; then
      python3 -c 'import socket,sys,time
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(); time.sleep(30)' "$PORT" >/dev/null 2>&1 &
      echo $! > "$FAKE_STATE/listener.pid"
    fi
    ;;
  bootout)
    label="$(basename "$2")"
    [ -f "$FAKE_STATE/loaded.$label" ] || exit 3
    rm -f "$FAKE_STATE/loaded.$label"
    ;;
  *) exit 0 ;;
esac
"""

FAKE_UNAME = """#!/bin/bash
if [ "${1:-}" = "-s" ]; then echo Darwin; else /usr/bin/uname "$@"; fi
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunShServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.agents = self.home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        self.state = self.root / "state"
        self.state.mkdir()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in (("launchctl", FAKE_LAUNCHCTL), ("uname", FAKE_UNAME)):
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        self.port = _free_port()
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "FAKE_STATE": str(self.state),
            "PORT": str(self.port),
        }

    def tearDown(self):
        pid_file = self.state / "listener.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), 9)
            except (OSError, ValueError):
                pass
        self._tmp.cleanup()

    def run_sh(self, *args, **extra_env):
        return subprocess.run(
            ["bash", str(RUN_SH), *args],
            env={**self.env, **extra_env},
            capture_output=True,
            text=True,
            timeout=60,
        )

    def log(self):
        path = self.state / "log"
        return path.read_text().splitlines() if path.exists() else []

    def test_start_service_reports_not_installed_with_exit_3(self):
        proc = self.run_sh("--start-service")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("not installed", proc.stderr)

    def test_start_service_bootstraps_job_and_waits_for_bind(self):
        (self.agents / f"{SERVER}.plist").write_text("<plist/>")
        (self.agents / f"{WORKER}.plist").write_text("<plist/>")
        proc = self.run_sh("--start-service", FAKE_LISTEN_LABEL=SERVER)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"Service started on port {self.port}", proc.stdout)
        log = self.log()
        self.assertTrue(any(line.startswith("bootstrap") and f"{SERVER}.plist" in line for line in log))
        self.assertTrue(any(line.startswith("bootstrap") and f"{WORKER}.plist" in line for line in log))

    def test_start_service_is_a_no_op_when_port_already_serves(self):
        (self.agents / f"{SERVER}.plist").write_text("<plist/>")
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", self.port))
            s.listen()
            proc = self.run_sh("--start-service")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(any(line.startswith(("bootstrap", "kickstart")) for line in self.log()))

    def test_start_service_kickstarts_a_loaded_job(self):
        (self.agents / f"{SERVER}.plist").write_text("<plist/>")
        (self.state / f"loaded.{SERVER}").touch()
        proc = self.run_sh("--start-service")
        # Nothing binds the port in this fake, so it reports the timeout.
        self.assertEqual(proc.returncode, 1)
        self.assertIn("did not bind", proc.stderr)
        self.assertIn(f"kickstart gui/{os.getuid()}/{SERVER}", self.log())

    def test_stop_service_boots_out_dashboard_only(self):
        (self.agents / f"{SERVER}.plist").write_text("<plist/>")
        (self.state / f"loaded.{SERVER}").touch()
        (self.state / f"loaded.{WORKER}").touch()
        proc = self.run_sh("--stop-service")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((self.state / f"loaded.{SERVER}").exists())
        self.assertTrue((self.state / f"loaded.{WORKER}").exists())
        self.assertTrue((self.agents / f"{SERVER}.plist").exists())

    def test_legacy_plist_migration_is_idempotent_and_keeps_backup(self):
        (self.agents / f"{LEGACY}.plist").write_text("legacy-plist")
        (self.state / f"loaded.{LEGACY}").touch()
        status = self.run_sh("--service-status")
        self.assertIn("legacy:", status.stdout)

        first = self.run_sh("--uninstall-service")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Migrating", first.stdout)
        backup = self.home / ".claude" / "command-center" / "service-backup" / f"{LEGACY}.plist"
        self.assertEqual(backup.read_text(), "legacy-plist")
        self.assertFalse((self.agents / f"{LEGACY}.plist").exists())
        self.assertFalse((self.state / f"loaded.{LEGACY}").exists())

        second = self.run_sh("--uninstall-service")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("Migrating", second.stdout)
        self.assertEqual(backup.read_text(), "legacy-plist")

    def test_isolated_label_override_never_touches_default_labels(self):
        (self.agents / f"{LEGACY}.plist").write_text("legacy-plist")
        (self.agents / "test.ccc.server.plist").write_text("<plist/>")
        (self.state / "loaded.test.ccc.server").touch()
        proc = self.run_sh("--stop-service", CCC_SERVICE_LABEL="test.ccc.server")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((self.state / "loaded.test.ccc.server").exists())
        self.assertTrue((self.agents / f"{LEGACY}.plist").exists())
        self.assertFalse(any(LEGACY in line for line in self.log()))


if __name__ == "__main__":
    unittest.main()
