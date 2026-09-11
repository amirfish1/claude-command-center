"""Regression coverage for bidirectional conversation composer input."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_primary_conversation_composer_detects_text_direction():
    """Typing an RTL prompt in the main composer must align it correctly."""
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"<textarea\s+[^>]*\bid=\"convInput\"[^>]*>", html)

    assert match, "primary conversation composer textarea is present"
    assert 'dir="auto"' in match.group(0)
