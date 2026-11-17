"""Files scanning keeps exact token boundaries when skipping plain text."""

import itertools
import re

import server


def test_path_scanner_preserves_boundaries_and_nested_candidates():
    samples = {
        "/tmp/start.png": ["/tmp/start.png"],
        "text/tmp/hidden.png": [],
        "text/tmp/hidden(/tmp/visible.png)": ["/tmp/visible.png"],
        "[/tmp/a.pdf] `/tmp/b.png`": ["/tmp/a.pdf", "/tmp/b.png"],
        "é/tmp/hidden.png\u2003/tmp/visible.png": ["/tmp/visible.png"],
        "/ /tmp/next.png": ["/tmp/next.png"],
    }
    for text, expected in samples.items():
        assert server._FFC_PATH_RE.findall(text) == expected


def test_path_scanner_matches_original_for_all_short_boundary_combinations():
    original = re.compile(r"(?:^|(?<=[\s\"'`(\[]))(/[^\s\"'`<>)\]]+)")
    for size in range(6):
        for chars in itertools.product("a /([\n", repeat=size):
            text = "".join(chars)
            assert server._FFC_PATH_RE.findall(text) == original.findall(text)
