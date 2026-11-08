import unittest
from unittest import mock

import server


class BlockParamsTests(unittest.TestCase):
    def test_rejects_bad_ref(self):
        payload, code = server._wt_block_params("../etc")
        self.assertEqual(code, 400)
        self.assertFalse(payload["ok"])

    def test_no_wt_cli(self):
        with mock.patch.object(server, "_wt_cli_available", return_value=False):
            payload, code = server._wt_block_params("CCC-1")
        self.assertEqual(code, 503)


if __name__ == "__main__":
    unittest.main()
