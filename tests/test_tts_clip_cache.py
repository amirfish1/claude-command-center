import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ccc_server import free_runtime as fr

ROOT = Path(__file__).resolve().parents[1]


class ClipCacheTests(unittest.TestCase):
    def setUp(self):
        fr._CLIP_CACHE.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = fr._LOCAL_TTS_DIR
        fr._LOCAL_TTS_DIR = Path(self._tmp.name)

    def tearDown(self):
        fr._LOCAL_TTS_DIR = self._old_dir
        fr._CLIP_CACHE.clear()
        self._tmp.cleanup()

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

    def test_memory_eviction_is_bounded(self):
        old = fr._CLIP_CACHE_MAX_BYTES
        fr._CLIP_CACHE_MAX_BYTES = 10
        try:
            fr._clip_cache_put(("a", "v"), b"123456")
            fr._clip_cache_put(("b", "v"), b"123456")
        finally:
            fr._CLIP_CACHE_MAX_BYTES = old
        self.assertNotIn(("a", "v"), fr._CLIP_CACHE)
        self.assertEqual(fr._CLIP_CACHE[("b", "v")], b"123456")

    def test_clip_survives_a_restart_on_disk(self):
        fr._clip_cache_put(("hello there", "af_nova"), b"mp3-bytes")
        fr._CLIP_CACHE.clear()   # what a dashboard restart does
        self.assertEqual(fr._clip_cache_get(("hello there", "af_nova")), b"mp3-bytes")
        fr._CLIP_CACHE.clear()
        self.assertEqual(fr._clip_cache_get_any_voice("hello there"), ("af_nova", b"mp3-bytes"))
        self.assertIsNone(fr._clip_cache_get(("hello there", "af_heart")))

    def test_disk_eviction_drops_least_recently_used(self):
        old = fr._CLIP_DISK_MAX_BYTES
        fr._CLIP_DISK_MAX_BYTES = 10
        try:
            fr._clip_cache_put(("a", "af_nova"), b"123456")
            past = time.time() - 100
            os.utime(fr._clip_disk_path(("a", "af_nova")), (past, past))
            fr._clip_cache_put(("b", "af_nova"), b"123456")
        finally:
            fr._CLIP_DISK_MAX_BYTES = old
        self.assertFalse(fr._clip_disk_path(("a", "af_nova")).exists())
        self.assertTrue(fr._clip_disk_path(("b", "af_nova")).exists())


def _load_priority_lock():
    # The voice server imports kokoro at load; take just the lock class.
    import ast
    source = (ROOT / "scripts" / "local_tts_server.py").read_text()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_PriorityLock")
    namespace = {"threading": threading}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "local_tts_server.py", "exec"), namespace)
    return namespace["_PriorityLock"]


class PriorityLockTests(unittest.TestCase):
    def test_waiting_fg_goes_before_waiting_bg(self):
        lock = _load_priority_lock()()
        order = []
        lock.acquire(fg=False)   # a background piece is being made

        def run(name, fg):
            lock.acquire(fg)
            order.append(name)
            lock.release()

        bg = threading.Thread(target=run, args=("bg", False))
        bg.start()
        time.sleep(0.05)
        fg = threading.Thread(target=run, args=("fg", True))
        fg.start()
        time.sleep(0.05)
        lock.release()
        bg.join(2)
        fg.join(2)
        self.assertEqual(order, ["fg", "bg"])


if __name__ == "__main__":
    unittest.main()
