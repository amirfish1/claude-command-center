import unittest
import re

import server  # noqa: F401  (ccc_server modules import through server)
from ccc_server import session_graph as g


class ThinOpenerTitleTest(unittest.TestCase):
    OPENER = ('what are these?  /tmp/x/pasted-images/paste-1.png'
              '<image name=[Image #1] path="/tmp/x/pasted-images/paste-1.png"></image>')

    def test_strip_image_markup(self):
        self.assertEqual(g._strip_image_markup(self.OPENER), "what are these?")

    def test_thin_opener_detected(self):
        self.assertTrue(g._title_opener_is_thin(self.OPENER))
        self.assertFalse(g._title_opener_is_thin("fix the sidebar scroll jump when a session updates"))

    def test_image_paths_preserve_legacy_title_cleanup(self):
        legacy = re.compile(r"\S*/pasted-images/\S+\.(?:png|jpe?g|gif|webp)", re.I)
        for text in (
            "keep /tmp/pasted-images/a.png then /tmp/pasted-images/b.JPG done",
            "before\u2003/tmp/PASTED-IMAGES/a.WeBp\nend",
            "prefix/tmp/pasted-images/a.gif?size=2 suffix",
            "keep /tmp/pasted-images/no-extension and ordinary/path.png",
            "/tmp/pa\u017fted-\u0131mages/a.png end",
            "x/pasted-images/a.png/pasted-images/b.png",
        ):
            with self.subTest(text=text):
                expected = " ".join(legacy.sub(" ", text).split())
                self.assertEqual(g._strip_image_markup(text), expected)

    def test_long_non_image_tokens_and_incomplete_paths_remain_visible(self):
        token = "a" * 100_000
        self.assertEqual(g._strip_image_markup(token), token)
        incomplete = token + "/pasted-images/no-extension"
        self.assertEqual(g._strip_image_markup(incomplete), incomplete)


if __name__ == "__main__":
    unittest.main()
