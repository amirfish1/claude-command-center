"""CLI captures remain visible when Codex creates no native rollout."""
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class EphemeralSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / ".git").mkdir()
        self.log = self.repo / 'spawn-codex-check-status-20261001T120000.log'
        self.log.write_text('\n'.join(json.dumps(row) for row in [
            {'type': 'thread.started', 'thread_id': 'capture-thread'},
            {'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': 'Finished checking.'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 20, 'output_tokens': 5}},
        ]) + '\n')
        for patch in [
            mock.patch.object(server, '_codex_fetch_threads', return_value=[]),
            mock.patch.object(server, '_codex_spawn_pid_by_thread_id', return_value={'capture-thread': {
                'pid': 42, 'log': str(self.log), 'cwd': str(self.repo), 'repo_path': str(self.repo),
                'prompt': 'Check status', 'model': 'example-model', 'alive': False}}),
            mock.patch.object(server, '_recent_codex_ccc_log_paths', return_value=[self.log]),
            mock.patch.object(server, '_codex_thread_row', return_value=None),
            mock.patch.object(server, '_resolve_codex_rollout_path', return_value=None),
            mock.patch.object(server, '_codex_logs_for_session', return_value=[(1, str(self.log))]),
            mock.patch.object(server, '_load_repo_pins', return_value={}),
            mock.patch.object(server, '_load_session_name_overrides', return_value={}),
            mock.patch.object(server, '_load_conversation_lifecycle_sets', return_value=(set(), set())),
            mock.patch.object(server, '_load_verified_conversations', return_value=[]),
            mock.patch.object(server, '_codex_spawn_parent_by_child', return_value={}),
            mock.patch.object(server, '_load_codex_parent_links', return_value={}),
            mock.patch.object(server, '_codex_goals_snapshot', return_value={}),
        ]:
            patch.start()
            self.addCleanup(patch.stop)

    def test_completed_capture_has_real_card_without_native_thread(self):
        rows = server.find_codex_conversations(repo_path=str(self.repo), resolve_pr_states=False, resolve_worktree_dirty=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['session_id'], 'capture-thread')
        self.assertEqual(rows[0]['spawn_pid'], 42)
        self.assertEqual(rows[0]['last_assistant_text'], 'Finished checking.')
        self.assertFalse(rows[0]['is_live'])
        self.assertEqual(rows[0]['last_event_type'], 'result')

    def test_capture_without_native_path_recovers_answer(self):
        result = server._codex_recover_log_conversation('capture-thread', None)
        self.assertIsNotNone(result)
        self.assertEqual(result['events'][-1]['type'], 'result')

    def test_other_repo_capture_is_excluded(self):
        other = self.repo / 'other'
        other.mkdir()
        (other / ".git").mkdir()
        rows = server.find_codex_conversations(repo_path=str(other), resolve_pr_states=False, resolve_worktree_dirty=False)
        self.assertEqual(rows, [])

    def test_capture_stream_works_without_native_rollout(self):
        handler = object.__new__(server.CommandCenterHandler)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda *args: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        with mock.patch.object(server.time, "sleep", side_effect=BrokenPipeError):
            handler._stream_codex_capture('capture-thread', None, 0)
        self.assertIn(b'Finished checking.', handler.wfile.getvalue())

    def test_capture_list_reads_only_bounded_log_content(self):
        from ccc_server import codex_log_recovery
        with self.log.open('a') as sink:
            sink.write('x' * 1000000)
        with mock.patch.object(server, '_codex_recover_log_conversation', side_effect=AssertionError('full transcript parsed on list path')):
            rows = server.find_codex_conversations(repo_path=str(self.repo), resolve_pr_states=False, resolve_worktree_dirty=False)
        self.assertEqual(len(rows), 1)

    def test_native_thread_suppresses_duplicate_capture_card(self):
        rows = server._codex_capture_rows([{'id': 'capture-thread'}], {}, str(self.repo))
        self.assertEqual(rows, [])

    def test_removed_capture_does_not_abort_list_metadata(self):
        self.log.unlink()
        self.assertEqual(server._codex_capture_tail('capture-thread', self.log), {})

    def test_capture_append_invalidates_archive_codex_rows(self):
        first = server._codex_capture_corpus_signature()
        with self.log.open('a') as sink:
            sink.write('new output\n')
        second = server._codex_capture_corpus_signature()
        self.assertNotEqual(first, second)
        old, new = 'capture-old', 'capture-new'
        key = 'ccc-codex-captures'
        with mock.patch.dict(server._ARCHIVE_STATMAP_BY_SIG, {
            old: ({}, {key: first}), new: ({}, {key: second}),
        }):
            self.assertEqual(server._archive_signature_delta(old, new), ([], [], {'codex'}))

    def test_discovery_replaces_cached_unknown_engine(self):
        with mock.patch.dict(server._ENGINE_DETECT_CACHE, {'capture-thread': ('claude', float('inf'))}):
            server.find_codex_conversations(repo_path=str(self.repo), resolve_pr_states=False, resolve_worktree_dirty=False)
            self.assertEqual(server._detect_session_engine('capture-thread'), 'codex')

    def test_direct_capture_lookup_works_before_list_scan(self):
        from ccc_server import codex_log_recovery
        sid = '00000000-0000-7000-8000-000000000001'
        self.log.write_text(self.log.read_text().replace('capture-thread', sid))
        codex_log_recovery._CAPTURE_ROWS.pop(sid, None)
        with mock.patch.object(server, '_known_repo_paths', return_value=[str(self.repo)]), \
             mock.patch.object(server, 'repo_log_dir', return_value=self.repo):
            row = server._codex_capture_thread_row(sid)
        self.assertIsNotNone(row)
        self.assertEqual(row['_ccc_capture'], str(self.log))

    def test_repo_from_session_uses_capture_cwd(self):
        capture = {'cwd': str(self.repo), '_ccc_capture': str(self.log)}
        with mock.patch.object(server, 'find_session_cwd', return_value=None), \
             mock.patch.object(server, '_spawn_registry_entry_for_session', return_value=None), \
             mock.patch.object(server, '_codex_capture_thread_row', return_value=capture):
            result = server.repo_from_session('capture-thread')
        self.assertEqual(result['cwd'], str(self.repo.resolve()))

    def _resume_patches(self, spawn):
        capture = {
            'cwd': str(self.repo),
            'title': 'check status',
            'model': 'example-model',
            '_ccc_capture': str(self.log),
        }
        return [
            mock.patch.object(server, '_control_plane_engine_call', return_value=None),
            mock.patch.object(server, '_pending_writer_compatibility_status', return_value={'ok': True}),
            mock.patch.object(server, '_queue_codex_resume', return_value=None),
            mock.patch.object(server, '_resolve_codex_bin', return_value={'available': True, 'bin': 'codex'}),
            mock.patch.object(server, '_spawn_registry_entry_for_session', return_value=None),
            mock.patch.object(server, 'find_session_cwd', return_value=None),
            mock.patch.object(server, '_codex_capture_thread_row', return_value=capture),
            mock.patch.object(server, '_get_session_override', return_value=None),
            mock.patch.object(server, '_model_policy_blocks', return_value=False),
            mock.patch.object(server, '_spawn_fallback_model_for_engine', return_value='example-model'),
            mock.patch.object(server, '_resume_ledger_append'),
            mock.patch.object(server, '_spawned_sessions', []),
            mock.patch.object(server, 'spawn_session_codex', spawn),
        ]

    def test_resume_ephemeral_capture_spawns_continuation(self):
        import ccc_server.continuation
        spawn = mock.Mock(return_value={'ok': True, 'session_id': 'new-thread', 'pid': 7, 'log': 'x.log'})
        patches = self._resume_patches(spawn)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10], patches[11], patches[12], \
             mock.patch.object(ccc_server.continuation, 'record_manual_forward') as fwd, \
             mock.patch.object(ccc_server.continuation, 'rebind_chain_to'):
            result = server.resume_session_codex('capture-thread', 'is it fixed now')
        self.assertTrue(result['ok'])
        self.assertEqual(result['via'], 'codex-continuation')
        self.assertEqual(result['new_session_id'], 'new-thread')
        spawn.assert_called_once()
        _, kwargs = spawn.call_args
        self.assertEqual(kwargs['cwd'], str(self.repo))
        self.assertEqual(kwargs['parent_session_id'], 'capture-thread')
        prompt = spawn.call_args[0][0]
        self.assertIn('Origin session id: capture-thread', prompt)
        self.assertIn(str(self.log), prompt)
        self.assertIn('CCC capture log', prompt)
        self.assertNotIn('find ~/.codex/sessions', prompt)
        self.assertIn('is it fixed now', prompt)
        fwd.assert_called_once_with('capture-thread', 'new-thread')

    def test_resume_ephemeral_steer_does_not_spawn(self):
        spawn = mock.Mock(return_value={'ok': True, 'session_id': 'new-thread', 'pid': 7, 'log': 'x.log'})
        patches = self._resume_patches(spawn)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10], patches[11], patches[12], \
             mock.patch.object(server, '_codex_headless_exec_refusal', return_value=None), \
             mock.patch.object(server, '_retry_pending_input_recovery', return_value=True), \
             mock.patch.object(server, '_claim_matching_pending_input', return_value=None), \
             mock.patch.object(server, '_resume_session_codex_native_delivery', return_value={'ok': False}):
            server.resume_session_codex('capture-thread', 'steer it', steer=True)
        spawn.assert_not_called()

    def test_concurrent_ephemeral_sends_share_one_successor(self):
        import concurrent.futures
        import contextlib
        import time
        from ccc_server import continuation
        forwards = {}
        def spawn_once(*args, **kwargs):
            time.sleep(0.05)
            return {'ok': True, 'session_id': 'new-thread'}
        spawn = mock.Mock(side_effect=spawn_once)
        with contextlib.ExitStack() as stack:
            for patch in self._resume_patches(spawn):
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(continuation, 'manual_forward_target', side_effect=lambda sid: forwards.get(sid, sid)))
            stack.enter_context(mock.patch.object(continuation, 'record_manual_forward', side_effect=lambda old, new: forwards.update({old: new})))
            stack.enter_context(mock.patch.object(continuation, 'rebind_chain_to'))
            stack.enter_context(mock.patch.object(server, '_inject_dedupe_record'))
            resume = stack.enter_context(mock.patch.object(server, 'resume_session_codex', return_value={'ok': True, 'via': 'codex-resume'}))
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(server._codex_continue_ephemeral, 'capture-thread', text,
                           cwd=str(self.repo), capture_row={'_ccc_capture': str(self.log)})
                           for text in ['first task', 'second task']]
                results = [f.result(timeout=5) for f in futures]
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(resume.call_count, 1)
        self.assertTrue(all(r['new_session_id'] == 'new-thread' for r in results))

    def test_ephemeral_first_message_is_deduped_on_successor(self):
        import contextlib
        from ccc_server import continuation
        spawn = mock.Mock(return_value={'ok': True, 'session_id': 'new-thread'})
        with contextlib.ExitStack() as stack:
            for patch in self._resume_patches(spawn):
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(continuation, 'manual_forward_target', side_effect=lambda sid: sid))
            stack.enter_context(mock.patch.object(continuation, 'record_manual_forward'))
            stack.enter_context(mock.patch.object(continuation, 'rebind_chain_to'))
            stack.enter_context(mock.patch.object(server, '_inject_dedupe_recent', {}))
            stack.enter_context(mock.patch.object(server, '_inject_dedupe_window_s', return_value=300))
            result = server._codex_continue_ephemeral('capture-thread', 'first task',
                cwd=str(self.repo), capture_row={'_ccc_capture': str(self.log)}, idempotency_key='send-1')
            duplicate = server._inject_duplicate_check('new-thread', 'first task', idempotency_key='send-1')
        self.assertTrue(result['ok'])
        self.assertIsNotNone(duplicate)
        self.assertTrue(duplicate['deduped'])

    def test_worker_continuation_records_successor_delivery_in_dashboard(self):
        with mock.patch.object(server, '_inject_dedupe_recent', {}), \
             mock.patch.object(server, '_inject_dedupe_inflight', {}), \
             mock.patch.object(server, '_inject_dedupe_window_s', return_value=300), \
             mock.patch.object(server, '_log_inject_result'), \
             mock.patch.object(server, '_inject_text_into_session_router', return_value={
                 'ok': True, 'via': 'codex-continuation', 'new_session_id': 'new-thread'}):
            result = server._inject_text_into_session('capture-thread', 'first task', idempotency_key='send-1')
            duplicate = server._inject_duplicate_check('new-thread', 'first task', idempotency_key='send-1')
        self.assertTrue(result['ok'])
        self.assertIsNotNone(duplicate)
        self.assertTrue(duplicate['deduped'])
