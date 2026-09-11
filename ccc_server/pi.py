# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Pi engine adapter -- bin resolution + a clean "not installed" spawn stub.

Unlike droid/aider/kilo/opencode, this repo has no prior Pi CLI integration
to build on and no `pi` binary available to probe its real invocation
contract against (model flag names, headless/non-interactive flag, output
format). Rather than guess an exec command that could silently misbehave
(hang waiting on stdin, or run in an unintended autonomy mode) this module
only resolves the binary and reports unavailability cleanly. Once a `pi`
CLI is actually available to test against, wire a real spawn here the same
way `spawn_session_droid` (ccc_server/droid.py) and `spawn_session_kilo`
(ccc_server/engines.py) do it -- see README's Engines section for the
install/config contract this module already exposes (`CCC_PI_BIN`).
"""

from __future__ import annotations

import os
import shutil


def _resolve_pi_bin():
    env_bin = os.environ.get("CCC_PI_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return {"available": True, "bin": env_bin, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "pi_unavailable",
            "reason": f"CCC_PI_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("pi")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    return {
        "available": False,
        "bin": None,
        "code": "pi_unavailable",
        "reason": "Pi CLI not found. Install the `pi` CLI and ensure it's on PATH, or set CCC_PI_BIN.",
    }


def spawn_session_pi(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None, reasoning_effort=None, env=None):
    """Spawn a headless Pi CLI run.

    Currently always reports unavailability (see module docstring): CCC has
    no verified Pi CLI invocation contract to spawn against yet.
    """
    resolved = _resolve_pi_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code", "pi_unavailable")}
    return {
        "ok": False,
        "error": "Pi CLI found but spawn is not yet wired in this CCC build (no verified invocation contract)",
        "code": "pi_unavailable",
    }
