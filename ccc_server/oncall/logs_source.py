"""Bounded local log intake; persisted evidence is scrubbed before storage."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import selectors
import stat
import signal
import subprocess
import time

from .ci_source import scrub
from .sources import Source, _Problem, _field, _finding, _text

FIELDS = [_field('path', 'Log file', optional=True),
          _field('unit', 'Journal unit', optional=True),
          _field('container', 'Docker container', optional=True),
          _field('every_s', 'Seconds between checks', '300', optional=True)]
MAX_READ = 256 * 1024
MAX_LINE = 16 * 1024
MAX_EVENTS = 128
MAX_FINDINGS = 3
MAX_OUTPUT = 512 * 1024
TIMEOUT = 10
_ERROR = re.compile(r'\b(error|exception|fatal|critical|panic|traceback)\b|\b\w+(?:Error|Exception):', re.I)
_TIME = re.compile(r'\b\d{4}-\d\d-\d\d[T ][\d:.]+(?:Z|[+-][\d:]+)?')
_NUMBER = re.compile(r'\b(?:0x[0-9a-f]+|\d+)\b', re.I)


def _clean(value):
    value = re.sub(r'(?i)-{2,}\s*BEGIN(?:\s+[^\r\n]*)?$', '[redacted private key]', value)
    value = scrub(value)
    # Also cover common query-string credentials and bearer values.
    value = re.sub(r'(?i)([?&](?:token|key|secret|password|auth)=)[^&\s]+', r'\1[redacted]', value)
    return re.sub(r'(?i)\bbearer\s+\S+', 'Bearer [redacted]', value)


def _excerpt(value):
    return value.encode('utf-8')[:3000].decode('utf-8', 'ignore')


def _signature(line):
    line = _NUMBER.sub('#', _TIME.sub('', line.lower()))
    return hashlib.sha256(' '.join(line.split()).encode()).hexdigest()


def _open_file(path):
    handle = os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb')
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
        handle.close()
        raise _Problem('Log path must be a regular file.')
    return handle


def _command(argv, *, skip=None, max_records=64, max_output=MAX_OUTPUT):
    """Read a bounded page of complete records, with a bounded replay budget."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        raise _Problem('Could not start the log command.')
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + TIMEOUT
    output, fragment = bytearray(), bytearray()
    scanned = 0
    limited = False
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Problem('Log command timed out; cursor was not advanced.')
            for key, _ in selector.select(remaining):
                data = os.read(key.fileobj.fileno(), 4096)
                if not data:
                    selector.unregister(key.fileobj)
                    if fragment:
                        raise _Problem('Log command returned an incomplete record; cursor was not advanced.')
                    break
                scanned += len(data)
                if scanned > 32 * 1024 * 1024:
                    raise _Problem('Log replay exceeded the scan limit; cursor was not advanced.')
                fragment.extend(data)
                while b'\n' in fragment:
                    line, _, rest = fragment.partition(b'\n')
                    fragment = bytearray(rest)
                    if len(line) > MAX_LINE:
                        raise _Problem('Log record exceeds the limit; cursor was not advanced.')
                    if skip and skip(line):
                        continue
                    output.extend(line + b'\n')
                    if len(output) >= max_output or output.count(b'\n') >= max_records:
                        limited = True
                        break
                if len(fragment) > MAX_LINE and not limited:
                    raise _Problem('Log record exceeds the limit; cursor was not advanced.')
                if limited:
                    break
            if limited:
                break
        if not limited:
            proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            if proc.returncode:
                raise _Problem('Log command failed; check its availability and permissions.')
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise _Problem('Log command timed out; cursor was not advanced.')
    finally:
        # The leader may already have exited while descendants still hold pipes.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        selector.close()
        proc.stdout.close()


