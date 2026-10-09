import os
import unittest
from unittest import mock

import server


class CarModeSpeechEngineTests(unittest.TestCase):
    KEYS = {"anthropic": True, "deepgram": False}

    def test_local_engine_is_the_default_and_needs_no_deepgram_key(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCC_VOICE_ENGINE", None)
            self.assertEqual(server._car_mode_speech_engine(), "local")
            with mock.patch.object(server._free_runtime, "local_tts_installed", return_value=True):
                self.assertEqual(server._car_mode_status_mode(self.KEYS), "voice")
            with mock.patch.object(server._free_runtime, "local_tts_installed", return_value=False):
                self.assertEqual(server._car_mode_status_mode(self.KEYS), "degraded_no_local_speech")

    def test_deepgram_engine_is_opt_in_and_still_needs_its_key(self):
        with mock.patch.dict(os.environ, {"CCC_VOICE_ENGINE": "deepgram"}):
            self.assertEqual(server._car_mode_speech_engine(), "deepgram")
            self.assertEqual(server._car_mode_status_mode(self.KEYS), "degraded_no_deepgram")
            self.assertEqual(server._car_mode_status_mode({"anthropic": True, "deepgram": True}), "voice")

    def test_anthropic_key_is_still_required(self):
        self.assertEqual(server._car_mode_status_mode({"anthropic": False, "deepgram": True}, "local"),
                         "unavailable_no_anthropic")


if __name__ == "__main__":
    unittest.main()
