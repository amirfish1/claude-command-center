"""Reloading on a phone from the conversation list must stay on the list.

The archive-mode boot path (setArchiveMode) restores the saved pane's
conversation itself. It must count as a boot restore, or
selectConversation() slides `.main` over the list after every reload.
"""
import pathlib
import re
import unittest

APP_JS = pathlib.Path(__file__).resolve().parent.parent / "static" / "app.js"


class MobileBootRestoreTest(unittest.TestCase):
    def test_archive_mode_restore_is_a_boot_restore(self):
        src = APP_JS.read_text(encoding="utf-8")
        start = src.index("async function setArchiveMode()")
        body = src[start:src.index("function wireArchiveMode()", start)]
        self.assertRegex(
            body,
            re.compile(
                r"_qfBootRestoreDepth\+\+;\s*try\s*\{\s*"
                r"await selectConversation\(savedConversationId, savedPane\.id\);\s*"
                r"\}\s*finally\s*\{\s*_qfBootRestoreDepth--;",
            ),
        )


if __name__ == "__main__":
    unittest.main()
