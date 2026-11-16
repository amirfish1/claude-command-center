"""Recorded turn provenance must not confuse automation with owner input."""
import builtins
import json

import pytest

from ccc_server import session_driver as driver


@pytest.mark.parametrize(('text', 'event', 'kind', 'name'), [
    ('Please fix the layout', {}, 'human', ''),
    ('<command-name>/goal</command-name><command-args>Fix the build</command-args>', {}, 'human', ''),
    ('Typed in terminal', {'turnOrigin': 'human'}, 'human', ''),
    ('Typed in dashboard', {'turnOrigin': 'sdk'}, 'human', ''),
    ('[sender: build-review (session) · msg test]\nReview this', {}, 'session', 'build-review'),
    ('Another Claude session sent a message:\n<cross-session-message from-name="peer-a">\nReview this\n</cross-session-message>', {'turnOrigin': 'peer', 'isMeta': True}, 'session', 'peer-a'),
    ('<cross-session-message from-name="wt:notify">\nA ticket changed', {'isMeta': True}, 'service', 'wt:notify'),
    ('[sender: unnamed (unknown) · msg test]\n[WATCHTOWER] Comment steer', {}, 'service', 'WatchTower'),
    ('[sender: schedule (service) · msg test]\nRoutine wake', {}, 'service', 'schedule'),
    ('[sender: peer (session) · msg test]\n[WATCHTOWER] Forwarded task', {}, 'session', 'peer'),
    ('[WATCHTOWER] Take over this work', {}, 'service', 'WatchTower'),
    ('<task-notification>Task complete</task-notification>', {'isMeta': True}, 'background', 'Background task'),
    ('Scheduled check', {'wakeupSource': 'hourly'}, 'service', 'hourly'),
    ('Unattributed peer text', {'turnOrigin': 'peer'}, 'unknown', ''),
    ('[sender: unnamed (unknown) · msg test]\nCheck again', {}, 'unknown', 'unnamed'),
    ('Task instructions', {'isSidechain': True}, 'unknown', ''),
    ('[Message attribution v1: {"author":{"kind":"session","id":"peer-b","name_at_send":"Builder"}}]\nReview', {}, 'session', 'Builder'),
    ('You continue the work of session old.\nTask: [sender: peer-c (session) · msg test]\nContinue', {}, 'session', 'peer-c'),
])
def test_classify(text, event, kind, name):
    result = driver.classify(text, event)
    assert result['kind'] == kind
    assert result['name'] == name


@pytest.mark.parametrize(('text', 'event'), [
    ('Summary', {'isCompactSummary': True}),
    ('Loaded skill text', {'isMeta': True}),
    ('Base directory for this skill: /example/skills/check\nGuidance', {}),
    ('This session is being continued from a previous conversation that ran out of context.', {}),
    ('<system-reminder>Run a hook</system-reminder>', {}),
    ('<user-prompt-submit-hook>Reminder</user-prompt-submit-hook>', {}),
    ('# AGENTS.md instructions\nRules', {}),
    ('<environment_context>Rules</environment_context>', {}),
    ('<local-command-stdout>Done</local-command-stdout>', {}),
    ('[Request interrupted by user]', {}),
    ('', {}),
])
def test_bookkeeping_keeps_previous_driver(text, event):
    meta = {}
    driver.record(meta, 'Owner ask', 100)
    before = dict(meta)
    driver.record(meta, text, 200, event)
    assert meta == before


def test_owner_timestamp_survives_machine_wakes_and_out_of_order_events():
    meta = {}
    driver.record(meta, 'Owner ask', 100)
    driver.record(meta, '[sender: peer (session) · msg test]\nContinue', 200)
    driver.record(meta, '[WATCHTOWER] Check', 300)
    driver.record(meta, 'Delayed owner echo', 150)
    assert meta['turn_driver']['kind'] == 'service'
    assert meta['turn_driver']['at'] == 300
    assert meta['last_owner_input_at'] == 150
    driver.record(meta, 'Owner returns', 400)
    assert meta['turn_driver']['kind'] == 'human'
    assert meta['last_owner_input_at'] == 400


