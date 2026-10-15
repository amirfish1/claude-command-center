"""A missing test dependency must never turn the push gate into a pass."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="POSIX push hook")
@pytest.mark.parametrize("python_state", ["absent", "no_pytest", "fails", "passes"])
def test_push_gate_requires_pytest_and_a_passing_run(tmp_path, python_state):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts" / "pre-push.sh"
    shutil.copyfile(source, scripts / "pre-push.sh")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_perf_budget.py").touch()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for command in ("dirname", "git", "grep"):
        (binaries / command).symlink_to(shutil.which(command))
    if python_state != "absent":
        python = binaries / "python3"
        python.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "-c" ]; then exit '
            + ("1" if python_state == "no_pytest" else "0")
            + '; fi\n'
            'printf "%s\\n" "$*" > "$GATE_CALL"\n'
            + ("exit 1\n" if python_state == "fails" else "exit 0\n")
        )
        python.chmod(0o755)
    marker = tmp_path / "gate-call"
    env = dict(os.environ, PATH=str(binaries), GATE_CALL=str(marker))
    result = subprocess.run(
        [shutil.which("bash"), str(scripts / "pre-push.sh")],
        input="", capture_output=True, text=True, env=env, timeout=10,
    )
    assert (result.returncode == 0) == (python_state == "passes"), result.stderr
    if python_state in {"absent", "no_pytest"}:
        assert "push blocked" in result.stderr
        assert not marker.exists()
    else:
        assert "-m pytest" in marker.read_text()
