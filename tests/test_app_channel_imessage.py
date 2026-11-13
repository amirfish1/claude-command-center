import importlib.util
import pathlib
import unittest
from unittest import mock

_p = pathlib.Path(__file__).resolve().parent.parent / "ccc_server" / "app_channels" / "imessage.py"
_s = importlib.util.spec_from_file_location("imessage_under_test", _p)
im = importlib.util.module_from_spec(_s)
_s.loader.exec_module(im)

CFG = {"enabled": True, "to": "+15555550100", "ssh_host": "macbox"}


def ok_run(*a, **k):
    return mock.Mock(returncode=0, stdout="", stderr="")


class T(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(im.configured({}))
        self.assertFalse(im.send({}, "t", "b", "")[0])

    def test_linux_needs_host(self):
        with mock.patch.object(im, "_system", return_value="Linux"):
            self.assertTrue(im.configured(CFG))
            self.assertFalse(im.configured({**CFG, "ssh_host": ""}))
            self.assertFalse(im.configured({**CFG, "ssh_host": "-oProxy=x"}))

    def test_bad_handle(self):
        with mock.patch.object(im, "_system", return_value="Darwin"):
            self.assertFalse(im.configured({**CFG, "to": "a b; rm"}))

    def test_refuses_other_recipient(self):
        with mock.patch.object(im, "_system", return_value="Darwin"), \
                mock.patch.object(im.subprocess, "run") as run:
            ok, err = im.send(CFG, "t", "b", "", to="+15555550199")
            self.assertFalse(ok)
            self.assertIn("owner", err)
            run.assert_not_called()

    def test_mac_send(self):
        with mock.patch.object(im, "_system", return_value="Darwin"), \
                mock.patch.object(im.subprocess, "run", side_effect=ok_run) as run:
            self.assertEqual(im.send(CFG, "Hi", "there", "http://x"), (True, ""))
            cmd = run.call_args[0][0]
            self.assertEqual(cmd[0], "osascript")
            self.assertEqual(cmd[-2], "+15555550100")
            self.assertEqual(cmd[-1], "Hi\nthere\nhttp://x")

    def test_linux_ssh_quotes(self):
        with mock.patch.object(im, "_system", return_value="Linux"), \
                mock.patch.object(im.subprocess, "run", side_effect=ok_run) as run:
            im.send(CFG, "it's", "b; rm -rf /", "")
            cmd = run.call_args[0][0]
            self.assertEqual(cmd[0], "ssh")
            self.assertIn("macbox", cmd)
            self.assertIn("'\"'\"'", cmd[-1])

    def test_dry_run_sends_nothing(self):
        with mock.patch.object(im, "_system", return_value="Darwin"), \
                mock.patch.object(im.subprocess, "run") as run:
            self.assertEqual(im.send(CFG, "t", "b", "", dry_run=True), (True, ""))
            run.assert_not_called()

    def test_failure_reported(self):
        bad = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch.object(im, "_system", return_value="Darwin"), \
                mock.patch.object(im.subprocess, "run", return_value=bad):
            ok, err = im.send(CFG, "t", "b", "")
            self.assertFalse(ok)
            self.assertIn("boom", err)


if __name__ == "__main__":
    unittest.main()
