"""CCC-142: the archive incremental refresh reuses unchanged Codex rows."""
from unittest.mock import Mock

from ccc_server import codex_parse


def _fake(rollout, alive=False):
    fake = Mock()
    fake._codex_fetch_threads.return_value = [{'id': 's1', 'first_user_message': 'hello'}]
    for name in ['_auto_titled_session_ids', '_load_repo_pins', '_load_session_name_overrides',
                 '_load_last_interactions', '_codex_spawn_parent_by_child', '_load_codex_parent_links',
                 '_codex_goals_snapshot', '_codex_app_server_activity_fields',
                 '_codex_activity_fields_from_tail', '_codex_stale_tool_fields']:
        getattr(fake, name).return_value = {}
    fake._codex_spawn_pid_by_thread_id.return_value = {'s1': {'alive': alive, 'pid': 7}} if alive else {}
    fake._extract_codex_tail_meta.return_value = {'last_prompt': 'p'}
    fake._session_graph.parent_snapshot.return_value = {}
    fake._load_conversation_lifecycle_sets.return_value = ({'s1'}, set())
    fake._load_verified_conversations.return_value = []
    fake._codex_capture_rows.return_value = []
    fake._codex_rollout_path_from_row.return_value = rollout
    fake._codex_ts_seconds.return_value = 0
    fake._strip_ccc_session_state_instruction.side_effect = lambda s: s or ''
    fake._strip_host_system_instruction.side_effect = lambda s: s or ''
    fake._spawn_timeline_mark.return_value = False
    fake._codex_display_name.return_value = 'A task'
    fake._codex_agent_task_label.return_value = ''
    fake._first_existing_dir.return_value = ''
    fake._continued_from_session_id_from_text.return_value = ''
    return fake


def _scan(**kw):
    return codex_parse.find_codex_conversations(
        repo_only=False, resolve_pr_states=False, resolve_worktree_dirty=False, **kw)


def test_unchanged_rollout_skips_tail_parse_but_refreshes_sidecars(tmp_path, monkeypatch):
    rollout = tmp_path / 'r.jsonl'
    rollout.write_text('{}\n')
    fake = _fake(rollout)
    monkeypatch.setattr(codex_parse, '_core', fake)
    first = _scan()
    assert first[0]['rollout_mtime'] == rollout.stat().st_mtime
    assert first[0]['archived'] is True
    fake._extract_codex_tail_meta.reset_mock()
    fake._load_conversation_lifecycle_sets.return_value = (set(), set())
    again = _scan(reuse_rows={'s1': first[0]})
    fake._extract_codex_tail_meta.assert_not_called()
    assert again[0]['last_prompt'] == 'p'
    assert again[0]['archived'] is False  # sidecar change still applies


def test_changed_or_live_rollout_is_reparsed(tmp_path, monkeypatch):
    rollout = tmp_path / 'r.jsonl'
    rollout.write_text('{}\n')
    fake = _fake(rollout)
    monkeypatch.setattr(codex_parse, '_core', fake)
    first = _scan()
    rollout.write_text('{}\n{"x":1}\n')
    fake._extract_codex_tail_meta.reset_mock()
    _scan(reuse_rows={'s1': first[0]})
    fake._extract_codex_tail_meta.assert_called_once()
    monkeypatch.setattr(codex_parse, '_core', _fake(rollout, alive=True))
    prev = dict(_scan()[0], is_live=False)
    live = _fake(rollout, alive=True)
    monkeypatch.setattr(codex_parse, '_core', live)
    _scan(reuse_rows={'s1': prev})
    live._extract_codex_tail_meta.assert_called_once()


def test_quiet_session_reused_without_touching_disk(tmp_path, monkeypatch):
    rollout = tmp_path / 'r.jsonl'
    rollout.write_text('{}\n')
    fake = _fake(rollout)
    fake._codex_fetch_threads.return_value = [{'id': 's1', 'first_user_message': 'hello', 'updated_at': 1000}]
    monkeypatch.setattr(codex_parse, '_core', fake)
    first = _scan()
    assert first[0]['codex_updated_at'] == 1000
    fake._codex_rollout_path_from_row.reset_mock()
    fake._extract_codex_tail_meta.reset_mock()
    fake._codex_display_name.reset_mock()
    again = _scan(reuse_rows={'s1': first[0]})
    fake._codex_rollout_path_from_row.assert_not_called()
    fake._extract_codex_tail_meta.assert_not_called()
    fake._codex_display_name.assert_not_called()  # no rename -> keep name
    assert again[0]['display_name'] == 'A task'


def test_recent_or_renamed_session_rechecked(tmp_path, monkeypatch):
    import time
    rollout = tmp_path / 'r.jsonl'
    rollout.write_text('{}\n')
    fake = _fake(rollout)
    fake._codex_fetch_threads.return_value = [{'id': 's1', 'first_user_message': 'hi', 'updated_at': int(time.time())}]
    monkeypatch.setattr(codex_parse, '_core', fake)
    first = _scan()
    fake._codex_rollout_path_from_row.reset_mock()
    fake._load_session_name_overrides.return_value = {'s1': 'Renamed'}
    fake._codex_display_name.return_value = 'Renamed'
    again = _scan(reuse_rows={'s1': first[0]})
    fake._codex_rollout_path_from_row.assert_called_once()  # recent: stat check
    assert again[0]['display_name'] == 'Renamed'


def test_unchanged_rename_not_recomputed(tmp_path, monkeypatch):
    rollout = tmp_path / 'r.jsonl'
    rollout.write_text('{}\n')
    fake = _fake(rollout)
    fake._codex_fetch_threads.return_value = [{'id': 's1', 'first_user_message': 'hi', 'updated_at': 1000}]
    fake._load_session_name_overrides.return_value = {'s1': 'Mine'}
    monkeypatch.setattr(codex_parse, '_core', fake)
    first = _scan()
    fake._codex_display_name.reset_mock()
    _scan(reuse_rows={'s1': first[0]})
    fake._codex_display_name.assert_not_called()
    fake._load_session_name_overrides.return_value = {'s1': 'Other'}
    _scan(reuse_rows={'s1': first[0]})
    fake._codex_display_name.assert_called_once()
