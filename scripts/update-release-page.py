#!/usr/bin/env python3
"""Refresh the homepage release labels without relabeling its screenshot."""
import re
import sys
from pathlib import Path


def update_page(page, version):
    match = re.search(r'<span class="version">v(\d+\.\d+)</span>', page)
    if not match:
        raise ValueError("homepage release label is missing")
    previous = match.group(1)
    current = ".".join(version.split(".")[:2])
    # Screenshot labels describe the captured version, rather than the release.
    page = re.sub(
        r'(<img\b[^>]*>)|\bv' + re.escape(previous) + r'(?:\.\d+)?\b',
        lambda m: m.group(0) if m.group(1) else (
            "v" + version if m.group(0).count(".") == 2 else "v" + current),
        page,
    )
    return re.sub(
        r'releases/download/v\d+\.\d+\.\d+/ccc\.dmg',
        'releases/latest/download/ccc.dmg', page,
    )


if __name__ == "__main__":
    version = sys.argv[1]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("version must be X.Y.Z")
    target = Path(__file__).resolve().parents[1] / "docs/index.html"
    target.write_text(update_page(target.read_text(encoding="utf-8"), version), encoding="utf-8")
