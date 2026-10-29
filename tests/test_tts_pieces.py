"""Kokoro piece sizing and voice-priority self-heal (CCC-144).

Long pieces made audio slower than a read plays (300 chars: 0.5x real time),
so reads paused between clips; short pieces keep generation ahead.
"""
import re
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import free_runtime

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local_tts_server.py"


def _splitter():
    # The script imports kokoro at module level; load only the splitter.
    src = SCRIPT.read_text(encoding="utf-8")
    ns = {"re": re}
    exec(src[src.index("FIRST_PIECE_CHARS ="):src.index("def _synth(")], ns)
    return ns


class PieceSplitTests(unittest.TestCase):
    def setUp(self):
        self.ns = _splitter()
        self.split = self.ns["split_for_stream"]

    def test_pieces_stay_short_and_cover_the_text(self):
        text = ("Yes, I sent two emails, and the center replied within the hour; the other one, "
                "which went to the county office about the permit renewal and the inspection, "
                "has not been answered yet. Next I will check again tomorrow. " * 3).strip()
        pieces = self.split(text)
        self.assertLessEqual(len(pieces[0]), self.ns["FIRST_PIECE_CHARS"])
        self.assertTrue(all(len(p) <= self.ns["PIECE_CHARS"] for p in pieces))
        self.assertEqual(" ".join(pieces).split(), text.split())

    def test_short_leftover_is_not_its_own_piece(self):
        text = ("Yes, I sent two emails, and the center replied within the hour; the other one, "
                "which went to the county office about the permit renewal, is still open.")
        self.assertTrue(all(len(p) >= 10 for p in self.split(text)))

    def test_server_rev_matches_dashboard_contract(self):
        rev = int(re.search(r"^SERVER_REV = (\d+)", SCRIPT.read_text(), re.M).group(1))
        self.assertEqual(rev, free_runtime._LOCAL_TTS_REV)

    def test_synth_caches_repeated_pieces_in_ram(self):
        import os
        import threading
        src = SCRIPT.read_text(encoding="utf-8")
        fake_kokoro = mock.MagicMock()
        fake_kokoro.create.return_value = ([0.1, 0.2, 0.3], 24000)
        ns = {"re": re, "os": os, "threading": threading, "time": __import__("time"), "_kokoro": fake_kokoro}
        exec(src[src.index("class _PriorityLock:"):src.index("class Handler(")], ns)
        synth = ns["_synth"]
        s1, r1 = synth("Repeated sentence.", "af_heart")
        s2, r2 = synth("Repeated sentence.", "af_heart")
        self.assertEqual((s1, r1), (s2, r2))
        fake_kokoro.create.assert_called_once()



class VoicePriorityTests(unittest.TestCase):
    def test_waiting_request_reuses_piece_made_while_it_waited(self):
        # A fg read re-requests a clip a bg prepare is making; the request
        # that waited for the lock must not make the same piece again.
        import os
        import threading
        import time
        src = SCRIPT.read_text(encoding="utf-8")
        started, release = threading.Event(), threading.Event()
        calls = []

        def create(text, **kw):
            calls.append(text)
            started.set()
            release.wait(5)
            return [0.1], 24000

        fake_kokoro = mock.MagicMock()
        fake_kokoro.create.side_effect = create
        ns = {"re": re, "os": os, "threading": threading, "time": time, "_kokoro": fake_kokoro}
        exec(src[src.index("class _PriorityLock:"):src.index("class Handler(")], ns)
        synth = ns["_synth"]
        bg = threading.Thread(target=synth, args=("Same piece.", "af_heart", False))
        bg.start()
        started.wait(5)
        fg = threading.Thread(target=synth, args=("Same piece.", "af_heart", True))
        fg.start()
        time.sleep(0.05)
        release.set()
        bg.join(5)
        fg.join(5)
        self.assertEqual(calls, ["Same piece."])

    def test_threads_scale_with_cores(self):
        with mock.patch("os.cpu_count", return_value=16):
            self.assertEqual(free_runtime._local_tts_threads(), 8)
        with mock.patch("os.cpu_count", return_value=4):
            self.assertEqual(free_runtime._local_tts_threads(), 2)

    def test_niced_voice_is_retired(self):
        health = {"ok": True, "nice": 10, "pid": 4242}
        with mock.patch.object(free_runtime, "_local_tts_health", return_value=health), \
                mock.patch("os.nice", return_value=0), \
                mock.patch.object(free_runtime, "_local_tts_retire") as retire:
            self.assertTrue(free_runtime._local_tts_renice_check())
        retire.assert_called_once_with(health)

    def test_voice_at_our_priority_is_kept(self):
        for health in ({"ok": True, "nice": 0}, {"ok": True}, {}):
            with mock.patch.object(free_runtime, "_local_tts_health", return_value=health), \
                    mock.patch("os.nice", return_value=0), \
                    mock.patch.object(free_runtime, "_local_tts_retire") as retire:
                self.assertFalse(free_runtime._local_tts_renice_check())
            retire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
