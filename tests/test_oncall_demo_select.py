"""One key works without Enter, and the caller's terminal mode is restored."""
import os
from pathlib import Path
import pty
import selectors
import subprocess
import sys
import termios


def test_scene_picker_one_key():
    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    root = Path(__file__).resolve().parents[1]
    script = '''
import tty
from ccc_server.oncall.demo_select import pick
original = tty.setcbreak
def ready(fd):
    original(fd)
    print('READY', flush=True)
tty.setcbreak = ready
print('SCENE=' + pick(), flush=True)
'''
    proc = subprocess.Popen([sys.executable, '-S', '-c', script], cwd=root,
                            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            assert selector.select(5), 'Picker did not become ready'
        assert proc.stdout.readline().strip() == b'READY'
        os.write(master, b'2')
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode == 0, stderr
        assert b'SCENE=ci' in stdout
        assert termios.tcgetattr(slave) == before
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(slave)
        os.close(master)
