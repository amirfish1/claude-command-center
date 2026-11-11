#!/usr/bin/env python3
"""Isolated team chat protocol proof. No real account or installed service is used."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def run(*args, **kw):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=180, **kw).strip()


def request(base, path, body=None, token='', method=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
        headers=dict({'Content-Type': 'application/json'}, **({'Authorization': 'Bearer ' + token} if token else {})),
        method=method or ('GET' if body is None else 'POST'))
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as res:
            return json.load(res), dict(res.headers)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'HTTP {exc.code}: {exc.read().decode()[:500]}') from None


def ready(base, path):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            request(base, path)
            return
        except (OSError, RuntimeError):
            threading.Event().wait(.5)
    raise TimeoutError('Local chat server did not start')


def matrix(root, container):
    data = root / 'synapse'
    data.mkdir()
    run('docker', 'run', '--rm', '-v', f'{data}:/data', '-e', 'SYNAPSE_SERVER_NAME=localhost',
        '-e', 'SYNAPSE_REPORT_STATS=no', '-e', f'UID={os.getuid()}', '-e', f'GID={os.getgid()}', 'matrixdotorg/synapse:v1.127.1', 'generate')
    cfg = data / 'homeserver.yaml'
    cfg.write_text(cfg.read_text() + '\nenable_registration: true\nenable_registration_without_verification: true\n')
    base = container('matrixdotorg/synapse:v1.127.1', 8008, ['-v', f'{data}:/data', '-e', f'UID={os.getuid()}', '-e', f'GID={os.getgid()}'])
    ready(base, '/_matrix/client/versions')
    users = []
    for name in ('bot', 'owner', 'stranger'):
        user, _ = request(base, '/_matrix/client/v3/register', {'username': name,
            'password': 'local-proof-password', 'auth': {'type': 'm.login.dummy'}})
        users.append(user)
    bot, owner, stranger = users
    room, _ = request(base, '/_matrix/client/v3/createRoom', {'preset': 'private_chat',
        'invite': [owner['user_id'], stranger['user_id']]}, bot['access_token'])
    room_id = room['room_id']
    from urllib.parse import quote
    rp = '/_matrix/client/v3/rooms/' + quote(room_id, safe='')
    for user in (owner, stranger): request(base, rp + '/join', {}, user['access_token'])
    def reply(root_id, wrong=False):
        user = stranger if wrong else owner
        request(base, rp + '/send/m.room.message/' + uuid.uuid4().hex,
            {'msgtype': 'm.text', 'body': 'Keep it.', 'm.relates_to': {
                'rel_type': 'm.thread', 'event_id': root_id,
                'm.in_reply_to': {'event_id': root_id}}}, user['access_token'], 'PUT')
    def read(ident):
        return request(base, rp + '/event/' + quote(ident, safe=''), token=owner['access_token'])[0]['content']['body']
    return dict(owner=owner['user_id'], channel_id=room_id, api_base=base), bot['access_token'], reply, read


def mattermost(root, container):
    network = root.name
    db = container('pgvector/pgvector:pg16', None, ['--network-alias', 'db',
        '-e', 'POSTGRES_USER=proof', '-e', 'POSTGRES_PASSWORD=local-proof-password', '-e', 'POSTGRES_DB=proof'])
    run('docker', 'exec', db, 'sh', '-c', 'for i in $(seq 1 60); do pg_isready -h 127.0.0.1 -U proof -d proof && exit 0; sleep 1; done; exit 1')
    base = container('mattermost/mattermost-team-edition:10.5.0', 8065, [
        '-e', 'GOMAXPROCS=2', '-e', 'GOMEMLIMIT=256MiB',
        '-e', 'MM_SQLSETTINGS_DRIVERNAME=postgres',
        '-e', 'MM_SQLSETTINGS_DATASOURCE=postgres://proof:local-proof-password@db:5432/proof?sslmode=disable&connect_timeout=10',
        '-e', 'MM_SERVICESETTINGS_SITEURL=http://localhost:8065',
        '-e', 'MM_SERVICESETTINGS_ENABLELOCALMODE=true',
        '-e', 'MM_SERVICESETTINGS_ENABLEUSERACCESSTOKENS=true',
        '-e', 'MM_SERVICESETTINGS_ENABLEBOTACCOUNTCREATION=true',
        '-e', 'MM_EMAILSETTINGS_REQUIREEMAILVERIFICATION=false',
        '-e', 'MM_TEAMSETTINGS_ENABLEOPENSERVER=true',
        '-e', 'MM_LOGSETTINGS_CONSOLELEVEL=ERROR'])
    ready(base, '/api/v4/system/ping')
    users = []
    for name in ('proofbot', 'proofowner', 'proofstranger'):
        user, _ = request(base, '/api/v4/users', {'username': name, 'email': name+'@example.test',
                                                 'password': 'Local-proof-password1!'})
        _, headers = request(base, '/api/v4/users/login', {'login_id': name, 'password': 'Local-proof-password1!'})
        user['token'] = next(v for k,v in headers.items() if k.lower() == 'token')
        users.append(user)
    bot, owner, stranger = users
    team, _ = request(base, '/api/v4/teams', {'name':'proof', 'display_name':'Proof', 'type':'O'}, bot['token'])
    for user in (owner, stranger):
        request(base, '/api/v4/teams/'+team['id']+'/members', {'team_id':team['id'], 'user_id':user['id']}, bot['token'])
    channel, _ = request(base, '/api/v4/channels', {'team_id':team['id'], 'name':'proof',
                          'display_name':'Proof', 'type':'P'}, bot['token'])
    for user in (owner, stranger):
        request(base, '/api/v4/channels/'+channel['id']+'/members', {'user_id':user['id']}, bot['token'])
    account, _ = request(base, '/api/v4/bots', {'username': 'oncallbot', 'display_name': 'On-call'}, bot['token'])
    bot_id = account['user_id']
    request(base, '/api/v4/teams/'+team['id']+'/members', {'team_id':team['id'], 'user_id':bot_id}, bot['token'])
    request(base, '/api/v4/channels/'+channel['id']+'/members', {'user_id':bot_id}, bot['token'])
    access, _ = request(base, '/api/v4/users/'+bot_id+'/tokens', {'description': 'Local proof'}, bot['token'])
    bot = {'id': bot_id, 'token': access['token']}
    def reply(root_id, wrong=False):
        request(base, '/api/v4/posts', {'channel_id':channel['id'], 'message':'Keep it.', 'root_id':root_id},
                (stranger if wrong else owner)['token'])
    def read(ident):
        return request(base, '/api/v4/posts/'+ident, token=owner['token'])[0]['message']
    return dict(owner=owner['id'], channel_id=channel['id'], api_base=base), bot['token'], reply, read


def child(root, kind, config, resume_ref=""):
    from ccc_server.oncall import engine, channels, wt, store
    spec = importlib.util.spec_from_file_location('protocol', REPO / 'scripts/oncall-proof.py')
    protocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protocol)
    cfg, token = config['cfg'], config['token']
    def build(entry, **kw): return channels.build(entry, secret=lambda _: token, **kw)
    eng = engine.Engine(root / 'oncall', wt=wt.WT(dispatch=False), build_channel=build, flag_on=lambda: True)
    watcher = protocol.start_watcher(root, eng.wt.argv())
    try:
        if not resume_ref:
            eng.wt._ok('config', '-q', 'CHAT', '--auto-drain', 'off')
            settings = store.default_config()
            settings.update(enabled=True, queue='CHAT', channels=[cfg], ack=False)
            eng.save_config(settings)
            assert eng.channels()[kind].check()[0]
            assert eng.tick(force=True)['active']  # Isolated engine, not the dashboard preview gate.
            ref = eng.wt.add('CHAT', title='Protocol proof', text='Fixture only.')
            report, _ = eng.store.report_upsert('proof', ref, title='Protocol proof', text='Fixture', queue='CHAT', state='filed')
            eng.store.report_update(report['id'], ticket_ref=ref)
            eng.wt._ok('block', ref, '--question', 'Keep the fallback?', '--wait-on', 'owner')
            eng.tick(force=True)
            alert = next(a for a in eng.store.alerts(state='open') if a['kind'] == 'decision')
            delivery = eng.store.delivery_latest(alert['id'], kind)
            assert delivery['ok'], delivery
            # Parent owns the local provider account fixtures, communicates over files.
            print(json.dumps({'alert':delivery['external_id'], 'ref':ref}), flush=True)
            assert sys.stdin.readline().strip() == 'wrong'
            eng.tick(force=True)
            assert eng.wt.find(ref)['needs_input']
            print('wrong ignored', flush=True)
            assert sys.stdin.readline().strip() == 'right'
            eng.store.close()
            protocol.stop_watcher(watcher)
            watcher = None
            # Replace the interpreter after the reply reaches the server, before intake.
            os.execv(sys.executable, [sys.executable, __file__, '--child', str(root), kind,
                                     str(root / 'fixture.json'), '--resume', ref])
        ref = resume_ref
        eng.tick(force=True)
        assert not eng.wt.find(ref)['needs_input'], eng.status()
        from watchtower import queue
        queue.close(ref, resolution={'summary':'Protocol fixture fixed.', 'commit':'abc1234'})
        eng.tick(force=True)
        fixed = next(a for a in eng.store.alerts(state='open') if a['kind'] == 'fixed' and a['ref'] == ref)
        notice = eng.store.delivery_latest(fixed['id'], kind)
        assert notice['ok'], notice
        print(json.dumps({'fixed':notice['external_id']}), flush=True)
    finally:
        protocol.stop_watcher(watcher)
        protocol.cleanup_isolated_processes(root)


def main():
    parser = argparse.ArgumentParser()
    for kind in ('slack', 'discord', 'matrix', 'mattermost'): parser.add_argument('--'+kind, action='store_true')
    parser.add_argument('--quick', action='store_true', help='local API fixture tests only; no Docker')
    args = parser.parse_args()
    kinds = [k for k in ('slack','discord','matrix','mattermost') if getattr(args,k)] or ['slack','discord','matrix','mattermost']
    start = time.monotonic()
    if args.quick:
        cmd = [sys.executable, '-m', 'pytest', 'tests/test_oncall_team_chat.py', '-q']
        if len(kinds) < 4:
            cmd += ['-k', ' or '.join(kinds)]
        return subprocess.call(cmd, cwd=REPO)
    for kind in kinds:
        if kind in ('slack','discord'):
            subprocess.run([sys.executable,'-m','pytest','tests/test_oncall_team_chat.py','-q','-k',kind], cwd=REPO, check=True)
            print(kind+': checked against the documented API with a local stand-in; not yet run against a real workspace')
            continue
        containers = []
        with tempfile.TemporaryDirectory(prefix='ccc-chat-proof-') as tmp:
            root = Path(tmp)
            network = root.name
            run('docker','network','create','--internal',network)
            proc = None
            try:
                def container(image, port, extra):
                    argv = ['docker','run','-d','--network',network]
                    cid = run(*argv,*extra,image)
                    containers.append(cid)
                    print('Started '+image, flush=True)
                    if not port: return cid
                    network_state = json.loads(run('docker','inspect',cid))[0]['NetworkSettings']['Networks'][network]
                    return 'http://' + network_state['IPAddress'] + ':' + str(port)
                cfg, token, reply, read = globals()[kind](root,container)
                cfg.update(id=kind,type=kind,token_vault='proof')
                config = root/'fixture.json'
                config.write_text(json.dumps({'cfg':cfg,'token':token})); config.chmod(0o600)
                bins=root/'bin'; bins.mkdir()
                for name in ('python3','wt'):
                    exe=shutil.which(name)
                    if exe: (bins/name).symlink_to(exe)
                env={'HOME':tmp,'PATH':str(bins),'PYTHONPATH':str(REPO),'CCC_STATE_DIR':str(root/'state'),
                     'WATCHTOWER_STORE':str(root/'queues.json'),'WT_NO_AUTOSTART':'1','WT_NO_SELF_UPDATE':'1',
                     'WATCHTOWER_DELEGATE_URL':'off','CCC_FF_ONCALL':'1','CCC_TELEMETRY_DISABLED':'1'}
                proc=subprocess.Popen([sys.executable,__file__,'--child',tmp,kind,str(config)],
                                      env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
                row=json.loads(proc.stdout.readline())
                assert 'Keep the fallback?' in read(row['alert'])
                reply(row['alert'],True)
                proc.stdin.write('wrong\n');proc.stdin.flush()
                assert proc.stdout.readline().strip()=='wrong ignored'
                reply(row['alert'])
                proc.stdin.write('right\n');proc.stdin.flush()
                fixed=json.loads(proc.stdout.readline())
                assert 'Fixed' in read(fixed['fixed'])
                assert proc.wait(timeout=30)==0
                print(kind+': PASS real server alert, wrong user, interpreter restart, ticket answer, fixture fixed send')
            finally:
                if proc and proc.poll() is None: proc.kill();proc.wait(timeout=10)
                # Remove all descendants with the exact proof HOME marker.
                for item in Path('/proc').iterdir():
                    if not item.name.isdigit() or int(item.name)==os.getpid(): continue
                    try:
                        if ('HOME='+tmp).encode() in (item/'environ').read_bytes().split(b'\0'): os.kill(int(item.name),9)
                    except OSError: pass
                for cid in reversed(containers):
                    if sys.exc_info()[0]:
                        print(run('docker', 'inspect', '--format', '{{json .State}}', cid), file=sys.stderr)
                        print(run('docker', 'logs', '--tail', '25', cid), file=sys.stderr)
                    run('docker','rm','-f','-v',cid)
                run('docker','network','rm',network)
    print(f'Elapsed: {time.monotonic()-start:.1f}s. No fixing agent or real external workspace used.')
    return 0


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':
        child(Path(sys.argv[2]), sys.argv[3], json.loads(Path(sys.argv[4]).read_text()),
              sys.argv[6] if len(sys.argv) > 6 and sys.argv[5] == '--resume' else '')
    else:
        import signal
        def expired(*_): raise TimeoutError('Proof interrupted or exceeded fifteen minutes')
        signal.signal(signal.SIGALRM,expired)
        signal.signal(signal.SIGTERM,expired)
        signal.alarm(900)
        sys.exit(main())
