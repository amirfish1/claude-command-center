"""Test-session ship databases must not accumulate between pytest runs."""

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest


@pytest.mark.parametrize("fail_test", [False, True])
def test_ship_graph_directory_removed_across_two_runs(tmp_path, fail_test):
    suite = tmp_path / "suite"
    suite.mkdir()
    shutil.copyfile(Path(__file__).with_name("conftest.py"), suite / "conftest.py")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (suite / "test_database.py").write_text(
        "import os, sqlite3\n"
        "from pathlib import Path\n"
        "def test_database():\n"
        "    db = Path(os.environ['CCC_SHIP_GRAPH_DB'])\n"
        "    assert db.parent.name.startswith('ccc-test-ship-')\n"
        "    with sqlite3.connect(db) as con:\n"
        "        con.execute('PRAGMA journal_mode=WAL')\n"
        "        con.execute('CREATE TABLE payload (data BLOB)')\n"
        "        con.execute('INSERT INTO payload VALUES (zeroblob(1048576))')\n"
        "    assert db.is_file()\n"
        f"    assert {not fail_test!r}\n",
    )
    env = dict(os.environ, TMPDIR=str(scratch), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTHONPATH=str(Path(__file__).resolve().parent.parent))
    env.pop("CCC_SHIP_GRAPH_DB", None)
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_database.py"],
            cwd=suite, env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == int(fail_test), result.stdout + result.stderr
        assert ("1 failed" if fail_test else "1 passed") in result.stdout
        assert list(scratch.glob("ccc-test-ship-*")) == []


def test_ship_graph_override_remains_caller_owned(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    shutil.copyfile(Path(__file__).with_name("conftest.py"), suite / "conftest.py")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    database = tmp_path / "caller.sqlite"
    (suite / "test_database.py").write_text(
        "import os, sqlite3\n"
        "from pathlib import Path\n"
        "def test_database():\n"
        "    assert not list(Path(os.environ['TMPDIR']).glob('ccc-test-ship-*'))\n"
        "    with sqlite3.connect(os.environ['CCC_SHIP_GRAPH_DB']) as con:\n"
        "        con.execute('CREATE TABLE payload (value INTEGER)')\n"
        "        con.execute('INSERT INTO payload VALUES (42)')\n",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_database.py"], cwd=suite,
        env=dict(os.environ, TMPDIR=str(scratch), CCC_SHIP_GRAPH_DB=str(database),
                 PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                 PYTHONPATH=str(Path(__file__).resolve().parent.parent)),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(scratch.glob("ccc-test-ship-*")) == []
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT value FROM payload").fetchone() == (42,)
