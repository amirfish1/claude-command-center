"""CCC-268: a session that only closed a WT ticket is not retitled after it."""
import importlib

import pytest


@pytest.fixture
def server(monkeypatch):
    server = importlib.import_module('server')
    monkeypatch.setattr(server, '_wt_read_worker_session_ids', lambda: [])
    monkeypatch.setattr(server, '_wt_read_workers', lambda *a, **k: [])
    monkeypatch.setattr(server, '_wt_read_session_origins', lambda: {})
    return server


def _item(ref, history):
    return {'project': 'CCC', 'ref': ref, 'status': 'closed', 'title': 'scratch',
            'claimed_by': 'wt-cli-1', 'claimed_session_id': 'S1',
            'closed_at': '2026-10-29T22:00:00Z', 'history': history}


CLOSE = {'event': 'close', 'by': {'kind': 'worker', 'session_id': 'S1'}}


def _name(server, monkeypatch, items):
    monkeypatch.setattr(server, '_wt_list_items_display_cached', lambda: items)
    rows = [{'session_id': 'S1', 'display_name': 'CCC#1'}]
    return server._apply_watchtower_worker_display_names(rows)[0]['display_name']


def test_close_only_ticket_does_not_retitle(server, monkeypatch):
    items = [_item('CCC-SCRATCH-1', [{'event': 'filed', 'by': {'kind': 'system'}}, CLOSE])]
    assert 'SCRATCH' not in _name(server, monkeypatch, items)


def test_claim_by_another_session_does_not_retitle_closer(server, monkeypatch):
    claim = {'event': 'claim', 'by': {'kind': 'worker', 'session_id': 'S2'}}
    items = [_item('CCC-SCRATCH-2', [claim, CLOSE])]
    assert 'SCRATCH' not in _name(server, monkeypatch, items)


def test_claimed_ticket_still_retitles(server, monkeypatch):
    claim = {'event': 'claim', 'by': {'kind': 'worker', 'session_id': 'S1'}}
    items = [_item('CCC-7', [claim, CLOSE])]
    assert 'CCC-7' in _name(server, monkeypatch, items).replace('#', '-')


def test_item_without_history_keeps_old_behavior(server):
    assert server._wt_item_worked_by_session({'claimed_session_id': 'S1'}, 'S1')
