"""Title cleanup retains its output for long pastes and Unicode whitespace."""
import random
import re

import pytest

import server
from ccc_server import session_graph


@pytest.mark.parametrize("limit", [1, 2, 3, 120])
def test_bounded_title_matches_full_normalization(monkeypatch, limit):
    monkeypatch.setattr(server, "SESSION_NAME_MAX_CHARS", limit)
    whitespace = [chr(n) for n in range(0x110000) if chr(n).isspace()]
    rng = random.Random(42)
    cases = [None, "", " ", "x", "x " * 10000, "x" * 10000,
             "a" * (limit - 1) + " \t b", "title" + "\n" * 10000 + "end"]
    cases.extend("".join(rng.choice(["a", "β", "界", *whitespace])
                         for _ in range(size)) for size in (1, 2, 119, 120, 121, 10000))
    for text in cases:
        expected = None if text is None else re.sub(r"\s+", " ", text).strip()
        if expected and len(expected) > limit:
            expected = expected[:limit - 1].rstrip() + "…"
        assert server._truncate_session_name(text) == expected


def test_image_cleanup_retains_every_unicode_whitespace_separator():
    whitespace = "".join(chr(n) for n in range(0x110000) if chr(n).isspace())
    text = whitespace + "first" + whitespace + "<image path='example.png'></image>"
    text += whitespace + "/tmp/pasted-images/example.png" + whitespace + "last" + whitespace
    assert session_graph._strip_image_markup(text) == "first last"
