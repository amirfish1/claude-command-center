"""Regression contract for the fleet-verifier browser fallback."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "fleet-verify.md"


class FleetVerifySkillTests(unittest.TestCase):
    def test_uses_puppeteer_before_optional_devtools(self):
        source = SKILL.read_text(encoding="utf-8")

        self.assertIn(
            "If gstack browse is unavailable, run CCC's `node snapshot.js` first",
            source,
        )
        self.assertIn(
            "only use chrome-devtools after `curl -fsS --max-time 2", source,
        )
        self.assertLess(
            source.index("node snapshot.js` first"),
            source.index("chrome-devtools after"),
        )

