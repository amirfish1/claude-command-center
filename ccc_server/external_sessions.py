"""Discover external headless Claude parentage from live process evidence.

No hooks or launch wrappers. Only confirmed edges and identifiers survive a
scan; raw arguments and environment are never written to disk or activity logs.
"""
import os
import re
import threading
import time

from ccc_server import core as _core
from ccc_server import process_identity

_SID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
_MAX_CANDIDATES = 16
_MAX_ATTEMPTS = 3
_RETRY_SECONDS = 15


def _birth(value):
    return ' '.join(str(value or '').split())


def _process_kind(comm, args):
    names = {os.path.basename(str(comm)), os.path.basename(str(args).split(None, 1)[0]) if args else ''}
    if 'claude' in names:
        return 'claude'
    if names & {'codex', 'Codex', 'codex-app-server'}:
        return 'codex'
    # Claude's native install can expose a versioned executable path.
    executable = str(args).split(None, 1)[0] if args else ''
    if '/claude/versions/' in str(comm) or '/claude/versions/' in executable:
        return 'claude'
    return ''


def _launch_options(argv):
    """Recognize a print launch without treating prompt/option values as flags.

    Unknown options before --print are deliberately inconclusive. Commands
    using --print first continue to work with any later Claude options.
    """
    valued = {'--model', '--permission-mode', '--session-id', '--name',
              '--append-system-prompt', '--system-prompt', '--settings',
              '--output-format', '--input-format', '--max-turns',
              '--max-budget-usd', '--json-schema', '--agent',
              '--allowedTools', '--disallowedTools', '--tools',
              '--mcp-config', '--setting-sources', '--add-dir'}
    flags = {'--dangerously-skip-permissions', '--verbose',
             '--no-session-persistence', '--strict-mcp-config',
             '--include-partial-messages', '--disable-slash-commands'}
    i, headless, model = 1, False, ''
    while i < len(argv):
        arg = argv[i]
        if arg == '--':
            break
        if arg in ('-p', '--print'):
            headless = True
            i += 1
            continue
        option, separator, attached = arg.partition('=')
        if option in valued:
            value = attached if separator else argv[i + 1] if i + 1 < len(argv) else ''
            if option == '--model' and re.fullmatch(r'[A-Za-z0-9_.\[\]-]{1,100}', value):
                model = value
            i += 1 if separator else 2
            continue
        if arg in flags:
            i += 1
            continue
        # After print has been seen, prompt text is harmless and later model
        # options can still be read. Before print, positional text is not an
        # unambiguous launch-option boundary.
        if not headless:
            return False, ''
        i += 1
    return headless, model


class _Discovery:
    def __init__(self):
        self._lock = threading.Lock()
        self._attempts = {}

    def scan(self, registry, process_rows, now=None):
        # Concurrent dashboard requests share one bounded scan.
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._scan(registry, process_rows, time.monotonic() if now is None else now)
        finally:
            self._lock.release()

    def _scan(self, registry, process_rows, now):
        graph = _core._session_graph
        keys = {}
        for sid, row in registry.items():
            if not _SID.fullmatch(str(sid)) or not isinstance(row, dict):
                continue
            try:
                pid = int(row.get('pid'))
            except (TypeError, ValueError):
                continue
            birth = _birth(row.get('procStart'))
            if pid > 0 and birth:
                keys[sid] = (sid, pid, birth)
        live_keys = set(keys.values())
        self._attempts = {key: value for key, value in self._attempts.items() if key in live_keys}
        candidates = []
        for sid, key in keys.items():
            count, after = self._attempts.get(key, (0, 0))
            if not graph.parent_of(sid) and count < _MAX_ATTEMPTS and now >= after:
                candidates.append(key)
        candidates = candidates[:_MAX_CANDIDATES]
        if not candidates:
            return
        snapshot = process_identity.process_snapshot()
        if not snapshot:
            return
        kinds = {int(pid): _process_kind(comm, args) for pid, _tty, comm, args in process_rows if str(pid).isdigit()}
        # Ambiguous PID-to-session mappings cannot establish an ancestor.
        pid_sessions = {}
        for sid, (_, pid, birth) in keys.items():
            if _birth(snapshot.get(pid, {}).get('birth')) == birth:
                pid_sessions.setdefault(pid, set()).add(sid)
        proposals = []
        codex_ids = set()
        for sid, pid, birth in candidates:
            count, _ = self._attempts.get((sid, pid, birth), (0, 0))
            self._attempts[(sid, pid, birth)] = (count + 1, now + _RETRY_SECONDS)
            if kinds.get(pid) != 'claude' or _birth(snapshot.get(pid, {}).get('birth')) != birth:
                continue
            identity = process_identity.read_launch_identity(pid)
            if not identity:
                continue
            argv = identity.get('argv', [])
            headless, model = _launch_options(argv)
            if not headless:
                continue
            env = identity.get('env', {})
            inherited = env.get('CODEX_THREAD_ID') or ''
            parent = ''
            evidence = ''
            chain = [pid]
            current = pid
            for _ in range(32):
                current = snapshot.get(current, {}).get('ppid', 0)
                if current <= 1 or current in chain or current not in snapshot:
                    break
                chain.append(current)
                if kinds.get(current) == 'claude':
                    matches = pid_sessions.get(current, set())
                    if len(matches) == 1:
                        parent = next(iter(matches))
                        evidence = 'claude-ancestor'
                    # Never skip an unidentified Claude to attach its child
                    # to an inherited Codex grandparent.
                    break
                if kinds.get(current) == 'codex':
                    if _SID.fullmatch(inherited) and inherited != sid:
                        parent = inherited
                        evidence = 'codex-environment'
                        codex_ids.add(parent)
                    break
            if parent and parent != sid:
                proposals.append((sid, parent, evidence, model, chain))
        known_codex = set()
        if codex_ids:
            ids = sorted(codex_ids)
            rows = _core._codex_fetch_threads('id IN (' + ','.join('?' for _ in ids) + ')', tuple(ids))
            known_codex = {row.get('id') for row in rows}
        proposals = [p for p in proposals if p[2] != 'codex-environment' or p[1] in known_codex]
        if not proposals:
            return
        # A second batched snapshot only for actual discoveries catches exit,
        # reparenting and PID reuse during metadata reads (no per-row forks).
        current_snapshot = process_identity.process_snapshot()
        for sid, parent, evidence, model, chain in proposals:
            if any(snapshot.get(pid) != current_snapshot.get(pid) for pid in chain):
                continue
            if graph.try_add_discovered_edge(parent, sid, model=model):
                _core._log_activity('spawn', 'DISCOVERED',
                    f'session={sid} parent_session={parent} engine=claude '
                    f'via=process-discovery evidence={evidence} model={model or "unknown"}')
        graph.save()


_discovery = _Discovery()
_enabled = False


def enable():
    """Enable only after the dashboard has loaded its durable graph."""
    global _enabled
    _enabled = os.environ.get('CCC_WORKER_PROCESS') != '1' and not os.environ.get('CCC_EPHEMERAL')


def discover(registry, process_rows):
    if _enabled:
        _discovery.scan(registry, process_rows)
