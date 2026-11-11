#!/usr/bin/env python3
"""Real local ntfy and TLS email escalation proof. Requires Docker and OpenSSL."""
import argparse
import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('protocol_proof', REPO / 'scripts/oncall-proof.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def child(root, web, smtp, imap):
    os.environ['CCC_STATE_DIR'] = str(Path(root) / 'state')
    os.environ['WT_NO_AUTOSTART'] = '1'
    sys.path.insert(0, str(REPO))
    from ccc_server.oncall import engine, store, wt
    from ccc_server.oncall.store import Inbound
    from ccc_server import vault
    now = [1800000000.0]
    eng = engine.Engine(Path(root) / 'oncall', clock=lambda: now[0], flag_on=lambda: True,
                        wt=wt.WT(dispatch=False))
    # No queue or sessions: tick uses the real engine but cannot dispatch work.
    cfg = eng.config()
    cfg.update(enabled=True, hourly_cap=100, alerts={'decisions': False}, channels=[
        {'id': 'first', 'type': 'ntfy', 'server': web, 'topic': 'first'},
        {'id': 'second', 'type': 'ntfy', 'server': web, 'topic': 'second'},
        {'id': 'third', 'type': 'email', 'address': 'oncall@example.test', 'owner': 'owner@example.test',
         'username': 'oncall', 'smtp_host': 'localhost', 'smtp_port': smtp, 'smtp_security': 'tls',
         'imap_host': 'localhost', 'imap_port': imap, 'password_vault': 'proof-mail'}],
        people=[{'id': 'backup', 'channels': ['second']}, {'id': 'mail', 'channels': ['third']}],
        escalation=[{'person': p, 'channels': [c], 'after': n*60}
                    for n, (p,c) in enumerate((('owner','first'),('backup','second'),('mail','third')))])
    # Use the same isolated vault path as the protocol proof CLI.
    from ccc_server.oncall import api
    api._store_secrets(cfg['channels'][2], {'password_vault': 'test-password'}, 'proof')
    eng.save_config(cfg)

    def events(topic):
        with urllib.request.urlopen(web+'/'+topic+'/json?poll=1&since=all', timeout=10) as response:
            return [json.loads(line) for line in response if line.strip()]

    def raise_alert(key):
        return eng.store.alert_upsert(key, kind='permission', title=key, body='Allow?', session_id='fixture', now=now[0])[0]

    def tick():
        eng.tick(force=True)

    def has(topic, token):
        return any(token in json.dumps(m) for m in events(topic))

    def mailbox():
        import imaplib, ssl
        conn = imaplib.IMAP4_SSL('localhost', imap, ssl_context=ssl.create_default_context())
        try:
            conn.login('owner', 'test-password')
            conn.select('INBOX', readonly=True)
            return conn.search(None, 'ALL')[1][0].split()
        finally:
            conn.logout()

    # A TLS greeting can precede GreenMail's fixture-user initialization.
    deadline = time.monotonic() + 30
    while True:
        try:
            mailbox()
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            base.threading.Event().wait(.2)

    class Deps:
        calls = []
        def approve(self, *args):
            self.calls.append(args)
            return True, ''
    eng.deps = Deps()
    try:
        tick()  # establish provider cursors before publishing replies
        a = raise_alert('No answer')
        tick()
        assert has('first', a['token']) and not has('second', a['token'])
        now[0] += 59
        tick()
        assert not has('second', a['token'])
        eng.store.close()
        eng._store = None  # reopen SQLite between steps
        eng._chan_cache = (None, {})
        now[0] += 1
        subprocess.run([sys.executable, __file__, '--resume', root, str(now[0])], check=True, timeout=30)
        assert has('second', a['token'])
        before = len(mailbox())
        now[0] += 60
        tick()
        assert len(mailbox()) == before + 1
        print('PASS no answer: step two at 60s, step three at 120s; fresh process delivered step two from persisted state', flush=True)
        eng.store.alert_close(a['id'], 'cleared', now=now[0])
        a = raise_alert('Backup answers')
        tick()
        now[0] += 60
        tick()
        before = len(mailbox())
        with urllib.request.urlopen(urllib.request.Request(web+'/second-reply', data=f"{a['token']} allow".encode()), timeout=10) as response:
            response.read()
        tick()
        assert eng.store.alert(a['id'])['state'] == 'answered', eng.store.recent_log(10)
        now[0] += 60
        tick()
        assert len(mailbox()) == before and len(eng.deps.calls) == 1
        assert any('handled by backup' in m.get('message', '') for m in events('first'))
        print('PASS backup reply through ntfy stops step three and notifies first person', flush=True)
        cfg['quiet_hours'] = {'start': '00:00', 'end': '23:59'}
        cfg['people'][0]['quiet_hours'] = {'start': '00:00', 'end': '00:00'}
        eng.save_config(cfg)
        a = raise_alert('Quiet first owner')
        tick()
        assert not has('first', a['token'])
        now[0] += 60
        tick()
        assert has('second', a['token'])
        print('PASS first owner quiet; backup gets the alert at 60s', flush=True)
    finally:
        eng.store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Fast clock (also used by the full run)')
    parser.parse_args()
    # Reuse the original proof's container, TLS, isolated HOME, allow-listed
    # environment, timeout and cleanup implementation. Its child is this file.
    original_run = base.run
    def run(*args, **kwargs):
        # Parallel proof containers can exceed GreenMail's two-second default.
        # A failed startup leaves listeners alive without initializing users.
        args = [arg + ' -Dgreenmail.startup.timeout=30000'
                if isinstance(arg, str) and arg.startswith('GREENMAIL_OPTS=') else arg for arg in args]
        return original_run(*args, **kwargs)
    base.run = run
    base.__file__ = __file__
    sys.argv = [__file__]
    def interrupted(signum, frame):
        raise RuntimeError(f'Interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    return base.main()


if __name__ == '__main__':
    if sys.argv[1:2] == ['--resume']:
        sys.path.insert(0, str(REPO))
        from ccc_server.oncall.engine import Engine
        from ccc_server.oncall.wt import WT
        e = Engine(Path(sys.argv[2]) / 'oncall', clock=lambda: float(sys.argv[3]), flag_on=lambda: True, wt=WT(dispatch=False))
        try:
            e.tick(force=True)
        finally:
            e.store.close()
    elif sys.argv[1:2] == ['--child']:
        child(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
    else:
        sys.exit(main())
