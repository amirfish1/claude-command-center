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
import tarfile
import io
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location('session_proof', REPO / 'scripts/oncall-session-proof.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def child(root, quick, with_agent=False):
    from ccc_server.oncall.engine import Engine
    from ccc_server.oncall.store import Store, default_config, save_config
    root = Path(root)
    rows, processes = [], []
    started = time.monotonic()
    def row(name, result, detail):
        rows.append((name, result, detail))
        print(' | '.join(rows[-1]) + f' [t+{time.monotonic()-started:.2f}s]', flush=True)
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
        helper.request(base + '/api/oncall/config', {'ack': True})
        status_reply = {'channel': 'local', 'id': 'status-once', 'sender': 'owner@example.test', 'text': 'status'}
        assert helper.request(base + '/api/oncall/inbound', status_reply)['new'] is True
        kill(dashboard)
        dashboard = launch()
        helper.request(base + '/api/oncall/tick', {})
        assert helper.request(base + '/api/oncall/inbound', status_reply)['new'] is False
        assert len([m for m in messages() if m['text'].startswith('On-call is on.')]) == 1
        helper.request(base + '/api/oncall/config', {'ack': False})
        row('direct acknowledgment across restart', 'PASS', 'one status reply for one provider ID')
        assert len([m for m in messages() if m['text'].startswith('An agent needs permission')
                    and 'Parked approval' in m['text']]) == 1
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
            if not quick:
                first = watcher()
                kill(first)
                second = watcher()
                assert wt.find(ref)['needs_input']
                kill(second)
                row('isolated watcher SIGKILL / restart', 'PASS', 'parked ticket remains parked; auto-drain off')
            else:
                row('isolated watcher restart', 'BOUNDARY', 'covered by the full proof')
            from watchtower import queue as wt_queue
            from ccc_server.oncall.store import Inbound
            wt_queue.close(ref, resolution={'summary': 'Fixture closed without a fix'})
            report, _ = store.report_upsert('followup-fixture', 'closed', title='Closed report', text='Fixture',
                state='closed', reporter_channel='local', reporter_address='reporter@example.test')
            # A poller commits this while the dashboard is down. Its next process
            # consumes the durable inbox even though the provider cursor advanced.
            store.report_update(report['id'], ticket_ref=ref)
            incoming = Inbound(channel='local', external_id='reporter-followup',
                sender='reporter@example.test', text='Still broken', thread=report['thread'])
            store.stage_poll([incoming], {})
            dashboard = launch()
            helper.wait_for(lambda: store._one("SELECT id FROM reports WHERE source='channel:local' "
                            "AND fingerprint='reporter-followup'"), 20, 'follow-up intake after restart')
            duplicate = helper.request(base + '/api/oncall/inbound', {
                'channel': 'local', 'id': 'reporter-followup', 'sender': 'reporter@example.test',
                'text': 'Still broken', 'thread': report['thread']})
            assert duplicate['new'] is False
            assert store._one("SELECT COUNT(*) AS n FROM reports WHERE source='channel:local' "
                              "AND fingerprint='reporter-followup'")['n'] == 1
            assert wt.find(ref)['status'] == 'closed'
            kill(dashboard)
            row('reporter after closure / restart / duplicate', 'PASS',
                'one held follow-up from durable inbox; original ticket remains closed')
        else:
            row('watcher restart', 'BOUNDARY', 'WatchTower not installed')
        if with_agent:
            agent_spec = importlib.util.spec_from_file_location('chaos_agent', REPO / 'scripts/oncall-chaos-agent.py')
            agent = importlib.util.module_from_spec(agent_spec)
            agent_spec.loader.exec_module(agent)
            agent.run(root, row)
        else:
            row('worker death', 'BOUNDARY', 'use --with-agent for paid automatic-worker faults')
        fault_spec = importlib.util.spec_from_file_location('transport_faults', REPO / 'tests/test_oncall_chaos_transport.py')
        faults = importlib.util.module_from_spec(fault_spec)
        fault_spec.loader.exec_module(faults)
        faults.test_ten_minute_refusal_then_recovery(root / 'refusal')
        row('ten-minute channel refusal', 'PASS', 'real loopback HTTP; simulated retry clock; recovers after six failures')
        faults.test_accept_then_disconnect_does_not_retry(root / 'disconnect')
        row('accept then disconnect', 'PASS', 'real HTTP adapter; one acceptance; uncertain attempt not repeated')
        faults.test_half_open_connection_is_held(root / 'half-open')
        row('half-open response', 'PASS', 'real HTTP; shortened 100ms timeout; one acceptance; held')
        faults.test_slow_successful_response_is_not_repeated(root / 'slow-response')
        row('slow successful response', 'PASS', 'real HTTP; delayed response; one acceptance')
        if not quick:
            month_spec = importlib.util.spec_from_file_location('chaos_month', REPO / 'scripts/oncall-chaos-month.py')
            month = importlib.util.module_from_spec(month_spec)
            month_spec.loader.exec_module(month)
            row('full fast-clock month', 'PASS', json.dumps(month.run(root / 'month')))
        else:
            row('full fast-clock month', 'BOUNDARY', 'omit --quick to run 3,600 reports, alerts and deliveries')
        row('elapsed', 'PASS', f'{time.monotonic()-started:.2f}s')
    finally:
        store.close()
        for proc in processes:
            if proc.poll() is None:
                kill(proc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--with-agent', action='store_true', help='Allow paid Codex worker fault stages')
    parser.add_argument('--child', metavar='ROOT')
    args = parser.parse_args()
    if args.child:
        child(args.child, args.quick, args.with_agent)
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
        for name in ('python3', 'node', 'git', 'bash', 'sh', 'env', 'ps', 'lsof', 'cat', 'uname', 'wt', 'codex', 'rg', 'ls', 'sed'):
            executable = shutil.which(name)
            if executable and not (name == 'wt' and args.with_agent):
                (bins / name).symlink_to(executable)
        login_before = None
        if args.with_agent:
            auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
            if not auth.exists() or not shutil.which('codex'):
                raise RuntimeError('Codex and auth.json are required for --with-agent')
            (root / '.codex').mkdir(mode=0o700)
            wt_spec = importlib.util.find_spec('watchtower')
            if wt_spec is None:
                raise RuntimeError('Importable WatchTower is required for --with-agent')
            installed = Path(wt_spec.origin).resolve().parent
            source_repo = subprocess.check_output(['git', '-C', str(installed), 'rev-parse', '--show-toplevel'], text=True).strip()
            revision = subprocess.check_output(['git', '-C', source_repo, 'rev-parse', 'HEAD'], text=True).strip()
            archive = subprocess.check_output(['git', '-C', source_repo, 'archive', revision])
            snapshot = root / 'watchtower-source'
            snapshot.mkdir()
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(snapshot, filter='data')
            wrapper = bins / 'wt'
            wrapper.write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(snapshot)!r})\nfrom watchtower.cli import main\nraise SystemExit(main())\n')
            wrapper.chmod(0o700)
            print('Pinned WatchTower revision: ' + revision, flush=True)
            login_before = auth.read_bytes()
            shutil.copyfile(auth, root / '.codex/auth.json')
            (root / '.codex/auth.json').chmod(0o600)
        env = {k: os.environ[k] for k in ('LANG', 'LC_ALL') if k in os.environ}
        env.update(HOME=tmp, CODEX_HOME=str(root / '.codex'), PATH=str(bins), TMPDIR=tmp, XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp,
                   CCC_STATE_DIR=str(root / 'state'), CCC_ONCALL_STATE_DIR=str(root / 'state/oncall'),
                   CCC_EPHEMERAL='1', CCC_FF_ONCALL='1', CCC_ONCALL_EPHEMERAL='1',
                   CCC_TELEMETRY_DISABLED='1', CCC_AUTO_UPDATE='0', CCC_BG_HELPER='0',
                   CCC_CONV_READER='0', CCC_BOOT_WARM_DELAY_S='86400', WT_NO_AUTOSTART='1',
                   WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_SELF_UPDATE='1',
                   WATCHTOWER_DELEGATE_URL='off', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        if args.with_agent:
            env['PYTHONPATH'] = str(root / 'watchtower-source')
        proc = None
        try:
            proc = subprocess.Popen([sys.executable, __file__, '--child', tmp,
                                     *(['--quick'] if args.quick else []),
                                     *(['--with-agent'] if args.with_agent else [])], env=env, start_new_session=True)
            code = proc.wait(timeout=1800 if args.with_agent else (55 if args.quick else 600))
            if code and (root / 'dashboard.log').exists():
                print((root / 'dashboard.log').read_text()[-6000:])
            return code
        finally:
            helper.cleanup(root, [proc] if proc else [])
            copied = root / '.codex/auth.json'
            if login_before is not None and copied.exists() and copied.read_bytes() != login_before:
                print('Copied Codex login renewed; discarded. A later sign-in may be needed.')


if __name__ == '__main__':
    sys.exit(main())