def test_claude_parser_caches_driver_without_additional_transcript_opens(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_emit_interrupts_from_meta', lambda *a: None)
    p = tmp_path / 'session.jsonl'
    events = [
        {'type': 'user', 'timestamp': '2026-01-01T00:00:00Z', 'message': {'content': 'Owner ask'}},
        {'type': 'user', 'timestamp': '2026-01-01T01:00:00Z', 'isMeta': True, 'turnOrigin': 'peer',
         'message': {'content': '<cross-session-message from-name="build-review">\nReview</cross-session-message>'}},
        {'type': 'user', 'timestamp': '2026-01-01T02:00:00Z', 'isCompactSummary': True,
         'message': {'content': 'Summary'}},
        {'type': 'user', 'timestamp': '2026-01-01T03:00:00Z',
         'message': {'content': [{'type': 'tool_result', 'content': 'Done'}]}},
    ]
    p.write_text(''.join(json.dumps(e, separators=(',', ':'))+'\n' for e in events))
    original = builtins.open
    opens = []
    def tracking(file, *a, **kw):
        if str(file) == str(p):
            opens.append(file)
        return original(file, *a, **kw)
    monkeypatch.setattr(builtins, 'open', tracking)
    first = server._extract_tail_meta(p)
    assert first['turn_driver']['kind'] == 'session'
    assert first['turn_driver']['name'] == 'build-review'
    assert first['last_owner_input_at'] == driver.epoch('2026-01-01T00:00:00Z')
    assert len(opens) == 1
    assert server._extract_tail_meta(p) is first
    assert len(opens) == 1


@pytest.mark.parametrize('subagent', [False, True])
def test_parent_only_driver_upgrade_reuses_unchanged_root_metadata(tmp_path, monkeypatch, subagent):
    import server
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_emit_interrupts_from_meta', lambda *a: None)
    p = tmp_path / 'parent' / 'subagents' / 'agent-review.jsonl' if subagent else tmp_path / 'owner.jsonl'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({'type': 'user', 'timestamp': '2026-01-01T00:00:00Z',
                            'isSidechain': subagent, 'message': {'content': 'Review changes'}}) + '\n')
    fresh = server._extract_tail_meta(p)
    cached = dict(fresh, driver_version=2)
    if subagent:
        cached.pop('driver_parent_session_id', None)
        cached['turn_driver'] = {'kind': 'unknown', 'name': '', 'basis': 'origin',
                                 'at': driver.epoch('2026-01-01T00:00:00Z')}
    server._conv_meta_cache[str(p)] = cached
    original = builtins.open
    opens = []
    def tracking(file, *a, **kw):
        if str(file) == str(p):
            opens.append(file)
        return original(file, *a, **kw)
    monkeypatch.setattr(builtins, 'open', tracking)
    result = server._extract_tail_meta(p)
    assert driver.fields(result) == driver.fields(fresh)
    assert len(opens) == int(subagent)
    assert (result is cached) == (not subagent)


