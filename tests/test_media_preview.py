"""Large /api/media images get a <=150kB JPEG preview (CCC-298)."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ccc_server import media_preview as mp

_HAS_CONVERTER = any(shutil.which(t) for t in ("magick", "convert", "ffmpeg", "sips"))


def _noisy_png(path, w=1800, h=1200):
    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        subprocess.run([magick, "-size", f"{w}x{h}", "xc:", "+noise", "Random", str(path)], check=True)
    else:
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"nullsrc=s={w}x{h},geq=random(1)*255:128:128",
                        "-frames:v", "1", str(path)], check=True)


class WantsPreviewTest(unittest.TestCase):
    def test_only_large_raster_images(self):
        big = mp.PREVIEW_MAX_BYTES + 1
        self.assertTrue(mp.wants_preview("/x/a.PNG", big))
        self.assertTrue(mp.wants_preview("/x/a.jpeg", big))
        self.assertFalse(mp.wants_preview("/x/a.png", mp.PREVIEW_MAX_BYTES))
        for ext in (".gif", ".svg", ".mp4", ".html", ".pdf"):
            self.assertFalse(mp.wants_preview("/x/a" + ext, big), ext)


@unittest.skipUnless(_HAS_CONVERTER and (shutil.which("magick") or shutil.which("convert") or shutil.which("ffmpeg")),
                     "no image converter on this host")
class PreviewPathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.src = self.tmp / "shot.png"
        _noisy_png(self.src)
        self.cache = self.tmp / "cache"

    def tearDown(self):
        self._tmp.cleanup()

    def test_large_png_becomes_a_small_jpeg_and_is_cached(self):
        st = self.src.stat()
        self.assertGreater(st.st_size, mp.PREVIEW_MAX_BYTES)
        out = mp.preview_path(self.src, st, cache_dir=self.cache)
        self.assertIsNotNone(out)
        data = out.read_bytes()
        self.assertLessEqual(len(data), mp.PREVIEW_MAX_BYTES)
        self.assertEqual(data[:3], b"\xff\xd8\xff")  # JPEG
        with mock.patch.object(mp.subprocess, "run", side_effect=AssertionError("re-encoded")):
            self.assertEqual(mp.preview_path(self.src, st, cache_dir=self.cache), out)
        self.assertEqual([p.name for p in self.cache.iterdir()], [out.name])

    def test_changed_source_gets_a_new_preview(self):
        first = mp.preview_path(self.src, self.src.stat(), cache_dir=self.cache)
        _noisy_png(self.src, 1700, 1100)
        os.utime(self.src, ns=(1, 1))
        second = mp.preview_path(self.src, self.src.stat(), cache_dir=self.cache)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)

    def test_no_converter_falls_back_to_none(self):
        with mock.patch.object(mp.shutil, "which", return_value=None):
            self.assertIsNone(mp.preview_path(self.src, self.src.stat(), cache_dir=self.cache))
        self.assertEqual(list(self.cache.glob("*.tmp.jpg")), [])


class ClientWiringTest(unittest.TestCase):
    def test_lightbox_and_folder_viewer_request_the_preview(self):
        js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
        self.assertIn("function _mediaPreviewUrl(href)", js)
        self.assertIn("if (isImage && !(opts && opts.noPreview)) src = _mediaPreviewUrl(src) || src;", js)
        self.assertIn("_mediaPreviewUrl(url) || url", js)
        self.assertIn("img-lightbox-full", js)


if __name__ == "__main__":
    unittest.main()
