"""Queue transfer never copies messages or starts a second delivery owner."""
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import server
from ccc_server import codex_queue_owner as owner
from ccc_server import codex_client as client


class QueueOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            mock.patch.object(server, 'PENDING_INPUTS_FILE', self.root / 'pending.json'),
            mock.patch.object(server, 'PENDING_INPUT_HANDOFF_DIR', self.root / 'handoffs'),
            mock.patch.object(server, '_codex_queue_pump_lock', return_value=contextlib.nullcontext()),
            mock.patch.object(server, '_retry_pending_input_recovery', return_value=True),
            mock.patch.object(server, '_refresh_pending_inputs_for_session', return_value=True),
            mock.patch.object(server, '_pending_inputs_session_snapshot', return_value={'resume':[], 'terminal':[]}),
        ]
        for patch in self.patches: patch.start()
    def tearDown(self):
        for patch in reversed(self.patches): patch.stop()
        self.temp.cleanup()
    def test_claim_is_persistent_and_idempotent(self):
        owner.claim_native_queue('task')
        owner.claim_native_queue('task')
        self.assertTrue(owner.native_queue_owned('task'))
        self.assertFalse(owner.native_queue_owned('another'))
    def test_claim_refuses_existing_ccc_input(self):
        with mock.patch.object(server, '_pending_inputs_session_snapshot', return_value={'resume':['hello']}):
            with self.assertRaisesRegex(ValueError, 'CCC queued'): owner.claim_native_queue('task')
        self.assertFalse(owner.native_queue_owned('task'))
    def test_claim_refuses_recovery_and_handoffs(self):
        with mock.patch.object(server, '_retry_pending_input_recovery', return_value=False):
            with self.assertRaisesRegex(ValueError, 'recovery'): owner.claim_native_queue('task')
        (self.root/'handoffs').mkdir()
        (self.root/'handoffs'/'one.json').write_text(json.dumps({'session_id':'task','text':'pending'}))
        with self.assertRaisesRegex(ValueError, 'handoff'): owner.claim_native_queue('task')
    def test_release_requires_authoritative_empty_native_queue(self):
        owner.claim_native_queue('task')
        for result in ({'ok':False}, {'ok':True,'result':{}}, {'ok':True,'result':{'data':[{'id':'one'}]}},
                       {'ok':True,'result':{'data':[],'nextCursor':'more'}}):
            with self.assertRaises(ValueError): owner.release_native_queue('task', lambda:result)
            self.assertTrue(owner.native_queue_owned('task'))
        owner.release_native_queue('task', lambda:{'ok':True,'result':{'data':[],'nextCursor':None}})
        self.assertFalse(owner.native_queue_owned('task'))
    def test_pending_native_action_blocks_owner_switch(self):
        with mock.patch.object(client, '_client_scope', return_value={}), \
             mock.patch.object(client, '_CLIENT_ACTIONS', {'receipt':{'method':'thread/queue/add','thread_id':'task','state':'uncertain'}}):
            result=client.codex_client_dispatch('queue-owner',{'owner':'ccc','context':{'thread_id':'task','repo_path':str(self.root)}})
        self.assertFalse(result['ok'])
        self.assertIn('pending Codex queue',result['error'])
    def test_native_owned_handoff_is_not_written(self):
        from ccc_server import pending_inputs
        owner.claim_native_queue('task')
        self.assertIsNone(pending_inputs._write_pending_input_handoff('task','hello'))
        self.assertFalse((self.root/'handoffs').exists())
    def test_inflight_native_receipts_block_release_across_process_state(self):
        owner.begin_native_queue_action('task','action-one')
        owner.begin_native_queue_action('task','action-two')
        read=mock.Mock(return_value={'ok':True,'result':{'data':[]}})
        with self.assertRaisesRegex(ValueError, 'unconfirmed'): owner.release_native_queue('task',read)
        read.assert_not_called()
        owner.finish_native_queue_action('task','action-one')
        with self.assertRaisesRegex(ValueError, 'unconfirmed'): owner.release_native_queue('task',read)
        owner.finish_native_queue_action('task','action-two')
        owner.release_native_queue('task',read)
        self.assertFalse(owner.native_queue_owned('task'))
