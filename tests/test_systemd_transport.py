"""Systemd job collection on the server host versus a remote host."""
import subprocess
import unittest
from unittest.mock import patch

from ccc_server import scheduled_jobs as jobs
from ccc_server import scheduled_jobs_feed as feed


class SystemdTransport(unittest.TestCase):
    def test_linux_feed_uses_local_bash(self):
        result = subprocess.CompletedProcess([], 0, '@@CCCJOB:END\n', '')
        with patch('platform.system', return_value='Linux'), \
                patch.object(feed.subprocess, 'run', return_value=result) as run:
            collected, error = feed.collect_hermes()
        self.assertEqual(collected, [])
        self.assertIsNone(error)
        self.assertEqual(run.call_args.args[0], ['bash', '-s'])
        self.assertEqual(run.call_args.kwargs['input'], feed.REMOTE_SCRIPT)

    def test_macos_feed_keeps_remote_ssh(self):
        result = subprocess.CompletedProcess([], 0, '@@CCCJOB:END\n', '')
        with patch('platform.system', return_value='Darwin'), \
                patch.object(feed.subprocess, 'run', return_value=result) as run:
            collected, error = feed.collect_hermes()
        self.assertIsNone(error)
        self.assertEqual(run.call_args.args[0][0], 'ssh')
        self.assertEqual(run.call_args.args[0][-2:], ['hermes', 'bash -s'])

    def test_linux_live_log_uses_local_bash_and_cursor(self):
        result = subprocess.CompletedProcess([], 0, '@@STATE:active\nhello\n-- cursor: s=a;i=2\n', '')
        with patch('platform.system', return_value='Linux'), \
                patch.object(jobs.subprocess, 'run', return_value=result) as run:
            log = jobs.get_scheduled_job_log_live('hermes:example.service', cursor='s=a;i=1')
        self.assertTrue(log['ok'])
        self.assertTrue(log['running'])
        self.assertEqual(log['log'], 'hello')
        self.assertEqual(log['cursor'], 's=a;i=2')
        self.assertEqual(run.call_args.args[0], ['bash', '-s', '--', 'example.service', '200', 's=a;i=1'])

    def test_linux_log_tail_uses_local_journal(self):
        result = subprocess.CompletedProcess([], 0, 'hello\n', '')
        with patch('platform.system', return_value='Linux'), \
                patch.object(jobs.subprocess, 'run', return_value=result) as run:
            log = jobs.get_scheduled_job_log('hermes:example.service', max_lines=10)
        self.assertEqual(log['log'], 'hello\n')
        self.assertEqual(run.call_args.args[0], ['journalctl', '-u', 'example.service', '-n', '10', '--no-pager'])

    def test_linux_registry_uses_local_systemctl(self):
        result = subprocess.CompletedProcess([], 0, '[]', '')
        with patch('platform.system', return_value='Linux'), \
                patch.object(jobs.subprocess, 'run', return_value=result) as run:
            collected, host = jobs._collect_hermes_systemd_jobs()
        self.assertEqual(collected, [])
        self.assertEqual(host['status'], 'online')
        self.assertEqual(run.call_args.args[0], ['systemctl', 'list-timers', '--output=json'])
