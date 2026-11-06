import pathlib
import re
import unittest


STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"


class TestMobileMenuNoRestart(unittest.TestCase):
    """The mobile menu's "Restart session" item was removed; no UI copy may
    send a user looking for it."""

    def test_no_restart_session_label_or_handler(self):
        for name in ("index.html", "app.js"):
            text = (STATIC / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"Restart session", text), name)
            self.assertNotIn("_simpleForceRestart", text, name)


if __name__ == "__main__":
    unittest.main()
