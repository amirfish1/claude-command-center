"""Shared contract gates, run verbatim in WT and CCC with isolated state."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'watchtower/sender_envelope.py'
if not SOURCE.exists():
    SOURCE = ROOT / 'sender_envelope.py'
spec = importlib.util.spec_from_file_location('contract_envelope', SOURCE)
envelope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envelope)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('WATCHTOWER_STATE_DIR', str(tmp_path / 'wt'))
    monkeypatch.setenv('CCC_STATE_DIR', str(tmp_path / 'ccc'))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    monkeypatch.setenv('WT_ATTRIBUTION_EMIT', '1')
    monkeypatch.setenv('CCC_ATTRIBUTION_EMIT', '1')
    for key in envelope._INHERITED:
        monkeypatch.delenv(key, raising=False)
    envelope._cache.clear()
    monkeypatch.setattr(envelope, "_nearest_engine", lambda: "")


def test_contract_vectors_and_manifest():
    peer = ROOT / ("watchtower/peer_uds.py" if (ROOT / "watchtower").is_dir() else "ccc_peer_uds.py")
    assert hashlib.sha256(peer.read_bytes()).hexdigest() == (ROOT / "tests/fixtures/envelope_v1/PEER_SHA256").read_text().strip()
    p = ROOT / 'tests/fixtures/envelope_v1/vectors.json'
    assert hashlib.sha256(p.read_bytes()).hexdigest() == p.with_name('SHA256').read_text().strip()
    for vector in json.loads(p.read_text()):
        if vector['valid']:
            assert envelope.validate(vector['attr']) == vector['attr']
        else:
            with pytest.raises(ValueError):
                envelope.validate(vector['attr'])


def test_line_order_and_slash_exemption():
    a = envelope.service('wt:notify', 'recipient')
    text = 'orig_msg_id: reply-id\nbody\n⟨wt:dlv-1234⟩'
    carried = envelope.carry(text, a, app='wt')
    lines = carried.splitlines()
    assert lines[0] == 'orig_msg_id: reply-id'
    assert lines[1].startswith(envelope.COMPACT)
    assert lines[-1] == '⟨wt:dlv-1234⟩'
    assert envelope.journal(a, app='wt')
    assert envelope.from_text(carried) == a
    assert envelope.strip_header(carried) == text
    assert envelope.carry('/compact', a, app='wt') == '/compact'
    assert envelope.carry(carried, a, app='wt').count(envelope.COMPACT) == 1


def test_compact_header_is_short_and_human_text_has_none():
    a = envelope.service('wt:notify', 'recipient')
    line = envelope.carry('body', a, app='wt').splitlines()[0]
    assert line == '[sender: wt:notify (service) · msg ' + a['message_id'] + ']'
    human = envelope.make('recipient', who=envelope.author('human', '', 'You (dashboard)'), basis='claimed')
    assert envelope.carry('body', human, app='ccc') == 'body'
    assert envelope.carry('orig_msg_id: x\nbody', human, app='ccc') == 'orig_msg_id: x\nbody'


def test_unmatched_compact_header_is_a_name_only_claim():
    a = envelope.make('recipient', who=envelope.author('session', 'sid', 'Worker [7] · x', 'claude'), basis='runtime')
    a['reply_to'] = {'addr': 'route-123', 'route_token': 'route-123'}
    carried = envelope.carry('body', a, app='ccc')
    assert carried.splitlines()[0].endswith(' · reply: ccc send route-123]')
    found = envelope.from_text(carried)
    assert found['basis'] == 'claimed' and found['evidence'] == {'source': 'unmatched_header'}
    assert found['author']['kind'] == 'session' and found['author']['name_at_send'] == 'Worker 7 x'
    assert found['message_id'] == a['message_id']
    assert found['reply_to']['addr'] == 'route-123'
    assert envelope.lookup(text=carried)['basis'] == 'claimed'


def test_legacy_full_json_header_still_parses_and_strips():
    a = envelope.service('wt:notify', 'recipient')
    legacy = envelope.HEADER + json.dumps(a, separators=(',', ':')) + ']\nbody'
    assert envelope.from_text(legacy) == a
    assert envelope.strip_header(legacy) == 'body'
    assert envelope.carry(legacy, a, app='wt').count('\n') == 1


def test_cross_application_journal_after_sender_exit_and_rename():
    a = envelope.make('recipient', who=envelope.author('session','session','Original name','codex'), basis='runtime')
    assert envelope.journal(a, app='wt', transport_id='uds-id', nonce='⟨wt:dlv-1234⟩')
    a['author']['name_at_send'] = 'Renamed'
    found = envelope.lookup(transport_id='uds-id')
    assert found['author']['name_at_send'] == 'Original name'
    assert found['basis'] == 'runtime'
    assert envelope.lookup(text='body\n⟨wt:dlv-1234⟩')['message_id'] == a['message_id']
    assert envelope.lookup(transport_id='uds-id', recipient='different') is None


def test_remote_record_cannot_upgrade_in_band_claim(tmp_path):
    a = envelope.make('recipient', basis='runtime')
    envelope.local_node()
    directory = Path(os.environ['CCC_STATE_DIR']) / 'attribution'
    directory.mkdir(parents=True)
    (directory/'2020-01.jsonl').write_text(json.dumps({'attr':a,'node':'remote-node'})+'\n')
    found = envelope.lookup(text=envelope.carry('body', a, app='wt'))
    assert found['basis'] == 'claimed'


def test_composer_and_api_claims():
    a = envelope.request_attr({'source':'composer'}, 'recipient')
    assert a['author']['kind'] == 'unknown'
    a = envelope.request_attr({'source':'composer'}, 'recipient', browser_composer=True)
    assert a['author']['kind'] == 'human' and a['basis'] == 'claimed'
    a = envelope.request_attr({'peer_sender_sid':'sender'}, 'recipient')
    assert a['basis'] == 'claimed'
    assert a['author']['id'] == 'sender'


def test_cli_no_env_and_registry_match(monkeypatch):
    assert envelope.capture_cli()['author']['kind'] == 'unknown'
    sid = str(uuid.uuid4())
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', sid)
    assert envelope.capture_cli()['basis'] == 'claimed'
    directory = Path(os.environ['CLAUDE_CONFIG_DIR']) / 'sessions'
    directory.mkdir(parents=True)
    (directory/'123.json').write_text(json.dumps({'sessionId':sid,'pid':os.getpid(),'name':'Test agent'}))
    assert envelope.capture_cli()['basis'] == 'runtime'


def test_envelope_consumed_once_and_nested_child_unknown(monkeypatch):
    a = envelope.service('wt:notify')
    monkeypatch.setenv(envelope.ENV, json.dumps(a))
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'parent')
    assert envelope.consume_env() == a
    assert envelope.ENV not in os.environ
    clean = envelope.child_env()
    code = 'import importlib.util,json; s=importlib.util.spec_from_file_location("e",'+repr(str(SOURCE))+'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps(m.capture_cli()))'
    child = json.loads(subprocess.check_output([sys.executable,'-c',code], env=clean, text=True))
    assert child['author']['kind'] == 'unknown'
    assert 'CLAUDE_CODE_SESSION_ID' not in clean


def test_limits_and_rollback(monkeypatch):
    a = envelope.make()
    a['relay'] = [{'via':str(i)} for i in range(25)]
    assert envelope.validate(a)['relay'][0]['via'] == '5'
    a['evidence'] = {'huge':'x'*32768}
    with pytest.raises(ValueError):
        envelope.validate(a)
    monkeypatch.setenv('WT_ATTRIBUTION_EMIT','0')
    assert envelope.carry('original',envelope.make(),app='wt') == 'original'


def test_journal_failure_delivers_claim_and_warns_once(tmp_path, monkeypatch, capsys):
    file = tmp_path/'not-a-directory';file.write_text('x')
    monkeypatch.setenv('WATCHTOWER_STATE_DIR',str(file))
    a = envelope.make()
    assert envelope.journal(a,app='wt') is False
    assert envelope.journal(a,app='wt') is False
    assert capsys.readouterr().err.count('journal write failed') == 1
    carried = envelope.carry('body',a,app='wt')
    assert envelope.lookup(text=carried)['basis'] == 'claimed'


def test_innermost_engine_ignores_outer_identity(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID','outer-claude')
    monkeypatch.setenv('CODEX_THREAD_ID','inner-codex')
    monkeypatch.setattr(envelope,'_nearest_engine',lambda: 'codex')
    assert envelope.capture_cli()['author']['id']=='inner-codex'
    monkeypatch.delenv('CODEX_THREAD_ID')
    assert envelope.capture_cli()['author']['kind']=='unknown'


def test_manual_prune_is_only_retention_deletion(tmp_path,monkeypatch):
    directory=Path(os.environ['WATCHTOWER_STATE_DIR'])/'attribution'
    directory.mkdir(parents=True)
    for name in ('2025-01.jsonl','2026-01.jsonl'):
        (directory/name).write_text('')
    envelope.journal(envelope.make(),app='wt')
    assert (directory/'2025-01.jsonl').exists()
    monkeypatch.setattr(sys,'argv',['envelope','prune','--app','wt','--before','2026-01'])
    envelope.main()
    assert not (directory/'2025-01.jsonl').exists()
    assert (directory/'2026-01.jsonl').exists()


def test_late_receipt_keeps_complete_transport_snapshot():
    original = envelope.service('wt:notify', 'recipient')
    delivered = envelope.relay(original, 'wt-uds', attested_mode='bypass', delivered='recipient')
    assert envelope.journal(delivered, app='wt', event='transport')
    assert envelope.journal(original, app='wt', event='receipt', nonce='⟨wt:dlv-late⟩')
    for query in ({'message_id': original['message_id']}, {'text': 'body\n⟨wt:dlv-late⟩'}):
        found = envelope.lookup(**query)
        assert found['relay'][-1]['via'] == 'wt-uds'
        assert found['relay'][-1]['attested_mode'] == 'bypass'
        assert found['delivered_recipient']['id'] == 'recipient'
