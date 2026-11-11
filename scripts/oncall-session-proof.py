#!/usr/bin/env python3
"""Opt-in real dashboard/Codex/local ntfy proof (Linux, Docker, paid Codex login).

No On-call internals are imported or called. The production 120-second idle
clock is unchanged. All waits are bounded inside this one protocol watcher.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

REPO = Path(__file__).resolve().parents[1]
MODEL = 'gpt-6.1-terra'
GRACE = 120


def run(*argv, **kwargs):
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT,
                                   timeout=120, **kwargs).strip()


def request(url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        'Content-Type': 'application/json', 'Origin': url.split('/api/')[0]})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode()}") from exc


def wait_for(check, timeout, label):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError(label)
        threading.Event().wait(.5)


def ready(url):
    try:
        return request(url)
    except OSError:
        return False


def cleanup(home, processes):
    # Detached app-server children can leave their parent's process group.
    # Match the exact private HOME, never names or the real install's registry.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            env = (entry / 'environ').read_bytes().split(b'\0')
            if ('HOME=' + str(home)).encode() in env:
                os.kill(int(entry.name), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    for proc in processes:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=10)


def proof(root, rows, capture=False):
    processes = []
    recorder = None
    container_name = root.name + '-ntfy'
    bins = root / 'bin'
    bins.mkdir()
    for name in ('python3', 'node', 'codex', 'git', 'bash', 'sh', 'env', 'ps', 'lsof', 'cat', 'uname'):
        executable = shutil.which(name)
        if executable:
            (bins / name).symlink_to(executable)
    auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
    if not shutil.which('codex') or not auth.is_file():
        raise RuntimeError('Codex executable and existing auth.json are required')
    (root / '.codex').mkdir(mode=0o700)
    shutil.copyfile(auth, root / '.codex/auth.json')
    (root / '.codex/auth.json').chmod(0o600)
    original_auth = auth.read_bytes()
    (root / 'tmp').mkdir()
    project = root / 'project'
    project.mkdir()
    env = {k: os.environ[k] for k in ('LANG', 'LC_ALL') if k in os.environ}
    env.update(HOME=str(root), TMPDIR=str(root / 'tmp'), PATH=str(bins), CODEX_HOME=str(root / '.codex'),
               XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'),
               CCC_STATE_DIR=str(root / 'state'), CCC_ONCALL_STATE_DIR=str(root / 'oncall'), WT_NO_AUTOSTART='1', CCC_EPHEMERAL='1',
               CCC_FF_ONCALL='1', CCC_ONCALL_EPHEMERAL='1', CCC_TELEMETRY_DISABLED='1', CCC_AUTO_UPDATE='0',
               CCC_BG_HELPER='0', CCC_CONV_READER='0', CCC_BOOT_WARM_DELAY_S='86400',
               WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_SELF_UPDATE='1',
               WATCHTOWER_DELEGATE_URL='off', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull)
    run('git', 'init', str(project), env=env)
    (project / 'AGENTS.md').write_text('Use no tools, network, or delegation. Answer only the prompt.\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env['CCC_SERVER'] = base
    try:
        cid = run('docker', 'run', '--name', container_name, '-d', '-p', '127.0.0.1::80',
                  'binwiederhier/ntfy:v2.17.0', 'serve', '--cache-file', '/tmp/cache.db')
        bound = json.loads(run('docker', 'inspect', cid))[0]['NetworkSettings']['Ports']
        web = 'http://127.0.0.1:' + bound['80/tcp'][0]['HostPort']
        wait_for(lambda: ready(web + '/v1/health'), 60, 'ntfy startup')
        for script in ('ccc_worker.py', 'server.py'):
            log = (root / (script + '.log')).open('w')
            try:
                proc = subprocess.Popen([sys.executable, str(REPO / script), *(
                    ['--port', str(port)] if script == 'server.py' else [])],
                    cwd=project, env=env, stdout=log, stderr=log, start_new_session=True)
                processes.append(proc)
            finally:
                log.close()
        wait_for(lambda: ready(base + '/api/oncall/status'), 90, 'dashboard startup')
        topic = 'session-proof'
        request(base + '/api/oncall/channel', {'channel': {
            'type': 'ntfy', 'server': web, 'topic': topic}})
        request(base + '/api/oncall/config', {'queue': 'PROOF', 'repo': str(project), 'enabled': True})
        if capture:
            browser = sorted((Path.home() / '.cache/puppeteer/chrome').glob('*/chrome-linux64/chrome'))
            if not browser:
                raise RuntimeError('Puppeteer Chrome not found in the host cache')
            capture_env = dict(env, PUPPETEER_EXECUTABLE_PATH=str(browser[-1]))
            recorder = subprocess.Popen([shutil.which('node'), str(REPO / 'scripts/oncall-capture.cjs'), web, topic],
                                        env=capture_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, start_new_session=True)
            def capture_step(step, **values):
                recorder.stdin.write(json.dumps(dict(step=step, **values)) + '\n')
                recorder.stdin.flush()
                result = recorder.stdout.readline()
                if not result:
                    raise RuntimeError('Capture process stopped: ' + recorder.stderr.read())
                result = json.loads(result)
                if not result.get('ok'):
                    raise RuntimeError('Capture failed: ' + result.get('error', 'unknown error'))
            capture_step('open')
        print('Dashboard and ntfy ready; model=' + MODEL + '; effort=medium; grace=120s (unchanged)', flush=True)
        started = time.monotonic()
        spawn = request(base + '/api/sessions/spawn', {
            'engine': 'codex', 'model': MODEL, 'reasoning_effort': 'medium',
            'cwd': str(project), 'prompt': 'Ask me to choose blue or green before proceeding. Use no tools. '
                      'Ask: Should I use the blue option? If a session-state footer is required, '
                      'set NEXT_STEP_USER to that question. After my answer, acknowledge it '
                      'without another question and set NEXT_STEP_USER to None.'
        })
        if not spawn.get('ok'):
            raise RuntimeError('Spawn failed: ' + str(spawn))
        sid = spawn.get('session_id')
        if not sid:
            raise RuntimeError('Spawn returned no session_id: ' + str(spawn))

        event_cache, event_checked = [], 0.0

        def events():
            nonlocal event_cache, event_checked
            if time.monotonic() - event_checked < 3:
                return event_cache
            with urllib.request.urlopen(web + '/' + topic + '/json?poll=1&since=all', timeout=10) as res:
                event_cache = [json.loads(line) for line in res if line.strip()]
            event_checked = time.monotonic()
            return event_cache

        def session():
            return next((s for s in request(base + '/api/sessions/census')['sessions']
                         if s['session_id'] == sid), {})

        wait_for(lambda: session().get('state') == 'idle', 150, 'session did not become idle')
        idle = time.monotonic()
        print(f'First observed idle: {idle - started:.2f}s after spawn', flush=True)
        transcripts = list((root / '.codex/sessions').rglob(f'*{sid}*.jsonl'))
        assert len(transcripts) == 1, 'Expected one transcript for the spawned session'
        transcript = transcripts[0]
        turns = [json.loads(line) for line in transcript.read_text().splitlines()]
        final = next(row for row in reversed(turns) if row.get('type') == 'response_item'
                     and row.get('payload', {}).get('role') == 'assistant')
        assert 'Should I use the blue option?' in json.dumps(final)
        assert 'NEXT_STEP_USER: None' not in json.dumps(final), 'Agent footer says no answer needed'
        completed_at = datetime.fromisoformat(final['timestamp'].replace('Z', '+00:00')).timestamp()
        seen = []
        def alert_arrived():
            nonlocal seen
            seen = events()
            if seen and seen[0]['time'] - completed_at < GRACE - 1:
                raise AssertionError('Alert arrived before grace from final message (1s ntfy timestamp precision)')
            return seen
        try:
            wait_for(alert_arrived, GRACE + 70, 'plain_question alert missing')
        except Exception:
            status = request(base + '/api/oncall/status')
            print('On-call status: ' + json.dumps({k: status[k] for k in ('enabled', 'channels', 'waiting', 'log')}), flush=True)
            print('Session status: ' + json.dumps(session()), flush=True)
            raise
        elapsed = time.monotonic() - idle
        assert len(seen) == 1, 'Duplicate alerts before reply'
        waiting = request(base + '/api/oncall/status')['waiting']
        alert = next(a for a in waiting if a['kind'] == 'plain_question')
        rows.append(('plain_question grace', 'PASS', f'{elapsed:.2f}s after observed idle; {seen[0]["time"] - completed_at:.2f}s after final message'))
        # Observe three additional watcher cycles before answering.
        until = time.monotonic() + 20
        def no_repeat():
            assert len(events()) == 1, 'Alert repeated'
            return time.monotonic() >= until
        wait_for(no_repeat, 25, 'deduplication observation')
        rows.append(('plain_question deduplication', 'PASS', '20s, one ntfy message'))
        if capture:
            capture_step('session', base=base, sid=sid)
        token = alert.get('token') or str(alert['id'])
        answer = 'proof-answer-blue-accepted'
        reply_at = time.monotonic()
        if capture:
            capture_step('reply', text=f'{token} {answer}')
        else:
            with urllib.request.urlopen(urllib.request.Request(web + '/' + topic + '-reply',
                    data=f'{token} {answer}'.encode()), timeout=10) as response:
                json.load(response)
        def user_turn():
            for transcript in transcripts:
                for line in transcript.read_text().splitlines():
                    row = json.loads(line)
                    payload = row.get('payload', {})
                    if row.get('type') == 'response_item' and payload.get('role') == 'user':
                        if answer in json.dumps(payload):
                            return True
            return False
        wait_for(user_turn, 90, 'ntfy reply did not reach a user turn')
        wait_for(lambda: not any(a['id'] == alert['id'] for a in
                 request(base + '/api/oncall/status')['waiting']), 30, 'alert did not close')
        rows.append(('reply user turn / close', 'PASS', f'{time.monotonic() - reply_at:.2f}s'))
        if capture:
            capture_step('session_reply', text=answer)
            capture_step('finish')
    finally:
        if recorder is not None:
            try:
                os.killpg(recorder.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            recorder.wait(timeout=10)
        try:
            cleanup(root, processes)
        finally:
            removed = subprocess.run(['docker', 'rm', '-f', container_name],
                                     capture_output=True, text=True, timeout=30)
            if removed.returncode and 'No such container' not in removed.stderr:
                raise RuntimeError('Could not remove proof container: ' + removed.stderr)
        if (root / '.codex/auth.json').read_bytes() != original_auth:
            print('Copied login renewed; discarded. A later Codex login may be needed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-agent', action='store_true', help='Allow one paid Codex session')
    parser.add_argument('--capture', action='store_true', help='Capture the real local ntfy web reply')
    args = parser.parse_args()
    if not args.with_agent:
        parser.error('--with-agent is required; this proof uses your Codex model connection')
    if not Path('/proc/self/environ').exists():
        parser.error('Linux /proc is required for detached-child cleanup')
    rows = []
    code = 0
    def interrupted(signum, frame):
        raise RuntimeError(f'Interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.alarm(600)
    try:
        with tempfile.TemporaryDirectory(prefix='ccc-session-proof-') as tmp:
            proof(Path(tmp), rows, capture=args.capture)
    except Exception as exc:
        rows.append(('session proof', 'FAIL', str(exc)))
        code = 1
    signal.alarm(0)
    reason = 'Normal Codex spawn forces approvalPolicy=never and dangerFullAccess; no deterministic prompt'
    rows.extend((f'permission {decision}', 'BOUNDARY', reason) for decision in ('allow', 'deny'))
    print('\nCheck | Result | Measurement / reason')
    for row in rows:
        print(' | '.join(row))
    return code


if __name__ == '__main__':
    sys.exit(main())
