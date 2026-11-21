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
        scoped.setattr(server._ARCHIVE_LIST_TEMPLATE_ENCODER, 'encode', no_serialize)
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


def test_template_weight_estimates_builtin_metadata_without_json_escaping(monkeypatch):
    cache = server.ByteBudgetCache(1 << 20, 20)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    row = {'session_id': 'unicode', 'display_name': 'Caf\u00e9 \U0001f600',
           'session_state': {'nested': ['\u05d0\u05d1', '\ud800', 3]},
           'parser_only': {'fallback': frozenset(('example',))}}
    expected = 4 * len(server.marshal.dumps(row, 2)) + 2 * server.sys.getsizeof(row)
    def no_json(*args, **kwargs):
        raise AssertionError('builtin metadata should not need JSON sizing')
    monkeypatch.setattr(server._ARCHIVE_LIST_TEMPLATE_ENCODER, 'encode', no_json)
    server._archive_list_project_row(row, resumes={}, parents={})
    assert cache.bytes == expected


def test_template_weight_falls_back_for_unsupported_parser_values(monkeypatch):
    from decimal import Decimal

    cache = server.ByteBudgetCache(1 << 20, 20)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    row = {'session_id': 'fallback', 'display_name': 'Example',
           'parser_only': Decimal('1.25')}
    expected = 4 * len(server.json.dumps(row, default=str).encode()) + 2 * server.sys.getsizeof(row)
    out = server._archive_list_project_row(row, resumes={}, parents={})
    assert out['display_name'] == 'Example'
    assert 'parser_only' not in out
    assert cache.bytes == expected


def test_template_weight_remains_conservative_for_large_unicode_values(monkeypatch):
    cache = server.ByteBudgetCache(1 << 20, 20)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    text = '\U0001f600\u05d0' * 10000
    row = {'session_id': 'large-unicode', 'last_assistant_text': text}
    server._archive_list_project_row(row, resumes={}, parents={})
    assert cache.bytes >= server.sys.getsizeof(text) + 2 * server.sys.getsizeof(row)
    assert cache.bytes <= cache.max_bytes


def test_bulk_projection_uses_one_cache_read_and_validates_current_rows(monkeypatch):
    from unittest import mock
    cache = server.ByteBudgetCache(1 << 20, 20)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    rows = [{'session_id': 'one', 'display_name': 'Before', 'question_options': ['a']},
            {'session_id': 'two', 'display_name': 'Two'}]
    for row in rows:
        server._archive_list_project_row(row, resumes={}, parents={})
    rows[0]['display_name'] = 'After'
    inputs = ({}, {}, {}, rows)
    with mock.patch.object(cache, 'get', side_effect=AssertionError('per-row cache lock')), \
         mock.patch.object(cache, 'get_many', wraps=cache.get_many) as batch:
        projected = server._archive_list_project_rows(rows, _inputs=inputs)
    batch.assert_called_once()
    assert projected[0]['display_name'] == 'After'
    projected[0]['question_options'].append('caller edit')
    assert rows[0]['question_options'] == ['a']


def test_packed_rows_skip_template_lookup_without_shifting_raw_templates(monkeypatch):
    cache = server.ByteBudgetCache(1 << 20, 20)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    get_many = cache.get_many
    looked_up = []

    def capture(keys):
        looked_up.extend(keys)
        return get_many(looked_up)

    monkeypatch.setattr(cache, 'get_many', capture)
    first = {'session_id': 'first', 'display_name': 'First'}
    last = {'session_id': 'last', 'display_name': 'Last', 'question_options': ['second']}
    for row in (first, last):
        server._archive_list_project_row(row, resumes={}, parents={})
    packed = server._archive_sidebar_pages.StoredRow(
        ('session_id', 'display_name'), ('packed', 'Packed'), None)
    rows = [first, packed, last]

    projected = server._archive_list_project_rows(rows, _inputs=({}, {}, {}, rows))

    assert looked_up == [(None, 'first'), (None, 'last')]
    assert [row['display_name'] for row in projected] == ['First', 'Packed', 'Last']
    assert projected[-1]['question_options'] == ['second']


def test_unversioned_full_history_keeps_scoped_templates(monkeypatch):
    cache = server.ByteBudgetCache(1 << 20, 1)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    recent = {'session_id': 'recent', 'display_name': 'Recent', 'mtime': 10000}
    old = {'session_id': 'old', 'display_name': 'Old',
           'question_options': [{'text': 'Answer'}], 'mtime': 1}
    rows = [recent, old]
    expected = server._archive_list_payload(rows, now=10000)
    server._archive_list_project_row(recent)
    template = cache.get((None, 'recent'))

    actual = server._archive_list_replay_payload(rows, None, now=10000)

    assert server.json.dumps(actual) == server.json.dumps(expected)
    assert cache.get((None, 'recent')) is template
    assert (None, 'old') not in cache
    actual['conversations'][1]['question_options'][0]['text'] = 'Caller edit'
    assert old['question_options'] == [{'text': 'Answer'}]

    recent['display_name'] = 'Renamed'
    actual = server._archive_list_replay_payload(rows, None, now=10000)
    assert actual['conversations'][0]['display_name'] == 'Renamed'
    assert cache.get((None, 'recent')) is template


def test_batch_field_layouts_preserve_canonical_output_and_current_value_types(monkeypatch):
    cache = server.ByteBudgetCache(1 << 20, 20)
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', cache)
    rows = [
        {'display_name': 'First', 'session_id': 'one', 'goal': 'text',
         'question_options': ['a'], 'parser_only': 'private metadata'},
        {'display_name': 'Second', 'session_id': 'two', 'goal': {'text': 'nested'},
         'question_options': 'plain', 'parser_only': 'private metadata'},
        {'session_id': 'three', 'display_name': 'Third', 'pinned': True},
    ]
    expected = [server._archive_list_project_row(row, resumes={}, parents={}) for row in rows]
    cache.clear()
    layouts = {}
    actual = [server._archive_list_project_row(row, resumes={}, parents={},
              _fields_by_shape=layouts) for row in rows]
    assert server.json.dumps(actual) == server.json.dumps(expected)
    assert 'parser_only' not in actual[0]
    actual[1]['goal']['text'] = 'caller edit'
    assert rows[1]['goal']['text'] == 'nested'

    # Existing layouts must not hide added/removed fields or freeze a field's
    # scalar/container type when a later snapshot replaces the row.
    rows[0]['goal'] = {'text': 'now structured'}
    del rows[0]['question_options']
    rows[0]['pinned'] = False
    out = server._archive_list_project_row(rows[0], resumes={}, parents={},
                                          _fields_by_shape=layouts)
    assert out['goal'] == {'text': 'now structured'}
    assert 'question_options' not in out
    assert out['pinned'] is False


def test_warm_templates_skip_batch_field_discovery(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_LIST_ROW_TEMPLATES', server.ByteBudgetCache(1 << 20, 20))
    row = {'session_id': 'one', 'display_name': 'Same', 'question_options': ['a']}
    server._archive_list_project_row(row, resumes={}, parents={})

    class NoDiscovery(dict):
        def get(self, *args):
            raise AssertionError('warm template repeated field discovery')

    out = server._archive_list_project_row(dict(row), resumes={}, parents={},
                                          _fields_by_shape=NoDiscovery())
    assert out['display_name'] == 'Same'
    out['question_options'].append('caller edit')
    assert row['question_options'] == ['a']
