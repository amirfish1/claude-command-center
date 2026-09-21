# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Static import-closure fingerprint for the worker-staleness check.

content_hash.py hashes server.py plus every ccc_server/*.py, so any dashboard
only edit restarts the worker. This module hashes only the files the worker
can actually load: it walks `import` statements (and server.py's
`_adopt_ccc_module("name")` calls) from a root list, statically, with `ast`.

Stdlib-only and standalone-runnable (`python3 ccc_server/import_closure.py
<repo_root> [root.py ...]`) so run.sh can call it before the repo is
importable, like content_hash.py.

`includes_server` reports whether server.py is inside the closure. While the
worker still does `import server` it is; once the worker boots through a
server-free bootstrap it drops out, and that flips the fingerprint to its
target precision.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

DEFAULT_ROOTS = ("ccc_worker.py",)

# (root, roots) -> (signature, result). Signature is the (mtime_ns, size) of
# every file in the last closure, so a call costs one stat per file and only
# re-parses when something changed. Never parse per RPC.
_CACHE = {}


def _resolve(root, dotted):
    """Repo file for a dotted module name, or None (stdlib / third party)."""
    parts = dotted.split(".")
    base = root.joinpath(*parts)
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _imports(path):
    """Yield dotted module names referenced by `path` (any depth)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            yield node.module
            for a in node.names:  # `from ccc_server import acp` is a submodule
                yield f"{node.module}.{a.name}"
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if (
                name == "_adopt_ccc_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                yield f"ccc_server.{node.args[0].value}"


def _closure_files(root, roots):
    seen = {}
    stack = []
    for r in roots:
        p = (root / r)
        if p.is_file():
            stack.append(p)
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen[p] = None
        for dotted in _imports(p):
            # A package import also loads its parents' __init__.py.
            parts = dotted.split(".")
            for i in range(1, len(parts) + 1):
                dep = _resolve(root, ".".join(parts[:i]))
                if dep is not None and dep not in seen:
                    stack.append(dep)
    return sorted(seen)


def _signature(files):
    sig = []
    for p in files:
        try:
            st = p.stat()
            sig.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(p), 0, -1))
    return tuple(sig)


def compute_closure(repo_root, roots=DEFAULT_ROOTS):
    """{"hash", "file_count", "includes_server", "files"} for the closure."""
    root = Path(repo_root).resolve()
    roots = tuple(roots)
    key = (str(root), roots)
    cached = _CACHE.get(key)
    if cached is not None and _signature(cached[2]) == cached[0]:
        return cached[1]
    files = _closure_files(root, roots)
    h = hashlib.sha256()
    for p in files:
        h.update(str(p.relative_to(root)).encode("utf-8") + b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
        h.update(b"\0")
    result = {
        "hash": h.hexdigest()[:16],
        "file_count": len(files),
        "includes_server": (root / "server.py") in set(files),
        "files": [str(p.relative_to(root)) for p in files],
    }
    _CACHE[key] = (_signature(files), result, files)
    return result


if __name__ == "__main__":
    repo_root = sys.argv[1] if len(sys.argv) > 1 else "."
    extra = tuple(sys.argv[2:]) or DEFAULT_ROOTS
    try:
        out = compute_closure(repo_root, extra)
        print(f"{out['hash']} {out['file_count']} {int(out['includes_server'])}", end="")
    except OSError:
        print("", end="")
