"""Replay diagnostics distinguish changing inputs from entries not retained."""
import collections
import json

import server


def test_replay_diagnostics_report_dependencies_without_values(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_LIST_PROJECTION_DIAG_KEYS', collections.OrderedDict())
    monkeypatch.setattr(server, '_ARCHIVE_LIST_PROJECTION_CACHE', server.ByteBudgetCache(100, 2))
    first = ('7d', True, 1, 'context-private', 'row-private', (1, 1), 1)
    diag = server._archive_list_projection_diag('7d', first, False)
    assert diag['reason'] == 'first-observed'
    assert diag['budget_bytes'] == 100
    assert server._archive_list_projection_diag('7d', first, True)['reason'] == 'hit'
    assert server._archive_list_projection_diag('7d', first, False)['reason'] == 'not-retained'
    changed = ('7d', True, 2, 'new-context', 'new-row', (1, 2), 2)
    diag = server._archive_list_projection_diag('7d', changed, False)
    assert diag['reason'] == 'key-changed'
    assert diag['changed'] == ['snapshot', 'context', 'overlay', 'graph', 'topics']
    assert 'private' not in json.dumps(diag)


def test_replay_diagnostic_history_is_bounded(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_LIST_PROJECTION_DIAG_KEYS', collections.OrderedDict())
    for i in range(20):
        server._archive_list_projection_diag(str(i), (str(i), True, i, '', '', (), 0), False)
    assert len(server._ARCHIVE_LIST_PROJECTION_DIAG_KEYS) == 8
    assert list(server._ARCHIVE_LIST_PROJECTION_DIAG_KEYS) == [str(i) for i in range(12, 20)]
