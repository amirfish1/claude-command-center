import unittest

from ccc_server import free_runtime


class FreeTtsTests(unittest.TestCase):
    def test_audio_type_comes_from_bytes_not_router_label(self):
        self.assertEqual(free_runtime._audio_type(b"RIFF\x00\x00\x00\x00WAVE"), "audio/wav")
        self.assertEqual(free_runtime._audio_type(b"ID3\x04rest"), "audio/mpeg")
        self.assertEqual(free_runtime._audio_type(b"\x00\x01"), "application/octet-stream")

    def test_empty_text_is_rejected_before_any_network(self):
        status, audio, _ctype, _label = free_runtime.tts("   ")
        self.assertEqual((status, audio), (400, b""))

    def test_voice_catalog_has_no_duplicates(self):
        self.assertEqual(len(free_runtime.TTS_VOICES), len(set(free_runtime.TTS_VOICES)))

    def test_deepgram_off_without_key_or_when_disabled(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"CCC_DEEPGRAM": "0", "DEEPGRAM_API_KEY": "dg-test-XXXX"}):
            self.assertEqual(free_runtime.deepgram_tts("hello"), (b"", ""))
        self.assertEqual(len(free_runtime.DEEPGRAM_VOICES), len(set(free_runtime.DEEPGRAM_VOICES)))

    def test_deepgram_needs_explicit_opt_in_not_just_a_key(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as home, mock.patch.object(Path, "home", return_value=Path(home)):
            with mock.patch.dict(os.environ, {"DEEPGRAM_API_KEY": "dg-test-XXXX"}):
                os.environ.pop("CCC_DEEPGRAM", None)
                self.assertFalse(free_runtime.deepgram_enabled())
                self.assertEqual(free_runtime._deepgram_key(), "")
                (Path(home) / ".ccc").mkdir()
                (Path(home) / ".ccc" / "deepgram.on").touch()
                self.assertTrue(free_runtime.deepgram_enabled())
                self.assertEqual(free_runtime._deepgram_key(), "dg-test-XXXX")
            with mock.patch.dict(os.environ, {"CCC_DEEPGRAM": "1", "DEEPGRAM_API_KEY": "dg-test-XXXX"}):
                self.assertTrue(free_runtime.deepgram_enabled())

    def test_local_kokoro_speaks_first_before_cloud_voices(self):
        import os
        from unittest import mock
        wav = b"RIFF\x00\x00\x00\x00WAVEdata"
        with mock.patch.dict(os.environ, {"CCC_DEEPGRAM": "0"}), \
                mock.patch.object(free_runtime, "local_tts_installed", return_value=True), \
                mock.patch.object(free_runtime, "local_tts", return_value=(wav, "Kokoro: af_nova")) as local, \
                mock.patch.object(free_runtime, "unified_key") as router_key:
            status, audio, ctype, label = free_runtime.tts("hello there", "")
        self.assertEqual((status, audio, ctype, label), (200, wav, "audio/wav", "Kokoro: af_nova"))
        local.assert_called_once()
        router_key.assert_not_called()

    def test_read_started_on_a_cloud_voice_keeps_it(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"CCC_DEEPGRAM": "0"}), \
                mock.patch.object(free_runtime, "local_tts_installed", return_value=True), \
                mock.patch.object(free_runtime, "local_tts") as local, \
                mock.patch.object(free_runtime, "unified_key", return_value=""):
            status, _audio, _ctype, label = free_runtime.tts("hello", "Puck")
        local.assert_not_called()
        self.assertEqual((status, label), (503, "Puck"))


if __name__ == "__main__":
    unittest.main()