def test_codex_incremental_messages_preserve_last_owner_and_ignore_context(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_codex_tail_resume', {})
    p = tmp_path / 'rollout.jsonl'
    def message(text, hour, kind='response_item'):
        payload = ({'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}
                   if kind == 'response_item' else {'type': 'user_message', 'message': text})
        return json.dumps({'type': kind, 'timestamp': f'2026-01-01T{hour:02d}:00:00Z', 'payload': payload})+'\n'
    p.write_text(message('Owner ask', 0))
    owner = server._extract_codex_tail_meta(p)['last_owner_input_at']
    with p.open('a') as f:
        f.write(message('[sender: peer-a (session) · msg test]\nContinue', 1))
        f.write(message('# AGENTS.md instructions\nRules', 2))
        f.write(message('<system-reminder>Hook</system-reminder>', 3))
    incremental = server._extract_codex_tail_meta(p)
    assert incremental['turn_driver']['kind'] == 'session'
    assert incremental['last_owner_input_at'] == owner
    server._conv_meta_cache.clear()
    server._codex_tail_resume.clear()
    full = server._extract_codex_tail_meta(p)
    assert driver.fields(full) == driver.fields(incremental)
    with p.open('a') as f:
        f.write(message('[WATCHTOWER] New comment', 4, 'event_msg'))
    assert server._extract_codex_tail_meta(p)['turn_driver']['kind'] == 'service'


@pytest.mark.parametrize('engine', ['cursor', 'antigravity', 'gemini'])
def test_other_cached_native_parsers_keep_driver_and_owner(engine, tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    ts1, ts2 = '2026-01-01T00:00:00Z', '2026-01-01T01:00:00Z'
    prompts = [('Owner ask', ts1), ('[sender: reviewer (session) · msg test]\nCheck', ts2)]
    p = tmp_path / 'messages.jsonl'
    if engine == 'gemini':
        p = tmp_path / 'session.json'
        p.write_text(json.dumps({'messages': [{'type': 'user', 'timestamp': ts, 'content': text} for text, ts in prompts]}))
        monkeypatch.setattr(server, '_gemini_project_root_for_chat', lambda p: str(tmp_path))
        meta = server._extract_gemini_tail_meta(p)
    elif engine == 'cursor':
        monkeypatch.setattr(server, '_cursor_tail_resume', {})
        p.write_text(''.join(json.dumps({'role': 'user', 'timestamp': ts, 'content': text})+'\n' for text, ts in prompts))
        meta = server._extract_cursor_tail_meta(p)
    else:
        monkeypatch.setattr(server, '_antigravity_tail_resume', {})
        p.write_text(''.join(json.dumps({'type': 'USER_INPUT', 'source': 'USER_EXPLICIT', 'created_at': ts, 'content': text})+'\n' for text, ts in prompts))
        meta = server._extract_antigravity_tail_meta(p)
    assert meta['turn_driver']['kind'] == 'session'
    assert meta['turn_driver']['name'] == 'reviewer'
    assert meta['last_owner_input_at'] == driver.epoch(ts1)


def test_hermes_summary_classifies_existing_message_pass(monkeypatch):
    import server
    from ccc_server import hermes
    monkeypatch.setattr(hermes, '_hermes_fetch_messages', lambda *a: [
        {'role': 'user', 'timestamp': '2026-01-01T00:00:00Z', 'content': 'Owner ask'},
        {'role': 'user', 'timestamp': '2026-01-01T01:00:00Z', 'content': '[WATCHTOWER] Routine'},
    ])
    meta = hermes._hermes_message_summary(None, 'session')
    assert meta['turn_driver']['kind'] == 'service'
    assert meta['last_owner_input_at'] == driver.epoch('2026-01-01T00:00:00Z')


def test_queued_peer_body_keeps_origin_instead_of_becoming_owner(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    p = tmp_path / 'queued.jsonl'
    owner = {'type': 'attachment', 'timestamp': '2026-01-01T00:00:00Z',
             'attachment': {'type': 'queued_command', 'commandMode': 'prompt', 'prompt': 'Owner ask'}}
    p.write_text(json.dumps(owner, separators=(',', ':'))+'\n'+json.dumps({'type': 'attachment', 'timestamp': '2026-01-01T01:00:00Z',
        'attachment': {'type': 'queued_command', 'commandMode': 'prompt', 'prompt': 'Review',
                       'origin': {'kind': 'peer', 'name': 'reviewer', 'body': 'Review'}}}, separators=(',', ':'))+'\n')
    meta = server._extract_tail_meta(p)
    assert meta['turn_driver']['kind'] == 'session'
    assert meta['turn_driver']['name'] == 'reviewer'
    assert meta['last_owner_input_at'] == driver.epoch('2026-01-01T00:00:00Z')


def test_persisted_list_bodies_advance_with_row_schema(monkeypatch, tmp_path):
    import server
    monkeypatch.setattr(server, '_ARCHIVE_LIST_DISK_DIR', tmp_path)
    key = server._archive_response_cache_key()
    old = server._archive_list_disk_path(key, '1d')
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_BUILD_ID', 'next-row-schema')
    assert server._archive_list_disk_path(key, '1d') != old


def test_sidechain_is_attributed_to_owning_session_without_owner_input(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_emit_interrupts_from_meta', lambda *a: None)
    p = tmp_path / 'parent-session' / 'subagents' / 'agent-review.jsonl'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({'type': 'user', 'timestamp': '2026-01-01T00:00:00Z',
                            'isSidechain': True, 'message': {'content': 'Review changes'}})+'\n')
    meta = server._extract_tail_meta(p)
    assert meta['turn_driver'] == {'kind': 'session', 'id': 'parent-session',
                                   'name': 'parent-session', 'basis': 'subagent',
                                   'at': driver.epoch('2026-01-01T00:00:00Z')}
    assert driver.fields(meta)['last_owner_input_at'] == 0
    assert driver.classify('Review', {'isSidechain': True}, parent_session_id='parent')['basis'] == 'subagent'
    # An explicit author wins over the native parent context.
    assert driver.classify('[sender: Owner (human) · msg test]\nChange it',
                           {'isSidechain': True}, parent_session_id='parent')['kind'] == 'human'


@pytest.mark.parametrize('engine', ['claude', 'codex', 'grok'])
def test_persisted_subagent_row_uses_parent_without_overwriting_known_sender(engine, monkeypatch):
    import server
    monkeypatch.setattr(server, 'usage_limit_resume_at_for_session', lambda *a, **kw: None)
    row = {'session_id': 'child', 'engine': engine, 'turn_driver': {'kind': 'unknown', 'at': 100},
           'last_owner_input_at': 50}
    out = server._archive_list_project_row(row, parents={'child': 'parent'})
    assert out['turn_driver'] == {'kind': 'session', 'id': 'parent', 'name': 'parent',
                                  'basis': 'subagent', 'at': 100}
    assert out['last_owner_input_at'] == 50
    assert row['turn_driver']['kind'] == 'unknown'
    for kind in ['human', 'session', 'service', 'background']:
        row['turn_driver'] = {'kind': kind, 'name': 'explicit', 'at': 200}
        assert server._archive_list_project_row(row, parents={'child': 'parent'})['turn_driver'] == row['turn_driver']


def test_codex_native_subagent_prompt_is_not_owner_input(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_codex_tail_resume', {})
    p = tmp_path / 'rollout.jsonl'
    events = [
        {'type': 'session_meta', 'payload': {'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'parent'}}}}},
        {'type': 'event_msg', 'timestamp': '2026-01-01T00:00:00Z',
         'payload': {'type': 'user_message', 'message': 'Review the changes'}},
    ]
    p.write_text(''.join(json.dumps(e)+'\n' for e in events))
    meta = server._extract_codex_tail_meta(p)
    assert meta['turn_driver']['kind'] == 'session'
    assert meta['turn_driver']['id'] == 'parent'
    assert driver.fields(meta)['last_owner_input_at'] == 0
