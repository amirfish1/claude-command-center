"""Lifecycle evidence survives exec/signals and distinguishes cgroup memory."""
import json
import ast
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ccc_server import restart_audit as audit

ROOT = Path(__file__).resolve().parents[1]


def events(state):
    return [json.loads(line.split('@ccc-activity-v2 ', 1)[1])
            for line in (state / 'logs/activity.log').read_text().splitlines()]


def test_restart_stop_and_snapshot_are_correlated(tmp_path, monkeypatch):
    monkeypatch.setenv('INVOCATION_ID', 'ab' * 16)
    instance = audit.Audit(tmp_path)
    instance.record('START', 'process', 'dashboard-start')
    instance.record('RESTART', {'source': 'http', 'reported_pid': 123}, 'manual-restart')
    instance.stop('SIGTERM', 'systemd-or-external-signal')
    instance.stop('process-exit')
    rows = events(tmp_path)
    assert [row['verb'] for row in rows] == ['START', 'RESTART', 'STOP']
    details = [json.loads(row['detail']) for row in rows]
    assert len({row['generation'] for row in details}) == 1
    assert details[-1]['restart_request']['caller']['reported_pid'] == 123
    assert details[-1]['uptime_s'] >= 0
    saved = json.loads(audit._snapshot_file(tmp_path, 'ab' * 16).read_text())
    assert saved['phase'] == 'STOP'


def test_memory_separates_process_children_and_file_cache(tmp_path, monkeypatch):
    for name, value in {'memory.current': '9000', 'memory.peak': '15000',
                        'memory.high': '16000', 'memory.max': '20000',
                        'memory.events': 'oom 0\noom_kill 0\nhigh 0',
                        'memory.stat': 'anon 4000\nfile 4900\nkernel 100\nshmem 90',
                        'cgroup.procs': '11\n12'}.items():
        (tmp_path / name).write_text(value)
    monkeypatch.setattr(audit, '_cgroup', lambda: tmp_path)
    monkeypatch.setattr(audit, '_process_memory', lambda pid: {'pid': pid, 'rss_bytes': pid * 10})
    result = audit.memory_snapshot()
    assert result['cgroup_peak_bytes'] == 15000
    assert result['cgroup_breakdown_bytes']['file'] == 4900
    assert result['cgroup_top_rss'][0]['pid'] == 12
    assert result['cgroup_events']['oom_kill'] == 0
    assert 'rss_bytes' not in audit.memory_snapshot(include_self=False)


def test_post_stop_preserves_oom_and_marks_last_sample_age(tmp_path, monkeypatch):
    monkeypatch.setenv('INVOCATION_ID', 'cd' * 16)
    instance = audit.Audit(tmp_path)
    instance.record('START', 'process', 'dashboard-start')
    monkeypatch.setenv('SERVICE_RESULT', 'oom-kill')
    monkeypatch.setenv('EXIT_CODE', 'killed')
    monkeypatch.setenv('EXIT_STATUS', 'KILL')
    audit.systemd_stop_post(tmp_path)
    detail = json.loads(events(tmp_path)[-1]['detail'])
    assert detail['reason'] == 'oom-kill'
    assert detail['exit_status'] == 'KILL'
    assert detail['last_process_sample']['pid'] == os.getpid()
    assert detail['sample_age_s'] >= 0
    assert 'rss_bytes' not in detail['memory_after_stop']
    # Do not attach a previous invocation's process to a new stop.
    monkeypatch.setenv('INVOCATION_ID', 'ef' * 16)
    audit.systemd_stop_post(tmp_path)
    assert json.loads(events(tmp_path)[-1]['detail'])['last_process_sample'] is None


def test_caller_identity_is_bounded_and_reported(tmp_path):
    handler = SimpleNamespace(client_address=('127.0.0.1', 123), headers={})
    assert audit.request_caller(handler)['reported_client'] == 'browser-or-api'
    caller = audit.request_caller(handler, {'audit_caller': {'source': 'ccc-cli', 'pid': 42,
                                                          'ppid': 'private-text'}})
    assert caller['reported_pid'] == 42
    assert 'reported_ppid' not in caller
    audit._append(tmp_path, 'STOP', {'caller': 'untrusted\nsecond line'})
    assert len(events(tmp_path)) == 1
    assert audit._snapshot_file(tmp_path, '../../escape').parent == tmp_path / 'restart-audit'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX signals')
@pytest.mark.parametrize('signum', [signal.SIGTERM, getattr(signal, 'SIGKILL', signal.SIGTERM)])
def test_real_process_stop_retains_evidence(tmp_path, signum):
    code = '''
import signal, sys, time
from ccc_server import restart_audit as a
a.start(sys.argv[1])
def stop(sig, frame):
    a.stop('SIGTERM', 'external-signal')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
print('ready', flush=True)
time.sleep(30)
'''
    env = dict(os.environ, INVOCATION_ID='12' * 16)
    child = subprocess.Popen([sys.executable, '-c', code, str(tmp_path)], cwd=ROOT,
                             env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        child.send_signal(signum)
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()
    env.update(SERVICE_RESULT='success' if signum == signal.SIGTERM else 'signal',
               EXIT_CODE='exited' if signum == signal.SIGTERM else 'killed',
               EXIT_STATUS='0' if signum == signal.SIGTERM else 'KILL')
    subprocess.run([sys.executable, str(ROOT / 'ccc_server/restart_audit.py'),
                    '--systemd-stop-post', '--state-dir', str(tmp_path)], env=env, check=True)
    rows = events(tmp_path)
    assert [r['verb'] for r in rows] == (['START', 'STOP', 'STOP_POST'] if signum == signal.SIGTERM
                                       else ['START', 'STOP_POST'])
    detail = json.loads(rows[-1]['detail'])
    assert detail['last_process_sample']['pid'] == child.pid
    assert detail['last_process_sample']['memory']['rss_peak_bytes'] > 0


def test_service_templates_keep_off_process_stop_hook():
    for name in ('run.sh', 'systemd/ccc.service'):
        source = (ROOT / name).read_text()
        assert 'ExecStopPost=-/usr/bin/env python3' in source
        assert 'restart_audit.py' in source


def test_restart_is_recorded_only_after_drain_succeeds(monkeypatch):
    # Exercise the shipped scheduler without importing unrelated dashboard
    # background workers or launching an actual replacement process.
    tree = ast.parse((ROOT / 'server.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_schedule_restart')
    recorded = []
    monkeypatch.setattr(audit, 'record_restart', lambda caller, reason: recorded.append(('audit', caller, reason)))
    class ImmediateTimer:
        def __init__(self, delay, callback):
            self.callback = callback
        def start(self):
            self.callback()
    namespace = {'sys': SimpleNamespace(stdout=sys.stdout, stderr=sys.stderr, platform='linux', executable=sys.executable),
                 'threading': SimpleNamespace(Timer=ImmediateTimer),
                 'os': SimpleNamespace(execvp=lambda executable, argv: recorded.append(('exec',))),
                 '_server_launch_argv': lambda: ['server.py'],
                 '_quiesce_http_for_restart': lambda: False}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<shipped scheduler>', 'exec'), namespace)
    namespace['_schedule_restart'](caller='test-cli', reason='manual-restart')
    assert recorded == []
    namespace['_quiesce_http_for_restart'] = lambda: True
    namespace['_schedule_restart'](caller='test-cli', reason='manual-restart')
    assert recorded == [('audit', 'test-cli', 'manual-restart'), ('exec',)]
