"""The historical builder runs outside the dashboard without changing publication."""
import copy

import pytest

import server
from ccc_server import bg_helper


def snapshot():
    return {'schema': server._PRODUCTIVITY_SCHEMA, 'generated_at': 100,
            'datasets': {str(w): {'summary': {'turns': w}} for w in (6, 8, 12, 16)},
            'coverage': {'watchtower': {'available': True},
                         'presence': {'sample_minutes': 8, 'sampler_available': False}}}


def test_offload_uses_maintenance_helper_and_current_sampler(monkeypatch):
    data = snapshot()
    calls = []
    monkeypatch.setattr(bg_helper, 'enabled', lambda: True)
    monkeypatch.setattr(bg_helper, 'is_child', lambda: False)
    monkeypatch.setattr(bg_helper, 'call', lambda op, **kw: calls.append((op, kw)) or {'ok': True, 'data': data})
    monkeypatch.setattr(server, 'presence_health', lambda: {'sampler_available': True, 'reason': None})
    monkeypatch.setattr(server, '_productivity_build_snapshot', lambda: pytest.fail('dashboard scanned history'))
    result = server._productivity_build_snapshot_offloaded()
    assert calls == [('productivity_snapshot', {'timeout': 600, 'role': 'main'})]
    assert result['datasets'] == data['datasets']
    assert result['coverage']['presence'] == {'sample_minutes': 8, 'sampler_available': True, 'reason': None, 'source': 'macos_idle_sampler'}


@pytest.mark.parametrize('reply', [None, {}, {'ok': True, 'data': {}},
                                  {'ok': True, 'data': dict(snapshot(), schema=-1)},
                                  {'ok': True, 'data': dict(snapshot(), datasets={})}])
def test_unavailable_or_incompatible_helper_uses_existing_builder(monkeypatch, reply):
    monkeypatch.setattr(bg_helper, 'enabled', lambda: True)
    monkeypatch.setattr(bg_helper, 'is_child', lambda: False)
    monkeypatch.setattr(bg_helper, 'call', lambda *a, **kw: reply)
    expected = snapshot()
    calls = []
    monkeypatch.setattr(server, '_productivity_build_snapshot', lambda: calls.append(1) or expected)
    assert server._productivity_build_snapshot_offloaded() is expected
    assert calls == [1]


@pytest.mark.parametrize('enabled,child', [(False, False), (True, True)])
def test_disabled_or_child_never_offloads_recursively(monkeypatch, enabled, child):
    monkeypatch.setattr(bg_helper, 'enabled', lambda: enabled)
    monkeypatch.setattr(bg_helper, 'is_child', lambda: child)
    monkeypatch.setattr(bg_helper, 'call', lambda *a, **kw: pytest.fail('recursive helper call'))
    expected = snapshot()
    monkeypatch.setattr(server, '_productivity_build_snapshot', lambda: expected)
    assert server._productivity_build_snapshot_offloaded() is expected


def test_parent_still_publishes_and_coalesces_remote_builds(monkeypatch):
    data = snapshot()
    saved, jobs = [], []
    class Store:
        def save_payload(self, value, generated_at=None):
            saved.append((copy.deepcopy(value), generated_at))
    class Thread:
        def __init__(self, *, target, **kw):
            jobs.append(target)
        def start(self):
            pass
    monkeypatch.setattr(server, '_PRODUCTIVITY_REFRESH', {'state': 'idle'})
    monkeypatch.setattr(server, '_PRODUCTIVITY_STORE', Store())
    monkeypatch.setattr(server.threading, 'Thread', Thread)
    monkeypatch.setattr(server, '_productivity_build_snapshot_offloaded', lambda: data)
    assert server._productivity_refresh_start()['state'] == 'building'
    assert server._productivity_refresh_start(force=True)['state'] == 'building'
    assert len(jobs) == 1 and not saved
    jobs[0]()
    assert server._PRODUCTIVITY_REFRESH['state'] == 'complete'
    assert saved == [(data, 100)]


@pytest.mark.parametrize('available,expected', [(True, 'not_yet_sampled'), (False, 'unavailable')])
def test_sampler_source_uses_parent_health_when_no_samples(monkeypatch, available, expected):
    data = snapshot()
    data['coverage']['presence']['sample_minutes'] = 0
    monkeypatch.setattr(bg_helper, 'enabled', lambda: True)
    monkeypatch.setattr(bg_helper, 'is_child', lambda: False)
    monkeypatch.setattr(bg_helper, 'call', lambda *a, **kw: {'ok': True, 'data': data})
    monkeypatch.setattr(server, 'presence_health', lambda: {'sampler_available': available})
    result = server._productivity_build_snapshot_offloaded()
    assert result['coverage']['presence']['source'] == expected


def test_remote_watchtower_gap_preserves_last_good_parent_payload(monkeypatch):
    old = snapshot()
    data = snapshot()
    data['coverage']['watchtower']['available'] = False
    saved = []
    class Store:
        def load_payload(self):
            return {'payload': old}
        def save_payload(self, *a, **kw):
            saved.append(a)
    class Thread:
        def __init__(self, *, target, **kw):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(server, '_PRODUCTIVITY_REFRESH', {'state': 'idle'})
    monkeypatch.setattr(server, '_PRODUCTIVITY_STORE', Store())
    monkeypatch.setattr(server.threading, 'Thread', Thread)
    monkeypatch.setattr(bg_helper, 'enabled', lambda: True)
    monkeypatch.setattr(bg_helper, 'is_child', lambda: False)
    monkeypatch.setattr(bg_helper, 'call', lambda *a, **kw: {'ok': True, 'data': data})
    monkeypatch.setattr(server, 'presence_health', lambda: {})
    assert server._productivity_refresh_start()['state'] == 'failed'
    assert saved == []
