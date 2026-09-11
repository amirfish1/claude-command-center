# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Single source of truth for the worker-staleness fingerprint.

server.py was decomposed into ccc_server/*.py (17+ extracted subsystem
modules). The original fingerprint hashed server.py's own bytes only — a
change confined entirely to an extracted module (e.g. ccc_server/ask.py)
never touched server.py, so run.sh's "is the worker running old code?"
check and the worker's own reported hash silently agreed even when the
worker was, in fact, running stale code. Hash server.py plus every
ccc_server/*.py file, sorted for a deterministic order, so both sides of
that comparison mean the same thing.

Called two ways: `from ccc_server.content_hash import compute` (the worker
process, which already has the package importable) or as a standalone
script `python3 ccc_server/content_hash.py <repo_root>` (run.sh, before
anything else in the repo is importable) — kept stdlib-only on purpose so
both call sites stay cheap.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def compute(repo_root):
    """(version, content_hash[:16]) for server.py + sorted ccc_server/*.py."""
    root = Path(repo_root)
    text = (root / "server.py").read_text(encoding="utf-8")
    m = _VERSION_RE.search(text)
    version = m.group(1) if m else ""
    parts = [text.encode("utf-8")]
    for p in sorted((root / "ccc_server").glob("*.py")):
        parts.append(p.read_bytes())
    content_hash = hashlib.sha256(b"".join(parts)).hexdigest()[:16]
    return version, content_hash


if __name__ == "__main__":
    repo_root = sys.argv[1] if len(sys.argv) > 1 else "."
    try:
        version, content_hash = compute(repo_root)
    except OSError:
        version, content_hash = "", ""
    print(f"{version} {content_hash}", end="")
