"""?tail=N on a Claude JSONL must read from the end of the file, not iterate it.

The click -> conversation-open path fetches /api/conversations/<id>?tail=120.
On a baked-bytes cache miss (any live session: the file changes every turn)
_parse_conversation_windowed iterated the whole file in Python to keep the
last N lines: 79-113 ms on 1-15 MB transcripts at load 10, 875 ms on a 15 MB
one under load (measured 2026-09-12). Line numbers, last_line, first_line and
truncated_before must stay exactly what the full iteration produced.
"""
import builtins
import importlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class _CountingFile:
    def __init__(self, fh, counter):
        self._fh = fh
        self._c = counter

    def read(self, n=-1):
        data = self._fh.read(n)
        self._c["bytes"] += len(data)
        return data

    def readline(self, *a):
        data = self._fh.readline(*a)
        self._c["bytes"] += len(data)
        return data

    def __iter__(self):
        for line in self._fh:
            self._c["bytes"] += len(line)
            yield line

    def __getattr__(self, name):
        return getattr(self._fh, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._fh.__exit__(*exc)


def _fresh_server():
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


def _line(i):
    return json.dumps({"type": "user", "uuid": f"u{i}", "timestamp": "2026-09-12T00:00:00Z",
                       "message": {"role": "user", "content": f"message number {i} " + "x" * 200}}) + "\n"


def _write(path, n, trailing_newline=True):
    text = "".join(_line(i) for i in range(1, n + 1))
    if not trailing_newline:
        text = text.rstrip("\n")
    path.write_text(text)


def _reference(server, path, tail):
    """The pre-optimisation algorithm: iterate everything, keep the last N."""
    import collections
    buf = collections.deque(maxlen=tail)
    total = 0
    with open(path, "r") as f:
        for line in f:
            total += 1
            buf.append((total, line))
    return total, [ln for ln, _ in buf]


import unittest


class TestTailReadsFromEnd(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "conv.jsonl"
        self.counter = {"bytes": 0}
        real_open = builtins.open
        counter = self.counter

        def counting_open(file, *a, **k):
            fh = real_open(file, *a, **k)
            if str(file) == str(self.path):
                return _CountingFile(fh, counter)
            return fh

        self.server.open = counting_open
        self.addCleanup(lambda: delattr(self.server, "open"))

    def _tail(self, tail):
        return self.server._parse_conversation_windowed("c1", self.path, tail, None)

    def test_tail_reads_a_small_suffix_of_a_big_file(self):
        _write(self.path, 5000)
        size = self.path.stat().st_size
        # First sight of the file: one C-speed newline count over the whole
        # file is allowed (absolute line numbers need it), plus the tail.
        self.counter["bytes"] = 0
        self._tail(50)
        self.assertLess(self.counter["bytes"], size + 2 * self.server._TAIL_READ_CHUNK,
                        f"first read {self.counter['bytes']} of {size} bytes for tail=50")
        # Every later open (unchanged or appended file) touches only the suffix.
        with open(self.path, "a") as fh:
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "late"}}) + "\n")
        size = self.path.stat().st_size
        self.counter["bytes"] = 0
        res = self._tail(50)
        self.assertLess(self.counter["bytes"], size // 4,
                        f"read {self.counter['bytes']} of {size} bytes for tail=50")
        total, ref_lines = _reference(self.server, self.path, 50)
        self.assertEqual(res["last_line"], total)
        self.assertEqual([e["line"] for e in res["events"]], ref_lines)
        self.assertEqual(res["first_line"], ref_lines[0])
        self.assertTrue(res["truncated_before"])

    def test_tail_matches_reference_on_short_file_and_missing_trailing_newline(self):
        for n, trailing in ((3, True), (3, False), (120, False)):
            with self.subTest(n=n, trailing=trailing):
                _write(self.path, n, trailing_newline=trailing)
                res = self._tail(120)
                total, ref_lines = _reference(self.server, self.path, 120)
                self.assertEqual(res["last_line"], total)
                self.assertEqual([e["line"] for e in res["events"]], ref_lines)
                self.assertFalse(res["truncated_before"])

    def test_tail_line_numbers_stay_exact_after_appends(self):
        _write(self.path, 300)
        self._tail(10)
        with open(self.path, "a") as fh:
            fh.write(_line(301) + _line(302))
        res = self._tail(10)
        self.assertEqual(res["last_line"], 302)
        self.assertEqual([e["line"] for e in res["events"]], list(range(293, 303)))
        # Partial trailing line (writer mid-append) counts like the reference.
        with open(self.path, "a") as fh:
            fh.write(_line(303)[:40])
        res = self._tail(10)
        total, ref_lines = _reference(self.server, self.path, 10)
        self.assertEqual(res["last_line"], total)
        self.assertEqual(total, 303)


if __name__ == "__main__":
    unittest.main()
