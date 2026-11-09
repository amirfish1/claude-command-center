"""Boundary checks for the optional outcome HTTP adapter."""
import io
import unittest
from unittest.mock import patch

from ccc_server import outcome_api


class OutcomeAPITest(unittest.TestCase):
    def setUp(self):
        outcome_api._SETTINGS = None
        outcome_api._SETTINGS_AT = 0

    def enabled(self):
        return patch.object(outcome_api, 'settings', return_value=({'ok': True, 'enabled': True, 'available': True}, 200))

    def test_missing_capability_defaults_off_without_claiming_available(self):
        with patch.object(outcome_api, '_call', return_value=({'ok': False, 'error': 'unavailable'}, 503)):
            data, status = outcome_api.settings()
        self.assertEqual(status, 200)
        self.assertFalse(data['enabled'])
        self.assertFalse(data['available'])

    def test_disabled_does_not_read_or_mutate_outcome_store(self):
        with patch.object(outcome_api, 'settings', return_value=({'enabled': False, 'available': True}, 200)), patch.object(outcome_api, '_call') as call:
            data, status = outcome_api.operate('list', {})
        self.assertEqual(status, 503)
        self.assertEqual(data['code'], 'outcomes_disabled')
        call.assert_not_called()

    def test_browser_cannot_assert_delivery_or_write_verification_receipts(self):
        with self.enabled(), patch.object(outcome_api, '_call') as call:
            for action in ('accept', 'evidence', 'lead-result'):
                self.assertEqual(outcome_api.operate(action, {'id': 'OUT-1'})[1], 404)
            for fields in ({'state': 'delivered'}, {'evidence': [{'result': 'passed'}]}, {'criteria': [{'id': 'speed', 'text': 'Fast', 'verified': True}]}):
                self.assertEqual(outcome_api.operate('update', {'id': 'OUT-1', 'expected_revision': 1, 'patch': fields})[1], 400)
        call.assert_not_called()

    def test_stale_revision_is_required_at_edit_boundary(self):
        with self.enabled(), patch.object(outcome_api, '_call') as call:
            self.assertEqual(outcome_api.operate('update', {'id': 'OUT-1', 'patch': {'title': 'Updated'}})[1], 400)
            self.assertEqual(outcome_api.operate('update', {'id': 'OUT-1', 'expected_revision': True, 'patch': {'title': 'Updated'}})[1], 400)
        call.assert_not_called()

    def test_edit_preserves_backend_criterion_check_configuration(self):
        criterion = {'id': 'serving', 'text': 'Serving check passes', 'required': True,
                     'accepts': ['independent_verification'], 'max_age_s': 3600}
        with self.enabled(), patch.object(outcome_api, '_call', return_value=({'ok': True}, 200)) as call:
            data, status = outcome_api.operate('update', {'id': 'OUT-1', 'expected_revision': 1, 'patch': {'criteria': [criterion]}})
        self.assertEqual(status, 200)
        self.assertEqual(call.call_args.args[1]['patch']['criteria'], [criterion])

    def test_rank_distinguishes_explicit_zero_from_unset(self):
        with self.enabled(), patch.object(outcome_api, '_call', return_value=({'ok': True}, 200)) as call:
            for rank in (0, None):
                self.assertEqual(outcome_api.operate('update', {'id': 'OUT-1', 'expected_revision': 1, 'patch': {'rank': rank}})[1], 200)
                self.assertEqual(call.call_args.args[1]['patch']['rank'], rank)
            for rank in (True, -1, '0'):
                self.assertEqual(outcome_api.operate('update', {'id': 'OUT-1', 'expected_revision': 1, 'patch': {'rank': rank}})[1], 400)

    def test_inferred_link_cannot_impersonate_explicit_provenance(self):
        with self.enabled(), patch.object(outcome_api, '_call', return_value=({'ok': True}, 200)) as call:
            outcome_api.operate('link', {'id': 'OUT-1', 'ref': 'TASK-12', 'source': 'verifier', 'relation': 'serves'})
        self.assertEqual(call.call_args.args[1]['provenance']['source'], 'owner')
        self.assertEqual(call.call_args.args[1]['target'], 'TASK-12')
        self.assertEqual(call.call_args.args[1]['kind'], 'ticket')

    def test_owner_review_translates_to_a_manual_runtime_event(self):
        with self.enabled(), patch.object(outcome_api, '_call', return_value=({'ok': True}, 200)) as call:
            outcome_api.operate('event', {'id': 'OUT-1', 'type': 'owner_review', 'event_key': 'review-1', 'reason': 'Check current evidence'})
        self.assertEqual(call.call_args.args, ('event', {'id': 'OUT-1', 'type': 'manual', 'key': 'review-1', 'payload': {'summary': 'Check current evidence'}}))

    def test_runtime_events_cannot_be_forged_through_owner_endpoint(self):
        with self.enabled(), patch.object(outcome_api, '_call') as call:
            for kind in ('executor_completed', 'verified', 'accepted'):
                self.assertEqual(outcome_api.operate('event', {'id': 'OUT-1', 'type': kind})[1], 400)
        call.assert_not_called()

    def test_settings_requires_exact_boolean_and_rejects_extra_control_fields(self):
        with patch.object(outcome_api, '_call') as call:
            for payload in ({'enabled': 'true'}, {'enabled': 1}, {'enabled': True, 'model': 'other'}, []):
                self.assertEqual(outcome_api.settings(payload)[1], 400)
        call.assert_not_called()

    def test_identifier_and_repo_validation_precede_transport(self):
        with self.enabled(), patch.object(outcome_api, '_call') as call:
            self.assertEqual(outcome_api.operate('get', {'id': '../../state'})[1], 400)
            data, status = outcome_api.operate('create', {'title': 'Reliable releases', 'goal': 'Ship reliably', 'criteria': [{'id': 'serving', 'text': 'Serving check passes'}], 'repo_path': '/unknown'}, resolve_repo=lambda _: (_ for _ in ()).throw(ValueError('Unknown repository')))
        self.assertEqual(status, 400)
        self.assertIn('Unknown repository', data['error'])
        call.assert_not_called()

    def test_http_rejects_oversized_body_before_reading(self):
        class Handler:
            path = '/api/outcomes/create'
            headers = {'Content-Length': str(128 * 1024 + 1)}
            rfile = io.BytesIO(b'not read')
            command = 'POST'
            def send_json(self, data, status):
                self.result = data, status
        h = Handler()
        outcome_api.handle(h, 'POST', resolve_repo=lambda p: p)
        self.assertEqual(h.result[1], 413)
        self.assertEqual(h.rfile.tell(), 0)


    def test_legacy_spawns_do_not_contact_optional_service(self):
        with patch.object(outcome_api, '_call') as call:
            self.assertIsNone(outcome_api.prepare_spawn({}, 'parent-session'))
        call.assert_not_called()

    def test_only_explicit_parent_association_inherits(self):
        linked = {'id': 'OUT-1', 'state': 'active', 'associations': [{'session_id': 'parent'}]}
        unrelated = {'id': 'OUT-2', 'state': 'active', 'title': 'Same words'}
        with self.enabled(), patch.object(outcome_api, 'cached_enabled', return_value=True), patch.object(outcome_api, '_call', return_value=({'ok': True, 'outcomes': [linked, unrelated]}, 200)):
            self.assertEqual(outcome_api.prepare_spawn({}, 'parent')['id'], 'OUT-1')
            self.assertIsNone(outcome_api.prepare_spawn({}, 'unknown'))

    def test_ambiguous_parent_is_not_automatically_filed(self):
        rows = [{'id': 'OUT-1', 'state': 'active', 'session_ids': ['parent']}, {'id': 'OUT-2', 'state': 'active', 'session_ids': ['parent']}]
        with self.enabled(), patch.object(outcome_api, 'cached_enabled', return_value=True), patch.object(outcome_api, '_call', return_value=({'ok': True, 'outcomes': rows}, 200)):
            self.assertIsNone(outcome_api.prepare_spawn({}, 'parent'))

    def test_failed_association_does_not_turn_existing_process_into_failed_spawn(self):
        result = {'ok': True, 'session_id': 'child'}
        with patch.object(outcome_api, '_call', return_value=({'ok': False, 'error': 'store unavailable'}, 503)):
            outcome_api.associate_spawn({'id': 'OUT-1'}, result)
        self.assertTrue(result['ok'])
        self.assertEqual(result['outcome_association'], 'unconfirmed')


if __name__ == '__main__':
    unittest.main()
