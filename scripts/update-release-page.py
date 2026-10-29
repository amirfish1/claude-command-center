#!/usr/bin/env python3
"""Refresh the homepage release labels without relabeling its screenshot."""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def update_page(page, version, dmg_version):
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
    # A newer source-only release may have no DMG. Follow the signed appcast
    # rather than GitHub's latest release when linking the macOS download.
    page = re.sub(
        r'releases/(?:latest/download|download/v[0-9.]+)/ccc(?:-v[0-9.]+)?\.dmg',
        f'releases/download/v{dmg_version}/ccc-v{dmg_version}.dmg', page,
    )
    return re.sub(
        r'(?:v[0-9.]+ macOS app, source, and Homebrew release|macOS app v[0-9.]+ &middot; source and Homebrew v[0-9.]+)',
        f'macOS app v{dmg_version} &middot; source and Homebrew v{version}', page,
    )


if __name__ == "__main__":
    version = sys.argv[1]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("version must be X.Y.Z")
    target = Path(__file__).resolve().parents[1] / "docs/index.html"
    feed = ET.parse(target.with_name("appcast.xml"))
    dmg_version = feed.findtext("./channel/item/{http://www.andymatuschak.org/xml-namespaces/sparkle}version")
    if not dmg_version or not re.fullmatch(r"\d+\.\d+\.\d+", dmg_version):
        raise SystemExit("signed appcast release version is missing")
    target.write_text(update_page(target.read_text(encoding="utf-8"), version, dmg_version), encoding="utf-8")
