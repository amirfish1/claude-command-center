"""Known-repo discovery must not stall a request (CCC-1227).

A click on an image link (/api/media) took ~15 s: resolving its repo context
hit an expired known-repo memo, and the request rebuilt it inline, re-decoding
every ~/.claude/projects slug from scratch (~70k stats).
"""
import threading
import time
import unittest
from unittest import mock

import server
from ccc_server import repo_paths


class DecodeSlugMemoTest(unittest.TestCase):
    def setUp(self):
        server._DECODE_SLUG_MEMO.clear()

    def tearDown(self):
        server._DECODE_SLUG_MEMO.clear()

    def test_hit_skips_the_walk_and_revalidates_the_answer(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            real = pathlib.Path(d).resolve() / "my-app"
            real.mkdir()
            slug = str(real).replace("/", "-")
            first = server._decode_project_slug(slug)
            self.assertEqual(first, real)
            with mock.patch.object(server, "_decode_project_slug_walk",
                                   side_effect=AssertionError("walked again")):
                self.assertEqual(server._decode_project_slug(slug), real)
            real.rmdir()
            # The memoized dir is gone: walk again instead of returning it.
            self.assertIsNone(server._decode_project_slug(slug))

    def test_misses_are_memoized_until_the_ttl(self):
        slug = "-no-such-dir-ccc-1227"
        self.assertIsNone(server._decode_project_slug(slug))
        with mock.patch.object(server, "_decode_project_slug_walk",
                               side_effect=AssertionError("walked again")):
            self.assertIsNone(server._decode_project_slug(slug))
        server._DECODE_SLUG_MEMO[slug] = (None, time.time() - server._DECODE_SLUG_MISS_TTL_S - 1)
        with mock.patch.object(server, "_decode_project_slug_walk", return_value=None) as walk:
            server._decode_project_slug(slug)
        walk.assert_called_once()


class KnownRepoPathsRebuildTest(unittest.TestCase):
    def tearDown(self):
        server._invalidate_known_repo_paths()

    def test_expired_memo_serves_stale_and_rebuilds_off_thread(self):
        release = threading.Event()
        started = threading.Event()

        def slow_rebuild():
            started.set()
            release.wait(5)
            return ["/new"]

        with repo_paths._KNOWN_REPO_PATHS_LOCK:
            server._KNOWN_REPO_PATHS_CACHE["paths"] = ["/old"]
            server._KNOWN_REPO_PATHS_CACHE["at"] = 0.0
        with mock.patch.object(server, "_known_repo_paths_uncached", side_effect=slow_rebuild):
            t0 = time.time()
            self.assertEqual(repo_paths._known_repo_paths(), ["/old"])
            self.assertLess(time.time() - t0, 1.0)
            self.assertTrue(started.wait(2))
            release.set()
            for _ in range(100):
                if server._KNOWN_REPO_PATHS_CACHE["paths"] == ["/new"]:
                    break
                time.sleep(0.02)
        self.assertEqual(repo_paths._known_repo_paths(), ["/new"])


if __name__ == "__main__":
    unittest.main()
