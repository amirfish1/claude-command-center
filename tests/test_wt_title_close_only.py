"""CCC-268: a session that only closed a WT ticket is not retitled after it."""
import importlib
import base64
import json
import os

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


@pytest.mark.parametrize('has_snapshot', [True, False])
def test_detached_archive_titles_never_fetch_remote_queues(server, monkeypatch, tmp_path, has_snapshot):
    cache_file = tmp_path / 'tickets.json'
    claim = {'event': 'claim', 'by': {'kind': 'worker', 'session_id': 'S1'}}
    item = _item('CCC-7', [claim, CLOSE])
    if has_snapshot:
        cache_file.write_text(json.dumps({'items': [item]}))
        os.utime(cache_file, (1, 1))  # Expired display metadata still beats a network wait.
    monkeypatch.setattr(server, '_WT_ITEMS_SNAPSHOT_FILE', cache_file)
    monkeypatch.setattr(server, '_wt_items_memo', {'at': 0, 'items': None})
    monkeypatch.setattr(server, '_wt_snapshot_file_memo', {'mtime': None, 'items': []})
    monkeypatch.setattr(server, '_WT_DISPLAY_SNAPSHOT_ONLY', True)

    def unexpected_fetch(*args, **kwargs):
        pytest.fail('detached archive fetched remote ticket metadata')

    monkeypatch.setattr(server, '_wt_list_items_display_cached', unexpected_fetch)
    rows = [{'session_id': 'S1', 'display_name': 'CCC#1'}]
    name = server._apply_watchtower_worker_display_names(rows)[0]['display_name']
    assert ('CCC-7' in name.replace('#', '-')) == has_snapshot
    if not has_snapshot:
        assert name == 'CCC#1'


def test_archive_worker_enables_snapshot_only_before_computing(server, monkeypatch, tmp_path):
    from ccc_server import cpu_priority

    monkeypatch.setattr(cpu_priority, 'set_background_priority', lambda *a: None)
    monkeypatch.setattr(server, '_WT_DISPLAY_SNAPSHOT_ONLY', False)
    monkeypatch.setattr(server, '_load_conv_meta_cache', lambda **kwargs: None)

    def compute(*args, **kwargs):
        assert server._WT_DISPLAY_SNAPSHOT_ONLY
        return [], True

    monkeypatch.setattr(server, '_archive_compute_rows', compute)
    output = tmp_path / 'result.json'
    assert server._archive_refresh_worker_main(base64.urlsafe_b64encode(b'{}').decode(), output) == 0
    assert server._read_archive_refresh_result(output)[0]['rows'] == []
