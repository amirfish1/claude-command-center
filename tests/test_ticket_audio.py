"""Ticket audio transcription: format sniffing, limits, and a real decode smoke."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from ccc_server import ticket_audio as ta

HAVE = ta.availability()[0] and shutil.which("ffmpeg")


def _tone(fmt_args, seconds):
    d = tempfile.mkdtemp()
    out = os.path.join(d, "x")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    *fmt_args, out], check=True)
    data = open(out, "rb").read()
    shutil.rmtree(d)
    return data


class Handler:
    def __init__(self, path, body=b"", length=None):
        self.path = path
        self.headers = {"Content-Length": str(len(body) if length is None else length)}
        self.rfile = io.BytesIO(body)
        self.sent = None
        self.close_connection = False

    def send_json(self, data, status=200):
        self.sent = (status, data)


class SniffTests(unittest.TestCase):
    def test_sniff(self):
        self.assertEqual(ta.sniff_format(b"\0\0\0\x20ftypM4A "), "mov")
        self.assertEqual(ta.sniff_format(b"\x1a\x45\xdf\xa3rest"), "matroska")
        self.assertIsNone(ta.sniff_format(b"#EXTM3U\nhttp://evil/x"))
        self.assertIsNone(ta.sniff_format(b"ffconcat version 1.0\nfile /etc/passwd"))

    def test_model_env(self):
        with mock.patch.dict(os.environ, {"CCC_WHISPER_MODEL": "/nope/m.bin"}):
            ok, why = ta.availability()
            self.assertFalse(ok)

    def test_errors(self):
        h = Handler("/api/ticket-audio/transcribe", b"", 0)
        ta.handle(h, "POST"); self.assertEqual(h.sent[0], 400)
        h = Handler("/api/ticket-audio/transcribe", b"", ta.MAX_BYTES + 1)
        ta.handle(h, "POST"); self.assertEqual(h.sent[0], 413)
        h = Handler("/api/ticket-audio/transcribe", b"#EXTM3U\nhttp://x")
        ta.handle(h, "POST"); self.assertEqual(h.sent[0], 400)

    def test_unavailable_503(self):
        with mock.patch.object(ta, "availability", return_value=(False, "no model")):
            h = Handler("/api/ticket-audio/transcribe", b"\0\0\0\x20ftypM4A rest")
            ta.handle(h, "POST")
            self.assertEqual(h.sent[0], 503)
            h = Handler("/api/ticket-audio/status")
            ta.handle(h, "GET")
            self.assertEqual(h.sent[1]["available"], False)


@unittest.skipUnless(HAVE, "needs ffmpeg, whisper-cli and a local model")
class RealDecodeTests(unittest.TestCase):
    def test_mp4_and_webm_decode(self):
        for args in (["-c:a", "aac", "-f", "mp4", "-movflags", "+frag_keyframe+empty_moov"],
                     ["-c:a", "libopus", "-f", "webm"]):
            text = ta.transcribe(_tone(args, 2))
            self.assertIsInstance(text, str)

    def test_too_long(self):
        data = _tone(["-c:a", "libopus", "-f", "webm"], 125)
        with self.assertRaises(ta.AudioError) as c:
            ta.transcribe(data)
        self.assertEqual(c.exception.status, 400)

    def test_tempdir_cleaned(self):
        before = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("ccc-ticket-audio-")}
        ta.transcribe(_tone(["-c:a", "libopus", "-f", "webm"], 1))
        after = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("ccc-ticket-audio-")}
        self.assertEqual(before, after)

    def test_garbage_rejected(self):
        with self.assertRaises(ta.AudioError) as c:
            ta.transcribe(b"\0\0\0\x20ftyp" + b"garbage" * 100)
        self.assertEqual(c.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
