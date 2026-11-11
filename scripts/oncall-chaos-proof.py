#!/usr/bin/env python3
"""Isolated fault proof. Quick mode uses no model account or external service.

Rows distinguish actual process faults from boundaries. No success row implies
worker recovery or exactly-once provider delivery across an ambiguous timeout.
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location('session_proof', REPO / 'scripts/oncall-session-proof.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def child(root, quick):
    from ccc_server.oncall.engine import Engine
    from ccc_server.oncall.store import Store, default_config, save_config
    root = Path(root)
    rows, processes = [], []
    started = time.monotonic()
    def row(name, result, detail):
        rows.append((name, result, detail))
        print(' | '.join(rows[-1]), flush=True)
    print('Check | Result | Measurement / reason', flush=True)
    oncall = root / 'state/oncall'
    store = Store(oncall)
    cfg = default_config()
    sink = root / 'sink.py'
    sink.write_text('''import json, os, pathlib, signal, sys
root = pathlib.Path.home()
to = os.environ.get('CCC_ONCALL_TO', '')
with (root / 'messages.jsonl').open('a') as f:
    f.write(json.dumps({'to': to, 'text': sys.stdin.read()}) + '\\n')
if to == 'owner@example.test' and (root / 'block-owner').exists():
    (root / 'accepted-owner').touch()
    signal.pause()
if to == 'reporter@example.test' and (root / 'block').exists():
    (root / 'accepted').touch()
    signal.pause()
''')
    cfg.update(enabled=True, ack=False, channels=[{
        'id': 'local', 'type': 'command', 'owner': 'owner@example.test',
        'intake': True, 'send': f'{sys.executable} {sink}'}])
    save_config(oncall, cfg)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    os.environ['CCC_SERVER'] = base
    def status():
        return helper.ready(base + '/api/oncall/status')
    def launch():
        with (root / 'dashboard.log').open('a') as log:
            proc = subprocess.Popen([sys.executable, str(REPO / 'server.py'), '--port', str(port)],
                                    cwd=root, stdout=log, stderr=log, start_new_session=True)
        processes.append(proc)
        helper.wait_for(status, 60, 'dashboard readiness')
        return proc
    def kill(proc):
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
    def messages():
        file = root / 'messages.jsonl'
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []
    try:
        # A real uncommitted SQLite transaction is interrupted by SIGKILL.
        code = """import sqlite3, signal, sys
