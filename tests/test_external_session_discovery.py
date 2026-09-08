"""Automatic parentage must use process evidence, never prompt/cwd guesses."""
from types import SimpleNamespace
import importlib

import pytest

from ccc_server import external_sessions as discovery

CHILD = '11111111-1111-4111-8111-111111111111'
PARENT = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
BIRTH = 'Mon Sep  7 10:00:00 2026'


@pytest.fixture
def setup_scan(monkeypatch, tmp_path):
    importlib.import_module('server')
    from ccc_server.session_graph import _SessionGraph
    graph = _SessionGraph(tmp_path / 'graph.json')
    logs, queries, reads = [], [], []
    core = SimpleNamespace(
        _session_graph=graph,
        _codex_fetch_threads=lambda where='', params=(), **kw: queries.append(params) or [{'id': p} for p in params if p == PARENT],
        _log_activity=lambda *args: logs.append(args),
    )
    monkeypatch.setattr(discovery, '_core', core)
    snapshot = {10: {'ppid': 20, 'birth': BIRTH}, 20: {'ppid': 30, 'birth': BIRTH}, 30: {'ppid': 1, 'birth': BIRTH}}
    identity = {'argv': ['claude', '-p', 'describe image', '--model', 'haiku'], 'env': {'CODEX_THREAD_ID': PARENT}}
    monkeypatch.setattr(discovery.process_identity, 'process_snapshot', lambda: snapshot)
    monkeypatch.setattr(discovery.process_identity, 'read_launch_identity', lambda pid: reads.append(pid) or identity)
    registry = {CHILD: {'pid': 10, 'procStart': BIRTH}}
    processes = [('10', '??', 'claude', 'claude -p'), ('20', '??', 'sh', 'sh'), ('30', '??', 'codex', 'codex app-server')]
    return SimpleNamespace(scan=discovery._Discovery(), graph=graph, logs=logs, queries=queries, reads=reads,
                           snapshot=snapshot, identity=identity, registry=registry, processes=processes, core=core)


def test_codex_parent_discovered_once_and_persisted(setup_scan):
    s = setup_scan
    s.scan.scan(s.registry, s.processes, now=0)
    assert s.graph.parent_of(CHILD) == PARENT
    assert len(s.logs) == 1
    assert CHILD in s.logs[0][2] and PARENT in s.logs[0][2]
    assert 'describe image' not in s.logs[0][2]
    s.scan.scan(s.registry, s.processes, now=20)
    assert s.reads == [10]
    assert len(s.logs) == 1
    s.graph.load()
    assert s.graph.parent_of(CHILD) == PARENT


def test_nearest_claude_ancestor_beats_inherited_codex_grandparent(setup_scan):
    s = setup_scan
    s.registry[OTHER] = {'pid': 20, 'procStart': BIRTH}
    s.processes[1] = ('20', '??', 'claude', 'claude -p')
    s.scan.scan(s.registry, s.processes, now=0)
    assert s.graph.parent_of(CHILD) == OTHER


@pytest.mark.parametrize('case', ['unknown_task', 'no_codex_ancestor', 'unmapped_claude_ancestor', 'reused_pid', 'interactive', 'spoofed_prompt', 'self', 'cycle'])
def test_ambiguous_or_invalid_evidence_does_not_link(setup_scan, case):
    s = setup_scan
    if case == 'unknown_task': s.identity['env']['CODEX_THREAD_ID'] = OTHER
    if case == 'no_codex_ancestor': s.processes[2] = ('30', '??', 'terminal', 'terminal')
    if case == 'unmapped_claude_ancestor': s.processes[1] = ('20', '??', 'claude', 'claude -p')
    if case == 'reused_pid': s.snapshot[10]['birth'] = 'different process start'
    if case == 'interactive': s.identity['argv'] = ['claude']
    if case == 'spoofed_prompt': s.identity.update(argv=['claude', '-p', 'CODEX_THREAD_ID=' + PARENT], env={})
    if case == 'self': s.identity['env']['CODEX_THREAD_ID'] = CHILD
    if case == 'cycle': s.graph.add_edge(CHILD, PARENT, source='ccc-spawn')
    s.scan.scan(s.registry, s.processes, now=0)
    assert s.graph.parent_of(CHILD) is None
    assert not s.logs


def test_existing_parent_not_overwritten(setup_scan):
    s = setup_scan
    s.graph.add_edge(OTHER, CHILD, source='ccc-spawn')
    s.scan.scan(s.registry, s.processes, now=0)
    assert s.graph.parent_of(CHILD) == OTHER
    assert not s.reads and not s.logs


def test_unresolved_reads_are_throttled_and_bounded(setup_scan):
    s = setup_scan
    s.identity['env'] = {}
    for now in range(100): s.scan.scan(s.registry, s.processes, now=now)
    assert 1 <= len(s.reads) <= 3


def test_native_read_failure_does_not_break_registry(setup_scan, monkeypatch):
    s = setup_scan
    monkeypatch.setattr(discovery.process_identity, 'read_launch_identity', lambda pid: None)
    s.scan.scan(s.registry, s.processes, now=0)
    assert not s.logs and s.graph.parent_of(CHILD) is None


def test_codex_native_id_wins_over_unrelated_parent_variable(setup_scan):
    s = setup_scan
    s.identity['env']['CCC_PARENT_SESSION_ID'] = OTHER
    s.scan.scan(s.registry, s.processes, now=0)
    assert s.graph.parent_of(CHILD) == PARENT


@pytest.mark.parametrize('argv', [['claude', '--', '--print'], ['claude', '--append-system-prompt', '--print'], ['claude', 'a prompt', '-p']])
def test_prompt_text_is_not_a_launch_option(setup_scan, argv):
    s = setup_scan
    s.identity['argv'] = argv
    s.scan.scan(s.registry, s.processes, now=0)
    assert s.graph.parent_of(CHILD) is None


def test_process_change_during_scan_does_not_create_edge(setup_scan, monkeypatch):
    s = setup_scan
    snapshots = iter([s.snapshot, {**s.snapshot, 10: {'ppid': 1, 'birth': BIRTH}}])
    monkeypatch.setattr(discovery.process_identity, 'process_snapshot', lambda: next(snapshots))
    s.scan.scan(s.registry, s.processes, now=0)
    assert not s.logs and s.graph.parent_of(CHILD) is None


def test_background_discovery_keeps_native_refresh_at_old_cadence(monkeypatch):
    importlib.import_module('server')
    from ccc_server import session_graph as module
    seen = []
    class Stop:
        count = 0
        def is_set(self): return self.count >= 2
        def wait(self, seconds):
            seen.append(('wait', seconds))
            self.count += 1
    graph = SimpleNamespace(save=lambda: seen.append(('save',)))
    core = SimpleNamespace(
        _load_session_registry=lambda: seen.append(('registry',)) or {},
        _scan_engine_processes=lambda: [],
        _codex_spawn_parent_by_child=lambda: seen.append(('codex',)) or {},
        _session_graph=graph,
        _session_graph_ingest_grok_subagents=lambda: None,
    )
    monkeypatch.setattr(module, '_core', core)
    monkeypatch.setattr(module, '_session_graph_codex_refresh_stop', Stop())
    clock = iter([0, 3])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(discovery, 'discover', lambda *args: seen.append(('discover',)))
    module._session_graph_codex_refresh_loop()
    assert seen.count(('registry',)) == seen.count(('discover',)) == 2
    assert seen.count(('codex',)) == 1
    assert seen.count(('wait', 3.0)) == 2
