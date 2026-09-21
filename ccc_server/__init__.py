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

import os as _os
import sys as _sys


def test_isolation_active():
    """True when running under a test runner or in a child process of one.

    server.py stamps CCC_TEST_ISOLATION=1 into os.environ when it detects
    pytest/unittest, so multiprocessing "spawn" children and test-spawned
    subprocesses — fresh interpreters with neither runner in sys.modules —
    still resolve test-isolated state paths instead of the live
    ~/.claude/command-center files (CCC-1165).
    """
    return (
        "pytest" in _sys.modules
        or "unittest" in _sys.modules
        or bool(_os.environ.get("CCC_TEST_ISOLATION"))
    )


# name -> ccc_server module that owns it. Filled by register() as
# server.py adopts (or a worker bootstrap imports) each module, so the proxy
# can resolve names with one dict lookup when "server" is absent (the worker
# without `import server`). Holds modules, not values: reads stay live.
_registry = {}


def register(mod):
    """Record `mod` as the owner of its top-level names for `core` lookups.

    Later registrations win, matching `_adopt_ccc_module`'s globals().update
    order, so a name defined in two modules resolves the same way whether
    server.py or a bootstrap did the importing.
    """
    for k in vars(mod):
        if not k.startswith("__") and k != "_core":
            _registry[k] = mod
    return mod


class _CoreProxy:
    """Live view of the server module.

    Extracted modules reach names still living in server.py through this
    proxy instead of a direct `import server` binding. Attribute access
    resolves against sys.modules["server"] on every call, so it survives
    the test suite's server-module reloads and sees monkeypatched
    attributes either way.

    When "server" is absent (the worker decoupled from server.py) or lacks
    the name (the test suite's mid-reimport window, where adoption runs ~20k
    lines into server.py and background threads from the previous instance
    still touch `_core.X`), resolve through the registry of adopted
    ccc_server modules: one dict lookup. Names never registered fall back to
    scanning the imported ccc_server.* submodules. Monkeypatches on server
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
        owner = _registry.get(name)
        if owner is not None:
            try:
                return getattr(owner, name)
            except AttributeError:
                pass  # owner dropped the name; fall through to the scan
        for full, sub in list(_sys.modules.items()):
            if full.startswith("ccc_server.") and hasattr(sub, name):
                return getattr(sub, name)
        # e.g. atexit callbacks after the test suite popped every module
        raise AttributeError(name)

    def __setattr__(self, name, value):
        mod = _sys.modules.get("server")
        if mod is not None:
            setattr(mod, name, value)
            return
        owner = _registry.get(name)
        if owner is None:
            raise AttributeError(name)
        setattr(owner, name, value)


core = _CoreProxy()