c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE')
c.execute("INSERT INTO kv VALUES('torn', 'true')")
print('writing', flush=True); signal.pause()
"""
        writer = subprocess.Popen([sys.executable, '-c', code, str(store.path)], stdout=subprocess.PIPE,
                                  text=True, start_new_session=True)
        processes.append(writer)
        assert writer.stdout.readline().strip() == 'writing'
        kill(writer)
        assert store.kv_get('torn') is None
        assert store._one('PRAGMA integrity_check')['integrity_check'] == 'ok'
        row('SIGKILL during store transaction', 'PASS', 'rollback and integrity_check=ok')
        alert, _ = store.alert_upsert('before-start', kind='permission', title='Parked approval', body='Choose')
        dashboard = launch()
        helper.wait_for(lambda: store.alert(alert['id'])['sent_at'], 30, 'owner alert')
        kill(dashboard)
        dashboard = launch()
        helper.request(base + '/api/oncall/tick', {})
        assert len([m for m in messages() if 'Parked approval' in m['text']]) == 1
        out = subprocess.check_output([sys.executable, str(REPO / 'ccc'), 'oncall', 'status'], text=True, timeout=30)
        assert 'on' in out.lower()
        row('dashboard restart after alert / CLI', 'PASS', 'one persisted owner message; CLI status works')
        (root / 'block-owner').touch()
        interrupted, _ = store.alert_upsert('owner-interrupted', kind='permission', title='Interrupted owner', body='Choose')
        helper.wait_for(lambda: (root / 'accepted-owner').exists(), 30, 'owner acceptance')
        kill(dashboard)
        (root / 'block-owner').unlink()
        dashboard = launch()
        helper.request(base + '/api/oncall/tick', {})
        assert len([m for m in messages() if 'Interrupted owner' in m['text']]) == 1
        assert store.delivery_latest(interrupted['id'], 'local')['ok'] == 0
        row('SIGKILL mid-owner-send', 'PASS', 'one acceptance; unknown delivery stays parked')
        report, _ = store.report_upsert('fixture', 'report', title='Fixture', text='Fixture',
                    state='filed', reporter_channel='local', reporter_address='reporter@example.test')
        store.report_update(report['id'], ticket_ref='DEMO-1')
        eng = Engine(oncall, flag_on=lambda: True)
        pending = eng.request_message('DEMO-1', 'tell', 'Fixed fixture')
        eng.store.close()
        (root / 'block').touch()
        payload = {'channel': 'local', 'id': 'approval-once', 'sender': 'owner@example.test',
                   'text': f"{pending['alert']} yes"}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(helper.request, base + '/api/oncall/inbound', payload)
            helper.wait_for(lambda: (root / 'accepted').exists(), 30, 'customer acceptance')
            kill(dashboard)
            try:
                future.result(timeout=10)
            except (OSError, RuntimeError):
                pass
        (root / 'block').unlink()
        dashboard = launch()
        helper.request(base + '/api/oncall/tick', {})
        assert helper.request(base + '/api/oncall/inbound', payload)['new'] is False
        # A second provider ID for the same approval must also not resend.
        payload['id'] = 'approval-duplicate'
        helper.request(base + '/api/oncall/inbound', payload)
        assert len([m for m in messages() if m['to'] == 'reporter@example.test']) == 1
        assert store.customer_message(pending['message'])['state'] == 'uncertain'
        assert store.alert_by_key('delivery-problem:' + str(pending['message']))
        row('SIGKILL mid-send / reply / approval', 'PASS', 'one customer acceptance; duplicate replies do not resend; owner review')
        kill(dashboard)
        # Month-shaped history, not a month of live agents or network polling.
        count = 3000 if quick else 30000
        begin = time.monotonic()
        with store._db:
            store._db.executemany("INSERT INTO alerts(key,kind,token,created_at,state,sent_at) "
                                  "VALUES(?, 'fixed', '', ?, 'answered', 1)",
                                  [(f'history:{i}', i * 86400 * 30 / count) for i in range(count)])
        eng = Engine(oncall, flag_on=lambda: True)
        before = time.monotonic()
        eng.tick()
        tick = time.monotonic() - before
        eng.store.close()
        row('month-shaped alert history', 'PASS', f'{count} rows; tick {tick:.3f}s; database {store.path.stat().st_size} bytes; load {time.monotonic()-begin:.3f}s')
        if shutil.which('wt'):
            from ccc_server.oncall.wt import WT
            wt = WT(argv=[shutil.which('wt')], dispatch=False)
            wt._ok('config', '-q', 'CHAOS', '--auto-drain', 'off')
            ref = wt.add('CHAOS', title='Isolated restart fixture', text='No worker should run.')
            wt._ok('block', ref, '--question', 'Keep parked?', '--wait-on', 'owner')
            def watcher():
                log_path = root / 'watcher.log'
                with log_path.open('w') as log:
                    proc = subprocess.Popen([*wt.argv(), 'start', '--foreground', '--auto-spawn',
                                             '--host', '127.0.0.1', '--port', '0'],
                                            stdout=log, stderr=log, start_new_session=True)
                processes.append(proc)
                helper.wait_for(lambda: 'HTTP server on' in log_path.read_text(), 20, 'watcher readiness')
                return proc
            first = watcher()
            kill(first)
            second = watcher()
            assert wt.find(ref)['needs_input']
            kill(second)
            row('isolated watcher SIGKILL / restart', 'PASS', 'parked ticket remains parked; auto-drain off')
        else:
            row('watcher restart', 'BOUNDARY', 'WatchTower not installed')
        row('worker death', 'BOUNDARY', 'no fixing agent started')
        fault_spec = importlib.util.spec_from_file_location('transport_faults', REPO / 'tests/test_oncall_chaos_transport.py')
        faults = importlib.util.module_from_spec(fault_spec)
        fault_spec.loader.exec_module(faults)
        faults.test_ten_minute_refusal_then_recovery(root / 'refusal')
        row('ten-minute channel refusal', 'PASS', 'real loopback HTTP; simulated retry clock; recovers after six failures')
        faults.test_accept_then_disconnect_does_not_retry(root / 'disconnect')
        row('accept then disconnect', 'PASS', 'real HTTP adapter; one acceptance; uncertain attempt not repeated')
        faults.test_half_open_connection_is_held(root / 'half-open')
        row('half-open response', 'PASS', 'real HTTP; shortened 100ms timeout; one acceptance; held')
        row('slow successful replies', 'BOUNDARY', 'not exercised')
        row('full month / store retention', 'BOUNDARY', 'history load only; history has no retention bound')
        row('elapsed', 'PASS', f'{time.monotonic()-started:.2f}s')
    finally:
        store.close()
        for proc in processes:
            if proc.poll() is None:
                kill(proc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--child', metavar='ROOT')
    args = parser.parse_args()
    if args.child:
        child(args.child, args.quick)
        return 0
    if not Path('/proc/self/environ').exists():
        parser.error('Linux /proc is required for isolated process cleanup')
    def interrupted(signum, frame):
        raise RuntimeError(f'Interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with tempfile.TemporaryDirectory(prefix='ccc-chaos-proof-') as tmp:
        root = Path(tmp)
        bins = root / 'bin'
        bins.mkdir()
        for name in ('python3', 'node', 'git', 'bash', 'sh', 'env', 'ps', 'lsof', 'cat', 'uname', 'wt'):
            executable = shutil.which(name)
            if executable:
                (bins / name).symlink_to(executable)
        env = {k: os.environ[k] for k in ('LANG', 'LC_ALL') if k in os.environ}
        env.update(HOME=tmp, PATH=str(bins), TMPDIR=tmp, XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp,
                   CCC_STATE_DIR=str(root / 'state'), CCC_ONCALL_STATE_DIR=str(root / 'state/oncall'),
                   CCC_EPHEMERAL='1', CCC_FF_ONCALL='1', CCC_ONCALL_EPHEMERAL='1',
                   CCC_TELEMETRY_DISABLED='1', CCC_AUTO_UPDATE='0', CCC_BG_HELPER='0',
                   CCC_CONV_READER='0', CCC_BOOT_WARM_DELAY_S='86400', WT_NO_AUTOSTART='1',
                   WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_SELF_UPDATE='1',
                   WATCHTOWER_DELEGATE_URL='off', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        proc = None
        try:
            proc = subprocess.Popen([sys.executable, __file__, '--child', tmp,
                                     *(['--quick'] if args.quick else [])], env=env, start_new_session=True)
            code = proc.wait(timeout=240)
            if code and (root / 'dashboard.log').exists():
                print((root / 'dashboard.log').read_text()[-6000:])
            return code
        finally:
            helper.cleanup(root, [proc] if proc else [])


if __name__ == '__main__':
    sys.exit(main())
