import io
import unittest

from ccc_server import free_runtime as fr


class ClipCacheTests(unittest.TestCase):
    def setUp(self):
        fr._CLIP_CACHE.clear()

    def test_tee_stores_only_completed_clip_and_replays(self):
        key = ("hello", "af_nicole")
        tee = fr._TeeClip(io.BytesIO(b"abcdef"), key)
        with tee:
            while tee.read(4):
                pass
        self.assertEqual(fr._clip_cache_get(key), b"abcdef")
        with fr._CachedClip(fr._clip_cache_get(key)) as clip:
            self.assertEqual(clip.read(4) + clip.read(4) + clip.read(4), b"abcdef")

    def test_abandoned_stream_is_not_cached(self):
        key = ("hello", "af_nicole")
        tee = fr._TeeClip(io.BytesIO(b"abcdef"), key)
        with tee:
            tee.read(2)
        self.assertIsNone(fr._clip_cache_get(key))

    def test_eviction_is_bounded(self):
        old = fr._CLIP_CACHE_MAX_BYTES
        fr._CLIP_CACHE_MAX_BYTES = 10
        try:
            fr._clip_cache_put(("a", "v"), b"123456")
            fr._clip_cache_put(("b", "v"), b"123456")
        finally:
            fr._CLIP_CACHE_MAX_BYTES = old
        self.assertIsNone(fr._clip_cache_get(("a", "v")))
        self.assertEqual(fr._clip_cache_get(("b", "v")), b"123456")


if __name__ == "__main__":
    unittest.main()
