"""Regression coverage for WatchTower comments left from the CCC dashboard."""

import importlib
import unittest
from types import ModuleType
from unittest import mock


class TestWatchtowerCommentNotification(unittest.TestCase):
    def test_notifies_the_claimed_worker_when_a_comment_is_added(self):
        server = importlib.import_module("server")
        item = {
            "ref": "CCC-617",
            "status": "in_progress",
            "claimed_session_id": "11111111-2222-3333-4444-555555555555",
        }
        queue = mock.Mock()
        queue.comment.return_value = item
        messages = mock.Mock()
        messages.send.return_value = {"ok": True, "transport": "fifo"}
        package = ModuleType("watchtower")
        package.messages = messages

        with mock.patch.object(server, "_q", queue), \
             mock.patch.object(server, "_WT_QUEUE_AVAILABLE", True), \
             mock.patch.dict("sys.modules", {
                 "watchtower": package,
                 "watchtower.messages": messages,
             }):
            returned, delivery = server._comment_queue_item_and_notify_worker(
                "CCC-617", "Please use the safer parser."
            )

        self.assertIs(returned, item)
        self.assertEqual(delivery, {"ok": True, "transport": "fifo"})
        messages.send.assert_called_once_with(
            "11111111-2222-3333-4444-555555555555",
            "[WATCHTOWER] A new comment was added to your claimed ticket "
            "CCC-617:\n\nPlease use the safer parser.",
            mode="steer",
        )


if __name__ == "__main__":
    unittest.main()
