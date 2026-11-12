"""An isolated, persistent WatchTower CLI process for the fast local demo.

The production On-call WT adapter sends normal CLI arguments here. Keeping
one parser alive avoids interpreter startup for every message in the demo.
Only this child imports WatchTower, after its private environment is installed.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import selectors
import signal
import shutil
import shlex
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ccc_server.oncall.wt import WT, WTError, decision_details


def interpreter():
    if importlib.util.find_spec('watchtower') is not None:
        return sys.executable
    # pipx/venv installs can expose wt without exposing its package to CCC.
    executable = shutil.which('wt')
    if executable:
        with open(executable, 'rb') as stream:
            first = stream.readline(512).decode('utf-8', errors='replace')
        if first.startswith('#!'):
            parts = shlex.split(first[2:])
            if parts and Path(parts[0]).name == 'env':
                parts = parts[1:]
            if parts and 'python' in Path(parts[0]).name:
                return shutil.which(parts[0])
    return None


def installed():
    return bool(interpreter())


class RealTickets(WT):
    backend = 'watchtower'

    def __init__(self, root, env):
        self.ref = ''
        spec = importlib.util.find_spec('watchtower')
        # WatchTower is stdlib-only. Avoid unrelated site hooks on the hot path
        # while retaining the installed package's actual CLI implementation.
        argv = ([sys.executable, '-S', str(Path(__file__).resolve()), '--bridge',
                 # Hand over the parent's whole import path: the package's
                 # parent directory is not always where an editable or
                 # path-file install actually resolves it from.
                 os.pathsep.join([str(Path(spec.origin).parent.parent)]
                                 + [p for p in sys.path if p])]
                if spec and spec.origin else
                [interpreter(), str(Path(__file__).resolve()), '--bridge'])
        self.proc = subprocess.Popen(argv,
                                     cwd=root / 'project', env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, start_new_session=True)
        super().__init__(argv=['wt'], run=self.call, env=env, dispatch=False)

    def call(self, argv, **kwargs):
        try:
            self.proc.stdin.write(json.dumps(argv[1:]) + '\n')
            self.proc.stdin.flush()
        except BrokenPipeError:
            # The bridge died before reading: its stderr is the real reason.
            self.proc.wait(timeout=5)
            raise WTError('Isolated WatchTower exited: ' + self.proc.stderr.read()[-1000:])
        with selectors.DefaultSelector() as selector:
            selector.register(self.proc.stdout, selectors.EVENT_READ)
            if not selector.select(kwargs.get('timeout', 30)):
                raise subprocess.TimeoutExpired(argv, 30)
        line = self.proc.stdout.readline()
        if not line:
            raise WTError('Isolated WatchTower exited: ' + self.proc.stderr.read()[-1000:])
        result = json.loads(line)
        return subprocess.CompletedProcess(argv, result['code'], result['out'], result['err'])

    @property
    def item(self):
        return self.find(self.ref)

    def add(self, *args, **kwargs):
        self.ref = super().add(*args, **kwargs)
        return self.ref

    def start(self):
        self._ok('claim', '-q', 'DEMO', self.ref, '--worker', 'demo-worker')

    def block(self):
        self._ok('block', self.ref, '--question',
                 'An empty cart crashes. Show zero or hide the total? Reply zero or hide.',
                 '--wait-on', 'owner')

    def answered(self, answer):
        return any(h.get('event') == 'answer' and h.get('text') == answer
                   for h in self.item.get('history', []))

    def close(self, commit):
        self._ok('close', self.ref, '--worker', 'demo-worker', '--commit', commit, '--summary', 'Sample test passes.')

    def refund(self, commit):
        from .demo_refund import QUESTION
        self._ok('block', self.ref, '--worker', 'demo-worker', '--question', QUESTION,
                 '--weight', 'minor', '--class', 'code-change-revertable', '--confidence', '0.99',
                 '--recommend', 'Issue the requested $5 refund.', '--commit', commit,
                 '--undo', 'git revert ' + commit,
                 '--attest', 'money=yes,customer-sends=no,public-posts=no,ad-spend=no,irreversible=no')
        evidence = [h for h in self.item.get('history', []) if h.get('event') == 'auto_decision_declined']
        if not evidence or not self.item['needs_input']:
            raise RuntimeError('WatchTower did not reject the refund proposal.')
        return evidence[-1].get('text', '')

    def minor(self, commit):
        ref = super().add('DEMO', title='Ignore Python cache files', text='Ignore generated Python cache files.')
        outcome = self._ok('block', ref, '--worker', 'demo-worker', '--question', 'Ignore Python cache files?',
                 '--weight', 'minor', '--class', 'code-change-revertable',
                 '--confidence', '0.99', '--recommend', 'Ignore generated Python cache files.',
                 '--commit', commit, '--undo', 'git revert ' + commit,
                 '--attest', 'money=no,customer-sends=no,public-posts=no,ad-spend=no,irreversible=no')
        rows = self.decisions('DEMO')
        if not any(r['ref'] == ref for r in rows):
            raise RuntimeError('WatchTower did not approve the minor decision: ' + outcome)
        details = decision_details(self.find(ref))
        self._ok('close', ref, '--worker', 'demo-worker', '--commit', commit, '--summary', 'Cache ignore applied and verified.')
        return details[-1]

    def shutdown(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(self.proc.stdout, selectors.EVENT_READ)
                    if not selector.select(3):
                        raise subprocess.TimeoutExpired('WatchTower exit', 3)
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=3)
        self.proc.stdout.close()
        self.proc.stderr.close()


def close_verified(cli, args):
    """Use WatchTower's proof and store without installed-checkout deployment.

    cmd_close also syncs the installed WatchTower checkout. A demo must never
    run that housekeeping, even with a private HOME and ticket store.
    """
    from watchtower import queue
    if not args.commit or not args.summary:
        raise ValueError('The demo close requires a commit and summary.')
    verified, error = cli._verify_close_commit(args.ref, args.commit)
    if error:
        raise ValueError(error)
    args.commit = verified
    item = queue.close(args.ref, args.worker,
                       resolution=cli._resolution_from_args(args))
    if not item or item.get('status') != 'closed':
        raise RuntimeError('WatchTower did not close the demo ticket.')
    print('CLOSED: ' + item['ref'])
    return 0


def bridge():
    from watchtower import cli
    parser = cli.build_parser()
    with contextlib.redirect_stdout(sys.stderr):
        setup = parser.parse_args(['config', '-q', 'DEMO', '--auto-drain', 'off'])
        setup.message_attr = None
        setup.func(setup)
    for line in sys.stdin:
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                argv = json.loads(line)
                args = parser.parse_args(argv)
                args.message_attr = None
                code = (close_verified(cli, args) if argv[0] == 'close'
                        else args.func(args)) or 0
        except (Exception, SystemExit) as exc:
            code = 1
            err.write('Command ' + repr(argv) + ': ' + str(exc))
        print(json.dumps(dict(code=code, out=out.getvalue(), err=err.getvalue())), flush=True)


if __name__ == '__main__':
    if len(sys.argv) > 2:
        sys.path[:0] = sys.argv[2].split(os.pathsep)
    bridge()
