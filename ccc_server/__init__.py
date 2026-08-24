# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Modules extracted from server.py, one subsystem per file.

Each module may reach names still living in server.py via
`import server as _core` (resolved at call time, never import time).
server.py aliases itself into sys.modules as "server" before importing
anything from this package, so these imports never re-execute server.py.

Rules: stdlib-only (same as server.py); no side effects at import beyond
def/class/constants; new subsystems start here, not in server.py.
"""

import sys as _sys


class _CoreProxy:
    """Live view of the server module.

    Extracted modules reach names still living in server.py through this
    proxy instead of a direct `import server` binding. Attribute access
    resolves against sys.modules["server"] on every call, so it survives
    the test suite's server-module reloads and sees monkeypatched
    attributes either way.
    """

    __slots__ = ()

    def __getattr__(self, name):
        mod = _sys.modules.get("server")
        if mod is None:
            # e.g. atexit callbacks after the test suite popped the module
            raise AttributeError(name)
        return getattr(mod, name)

    def __setattr__(self, name, value):
        setattr(_sys.modules["server"], name, value)


core = _CoreProxy()
