"""One foreground capture client shared by the existing proof runners."""
import json
import os
import re
import time
from pathlib import Path
import selectors
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / '.claude/screenshots/launch-captures'


def reply_body(text):
    """Remove only CCC's standard On-call service header, retain the body."""
    return re.sub(r"^\[sender: ccc:oncall \(service\) · msg [0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\]\r?\n",
                  "", text.strip(), count=1)


def cleanup_receipt(home, **details):
    """Verify exact private HOME cleanup without recording that private path."""
    marker = ('HOME=' + str(home)).encode()
    deadline = time.monotonic() + 3
    while True:
        remaining = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                if marker in (entry / 'environ').read_bytes().split(b'\0'):
                    remaining.append(int(entry.name))
            except OSError:
                pass
        if not remaining or time.monotonic() >= deadline:
            break
        time.sleep(.05)
    data = {'status': 'FAIL' if remaining or details.get('docker_cleanup_errors') else 'PASS',
            'remaining_private_home_process_count': len(remaining), **details}
    (output_dir() / 'cleanup.json').write_text(json.dumps(data, indent=2))
    if remaining:
        raise RuntimeError('Private HOME processes survived cleanup')
    return data


def output_dir():
    dest = Path(os.environ.get('PROOF_CAPTURE_DIR', OUTPUT)).resolve()
    if not dest.is_relative_to(OUTPUT.resolve()):
        raise ValueError('Capture output must stay inside launch-captures')
    dest.mkdir(parents=True, exist_ok=True)
    return dest


class Capture:
    def __init__(self, web, topic, env=None):
        env = dict(os.environ if env is None else env)
        node = env.get('PROOF_NODE') or shutil.which('node')
        self.log = (output_dir() / 'capture-stderr.log').open('w')
        self.proc = subprocess.Popen(['/usr/bin/nice', '-n', '15', node,
            str(REPO / 'scripts/oncall-capture.cjs'), web, topic], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, start_new_session=True)

    def step(self, step, **values):
        self.proc.stdin.write(json.dumps(dict(step=step, **values)) + '\n')
        self.proc.stdin.flush()
        with selectors.DefaultSelector() as poll:
            poll.register(self.proc.stdout, selectors.EVENT_READ)
            if not poll.select(90):
                raise TimeoutError('Capture command exceeded 90 seconds: ' + step)
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('Capture stopped; inspect capture-stderr.log')
        result = json.loads(line)
        if not result.get('ok'):
            raise RuntimeError('Capture failed: ' + result.get('error', 'unknown'))
        return result.get('result')

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.log.close()
