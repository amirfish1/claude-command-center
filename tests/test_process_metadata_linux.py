"""Native process metadata must not fork once per candidate or ancestor."""
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from ccc_server import session_graph as graph


@pytest.mark.linux_only
def test_cwd_tracks_real_process_without_lsof(tmp_path):
    with subprocess.Popen([sys.executable, "-u", "-c",
                           "import sys; print('ready', flush=True); sys.stdin.read()"],
                          cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as child:
        try:
            assert child.stdout.readline().strip() == "ready"
            graph._proc_cwd.cache_clear()
            with mock.patch.object(graph.subprocess, "run", side_effect=AssertionError("unexpected fork")):
                assert graph._proc_cwd(child.pid) == str(tmp_path)
        finally:
            child.communicate(input="", timeout=5)
    graph._proc_cwd.cache_clear()
    assert graph._proc_cwd(child.pid) is None


@pytest.mark.linux_only
def test_terminal_walk_uses_real_parent_chain_with_parentheses_in_comm():
    parent_script = """
import ctypes, subprocess, sys
ctypes.CDLL(None).prctl(15, b'kitty', 0, 0, 0)
script = "import ctypes,sys; ctypes.CDLL(None).prctl(15,b'worker ) tail',0,0,0); print('ready',flush=True); sys.stdin.read()"
with subprocess.Popen([sys.executable, '-u', '-c', script], stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, text=True) as child:
    try:
        assert child.stdout.readline().strip() == 'ready'
        print(child.pid, flush=True)
        sys.stdin.read()
    finally:
        child.communicate(input='', timeout=5)
"""
    with subprocess.Popen([sys.executable, "-u", "-c", parent_script],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as parent:
        try:
            child_pid = int(parent.stdout.readline())
            graph._proc_ancestor_terminal.cache_clear()
            with mock.patch.object(graph.subprocess, "run", side_effect=AssertionError("unexpected fork")):
                assert graph._proc_ancestor_terminal(child_pid) == ("kitty", parent.pid)
        finally:
            parent.communicate(input="", timeout=5)
    graph._proc_ancestor_terminal.cache_clear()
    assert graph._proc_ancestor_terminal(parent.pid) == (None, None)
