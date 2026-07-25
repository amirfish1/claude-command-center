"""Regression coverage for the Python interpreter selected by run.sh."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_run_script_prefers_the_repo_virtualenv_python():
    script = (PROJECT_ROOT / "run.sh").read_text(encoding="utf-8")

    assert 'PYTHON="$HERE/.venv/bin/python3"' in script
    assert 'exec "$PYTHON" "$HERE/server.py"' in script


def test_run_script_accepts_python39():
    script = (PROJECT_ROOT / "run.sh").read_text(encoding="utf-8")

    assert "sys.version_info >= (3, 9)" in script
    assert "requires Python 3.9+" in script
