"""Continuous read sessions (CCC-144): one mp3 stream for a whole message."""
import threading
import time
import unittest
from unittest import mock

from ccc_server import free_runtime as fr


def _fake_open(clips):
    """local_tts_open stand-in: text -> bytes, None for a clip that fails."""
    def open_(text, voice="", prio="fg"):
        data = clips.get(text)
        if data is None:
            return None, ""
        return fr._CachedClip(data), fr._KOKORO_LABEL + "af_heart"
    return open_


class TtsSessionTest(unittest.TestCase):
    def setUp(self):
        fr._TTS_SESSIONS.clear()
        patches = [
            mock.patch.object(fr, "local_tts_installed", return_value=True),
            mock.patch.object(fr, "_local_tts_start", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _wait_done(self, session):
        deadline = time.time() + 5
        while not session.info()["done"] and time.time() < deadline:
            time.sleep(0.01)
        return session.info()

    def test_clips_stream_back_to_back_with_marks_and_replay(self):
        clips = {"one": b"a" * 8000, "two": b"b" * 16000, "three": b"c" * 4000}
        with mock.patch.object(fr, "local_tts_open", side_effect=_fake_open(clips)):
            sid = fr.tts_session_create(["one", "two", "three"], "Kokoro: af_heart")
            session = fr.tts_session_get(sid)
            info = self._wait_done(session)
        self.assertEqual(info["marks"], [0.0, 1.0, 3.0])
        self.assertIsNone(info["failed"])
        self.assertFalse(info["cancelled"])
        first = b"".join(session.iter_bytes(timeout=1))
        second = b"".join(session.iter_bytes(timeout=1))
        self.assertEqual(first, clips["one"] + clips["two"] + clips["three"])
        self.assertEqual(first, second)   # Safari's probe/retry replays the same bytes

    def test_failed_clip_is_reported_with_its_index(self):
        clips = {"one": b"a" * 800}
        with mock.patch.object(fr, "local_tts_open", side_effect=_fake_open(clips)):
            session = fr.tts_session_get(fr.tts_session_create(["one", "missing", "three"]))
            info = self._wait_done(session)
        self.assertEqual(info["failed"], 1)
        self.assertEqual(b"".join(session.iter_bytes(timeout=1)), clips["one"])

    def test_new_read_cancels_older_session(self):
        gate = threading.Event()

        def slow_open(text, voice="", prio="fg"):
            gate.wait(2)
            return fr._CachedClip(b"x" * 100), fr._KOKORO_LABEL + "af_heart"

        with mock.patch.object(fr, "local_tts_open", side_effect=slow_open):
            old = fr.tts_session_get(fr.tts_session_create(["a", "b"]))
            new = fr.tts_session_get(fr.tts_session_create(["c"]))
            gate.set()
            self.assertTrue(self._wait_done(old)["cancelled"])
            self.assertFalse(self._wait_done(new)["cancelled"])

    def test_refuses_without_local_voice_or_texts(self):
        self.assertEqual(fr.tts_session_create([]), "")
        with mock.patch.object(fr, "local_tts_installed", return_value=False):
            self.assertEqual(fr.tts_session_create(["hi"]), "")
        self.assertIsNone(fr.tts_session_get("nope"))


if __name__ == "__main__":
    unittest.main()
