"""Regression coverage for WatchTower answers sent from the CCC dashboard."""

import importlib
import unittest
from types import ModuleType
from unittest import mock


class TestWatchtowerAnswerNotification(unittest.TestCase):
    def test_notifies_the_claimed_worker_when_an_answer_is_added(self):
        server = importlib.import_module("server")
        item = {
            "ref": "CCC-672",
            "status": "in_progress",
            "claimed_session_id": "11111111-2222-3333-4444-555555555555",
        }
        answer = mock.Mock(return_value=item)
        messages = mock.Mock()
        messages.send.return_value = {"ok": True, "transport": "fifo"}
        package = ModuleType("watchtower")
        package.messages = messages

        with mock.patch.object(server, "_queue_answer", answer), \
             mock.patch.dict("sys.modules", {
                 "watchtower": package,
                 "watchtower.messages": messages,
             }):
            returned, delivery = server._answer_queue_item_and_notify_worker(
                "CCC-672", "Workers should have their own effort default."
            )

        self.assertIs(returned, item)
        self.assertEqual(delivery, {"ok": True, "transport": "fifo"})
        answer.assert_called_once_with(
            "CCC-672", "Workers should have their own effort default.", session_id="ccc"
        )
        messages.send.assert_called_once_with(
            "11111111-2222-3333-4444-555555555555",
            "[WATCHTOWER] A human answered your blocked question on ticket "
            "CCC-672:\n\nWorkers should have their own effort default.\n\n"
            "Apply it, finish the ticket, and close it with `wt close CCC-672 "
            "--worker <your-id> --summary \"...\" --commit <SHA>` (or `--no-code` "
            "if no code changed). If it still cannot be resolved, run `wt block` "
            "again with the new open question.",
            mode="steer",
        )


if __name__ == "__main__":
    unittest.main()
