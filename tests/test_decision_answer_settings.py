"""Persisted opt-in agrees across display and delivery without service env edits."""
import json

import pytest

from ccc_server.decision_answer_api import OPT_IN
from ccc_server.decision_answer_http import answer_payload
from ccc_server.decision_answer_settings import answer_environment
from ccc_server.decision_requests import requests_payload
from tests.test_decision_sources import receipt, row


@pytest.mark.parametrize('raw', ['{}', '{"enabled":false}', '{"enabled":1}',
    '{"enabled":"true"}', '{"enabled":true,"extra":1}',
    '{"enabled":true,"enabled":true}', '{broken', 'null'])
def test_malformed_or_nonboolean_opt_in_fails_closed(tmp_path, raw):
    folder = tmp_path / 'decision-inbox'
    folder.mkdir()
    (folder / 'answer-settings.json').write_text(raw)
    assert answer_environment(tmp_path, {})[OPT_IN] == '0'
    assert answer_payload('record', b'{}', state_root=tmp_path, environ={})[1] == 503
    assert not (folder / 'requests.sqlite3').exists()


def test_missing_file_has_no_side_effect_and_explicit_environment_wins(tmp_path):
    assert answer_environment(tmp_path, {})[OPT_IN] == '0'
    assert list(tmp_path.iterdir()) == []
    folder = tmp_path / 'decision-inbox'
    folder.mkdir()
    (folder / 'answer-settings.json').write_text('{"enabled":true}')
    assert answer_environment(tmp_path, {})[OPT_IN] == '1'
    for explicit in ('0', 'false', ''):
        assert answer_environment(tmp_path, {OPT_IN: explicit})[OPT_IN] == explicit


def test_file_replacement_updates_readmodel_and_record_gate_without_restart(tmp_path, monkeypatch):
    from ccc_server.decision_registry import DecisionRegistry
    from ccc_server.decision_sources import apply_owner_export
    monkeypatch.delenv(OPT_IN, raising=False)
    registry = DecisionRegistry(tmp_path / 'decision-inbox' / 'requests.sqlite3')
    source = receipt([row()])
    projected = apply_owner_export(source, registry=registry, cards={})
    target = tmp_path / 'decision-inbox' / 'answer-settings.json'
    replacement = target.with_suffix('.tmp')
    replacement.write_text('{"enabled":true}')
    replacement.replace(target)
    model = requests_payload(state_root=tmp_path, source_loader=lambda **kw: projected)
    assert model['answers_enabled'] and model['requests'][0]['can_answer']
    request = model['requests'][0]
    data, status = answer_payload('record', json.dumps(dict(request_id=request['request_id'],
        expected_revision=request['revision'], text='Use region A', idempotency_key='file-opt-in')),
        state_root=tmp_path, export_source=lambda: source)
    assert status == 200 and data['state'] == 'pending'
    replacement.write_text('{"enabled":false}')
    replacement.replace(target)
    assert not requests_payload(state_root=tmp_path, source_loader=lambda **kw: projected)['answers_enabled']
    assert answer_payload('send', json.dumps({'answer_id': data['answer_id']}),
                          state_root=tmp_path)[1] == 503
