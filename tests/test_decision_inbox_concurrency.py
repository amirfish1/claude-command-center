"""Acknowledged cards and owner actions survive concurrent inbox writers."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from ccc_server import decision_inbox as di


def test_scan_preserves_reports_and_owner_actions_received_during_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(di, '_state_root', lambda: tmp_path)
    cfg = dict(di.DEFAULT_CONFIG)
    old = di.decision_inbox_ingest({'source_id': 'old', 'title': 'Old question'}, cfg=cfg)
    before = di.load_numbered_cards()[old['card_id']]['decision_number']
    candidate = {'source_id': 'board:review', 'kind': 'board', 'title': 'Review', 'context': ''}
    monkeypatch.setattr(di, 'board_candidates', lambda *a, **kw: [candidate])
    received = []

    def analyst(_):
        received.append(di.decision_inbox_ingest({'source_id': 'arrived', 'title': 'New question'}, cfg=cfg))
        assert di.decision_inbox_dismiss(old['card_id'])['ok']
        # Another producer may report the scan's candidate while analysis runs.
        received.append(di.decision_inbox_ingest({'source_id': 'board:review', 'title': 'Review'}, cfg=cfg))
        return {'title': 'Review', 'context': '', 'options': []}

    record = di.run_once(cfg=cfg, rows=[], live_ids=set(), wt_runner=lambda _: None, analyst=analyst)
    retained = di.load_numbered_cards()
    assert all(r['ok'] and r['card_id'] in retained for r in received)
    assert retained[old['card_id']]['status'] == 'dismissed'
    assert retained[old['card_id']]['decision_number'] == before
    assert len([c for c in retained.values() if c['source_id'] == 'board:review']) == 1
    assert record['created'] == []
    assert record['skipped_dedupe'] == 1


def test_parallel_ingest_retains_every_acknowledgement_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setattr(di, '_state_root', lambda: tmp_path)
    cfg = dict(di.DEFAULT_CONFIG)
    count = 16
    start = Barrier(count)

    def report(i):
        start.wait(timeout=10)
        return di.decision_inbox_ingest({'source_id': f'producer-{i // 2}', 'title': 'Choose'}, cfg=cfg)

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(report, range(count)))
    retained = di.load_numbered_cards()
    assert all(r['ok'] and r['card_id'] in retained for r in results)
    assert len(retained) == count // 2
    assert len({c['decision_number'] for c in retained.values()}) == len(retained)
    assert all(c['seen_count'] == 2 for c in retained.values())
