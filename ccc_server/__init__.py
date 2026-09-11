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

    During the test suite's pop-and-reimport of "server" there is a wide
    window where the fresh module has not yet run `_adopt_ccc_module` for
    the subsystem that owns a name (adoption happens ~20k lines into
    server.py). Background threads spawned by the previous server instance
    (queue pumps, rollout watchers) that touch `_core.X` in that window
    used to die on AttributeError. When "server" lacks the name (or is
    popped entirely), fall back to the already-imported ccc_server.*
    submodule that defines it — `_adopt_ccc_module` rebinds server to the
    very same object once import catches up, and monkeypatches on server
    still win on the primary path.
    """

    __slots__ = ()

    def __getattr__(self, name):
        mod = _sys.modules.get("server")
        if mod is not None:
            try:
                return getattr(mod, name)
            except AttributeError:
                pass  # server mid-reimport; try the owning submodule
        for full, sub in list(_sys.modules.items()):
            if full.startswith("ccc_server.") and hasattr(sub, name):
                return getattr(sub, name)
        # e.g. atexit callbacks after the test suite popped every module
        raise AttributeError(name)

    def __setattr__(self, name, value):
        setattr(_sys.modules["server"], name, value)


core = _CoreProxy()
