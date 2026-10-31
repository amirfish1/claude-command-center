import json
from pathlib import Path
import tempfile
import unittest

from ccc_server.settings_insights import model_usage, queue_overview, apply_worker_defaults


class SettingsInsightsTests(unittest.TestCase):
    def test_starts_deduplicate_resumes_and_ignore_old_future_invalid(self):
        rows = [dict(session_id='a', spawned_at=100, engine='codex', model='one'),
                dict(session_id='a', spawned_at=200, engine='codex', model='two'),
                dict(session_id='b', spawned_at=900000, engine='claude'),
                dict(session_id='c', spawned_at='bad'),
                dict(session_id='d', spawned_at=-900000)]
        result = model_usage(rows, 300)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['models'][0]['model'], 'one')

    def test_transcript_counts_initial_not_last_model(self):
        from ccc_server.settings_insights import transcript_starts
        import time
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            now = time.time()
            stamp = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
            path.write_text('\n'.join(json.dumps(row) for row in [
                {'type': 'user', 'timestamp': stamp},
                {'type': 'assistant', 'timestamp': stamp, 'message': {'model': 'first'}},
                {'type': 'assistant', 'timestamp': stamp, 'message': {'model': 'later'}},
            ]))
            rows = [{'session_id': 'transcript-test', 'jsonl_path': str(path), 'engine': 'claude'}]
            starts = transcript_starts(rows + rows, now)
            self.assertEqual(len(starts), 1)
            self.assertEqual(model_usage(starts, now)['models'][0]['model'], 'first')

    def test_bulk_preserves_other_fields_and_excludes_archived(self):
        defaults = {'worker_engine': 'codex'}
        cfg = {'AUTO': {'auto_drain': True, 'engine': 'claude', 'model': 'old', 'effort': 'high', 'desired_workers': 3, 'extra': {'keep': True}},
               'MANUAL': {'auto_drain': False, 'engine': 'claude'},
               'ARCHIVED': {'archived': True, 'auto_drain': True, 'engine': 'claude'}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(cfg))
            path.chmod(0o640)
            view = queue_overview(cfg, defaults)
            payload = dict(scope='auto', revision=view['revision'], defaults_revision=view['defaults_revision'])
            self.assertEqual(apply_worker_defaults(path, payload, defaults), ['AUTO'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['AUTO'], {'auto_drain': True, 'desired_workers': 3, 'extra': {'keep': True}})
            self.assertEqual(saved['MANUAL'], cfg['MANUAL'])
            self.assertEqual(saved['ARCHIVED'], cfg['ARCHIVED'])
            with self.assertRaisesRegex(ValueError, 'changed'):
                apply_worker_defaults(path, payload, defaults)
            view = queue_overview(saved, defaults)
            payload.update(scope='all', revision=view['revision'])
            self.assertEqual(apply_worker_defaults(path, payload, defaults), ['MANUAL'])
            self.assertEqual(json.loads(path.read_text())['ARCHIVED'], cfg['ARCHIVED'])

    def test_default_change_invalidates_preview(self):
        cfg = {'ONE': {'engine': 'claude'}}
        defaults = {'worker_engine': 'codex'}
        view = queue_overview(cfg, defaults)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError, 'changed'):
                apply_worker_defaults(path, dict(scope='all', revision=view['revision'], defaults_revision=view['defaults_revision']), {'worker_engine': 'claude'})
            self.assertEqual(json.loads(path.read_text()), cfg)

    def test_http_preview_apply_and_stale_rejection(self):
        import server
        import threading
        import urllib.request
        import urllib.error
        from unittest.mock import patch
        defaults = {'worker_engine': 'codex', 'worker_model': 'test-model'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue-config.json'
            path.write_text(json.dumps({'DEMO': {'auto_drain': True, 'engine': 'claude', 'desired_workers': 2}}))
            with patch.object(server, '_wt_config_path', return_value=path), \
                 patch.object(server, '_load_spawn_defaults', return_value=defaults), \
                 patch.object(server, '_WT_CONFIG_AVAILABLE', False), \
                 patch.object(server, '_reconcile_once_async'), \
                 patch.object(server, '_wt_log_queue_config_change'):
                httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                base = 'http://127.0.0.1:' + str(httpd.server_address[1])
                try:
                    with urllib.request.urlopen(base + '/api/settings/queue-engines', timeout=5) as response:
                        view = json.load(response)
                    payload = dict(scope='auto', revision=view['revision'], defaults_revision=view['defaults_revision'])
                    request = urllib.request.Request(base + '/api/settings/queue-engines/apply', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        self.assertEqual(json.load(response)['changed'], ['DEMO'])
                    self.assertEqual(json.loads(path.read_text())['DEMO'], {'auto_drain': True, 'desired_workers': 2})
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(error.exception.code, 400)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=5)

    def test_queue_view_uses_authoritative_resolution(self):
        result = queue_overview({'ONE': {'auto_drain': True, 'model': 'pin'}}, {}, lambda name: ('codex', 'pin', 'medium'))
        self.assertEqual(result['queues'][0]['engine'], 'codex')
        self.assertEqual(result['queues'][0]['pins'], {'model': 'pin'})


if __name__ == '__main__':
    unittest.main()
