"""Reuse immutable list fields without freezing mutable metadata or overlays."""
import server


def test_template_reuse_preserves_live_inputs_and_current_containers(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', server.ByteBudgetCache(1 << 20, 20))
    row = {'session_id': 'child', 'display_name': 'Before', 'last_assistant_text': 'Old reply',
           'cost_breakdown_usd': {'input': 1}, 'session_state': {'todos': [{'text': 'first'}]}}
    first = server._archive_list_project_row(row, resumes={}, parents={})
    template = server._ARCHIVE_LIST_ROW_TEMPLATES.get((row.get('source'), row['session_id']))
    row['cost_breakdown_usd']['input'] = 2
    row['session_state']['todos'][0]['text'] = 'second'
    second = server._archive_list_project_row(row, resumes={'child': {'resume_at': 9999999999}},
                                             parents={'child': 'parent'})
    assert server._ARCHIVE_LIST_ROW_TEMPLATES.get((row.get('source'), row['session_id'])) is template
    assert second['parent_session_id'] == 'parent'
    assert second['usage_limit_resume_at'] == 9999999999
    assert second['cost_breakdown_usd'] == {'input': 2}
    assert second['session_state'] == {'todos': [{'text': 'second'}]}
    second['session_state']['todos'][0]['text'] = 'caller mutation'
    assert row['session_state']['todos'][0]['text'] == 'second'
    assert first['session_state']['todos'][0]['text'] == 'first'

    # Lifecycle/title overlays mutate published row dictionaries in place.
    row.update(display_name='After', archived=True, last_assistant_text='New reply')
    third = server._archive_list_project_row(row, resumes={}, parents={})
    assert third['display_name'] == 'After'
    assert third['archived'] is True
    assert third['last_assistant_preview'] == 'New reply'
    assert 'parent_session_id' not in third
    assert 'usage_limit_resume_at' not in third


def test_template_rebuilds_when_fields_change_shape_or_disappear(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', server.ByteBudgetCache(1 << 20, 20))
    row = {'session_id': 'one', 'goal': 'text', 'question_options': ['a']}
    server._archive_list_project_row(row)
    row['goal'] = {'text': 'structured'}
    row['question_options'] = 'unstructured'
    out = server._archive_list_project_row(row)
    assert out['goal'] == {'text': 'structured'}
    assert out['question_options'] == 'unstructured'
    out['goal']['text'] = 'different'
    assert row['goal']['text'] == 'structured'
    del row['goal']
    del row['question_options']
    out = server._archive_list_project_row(row)
    assert 'goal' not in out and 'question_options' not in out


def test_oversized_rows_still_project_without_retaining_them(monkeypatch):
    cache = server.ByteBudgetCache(64, 2)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    row = {'session_id': 'large', 'first_message': 'x' * 10000, 'session_state': {'ok': True}}
    out = server._archive_list_project_row(row)
    assert out['first_message'] == row['first_message']
    assert out['session_state'] == {'ok': True}
    assert not len(cache)


def test_new_snapshot_reuses_template_but_copies_current_containers(monkeypatch):
    cache = server.ByteBudgetCache(1 << 20, 20)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    first = {'session_id': 'same', 'source': 'codex', 'display_name': 'Before',
             'question_options': ['one'], 'session_state': {'todos': ['original']}}
    server._archive_list_project_row(first, resumes={}, parents={})
    key = ('codex', 'same')
    template = cache.get(key)
    replacement = server.copy.deepcopy(first)
    # An unchanged snapshot must not pay the miss-only serialization cost.
    def no_serialize(*args, **kwargs):
        raise AssertionError('unchanged snapshot rebuilt the template')
    with monkeypatch.context() as scoped:
        scoped.setattr(server.json, 'dumps', no_serialize)
        out = server._archive_list_project_row(replacement, resumes={}, parents={})
    assert cache.get(key) is template
    assert len(cache) == 1
    out['session_state']['todos'].append('caller edit')
    assert replacement['session_state']['todos'] == ['original']
    assert first['session_state']['todos'] == ['original']
    replacement['display_name'] = 'After'
    replacement['question_options'] = ['two']
    out = server._archive_list_project_row(replacement, resumes={}, parents={})
    assert out['display_name'] == 'After'
    assert out['question_options'] == ['two']
    assert cache.get(key) is not template
    assert len(cache) == 1
