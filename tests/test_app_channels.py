import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ccc_server import app_channels, app_routines
from ccc_server.app_channels import whatsapp

SELF = "15551230000@s.whatsapp.net"


class ChannelsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = Path(self.tmp) / "c.json"
        p = mock.patch.dict(os.environ, {"CCC_APP_CHANNELS_FILE": str(self.cfg)})
        p.start()
        self.addCleanup(p.stop)

    def write(self, d):
        self.cfg.write_text(json.dumps(d))

    def test_nothing_by_default(self):
        self.assertEqual(app_channels.deliver("t"), {})
        self.write({"whatsapp": {"chat_id": SELF}})
        self.assertEqual(app_channels.deliver("t"), {})

    def test_dry_run_sends_nothing(self):
        self.write({"whatsapp": {"enabled": True, "chat_id": SELF}})
        with mock.patch("urllib.request.urlopen") as u:
            self.assertEqual(app_channels.deliver("t", "b", "/x"), {"whatsapp": (True, None)})
            u.assert_not_called()

    def test_groups_refused(self):
        for bad in ("123-456@g.us", "status@broadcast", "1@newsletter", "", None):
            self.assertFalse(whatsapp.configured({"chat_id": bad}))
            self.assertFalse(whatsapp.send({"chat_id": bad, "dry_run": False}, "t", "", "")[0])

    def test_live_send_payload(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b'{"success": true}'
        with mock.patch("urllib.request.urlopen", return_value=resp) as u:
            ok, err = whatsapp.send({"chat_id": SELF, "dry_run": False}, "Hi", "body", "/view/a")
        self.assertTrue(ok)
        req = u.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/send"))
        self.assertEqual(json.loads(req.data)["chatId"], SELF)

    def test_bridge_failure_is_reported_not_raised(self):
        self.write({"whatsapp": {"enabled": True, "chat_id": SELF, "dry_run": False}})
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            ok, err = app_channels.deliver("t")["whatsapp"]
        self.assertFalse(ok)

    def test_crashing_channel_contained(self):
        mod = types.SimpleNamespace(NAME="boom", configured=lambda c: True,
                                    send=mock.Mock(side_effect=RuntimeError("x")))
        self.write({"boom": {"enabled": True}})
        with mock.patch.object(app_channels, "_modules", return_value=[mod]):
            self.assertFalse(app_channels.deliver("t")["boom"][0])

    def test_notify_hook_survives_channel_crash(self):
        with mock.patch.object(app_channels, "deliver", side_effect=RuntimeError("x")), \
             mock.patch.object(app_routines, "_post_json") as post:
            app_routines._notify(8090, "ski", {"name": "R"}, {"title": "T"}, True)
            post.assert_called_once()

    def test_notify_hook_delivers_without_port(self):
        with mock.patch.object(app_channels, "deliver") as d:
            app_routines._notify(None, "ski", {"name": "R"}, {"title": "T", "body": "B"}, False)
            d.assert_called_once_with("T", "B", "/view/ski")


if __name__ == "__main__":
    unittest.main()
