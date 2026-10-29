"""End-to-end novice harness — skipped unless CCC_E2E_FREE=1.

This is not a unit test. It runs scripts/e2e-novice.sh for real: a temp HOME,
a freellmapi router built from source, a keyless Kilo provider, a spawned
`claude -p` session through POST /api/sessions/spawn, and an on-disk file
assertion — the whole "$0 first run" the onboarding promises.

Run it locally with:

    CCC_E2E_FREE=1 CCC_FREELLMAPI_SRC=/path/to/freellmapi \\
        python3 -m pytest tests/test_e2e_novice_free.py -q

It is opt-in because it needs network (npm/registry or a local checkout),
several minutes, and a real `claude` binary. CI must never set CCC_E2E_FREE.
"""
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "e2e-novice.sh"
# Building freellmapi (npm ci + build) plus a free-model agent run is slow.
E2E_TIMEOUT_S = int(os.environ.get("CCC_E2E_TEST_TIMEOUT_S", "1500"))

_SKIP_REASON = "opt-in E2E: set CCC_E2E_FREE=1 (builds a real router, spawns claude)"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(os.environ.get("CCC_E2E_FREE") != "1", reason=_SKIP_REASON)
def test_e2e_novice_free_run(tmp_path):
    """The novice flow works end to end: a spawned agent creates a file at $0."""
    home = tmp_path / "e2e-home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "CCC_E2E_HOME": str(home),
            "CCC_E2E_CCC_PORT": str(_free_port()),
            "CCC_E2E_KEEP": "1",  # pytest cleans tmp_path; keep logs on failure
        }
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=E2E_TIMEOUT_S,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)

    # E2E_RESULT is pretty-printed multi-line JSON; raw_decode consumes the
    # whole object and ignores the trailing PASS/cleanup lines.
    marker = "E2E_RESULT "
    idx = proc.stdout.rfind(marker)
    assert idx >= 0, "script produced no E2E_RESULT line"
    result, _ = json.JSONDecoder().raw_decode(proc.stdout[idx + len(marker):])

    assert proc.returncode == 0, (
        f"e2e-novice.sh failed (rc={proc.returncode}); "
        f"logs under {home}/logs"
    )
    assert result["ok"] is True
    assert Path(result["proof_file"]).is_file()
    # The strongest "$0" evidence: the router itself recorded serving traffic.
    assert result["router_requests_delta"] > 0


def test_e2e_script_is_sane_shell():
    """Cheap guard so the real run doesn't die on a typo: bash -n the script."""
    proc = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
