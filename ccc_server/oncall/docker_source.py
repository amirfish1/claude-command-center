"""One-container Docker health monitoring with bounded evidence."""
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import time
import uuid

from .ci_source import scrub
from .sources import Source, _Problem, _field, _finding, _int, _need

FIELDS = [_field('container', 'Container name'),
          _field('restart_threshold', 'Restarts per poll', '3', optional=True),
          _field('every_s', 'Seconds between checks', '300', optional=True)]
MAX_BYTES = 65536
TIMEOUT = 10


def _command(argv):
    """Capture at most MAX_BYTES and kill the child on overflow or timeout."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        raise _Problem('Could not start Docker.')
    data = bytearray()
    deadline = time.monotonic() + TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Problem('Docker command timed out.')
                if not selector.select(remaining):
                    raise _Problem('Docker command timed out.')
                chunk = os.read(proc.stdout.fileno(), min(8192, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise _Problem('Docker output exceeded the limit.')
        try:
            code = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise _Problem('Docker command timed out.')
        if code:
            raise _Problem('Docker command failed.')
        return bytes(data)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        proc.stdout.close()


class DockerSource(Source):
    type = 'docker'

    def _setup(self, cfg):
        self.container = _need(cfg, 'container')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', self.container):
            raise ValueError('Invalid container name.')
        self.threshold = _int(cfg, 'restart_threshold', 3, 1, 10000)
        self.command = _command

    def _inspect(self):
        try:
            rows = json.loads(self.command(['docker', 'inspect', '--', self.container]))
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError
            row = rows[0]
            state = row['State']
            count = int(row['RestartCount'])
            if not isinstance(state, dict) or count < 0 or not isinstance(state.get('Status'), str):
                raise ValueError
            return state, count
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise _Problem('Docker inspect returned an unexpected response.')

    def _check(self):
        _state, count = self._inspect()
        if not self._get('docker_state'):
            self._set('docker_state', {'count': count, 'down': False})
        return 'Docker container found.'

    def _poll(self):
        state, count = self._inspect()
        old = self._get('docker_state', {})
        status = state['Status']
        health = state.get('Health') or {}
        bad = (status == 'restarting' or bool(state.get('Restarting')) or
               (isinstance(health, dict) and health.get('Status') == 'unhealthy') or
               (status == 'exited' and int(state.get('ExitCode', 0)) != 0) or
               (old.get('count') is not None and count - old['count'] >= self.threshold))
        findings = []
        if bad and not old.get('down'):
            try:
                output = self.command(['docker', 'logs', '--tail', '60', '--', self.container])
                excerpt = scrub(output.decode('utf-8', 'replace'), self._redact)[-3000:]
            except _Problem:
                excerpt = '(logs unavailable)'
            fp = 'docker:' + hashlib.sha256((self.id + ':' + uuid.uuid4().hex).encode()).hexdigest()[:40]
            findings.append(_finding(fp, 'Docker container unhealthy: ' + self.container,
                scrub(f'Container: {self.container}\nStatus: {status}\nHealth: {health.get("Status", "unknown") if isinstance(health, dict) else "unknown"}\nRestart count: {count}\nRecent logs (untrusted):\n{excerpt}', self._redact)))
        self._set('docker_state', {'down': bool(bad), 'count': count})
        return findings
