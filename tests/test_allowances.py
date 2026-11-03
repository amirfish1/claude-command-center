import json
import pytest
from ccc_server import allowances as a


def test_manual_persistence_validation_and_expiration(tmp_path, monkeypatch):
    ledger = tmp_path / 'allowances.json'
    monkeypatch.setattr(a, '_path', lambda: ledger)
    ledger.write_text('[]')
    entry = dict(account='Secondary', provider='Codex', kind='Coding',
                 remaining=50, unit='%', deadline_type='reset',
                 deadline='2030-01-01T09:00:00+00:00')
    a.mutate(entry)
    result = a.payload(now=1_800_000_000)
    row = result['rows'][0]
    assert row['remaining'] == 50 and row['status'] == 'available'
    assert result['recommended_id'] == row['id']
    assert a.payload(now=2_000_000_000)['rows'][0]['status'] == 'needs_update'
    assert a.payload(now=2_000_000_000)['recommended_id'] is None
    for bad in (101, -1, float('nan'), True):
        with pytest.raises(ValueError):
            a.mutate(dict(entry, remaining=bad))
    with pytest.raises(ValueError):
        a.mutate(dict(entry, deadline='2030-01-01T09:00:00'))
    a.mutate({'id': row['id'], 'delete': True})
    assert json.loads(ledger.read_text()) == []


def test_recommendation_uses_deadline_and_blocks_exhausted_account(tmp_path, monkeypatch):
    entries = [dict(id='a', account='Large', provider='Codex', source='codex', codex_home=str(tmp_path)),
               dict(id='b', account='Small', provider='Codex', source='manual', remaining=50,
                    deadline_type='reset', deadline='2030-01-01T09:00:00Z'),
               dict(id='c', account='Unknown', provider='Claude', source='manual', remaining=None,
                    deadline_type='expiration', deadline='2029-01-01T00:00:00Z')]
    monkeypatch.setattr(a, '_load', lambda: entries)
    monkeypatch.setattr(a, '_CACHE', {})
    monkeypatch.setattr(a, '_read_codex', lambda home: dict(
        weekly=dict(pct=96, resets_at='2030-01-02T09:00:00Z'),
        session=dict(pct=100, resets_at='2029-12-31T09:00:00Z'), fetched_at='2028-01-01T00:00:00Z'))
    result = a.payload(now=1_800_000_000)
    assert result['recommended_id'] == 'b'
    assert {r['status'] for r in result['rows'] if r['id'] == 'a'} == {'blocked', 'empty'}


def test_duplicate_homes_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(a, '_path', lambda: tmp_path / 'ledger.json')
    monkeypatch.setattr(a, '_load', lambda: [dict(id='a', source='codex', codex_home=str(tmp_path))])
    with pytest.raises(ValueError, match='already connected'):
        a.mutate(dict(account='Duplicate', provider='Codex', source='codex', codex_home=str(tmp_path)))
