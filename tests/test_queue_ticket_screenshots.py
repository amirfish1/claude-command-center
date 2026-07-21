"""Regression coverage for screenshot URLs in Queue ticket details."""

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _extract_images(text):
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function _uxqExtractImages(text)")
    end = app_js.index("function _uxqOpenItemModal(item)", start)
    helper = app_js[start:end]
    result = subprocess.run(
        ["node", "-e", helper + "; console.log(JSON.stringify(_uxqExtractImages(process.argv[1])));", text],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestQueueTicketScreenshots(unittest.TestCase):
    def test_ticket_detail_keeps_external_screenshot_urls_intact(self):
        """External markdown images must not be mistaken for local file paths."""
        screenshot_url = (
            "https://example.invalid/storage/v1/object/public/bug-reports/"
            "appointment.jpg"
        )
        local_screenshot = "/Users/example/.claude/command-center/pasted-images/paste-123.png"

        self.assertEqual(
            _extract_images(
                "### Screenshot\n\n![screenshot](" + screenshot_url + ")\n\n" + local_screenshot
            ),
            [screenshot_url, local_screenshot],
        )
