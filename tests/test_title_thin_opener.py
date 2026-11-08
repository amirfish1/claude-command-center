import unittest

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


if __name__ == "__main__":
    unittest.main()