class LogsSource(Source):
    type = 'logs'

    def _setup(self, cfg):
        values = [(key, _text(cfg, key)) for key in ('path', 'unit', 'container')]
        selected = [(key, value) for key, value in values if value]
        if len(selected) != 1:
            raise ValueError('Specify exactly one of path, unit or container.')
        self.kind, self.target = selected[0]
        if self.kind != 'path' and (self.target.startswith('-') or '\n' in self.target):
            raise ValueError('Invalid log target.')
        if self.kind == 'path':
            self.target = os.path.expanduser(self.target)

    def _check(self):
        if self.kind == 'path':
            try:
                with _open_file(self.target) as handle:
                    stat = os.fstat(handle.fileno())
                    self._set('position', {'dev': stat.st_dev, 'ino': stat.st_ino, 'offset': stat.st_size})
            except OSError:
                raise _Problem('Cannot read the log file.')
        elif self.kind == 'unit':
            rows = self._journal(None, baseline=True)
            self._set('cursor', rows[-1]['__CURSOR'] if rows else '')
            self._set('journal_since', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self._clock())))
        else:
            tail = self._docker(None, baseline=True)
            if not tail:
                position = self._docker_position([])
            else:
                marker = tail[-1]
                rows = self._docker(marker[0], baseline_scan=True)
                if len(rows) > 1000:
                    raise _Problem('Docker baseline exceeds 1000 records; retry when logging slows.')
                if marker not in rows:
                    raise _Problem('Docker baseline could not recover the latest record within the limit.')
                # Setup ends at this snapshot. Count every existing identical
                # record so old errors cannot become new on the first poll.
                position = self._docker_position(rows)
            self._set('docker', position)
        self._set('baselined', True)
        return 'Log source connected; existing content is ignored.'

    def _journal(self, cursor, baseline=False):
        args = ['journalctl', '--no-pager', '--output=json', '--unit', self.target]
        args += ['--lines=1'] if baseline else (['--after-cursor', cursor] if cursor else ['--since', self._get('journal_since', 'now')])
        raw = _command(args)
        try:
            rows = [json.loads(line) for line in raw.splitlines()]
            if any(not isinstance(r, dict) or not isinstance(r.get('__CURSOR'), str) for r in rows):
                raise ValueError
            return rows
        except (ValueError, UnicodeError):
            raise _Problem('Journal output was incomplete or malformed; cursor was not advanced.')

    def _docker(self, since, baseline=False, seen=None, baseline_scan=False):
        args = ['docker', 'logs', '--timestamps']
        if baseline:
            args += ['--tail', '1']
        elif since:
            args += ['--since', since]
        args += [self.target]
        remaining = dict(seen or {})
        def skip(line):
            timestamp, sep, message = line.partition(b' ')
            if not sep or timestamp.decode('ascii', 'replace') != since:
                return False
            digest = hashlib.sha256(message.decode('utf-8', 'replace').encode()).hexdigest()
            if remaining.get(digest, 0):
                remaining[digest] -= 1
                return True
            return False
        if baseline_scan:
            raw = _command(args, max_records=1001, max_output=1001 * (MAX_LINE + 1))
        else:
            raw = _command(args, skip=skip) if not baseline else _command(args)
        rows = []
        for line in raw.splitlines():
            timestamp, sep, message = line.partition(b' ')
            if not sep or not re.fullmatch(rb'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z', timestamp):
                raise _Problem('Docker output was incomplete or malformed; cursor was not advanced.')
            rows.append((timestamp.decode('ascii'), message.decode('utf-8', 'replace')))
        return rows

    def _docker_position(self, rows):
        if not rows:
            # Baseline based on wall clock for an empty container.
            return {'since': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self._clock())), 'seen': {}}
        latest = rows[-1][0]
        counts = {}
        for stamp, message in rows:
            if stamp == latest:
                digest = hashlib.sha256(message.encode()).hexdigest()
                counts[digest] = counts.get(digest, 0) + 1
        return {'since': latest, 'seen': counts}

    def _read_file(self):
        pos = self._get('position', {})
        try:
            handle = _open_file(self.target)
            stat = os.fstat(handle.fileno())
            same = (stat.st_dev, stat.st_ino) == (pos.get('dev'), pos.get('ino'))
            if pos and not same:
                handle.close()
                handle = None
                # A rename leaves the old inode in the same directory in common
                # rotations. Refuse to skip it if it cannot be recovered.
                with os.scandir(os.path.dirname(os.path.abspath(self.target))) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 1024:
                            break
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if (info.st_dev, info.st_ino) == (pos['dev'], pos['ino']):
                                candidate = _open_file(entry.path)
                                found = os.fstat(candidate.fileno())
                                if (found.st_dev, found.st_ino) == (pos['dev'], pos['ino']):
                                    handle = candidate
                                    break
                                candidate.close()
                        except OSError:
                            continue
                if handle is None:
                    raise _Problem('Rotated log inode is unavailable; cursor was not advanced.')
                stat = os.fstat(handle.fileno())
                if stat.st_size <= pos.get('offset', 0):
                    handle.close()
                    handle = _open_file(self.target)
                    stat = os.fstat(handle.fileno())
                    same = False
                else:
                    same = True
            with handle:
                offset = pos.get('offset', 0) if same and stat.st_size >= pos.get('offset', 0) else 0
                handle.seek(offset)
                data = handle.read(MAX_READ)
                end = data.rfind(b'\n')
                if end < 0 and len(data) >= MAX_LINE:
                    raise _Problem('Log line exceeds the limit; cursor was not advanced.')
                if end < 0:
                    return [], {'dev': stat.st_dev, 'ino': stat.st_ino, 'offset': offset}
                block = data[:end + 1]
                parts = block.splitlines(keepends=True)
                block = b''.join(parts[:64])
                if any(len(line) > MAX_LINE for line in block.splitlines()):
                    raise _Problem('Log line exceeds the limit; cursor was not advanced.')
                return block.decode('utf-8', 'replace').splitlines(), {
                    'dev': stat.st_dev, 'ino': stat.st_ino, 'offset': offset + len(block)}
        except OSError:
            raise _Problem('Cannot read the log file; cursor was not advanced.')

    def _poll(self):
        if not self._get('baselined'):
            raise _Problem('Check the source before polling to establish a baseline.')
        pending = self._get('pending', [])
        # Drain first, keeping intake bounded and preserving excess signatures.
        if pending:
            return self._emit(pending)
        if self.kind == 'path':
            lines, position = self._read_file()
        elif self.kind == 'unit':
            rows = self._journal(self._get('cursor', ''))
            lines = [str(row.get('MESSAGE', '')) for row in rows]
            position = rows[-1]['__CURSOR'] if rows else self._get('cursor', '')
        else:
            old = self._get('docker', {})
            rows = self._docker(old.get('since'), seen=old.get('seen'))
            lines = [message for _, message in rows]
            if rows:
                position = self._docker_position(rows)
                if position['since'] == old.get('since'):
                    for digest, count in old.get('seen', {}).items():
                        position['seen'][digest] = position['seen'].get(digest, 0) + count
            else:
                position = old
        # Process only as much as can be safely queued; otherwise retry unchanged.
        pending, active = self._collect(lines, pending, self._get('active'))
        self._set('pending', pending)
        self._set('active', active)
        self._set({'path': 'position', 'unit': 'cursor', 'container': 'docker'}[self.kind], position)
        return self._emit(pending)

    def _collect(self, lines, pending, active):
        pending = copy.deepcopy(pending)
        active = copy.deepcopy(active)
        def finish(event):
            if not event:
                return
            fp = 'logs:' + event['sig']
            if self._get('seen:' + event['sig'], False):
                return
            existing = next ( (p for p in pending if p['sig'] == event['sig']), None)
            if existing:
                existing['count'] += event['count']
            else:
                if len(pending) >= MAX_EVENTS:
                    raise _Problem('Too many distinct log errors in one read; cursor was not advanced.')
                pending.append(event)
        for raw in lines:
            line = _clean(raw)[:MAX_LINE]
            continuation = bool(active and (raw.startswith((' ', '\t')) or
                                  re.match(r'^(?:Traceback|During handling|The above exception)', raw)))
            if continuation:
                if re.match(r'^\s*File \"', raw):
                    active['frames'] = (active.get('frames', '') + '\n' + line)[-2000:]
                # Keep stack locations, not arbitrary source or key material.
                if re.match(r'^\s*File \"', raw):
                    active['excerpt'] = _excerpt(active['excerpt'] + '\n' + line)
                continue
            if active and 'traceback' in active['excerpt'].lower() and re.match(r'^(?:[\w.]*Error|[\w.]*Exception)(?::|$)', raw):
                active['excerpt'] = _excerpt(active['excerpt'] + '\n' + line)
                active['sig'] = _signature(active.get('frames', '') + '\n' + line)
                active.pop('frames', None)
                finish(active)
                active = None
                continue
            if active:
                finish(active)
                active = None
            if _ERROR.search(line):
                active = {'sig': _signature(line), 'excerpt': _excerpt(line), 'count': 1}
        # Hold traceback blocks over polls; ordinary lines can be filed immediately.
        if active and not ('traceback' in active['excerpt'].lower()):
            finish(active)
            active = None
        return pending, active

    def _emit(self, pending):
        batch = pending[:MAX_FINDINGS]
        findings = [_finding('logs:' + p['sig'], 'Log error: ' + p['excerpt'].splitlines()[0][:90],
                             'Occurrences in this batch: %s\n\n%s' % (p['count'], p['excerpt'])) for p in batch]
        for item in batch:
            self._set('seen:' + item['sig'], True)
        self._set('pending', pending[len(batch):])
        return findings
