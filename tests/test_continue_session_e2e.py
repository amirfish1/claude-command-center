"""B15+B16 E2E: a limit-hit session continues on a router and switches back.

Runs scripts/e2e_continue_session.py: real Claude Code CLI, real CCC
failover code, fake plan + fake router on loopback, simulated limit and
reset, throwaway HOME. No network, no spend. Skipped when `claude` is
not installed (CI), or with CCC_E2E_SKIP_CLAUDE=1.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts/e2e_continue_session.py"


@pytest.mark.skipif(
    not shutil.which("claude") or os.environ.get("CCC_E2E_SKIP_CLAUDE") == "1",
    reason="needs the claude CLI (local fake router + simulated limit)",
)
def test_limit_hit_continues_on_router_then_switches_back(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "home")],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    lines = [l for l in proc.stdout.splitlines() if l.startswith("E2E_RESULT ")]
    assert len(lines) == 1, proc.stdout[-2000:]
    result = json.loads(lines[0][len("E2E_RESULT "):])
    assert result["ok"] is True
    assert [m["event"] for m in result["markers"]] == ["failover_start", "failover_back"]
    assert result["router_requests"] >= 2  # approved hop + a fresh-spawn follow-up
    assert result["plan_requests"] >= 2  # seed + post-reset turn
    assert result["router_models"] == ["e2e-router/free-model"]
