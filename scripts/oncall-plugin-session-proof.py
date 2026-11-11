#!/usr/bin/env python3
"""Real local server/ntfy hook proof. Optional ONE paid isolated Codex session.

No Claude session, Claude credential, real CCC config or real queue is used.
All observation has deadlines. Cleanup identifies the exact temporary HOME.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / 'plugins/oncall/scripts/hook.py'
spec = importlib.util.spec_from_file_location('session_proof', REPO / 'scripts/oncall-session-proof.py')
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)


def request(base, name, body=None):
    return common.request(base + '/api/oncall/' + name, body)


def proof(root, with_agent):
    processes = []
    container = root.name + '-ntfy'
    auth = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'auth.json'
    bins = root / 'bin'
    bins.mkdir()
    for name in ('python3', 'node', 'codex', 'git', 'bash', 'sh', 'env', 'ps', 'lsof', 'cat', 'uname'):
        executable = shutil.which(name)
        if executable:
            (bins / name).symlink_to(executable)
    (bins / 'ccc').symlink_to(REPO / 'ccc')
    project = root / 'project'
    project.mkdir()
    (project / 'AGENTS.md').write_text('Use no tools or network. Answer only the prompt.\n')
    env = {'HOME': str(root), 'PATH': str(bins), 'CODEX_HOME': str(root / 'codex'),
           'CLAUDE_CONFIG_DIR': str(root / 'claude'), 'CCC_STATE_DIR': str(root / 'state'),
           'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data'),
           'CCC_EPHEMERAL': '1', 'CCC_ONCALL_EPHEMERAL': '1', 'CCC_FF_ONCALL': '1',
           'CCC_TELEMETRY_DISABLED': '1', 'CCC_AUTO_UPDATE': '0', 'CCC_BG_HELPER': '0',
           'CCC_CONV_READER': '0', 'CCC_BOOT_WARM_DELAY_S': '86400',
           'WATCHTOWER_STORE': str(root / 'queues.json'), 'WT_NO_AUTOSTART': '1',
           'WT_NO_SELF_UPDATE': '1', 'WATCHTOWER_DELEGATE_URL': 'off',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'CCC_ONCALL_WAIT': '1', 'CCC_ONCALL_WAIT_SECONDS': '90'}
    def run(*args, **kw):
        result = subprocess.run(args, env=env, cwd=project, capture_output=True, text=True,
                                timeout=90, **kw)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout.strip()
    def payload(event, sid):
        return {'session_id': sid, 'hook_event_name': event, 'cwd': str(project),
                'last_assistant_message': 'Should I use blue?', 'stop_hook_active': False,
                'tool_name': 'Bash', 'tool_input': {'command': 'echo example'},
                'notification_type': 'permission_prompt'}
    def start(event, sid, **overrides):
        extra = overrides.pop('env', {})
        data = dict(payload(event, sid), **overrides)
        proc = subprocess.Popen([sys.executable, str(HOOK), event], env=dict(env, **extra),
                                cwd=project, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        proc.stdin.write(json.dumps(data))
        proc.stdin.close()
        proc.stdin = None
        processes.append(proc)
        return proc
    def finish(proc, timeout=15):
        output, error = proc.communicate(timeout=timeout)
        assert proc.returncode == 0 and not error, (proc.returncode, error)
        return json.loads(output) if output.strip() else {}
    started = time.monotonic()
    copied_auth = None
    try:
        cid = common.run('docker', 'run', '--name', container, '-d', '-p', '127.0.0.1::80',
                         'binwiederhier/ntfy:v2.17.0', 'serve', '--cache-file', '/tmp/cache.db')
        ports = json.loads(common.run('docker', 'inspect', cid))[0]['NetworkSettings']['Ports']
        web = 'http://127.0.0.1:' + ports['80/tcp'][0]['HostPort']
        common.wait_for(lambda: common.ready(web + '/v1/health'), 30, 'ntfy startup')
        assert finish(start('SessionStart', 'proof-start'), 2) == {}
        base = run(sys.executable, str(REPO / 'ccc'), 'oncall', 'agent-service')
        env['CCC_SERVER'] = base
        topic = 'proof-' + secrets.token_hex(8)
        request(base, 'channel', {'channel': {'type': 'ntfy', 'server': web, 'topic': topic}})
        request(base, 'config', {'queue': 'PROOF', 'repo': str(project), 'enabled': True,
                                 'daily_summary': {'enabled': False}})
        bad_origin = urllib.request.Request(base + '/api/oncall/hook-register', data=b'{}',
            headers={'Content-Type': 'application/json', 'Origin': 'https://example.test'})
        try:
            urllib.request.urlopen(bad_origin, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError('Foreign Origin reached hook API')
        print('Local service and ntfy ready; foreign Origin refused.', flush=True)
        def alerts():
            return request(base, 'status')['waiting']
        def pending(sid):
            return next((a for a in alerts() if a.get('session_id') == sid
                         and a.get('phone_wait')), None)
        def events():
            with urllib.request.urlopen(web + '/' + topic + '/json?poll=1&since=all', timeout=5) as res:
                return [json.loads(line) for line in res if line.strip()]
        def sent(alert):
            return next((e for e in events() if '(#%d)' % alert['id'] in e.get('message', '')), None)
        def publish(text):
            with urllib.request.urlopen(urllib.request.Request(web + '/' + topic + '-reply',
                                        data=text.encode()), timeout=5) as res:
                return json.load(res)
        # Two real hook processes, two distinct numbered messages. A number
        # that doesn't exist must not become either session's answer.
        a, b = start('Stop', 'proof-blue'), start('Stop', 'proof-green')
        aa = common.wait_for(lambda: pending('proof-blue'), 10, 'blue registration')
        bb = common.wait_for(lambda: pending('proof-green'), 10, 'green registration')
        common.wait_for(lambda: sent(aa) and sent(bb), 30, 'two ntfy notices')
        publish('999999 wrong-session-answer')
        publish(f"{bb['id']} green-phone-answer")
        out_b = finish(b, 30)
        assert 'green-phone-answer' in out_b['reason'] and out_b['decision'] == 'block'
        assert a.poll() is None, 'A reply for B released A'
        publish(f"{aa['id']} blue-phone-answer")
        out_a = finish(a, 30)
        assert 'blue-phone-answer' in out_a['reason'] and 'green' not in out_a['reason']
        print('PASS two Stop hooks, wrong number, distinct ntfy replies and continuation JSON.', flush=True)
        # Capability claim cannot be used with a different session identity.
        claim = request(base, 'hook-register', {'session_id': 'proof-claim', 'event': 'Stop',
                                               'text': 'Should I use blue?', 'seconds': 30})
        try:
            request(base, 'hook-wait', dict(claim, session_id='proof-other'))
        except RuntimeError as exc:
            assert 'HTTP 400' in str(exc)
        else:
            raise AssertionError('Wrong-session claim was accepted')
        request(base, 'hook-cancel', {'session_id': 'proof-claim'})
        for decision in ('allow', 'deny'):
            sid = 'proof-permission-' + decision
            proc = start('PermissionRequest', sid)
            alert = common.wait_for(lambda: pending(sid), 10, 'permission registration')
            common.wait_for(lambda: sent(alert), 30, 'permission ntfy notice')
            publish(f"{alert['id']} {decision}")
            output = finish(proc, 30)
            assert output['hookSpecificOutput']['decision']['behavior'] == decision
        print('PASS exact permission allow and deny through ntfy.', flush=True)
        for event in ('Stop', 'PermissionRequest'):
            tick = time.monotonic()
            assert finish(start(event, 'proof-timeout-' + event,
                                env={'CCC_ONCALL_WAIT_SECONDS': '1'}), 4) == {}
            assert time.monotonic() - tick < 4
            assert finish(start(event, 'proof-down-' + event,
                                env={'CCC_SERVER': 'http://127.0.0.1:1'}), 2) == {}
            assert finish(start(event, 'proof-fast-' + event,
                                env={'CCC_ONCALL_WAIT': '0'}), 2) == {}
        for cancel_event in ('UserPromptSubmit', 'SessionEnd'):
            sid = 'proof-cancel-' + cancel_event
            proc = start('Stop', sid)
            common.wait_for(lambda: pending(sid), 10, 'cancel registration')
            assert finish(start(cancel_event, sid), 2) == {}
            assert finish(proc, 2) == {}
        proc = start('Stop', 'proof-signal')
        common.wait_for(lambda: pending('proof-signal'), 10, 'signal registration')
        proc.terminate()
        assert finish(proc, 2) == {}
        assert finish(start('Stop', 'proof-loop', stop_hook_active=True), 2) == {}
        for event in ('Notification', 'PostToolUse'):
            assert finish(start(event, 'proof-fast-PermissionRequest'), 2) == {}
        assert not (root / 'state/live-state/proof-fast-PermissionRequest_needs_approval.json').exists()
        assert (root / 'state/live-state/proof-fast-Stop.json').exists()
        assert not (root / '.claude/command-center/live-state/proof-fast-Stop.json').exists()
        print('PASS timeout, service down, opt-out, cancellation, loop guard, markers and CCC_STATE_DIR.', flush=True)
        if with_agent:
            if not auth.is_file() or not shutil.which('codex'):
                raise RuntimeError('Paid proof needs Codex and an existing auth.json')
            home = root / 'codex'
            home.mkdir(mode=0o700, exist_ok=True)
            shutil.copyfile(auth, home / 'auth.json')
            (home / 'auth.json').chmod(0o600)
            copied_auth = (home / 'auth.json').read_bytes()
            run(sys.executable, str(REPO / 'ccc'), 'oncall', 'install-codex')
            # Explicit trust bypass is confined to this vetted proof invocation.
            # The installer and normal user commands never bypass trust review.
            log = (root / 'codex-output.jsonl').open('w')
            try:
                proc = subprocess.Popen([str(bins / 'codex'), '--no-daemon',
                    '--dangerously-bypass-hook-trust', 'exec', '--json', '--skip-git-repo-check',
                    '-m', 'gpt-6.1-terra', '-c', 'model_reasoning_effort="medium"',
                    '-c', 'sandbox_mode="read-only"',
                    'Use no tools. Ask exactly: Should I use blue? Do not continue until I answer. '
                    'When a later message gives my answer, repeat the full answer verbatim and stop.'],
                    env=env, cwd=project, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=log, start_new_session=True)
                processes.append(proc)
            finally:
                log.close()
            def real_pending():
                if proc.poll() is not None:
                    raise RuntimeError('Codex exited before its hook waited: ' +
                                       (root / 'codex-output.jsonl').read_text()[-8000:])
                return next((a for a in alerts() if a.get('phone_wait')
                             and not a.get('session_id', '').startswith('proof-')), None)
            alert = common.wait_for(real_pending, 150, 'real Codex Stop hook')
            common.wait_for(lambda: sent(alert), 30, 'real Codex ntfy delivery')
            answer = 'blue-' + secrets.token_hex(10)
            publish(f"{alert['id']} {answer}")
            proc.wait(timeout=150)
            log_text = (root / 'codex-output.jsonl').read_text()
            assert proc.returncode == 0, log_text[-8000:]
            rows = [json.loads(line) for line in log_text.splitlines() if line.startswith('{')]
            assistant = [row.get('item', {}).get('text', '') for row in rows
                         if row.get('type') == 'item.completed'
                         and row.get('item', {}).get('type') == 'agent_message']
            assert any(answer in text for text in assistant), log_text[-8000:]
            print('PASS real Codex gpt-6.1-terra medium: phone-only random answer returned in assistant output.', flush=True)
        print('PASS total %.1fs; no Claude model or credential used.' % (time.monotonic() - started), flush=True)
    finally:
        if copied_auth is not None and (root / 'codex/auth.json').read_bytes() != copied_auth:
            print('Copied Codex login renewed and will be discarded; a later login may be needed.', flush=True)
        common.cleanup(root, processes)
        removed = subprocess.run(['docker', 'rm', '-f', container], text=True, capture_output=True, timeout=30)
        if removed.returncode and 'No such container' not in removed.stderr:
            raise RuntimeError('Container cleanup failed: ' + removed.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-agent', action='store_true')
    args = parser.parse_args()
    if not Path('/proc/self/environ').exists():
        parser.error('Linux /proc is required for cleanup')
    def interrupted(*_):
        raise RuntimeError('Proof interrupted or exceeded its 600-second budget')
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(signum, interrupted)
    signal.alarm(600)
    try:
        with tempfile.TemporaryDirectory(prefix='ccc-plugin-session-') as tmp:
            proof(Path(tmp), args.with_agent)
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
