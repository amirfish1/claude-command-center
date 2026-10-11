"""Tests for systemd unit configurations."""

import os
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestSystemdUnits(unittest.TestCase):
    def test_ccc_service_definition(self):
        service_file = REPO_ROOT / "systemd" / "ccc.service"
        self.assertTrue(service_file.exists(), "systemd/ccc.service must exist")
        content = service_file.read_text(encoding="utf-8")

        self.assertIn("User=hermes", content)
        self.assertIn("Group=hermes", content)
        self.assertIn("Environment=PORT=8091", content)
        self.assertIn("MemoryMax=8G", content)
        self.assertIn("KillMode=process", content)
        self.assertIn("multi-user.target", content)

        # Ensure documentation comment exists explaining the 8090 collision
        self.assertIn("8090", content)
        self.assertIn("Hermes agent gateway", content)
        self.assertIn("DECISION_INBOX_URL", content)

    def test_ccc_worker_service_definition(self):
        worker_file = REPO_ROOT / "systemd" / "ccc-worker.service"
        self.assertTrue(worker_file.exists(), "systemd/ccc-worker.service must exist")
        content = worker_file.read_text(encoding="utf-8")

        self.assertIn("User=hermes", content)
        self.assertIn("Group=hermes", content)
        self.assertIn("MemoryMax=4G", content)
        self.assertIn("KillMode=process", content)
        self.assertIn("ccc_worker.py", content)

    def test_install_script(self):
        install_script = REPO_ROOT / "systemd" / "install.sh"
        self.assertTrue(install_script.exists(), "systemd/install.sh must exist")
        self.assertTrue(os.access(install_script, os.X_OK), "install.sh must be executable")
        content = install_script.read_text(encoding="utf-8")
        self.assertIn("/etc/systemd/system/ccc.service", content)
        self.assertIn("/etc/systemd/system/ccc-worker.service", content)
        self.assertIn("systemctl daemon-reload", content)

    def test_leaderboard_weekly_user_timer(self):
        service = (REPO_ROOT / "systemd" / "ccc-leaderboard-weekly.service").read_text(encoding="utf-8")
        timer = (REPO_ROOT / "systemd" / "ccc-leaderboard-weekly.timer").read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", service)
        self.assertIn("scripts/leaderboard-weekly.py run", service)
        self.assertNotIn("publish-leaderboard.sh --apply", service)
        self.assertNotIn("User=", service)  # a user unit, no root needed
        self.assertIn("OnCalendar=Mon", timer)
        self.assertIn("Persistent=true", timer)
        install = REPO_ROOT / "systemd" / "install-leaderboard-weekly.sh"
        self.assertTrue(os.access(install, os.X_OK))
        self.assertIn("systemctl --user enable --now ccc-leaderboard-weekly.timer",
                      install.read_text(encoding="utf-8"))
        self.assertTrue((REPO_ROOT / "scripts" / "leaderboard-weekly.py").exists())


if __name__ == "__main__":
    unittest.main()
