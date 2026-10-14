import unittest
from unittest import mock

import server
from ccc_server import engines

SID = "16751525-8b2f-4206-8785-e31502cc9940"


def _ps(lines):
    return mock.Mock(returncode=0, stdout="\n".join(lines))


class TerminalHolderTest(unittest.TestCase):
    def test_detects_equals_form_on_tty(self):
        out = _ps([f"19324 ttys001 agy --dangerously-skip-permissions --conversation={SID}"])
        with mock.patch.object(engines.subprocess, "run", return_value=out):
            self.assertEqual(engines._antigravity_terminal_holder_pid(SID), 19324)

    def test_ignores_headless_print_resume(self):
        out = _ps([f"11 ?? agy --conversation {SID} -p hello"])
        with mock.patch.object(engines.subprocess, "run", return_value=out):
            self.assertIsNone(engines._antigravity_terminal_holder_pid(SID))

    def test_ignores_other_conversation(self):
        out = _ps(["12 ttys002 agy --conversation=other"])
        with mock.patch.object(engines.subprocess, "run", return_value=out):
            self.assertIsNone(engines._antigravity_terminal_holder_pid(SID))


if __name__ == "__main__":
    unittest.main()
