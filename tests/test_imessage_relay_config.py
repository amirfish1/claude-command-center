"""The iMessage relay reads its contact and session from a local file.

The contact is personal data, so it must never be hardcoded in the public
source; with no config file the poller exits without touching chat.db.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server

ROOT = Path(__file__).resolve().parent.parent


class IMessageRelayConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self._tmp.name) / "imessage-relay.json"
        patcher = mock.patch.object(server, "_CHUCK_CONFIG_FILE", self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        for name in ("_CHUCK_PHONE", "_CHUCK_SESSION_ID"):
            p = mock.patch.object(server, name, "")
            p.start()
            self.addCleanup(p.stop)

    def test_missing_config_disables_poller(self):
        self.assertFalse(server._chuck_load_config())
        with mock.patch.object(server, "_chuck_fetch_new_messages") as fetch, \
                mock.patch.object(server.time, "sleep") as sleep:
            server._chuck_imessage_poller()
        fetch.assert_not_called()
        sleep.assert_not_called()

    def test_incomplete_config_disables_poller(self):
        self.cfg.write_text(json.dumps({"phone": "+15550000000"}))
        self.assertFalse(server._chuck_load_config())

    def test_config_sets_contact_and_session(self):
        self.cfg.write_text(json.dumps(
            {"phone": "+15550000000", "session_id": "00000000-0000-0000-0000-000000000000"}))
        self.assertTrue(server._chuck_load_config())
        self.assertEqual(server._CHUCK_PHONE, "+15550000000")
        self.assertEqual(server._CHUCK_SESSION_ID, "00000000-0000-0000-0000-000000000000")

    def test_no_phone_number_in_server_source(self):
        src = (ROOT / "server.py").read_text()
        self.assertIsNone(re.search(r'"\+1\d{10}"', src))


if __name__ == "__main__":
    unittest.main()
