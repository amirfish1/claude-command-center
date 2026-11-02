"""Historical inventory connects to real guards without replaying actions."""
import json

from ccc_server import decision_inbox as di
from ccc_server.decision_legacy_http import reconciliation_payload
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_sources import apply_owner_export
from tests.test_decision_sources import receipt, row


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(di, '_state_root', lambda: tmp_path)
    monkeypatch.setenv('CCC_DECISION_ANSWERS_ENABLED', '1')
    folder = tmp_path / 'decision-inbox'
    folder.mkdir()
    cards = {'old': dict(id='old', title='Old question', status='open', source_id='routine:test:x',
        source={'source': 'routine', 'tags': ['EXAMPLE-1']},
        options=[{'label': 'Old answer', 'action': {'kind': 'spawn', 'prompt': 'Do old work'}}])}
    (folder / 'cards.json').write_text(json.dumps(cards))
    registry = DecisionRegistry(folder / 'requests.sqlite3')
    observation = apply_owner_export(receipt([row()]), registry=registry, cards=cards)
    return folder, cards, observation


def test_complete_inventory_links_current_question_without_mutating_reports(tmp_path, monkeypatch):
    folder, cards, source = setup(tmp_path, monkeypatch)
    before = (folder / 'cards.json').read_bytes()
    result = reconciliation_payload(state_root=tmp_path, source_loader=lambda **kw: source)
    assert result['source_complete'] and result['counts']['total'] == 1
    old = result['cards'][0]
    assert old['canonical_required'] and old['action']['replay_allowed'] is False
    assert old['current_questions'][0]['request_id'] == source['requests'][0]['request_id']
    assert old['card'] == cards['old'] and old['legacy_delivery_state'] == 'unknown'
    assert (folder / 'cards.json').read_bytes() == before


def test_actual_legacy_handler_blocks_all_old_options_before_execution(tmp_path, monkeypatch):
    _, cards, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(di, 'perform_action', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('replayed')))
    monkeypatch.setattr(di, 'queue_github_repos', lambda: {})
    for source in ({'source': 'routine'}, {'tags': ['EXAMPLE-1']}, {'source_request': {}}):
        cards['old']['source'] = source
        result = di.decision_inbox_decide('old', 0, cards=cards, persist=False)
        assert not result['ok'] and result['code'] == 'canonical_question_required'
    result = di.decision_inbox_api_payload(cards=cards, cfg=dict(di.DEFAULT_CONFIG), review_cards=[])
    assert result['cards'][0]['canonical_required']


def test_failed_source_retains_reports_and_holds_without_claiming_withdrawal(tmp_path, monkeypatch):
    _, _, _ = setup(tmp_path, monkeypatch)
    result = reconciliation_payload(state_root=tmp_path, source_loader=lambda **kw: {'ok': False})
    assert not result['source_complete']
    assert result['cards'][0]['classification'] == 'source-unavailable'
    assert not result['cards'][0]['current_questions']
    assert result['cards'][0]['canonical_required']


def test_source_read_exception_is_unavailable_inventory_not_empty_success(tmp_path, monkeypatch):
    _, cards, _ = setup(tmp_path, monkeypatch)
    def offline(**kwargs):
        raise OSError('source offline')
    result = reconciliation_payload(state_root=tmp_path, source_loader=offline)
    assert not result['source_complete'] and result['counts']['total'] == len(cards)
    assert result['cards'][0]['card'] == cards['old']
    assert result['cards'][0]['canonical_required']


def test_disabling_replies_does_not_reactivate_old_report_options(tmp_path, monkeypatch):
    folder, cards, source = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('CCC_DECISION_ANSWERS_ENABLED', '0')
    (folder / 'answer-settings.json').write_text('{"enabled":false}')
    result = reconciliation_payload(state_root=tmp_path, source_loader=lambda **kw: source)
    assert result['answers_enabled'] is False
    assert result['cards'][0]['canonical_required']
    assert di.decision_inbox_decide('old', 0, cards=cards, persist=False)['code'] == 'canonical_question_required'
