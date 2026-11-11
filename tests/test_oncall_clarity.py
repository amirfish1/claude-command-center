"""Alert contracts using only fixture transcripts, stores and transports."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccc_server.oncall import alerts, api, clarity, home, session_signals as signals
from ccc_server.oncall.engine import Engine
from ccc_server.oncall.store import Store
from tests.test_oncall_engine import rig


def write(path, rows):
    path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')


def test_head_tail_cache_survives_store_reopen_and_invalidates(tmp_path, monkeypatch):
    transcript = tmp_path / 'turns.jsonl'
    write(transcript, [
        {'type': 'user', 'message': {'content': 'Fix the checkout crash'}},
        {'type': 'assistant', 'message': {'content': 'Should I keep the redirect?'}},
    ])
    store = Store(tmp_path / 'state')
    first = signals.context(store, 's', transcript)
    assert first['first'] == 'Fix the checkout crash'
    assert first['last'] == 'Should I keep the redirect?'
    store.close()
    store = Store(tmp_path / 'state')
    original = Path.open
    reads = []
    def opened(target, *a, **kw):
        if target == transcript:
            reads.append(target)
        return original(target, *a, **kw)
    monkeypatch.setattr(Path, 'open', opened)
    assert signals.context(store, 's', transcript) == first
    assert not reads
    write(transcript, [{'type': 'assistant', 'message': {'content': 'Should I remove it instead?'}}])
    reads.clear()
    assert signals.context(store, 's', transcript)['last'] == 'Should I remove it instead?'
    assert len(reads) == 1
    store.close()


@pytest.mark.parametrize('engine', ['claude', 'codex'])
def test_structured_question_with_descriptions(engine, tmp_path):
    transcript = tmp_path / 'turns.jsonl'
    data = {'questions': [{'id': 'pick', 'question': 'Which redirect?', 'options': [
        {'label': 'Keep', 'description': 'Preserves compatibility'},
        {'label': 'Remove', 'description': 'Drops legacy behavior'}]}]}
    block = {'type': 'tool_use', 'name': 'AskUserQuestion', 'input': data}
    row = {'type': 'assistant', 'message': {'content': [block]}}
    if engine == 'codex':
        row = {'type': 'response_item', 'payload': {'type': 'function_call',
               'name': 'request_user_input', 'arguments': json.dumps(data)}}
    write(transcript, [row])
    store = Store(tmp_path / 'state')
    info = signals.context(store, 's', transcript)['structured']
    assert info['body'].startswith('Which redirect?')
    assert 'Preserves compatibility' in info['body']
    assert info['meta']['choices'] == [['Keep', 'Keep'], ['Remove', 'Remove']]
    store.close()


def test_missing_formal_question_waits_once_and_clears(rig):
    cur = {'s': {'question_waiting': True}}
    assert api.observe_sessions({}, cur, lambda sid: ('Fix checkout', ''), eng=rig.eng) == 0
    assert not rig.eng.store.alerts()
    assert api.observe_sessions(cur, cur, lambda sid: ('Fix checkout', ''), eng=rig.eng) == 1
    alert = rig.eng.store.alerts()[0]
    assert 'could not read' in alert['body'] and 'Open this session in CCC' in alert['body']
    assert alert['meta']['at_keyboard'] and not alert['needs_reply']
    assert api.observe_sessions(cur, cur, eng=rig.eng) == 0
    card = home.summary(rig.eng)['needs_you'][0]
    assert card['session_only'] and not card['choices'] and not card['free_text']
    api.observe_sessions(cur, {}, eng=rig.eng)
    assert rig.eng.store.alert(alert['id'])['state'] == 'cleared'


def test_content_arriving_on_next_observation_is_used(rig):
    cur = {'s': {'question_waiting': True}}
    api.observe_sessions({}, cur, lambda sid: ('Fix checkout', ''), eng=rig.eng)
    cur['s'].update(question_text='Which redirect?', question_options=['Keep', 'Remove'])
    api.observe_sessions(cur, cur, eng=rig.eng)
    alert = rig.eng.store.alerts()[0]
    assert alert['body'] == 'Which redirect?'
    assert alerts.render(alert)[2] == (('Keep', 'Keep'), ('Remove', 'Remove'))


def test_missing_permission_waits_once_and_stays_keyboard_only(rig):
    rig.deps.permission = lambda sid: None
    cur = {'s': {'needs_approval': True}}
    signals.observe(rig.eng, cur)
    assert not rig.eng.store.alerts()
    signals.observe(rig.eng, cur)
    alert = rig.eng.store.alerts()[0]
    assert alert['kind'] == 'permission' and alert['meta']['at_keyboard']
    assert alerts.render(alert)[2] == ()
    assert 'could not read' in alerts.render(alert)[1]
    signals.observe(rig.eng, {})
    assert rig.eng.store.alert(alert['id'])['state'] == 'cleared'


@pytest.mark.parametrize('reply', ['2', 'Remove', 'remove'])
def test_number_and_word_reply_translate_after_routing(rig, reply):
    alert, _ = rig.eng.store.alert_upsert('pick', kind='question', title='Checkout fix',
        session_id='s', body='Which redirect?', meta={'choices': [['Keep', 'Keep the redirect.'],
                                                               ['Remove', 'Remove the redirect.']]})
    rig.eng.store.alert_mark_sent(alert['id'])
    rig.msg('tg', f"{alert['id']} {reply}", '4242')
    rig.tick()
    assert rig.deps.injected == [('s', 'Remove the redirect.')]


def test_trailing_options_and_non_question_checklist():
    text = 'Which redirect?\n\n1. Keep\n2. Remove'
    signal = signals.from_message(text)
    assert signal['meta']['choices'] == [['Keep', 'Keep'], ['Remove', 'Remove']]
    assert not signals.from_message('Completed:\n1. Tested\n2. Shipped')
    assert not clarity.numbered('Which redirect?\n1. Keep\n3. Remove')


@pytest.mark.parametrize('kind,expected', [('permission', 'allow'), ('report', 'go'), ('send', 'yes')])
def test_canonical_binary_numeric_replies(kind, expected):
    assert clarity.translate({'kind': kind, 'meta': {}}, '1') == expected
    assert clarity.translate({'kind': kind, 'meta': {'at_keyboard': True}}, '1') == '1'


def test_home_and_owner_choices_do_not_leak_secret(rig, monkeypatch):
    from ccc_server import vault
    monkeypatch.setattr(vault, 'vault_list_entries', lambda: [{'name': 'fixture'}])
    monkeypatch.setattr(vault, 'vault_get_value', lambda name: 'SYNTHETIC-CHOICE-SECRET')
    alert, _ = rig.eng.store.alert_upsert('pick', kind='question', title='Checkout fix',
        session_id='s', body='Which redirect?', meta={'choices': [['Keep', 'SYNTHETIC-CHOICE-SECRET']]})
    card = home.summary(rig.eng)['needs_you'][0]
    assert 'SYNTHETIC-CHOICE-SECRET' not in str(card)
    assert card['choices'] == [] and '[secret redacted]' in card['message']
    alerts.send_alert(rig.eng.store, [rig.chan('tg')], alert)
    assert 'SYNTHETIC-CHOICE-SECRET' not in str(rig.chan('tg').sent)


def test_all_action_cards_have_context_and_accepted_replies():
    for kind in ('question', 'plain_question', 'permission', 'stopped', 'stuck', 'report', 'send', 'decision'):
        _, text, _ = alerts.render({'id': 7, 'kind': kind, 'title': 'Checkout fix',
                                   'body': 'The checkout redirect needs attention.', 'ref': 'SHOP-1'})
        assert 'Checkout fix' in text or kind == 'send'
        assert 'checkout redirect' in text
        assert 'Reply ' in text or 'No reply action' in text


def test_stop_without_content_waits_for_one_observation(rig):
    assert api.session_stopped('s', 'Checkout fix', '', 'turn', eng=rig.eng) is None
    assert not rig.eng.store.alerts()
    api.observe_sessions({}, {}, eng=rig.eng)
    alert = rig.eng.store.alerts()[0]
    assert alert['kind'] == 'stopped'
    assert 'could not read the last assistant message' in alert['body']
    assert 'Open this session in CCC' in alert['body']


def test_multi_question_stays_in_session(rig):
    structured = signals.structured_question({'questions': [
        {'question': 'Which region?', 'options': ['East', 'West']},
        {'question': 'Which tier?', 'options': ['Basic', 'Pro']}]})
    cur = {'s': {'question_waiting': True}}
    api.observe_sessions({}, cur, lambda sid: ('Checkout fix', structured), eng=rig.eng)
    alert = rig.eng.store.alerts()[0]
    assert alert['meta']['at_keyboard'] and not alert['needs_reply']
    assert not alerts.render(alert)[2]


def test_claude_structured_answer_keeps_expected_nonce(tmp_path, monkeypatch):
    import server
    request = {'nonce': 'old-nonce', 'questions': [{'question': 'Which redirect?',
                                                  'options': [{'label': 'Keep'}, {'label': 'Remove'}]}]}
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'claude')
    monkeypatch.setattr(server, '_read_question_request', lambda sid: request)
    monkeypatch.setattr(server, 'QUESTION_RELAY_DIR', tmp_path / 'relay')
    monkeypatch.setattr(server, 'SIDECAR_STATE_DIR', tmp_path / 'sidecars')
    deps = server._OncallDeps()
    store = Store(tmp_path / 'state')
    signal = deps.waiting_question('s', store)
    store.close()
    assert signal['meta']['question_binding']['nonce'] == 'old-nonce'
    request['nonce'] = 'new-nonce'
    ok, detail = deps.answer_question('s', 'Keep', signal['meta'])
    assert not ok and 'no longer pending' in detail
    assert not (tmp_path / 'relay' / 's.answer.json').exists()
    request['nonce'] = 'old-nonce'
    assert deps.answer_question('s', 'Keep', signal['meta'])[0]
    written = json.loads((tmp_path / 'relay' / 's.answer.json').read_text())
    assert written['nonce'] == 'old-nonce' and written['answers'][0]['text'] == 'Keep'


def test_codex_structured_answer_preserves_connection_identity(tmp_path, monkeypatch):
    import server
    from ccc_server import codex_client, notify
    calls = []
    pending = {'method': 'item/tool/requestUserInput', 'key': 'bound-key',
               'generation': 'bound-generation', 'state': 'pending',
               'params': {'questions': [{'id': 'redirect', 'question': 'Which redirect?',
                                        'options': [{'label': 'Keep'}, {'label': 'Remove'}]}]}}
    def client(action, data):
        calls.append((action, data))
        return {'requests': [pending]} if action == 'state' else {'ok': False, 'error': 'connection changed'}
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'codex')
    monkeypatch.setattr(codex_client, 'codex_client_call', client)
    monkeypatch.setattr(notify, '_session_identity', lambda sid: {'repo_path': '/fixture/app'})
    deps = server._OncallDeps()
    store = Store(tmp_path / 'state')
    signal = deps.waiting_question('s', store)
    ok, detail = deps.answer_question('s', 'Remove', signal['meta'])
    assert not ok and detail == 'connection changed'
    action, response = calls[-1]
    assert action == 'respond' and response['key'] == 'bound-key'
    assert response['generation'] == 'bound-generation'
    assert response['result'] == {'answers': {'redirect': {'answers': ['Remove']}}}
    store.close()


def test_bound_choice_uses_native_answer_handler(rig):
    received = []
    rig.deps.answer_question = lambda sid, text, meta: (received.append((sid, text, meta)) or (True, ''))
    alert, _ = rig.eng.store.alert_upsert('bound', kind='question', title='Checkout fix',
        session_id='s', body='Which redirect?', meta={'question_binding': {'nonce': 'original'},
            'choices': [['Keep', 'Keep the redirect'], ['Remove', 'Remove the redirect']]})
    rig.eng.store.alert_mark_sent(alert['id'])
    rig.msg('tg', f"{alert['id']} 2", '4242')
    rig.tick()
    assert received[0][0:2] == ('s', 'Remove the redirect')
    assert received[0][2]['question_binding']['nonce'] == 'original'
    assert not rig.deps.injected


def test_permission_punctuation_does_not_become_approval():
    alert = {'kind': 'permission', 'meta': {}}
    for reply in ('allow!', 'allow please', 'yes'):
        assert clarity.translate(alert, reply) == reply


def test_numeric_choice_cannot_inject_a_masked_secret(rig, monkeypatch):
    from ccc_server import vault
    monkeypatch.setattr(vault, 'vault_list_entries', lambda: [{'name': 'fixture'}])
    monkeypatch.setattr(vault, 'vault_get_value', lambda name: 'SYNTHETIC-CHOICE-SECRET')
    alert, _ = rig.eng.store.alert_upsert('secret-choice', kind='question', title='Checkout fix',
        session_id='s', body='Which redirect?', meta={'choices': [['Keep', 'SYNTHETIC-CHOICE-SECRET']]})
    rig.eng.store.alert_mark_sent(alert['id'])
    rig.msg('tg', f"{alert['id']} 1", '4242')
    rig.tick()
    assert not rig.deps.injected
    assert rig.eng.store.alert(alert['id'])['state'] == 'open'
    assert 'possible secret' in rig.chan('tg').sent[-1].text


def test_display_name_then_first_request_never_raw_id(tmp_path, monkeypatch):
    import server
    from ccc_server import notify
    deps = server._OncallDeps()
    monkeypatch.setattr(server, '_oncall_engine', lambda: SimpleNamespace(store=None))
    monkeypatch.setattr(deps, 'context', lambda sid, store: {'first': 'Fix the checkout crash'})
    identity = {'name': 'Checkout fix', 'repo_path': '/fixture/app'}
    monkeypatch.setattr(notify, '_session_identity', lambda sid: identity)
    assert deps.label('session-12345678') == ('Checkout fix', 'app')
    identity['name'] = 'Agent session-12345678'
    assert deps.label('session-12345678') == ('Fix the checkout crash', 'app')


def test_disabled_decisions_do_not_probe_candidates(rig):
    cfg = rig.eng.config()
    cfg['alerts']['decisions'] = False
    rig.eng.save_config(cfg)
    def forbidden(*a, **kw):
        raise AssertionError('disabled alerts must not inspect session state')
    rig.deps.permission = forbidden
    rig.deps.question = forbidden
    signals.observe(rig.eng, {'s': {'needs_approval': True}, 'idle': {'state': 'idle'}})
    api.observe_sessions({}, {'s': {'question_waiting': True}}, forbidden, eng=rig.eng)


def test_codex_structured_idle_candidate_keeps_native_binding(tmp_path, monkeypatch):
    import server
    deps = server._OncallDeps()
    signal = {'body': 'Which redirect?', 'meta': {'choices': [['Keep', 'Keep']],
              'question_binding': {'engine': 'codex', 'key': 'original-key', 'generation': 'original-generation'}}}
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'codex')
    monkeypatch.setattr(server, 'SIDECAR_STATE_DIR', tmp_path)
    monkeypatch.setattr(deps, 'context', lambda sid, store: {'mtime': 1000, 'structured': signal})
    monkeypatch.setattr(deps, 'waiting_question', lambda sid, store: signal)
    store = Store(tmp_path / 'state')
    found = deps.question('s', store)
    assert found['kind'] == 'plain_question' and found['body'] == 'Which redirect?'
    assert found['meta']['question_binding']['generation'] == 'original-generation'
    store.close()


@pytest.mark.parametrize('reply', ['2', 'Status', 'status'])
def test_bare_choice_and_reserved_option_words_reach_only_waiting_session(rig, reply):
    alert, _ = rig.eng.store.alert_upsert('reserved', kind='question', title='Checkout fix',
        session_id='fixture', body='Which view?', meta={'choices': [['Summary', 'Show summary'], ['Status', 'Show status']]})
    rig.eng.store.alert_mark_sent(alert['id'])
    rig.msg('tg', reply, '4242')
    rig.tick()
    assert rig.deps.injected == [('fixture', 'Show status')]


@pytest.mark.parametrize('name', ['session-', 'session-01234567', '11111111-2222-4333-8444-555555555555'])
def test_opaque_names_are_not_display_names(name):
    assert clarity.useful_name(name, 'different-session') == ''


def test_last_words_exclude_machine_footer_and_keep_question():
    text = 'Earlier detail. The redirect test fails. Should I keep compatibility?\n<session-state>machine metadata</session-state>'
    assert clarity.excerpt(text) == 'The redirect test fails. Should I keep compatibility?'
