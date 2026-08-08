# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Hook-side shim over `ccc_server.errlog`.

Hooks run as short-lived subprocesses inside agent pipelines: they must exit
fast, never prompt, and never fail the turn. That is why every hook wraps
`main()` in a blanket handler — but a hook that silently stops writing its
marker looks exactly like a hook that never ran, and the dashboard just goes
quiet. One stderr line (the pipeline captures it, exit status is untouched)
makes the difference diagnosable.

Falls back to a local implementation when the hook has been copied out of
the repo and `ccc_server` is not importable.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from ccc_server.errlog import log_swallowed
except ImportError:  # hook copied outside the repo tree
    def log_swallowed(context, exc=None):
        if os.environ.get("CCC_QUIET_ERRORS") == "1":
            return
        if exc is None:
            exc = sys.exc_info()[1]
        print(f"[ccc:swallowed] {context}: {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)

__all__ = ["log_swallowed"]
