"""Regression coverage for the text-only automatic title prompt."""

import importlib
from types import SimpleNamespace
import unittest
from unittest import mock


class AutoTitlePromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        importlib.import_module("server")
        cls.session_graph = importlib.import_module("ccc_server.session_graph")

    def test_image_path_prefix_is_not_sent_to_the_title_model(self):
        image_path = "/tmp/pasted-images/annotation.png"
        first_message = f"{image_path} Describe the annotation and fix the overlap"
        captured = {}

        def run(argv, **kwargs):
            captured["instruction"] = argv[-1]
            return SimpleNamespace(returncode=0, stdout="Fix annotation overlap\n", stderr="")

        with mock.patch.object(
            self.session_graph._core,
            "_resolve_claude_bin",
            return_value={"available": True, "bin": "/usr/bin/claude"},
        ), mock.patch.object(self.session_graph.subprocess, "run", side_effect=run):
            result = self.session_graph._summarize_title_text(first_message, validate=True)

        self.assertTrue(result["ok"])
        self.assertNotIn(image_path, captured["instruction"])
        self.assertIn("Describe the annotation and fix the overlap", captured["instruction"])


if __name__ == "__main__":
    unittest.main()
