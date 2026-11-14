# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Dashboard lifecycle evidence, also runnable by systemd after an OOM kill.

No server imports: ExecStopPost must work when the dashboard cannot. Memory
samples separate process RSS from cgroup charges (including children and file
cache). A killed process's final RSS is unknowable; retain the sample timestamp
instead of presenting the last sample as memory measured at death.
"""

import atexit
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid

try:
    import resource
except ImportError:  # Windows has no resource or /proc.
    resource = None


_active = None


def _read_number(target):
    try:
        return int(target.read_text().strip())
    except (OSError, ValueError):
        return None


def _pairs(target):
    try:
        return {key: int(value) for key, value in
                (line.split() for line in target.read_text().splitlines())}
    except (OSError, ValueError):
        return {}


def _cgroup():
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            if line.startswith('0::'):
                relative = line[3:].lstrip('/')
                if '..' not in Path(relative).parts:
                    return Path('/sys/fs/cgroup') / relative
    except OSError:
        pass
    return None


def _process_memory(pid):
    result = {'pid': pid}
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            key, _, value = line.partition(':')
            if key in ('VmRSS', 'VmHWM'):
                result[{'VmRSS': 'rss_bytes', 'VmHWM': 'rss_peak_bytes'}[key]] = int(value.split()[0]) * 1024
            elif key == 'Name':
                result['comm'] = value.strip()
    except (OSError, ValueError):
        pass
    return result


def memory_snapshot(include_self=True):
    result = {}
    if include_self:
        result.update(_process_memory(os.getpid()))
        # macOS reports bytes; Linux reports KiB. This is a high-water mark,
        # never a substitute for current RSS on platforms without /proc.
        if resource is not None:
            result.setdefault('rss_peak_bytes', int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                              * (1 if sys.platform == 'darwin' else 1024))
    group = _cgroup()
    if group is not None:
        for name in ('current', 'peak', 'high', 'max'):
            result[f'cgroup_{name}_bytes'] = _read_number(group / f'memory.{name}')
        result['cgroup_events'] = _pairs(group / 'memory.events')
        stats = _pairs(group / 'memory.stat')
        result['cgroup_breakdown_bytes'] = {key: stats[key] for key in
                                           ('anon', 'file', 'kernel', 'shmem') if key in stats}
        try:
            processes = [_process_memory(int(pid)) for pid in (group / 'cgroup.procs').read_text().split()]
            result['cgroup_process_count'] = len(processes)
            result['cgroup_top_rss'] = sorted(processes, key=lambda p: p.get('rss_bytes', 0), reverse=True)[:8]
        except (OSError, ValueError):
            pass
    return result


def _append(state_dir, event, payload):
    log = Path(state_dir) / 'logs' / 'activity.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    # Use the existing activity-v2 envelope: JSON escapes untrusted caller
    # strings/newlines, and old/new log readers both retain the full detail.
    envelope = {'category': 'dashboard', 'verb': event,
                'detail': json.dumps(payload, separators=(',', ':'))}
    line = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()) + '  @ccc-activity-v2 ' + json.dumps(envelope) + '\n'
    with log.open('a', encoding='utf-8') as stream:
        stream.write(line)


def _snapshot_file(state_dir, invocation):
    # systemd invocation IDs are hex; never use arbitrary env text as a path.
    safe = ''.join(c for c in invocation if c in '0123456789abcdef')[:32]
    return Path(state_dir) / 'restart-audit' / f'{safe or "foreground"}.json'


class Audit:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.started = time.monotonic()
        self.generation = uuid.uuid4().hex
        self.invocation = os.environ.get('INVOCATION_ID', '')
        self.done = threading.Event()
        self.ended = False
        self.last_request = None
        self.last_peak = 0

    def sample(self):
        return {'generation': self.generation, 'invocation_id': self.invocation,
                'pid': os.getpid(), 'sampled_at': time.time(),
                'uptime_s': round(time.monotonic() - self.started, 3),
                'memory': memory_snapshot(), 'restart_request': self.last_request}

    def save(self, payload):
        target = _snapshot_file(self.state_dir, self.invocation)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.sample-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream)
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def record(self, event, caller, reason):
        try:
            if event == 'RESTART':
                self.last_request = {'caller': caller, 'reason': reason, 'at': time.time()}
            payload = self.sample()
            payload.update(caller=caller, reason=reason, phase=event)
            self.save(payload)
            _append(self.state_dir, event, payload)
        except (OSError, ValueError):
            pass  # Diagnostics must not prevent stopping/restarting.

    def stop(self, reason, caller='process'):
        if not self.ended:
            self.ended = True
            self.done.set()
            self.record('STOP', caller, reason)

    def monitor(self):
        while not self.done.wait(30):
            try:
                payload = self.sample()
                self.save(payload)
                peak = payload['memory'].get('cgroup_peak_bytes') or 0
                if peak >= self.last_peak + 64 * 1024 * 1024:
                    self.last_peak = peak
                    _append(self.state_dir, 'MEMORY', payload)
            except (OSError, ValueError):
                pass


def start(state_dir):
    global _active
    _active = Audit(state_dir)
    _active.record('START', 'process', 'dashboard-start')
    atexit.register(_active.stop, 'process-exit')
    threading.Thread(target=_active.monitor, name='restart-audit', daemon=True).start()


def record_restart(caller, reason):
    if _active is not None:
        _active.record('RESTART', caller, reason)


def stop(reason, caller='process'):
    if _active is not None:
        _active.stop(reason, caller)


def request_caller(handler, payload=None):
    """Transport evidence, not authenticated identity; no URL/query or secrets."""
    result = {'source': 'http', 'peer': str(handler.client_address[0])[:80],
              'reported_client': 'browser-or-api'}
    reported = payload.get('audit_caller') if isinstance(payload, dict) else None
    if isinstance(reported, dict) and reported.get('source') == 'ccc-cli':
        result['reported_client'] = 'ccc-cli'
        for field in ('pid', 'ppid'):
            value = reported.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result['reported_' + field] = value
        chain = reported.get('pid_chain')
        if isinstance(chain, list):
            result['reported_pid_chain'] = [pid for pid in chain[:16]
                                            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0]
    return result


def systemd_stop_post(state_dir):
    invocation = os.environ.get('INVOCATION_ID', '')
    try:
        sample = json.loads(_snapshot_file(state_dir, invocation).read_text())
    except (OSError, ValueError):
        sample = {}
    if not isinstance(sample, dict) or sample.get('invocation_id') != invocation:
        sample = {}
    sampled_at = sample.get('sampled_at')
    payload = {'caller': 'systemd', 'reason': os.environ.get('SERVICE_RESULT', 'unknown'),
               'exit_code': os.environ.get('EXIT_CODE', 'unknown'),
               'exit_status': os.environ.get('EXIT_STATUS', 'unknown'),
               'invocation_id': invocation, 'last_process_sample': sample or None,
               'sample_age_s': round(time.time() - sampled_at, 3) if isinstance(sampled_at, (int, float)) else None,
               'memory_after_stop': memory_snapshot(include_self=False),
               'initiating_client': 'unknown (systemd does not pass the requesting client to ExecStopPost)'}
    try:
        _append(state_dir, 'STOP_POST', payload)
    except OSError:
        pass


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--systemd-stop-post', action='store_true', required=True)
    parser.add_argument('--state-dir', type=Path, default=Path.home() / '.claude' / 'command-center')
    args = parser.parse_args()
    systemd_stop_post(args.state_dir)
