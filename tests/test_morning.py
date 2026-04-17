import unittest


class TestInfraSmoke(unittest.TestCase):
    """Sanity check: unittest discovery works from the repo root."""

    def test_one_plus_one(self):
        self.assertEqual(1 + 1, 2)


if __name__ == "__main__":
    unittest.main()
