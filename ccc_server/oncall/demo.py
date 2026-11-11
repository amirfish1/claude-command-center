"""A local On-call walkthrough, with production routing and demo tickets."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import webbrowser

from .demo_phone import Phone
from .demo_output import Output
from .demo_ticket import DemoTickets
from .demo_wt import RealTickets, installed
from .engine import Engine
from .store import default_config

SCRIPTED = 'This run used a scripted worker. With Codex or Claude Code installed, a real agent does this step.'
BROKEN = 'def total(items):\n    return sum(items) / len(items)\n'


def isolated_env(root):
    """No real state paths, service URLs, shell hooks or credential variables."""
    env = {k: os.environ[k] for k in ('LANG', 'LC_ALL', 'SYSTEMROOT') if k in os.environ}
    bins = root / 'bin'
    bins.mkdir()
    for name in ('git', 'python3', 'sh', 'bash', 'env', 'node', 'codex', 'cat', 'ls', 'rg', 'sed', 'bwrap'):
        executable = shutil.which(name)
        if executable:
            (bins / name).symlink_to(executable)
    env.update(HOME=str(root), PATH=str(bins), TMPDIR=str(root),
               CODEX_HOME=str(root / '.codex'), XDG_CONFIG_HOME=str(root / 'config'),
               XDG_DATA_HOME=str(root / 'data'), CCC_STATE_DIR=str(root / 'state'),
               CCC_ONCALL_STATE_DIR=str(root / 'state/oncall'), CCC_EPHEMERAL='1',
               WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_AUTOSTART='1',
               WT_NO_SELF_UPDATE='1', WATCHTOWER_DELEGATE_URL='off',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    return env


def agent_fix(project, env, auth, answer):
    """Explicit paid opt-in; only Codex file credentials are supported."""
    home = Path(env['CODEX_HOME'])
    home.mkdir(mode=0o700)
    prompt = ('Fix cart.py so test_cart.py passes. Owner chose ' + answer + '. '
              'Only edit cart.py. Do not use network, delegates, background processes or git. '
              'Do not read outside this workspace. Run python3 test_cart.py.')
    proc = None
    started = time.monotonic()
    try:
        shutil.copyfile(auth, home / 'auth.json')
        (home / 'auth.json').chmod(0o600)
        with (project / 'agent.log').open('w') as log:
            proc = subprocess.Popen(['codex', 'exec', '--skip-git-repo-check',
                                     '--sandbox', 'workspace-write', '-m', 'gpt-6.1-terra',
                                     '-c', 'model_reasoning_effort="medium"', prompt],
                                    cwd=project, env=env, stdout=log, stderr=log,
                                    start_new_session=True)
            if proc.wait(timeout=180):
                raise RuntimeError('Codex failed. See agent.log with --keep.')
    finally:
        if proc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)
        shutil.rmtree(home)
    return round(time.monotonic() - started, 3)


def walkthrough(root, args, emit, lines):
    env = isolated_env(root)
    project = root / 'project'
    project.mkdir()
    (project / 'cart.py').write_text(BROKEN)
    (project / 'test_cart.py').write_text('from cart import total\nassert total([]) == 0\nassert total([2, 4]) == 3\n')
    phone = Phone(emit)
    tickets = RealTickets(root, env) if installed() else DemoTickets(root)
    engine = Engine(root / 'oncall', wt=tickets, flag_on=lambda: True,
                    build_channel=lambda entry, **kw: phone)
    cfg = default_config()
    cfg.update(enabled=True, queue='DEMO', repo=str(project), channels=[dict(
        id='demo', type='command', owner=phone.owner, intake=True)])
    cfg['customers']['known'] = [{'address': 'reporter@example.test'}]
    # The walkthrough drives its own worker, not an unattended queue.
    cfg['alerts']['stuck'] = False
    engine.save_config(cfg)

    def run(*argv, check=True):
        return subprocess.run(argv, cwd=project, env=env, capture_output=True,
                              text=True, timeout=15, check=check)

    def head_commit():
        # This fresh repository has only loose refs, created by git above.
        ref = (project / '.git/HEAD').read_text().strip().removeprefix('ref: ')
        return (project / '.git' / ref).read_text().strip()

    def reply(default):
        if args.json:
            text = default
            emit('Demo auto-reply: ' + text)
        elif args.terminal:
            try:
                text = input('Reply: ').strip()
            except EOFError:
                raise RuntimeError('Input ended. Use --json for the automatic test walkthrough.')
        else:
            try:
                text = phone.replies.get(timeout=180)
            except queue.Empty:
                raise RuntimeError('No browser reply within three minutes. Try --terminal.')
        emit('You: ' + text)
        return text

    try:
        if not args.terminal and not args.json:
            url = phone.start(lines)
            emit('Local phone: ' + url)
            try:
                opened = webbrowser.open(url)
            except webbrowser.Error:
                opened = False
            if not opened:
                emit('No browser available. Continuing in the terminal.')
                args.terminal = True
        emit('Tickets: real WatchTower in a temporary store.' if tickets.backend == 'watchtower'
             else 'Tickets: built-in fixture because WatchTower is not installed.')
        if not args.agent and shutil.which('codex'):
            emit('To try a real Codex worker with an existing login, rerun with --agent.')
        run('git', 'init', '-q')
        run('git', 'add', 'cart.py', 'test_cart.py')
        run('git', '-c', 'user.name=Demo', '-c', 'user.email=demo@example.test',
            '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'test: reproduce empty cart crash')
        phone.state = 'Reported'
        emit('Customer: Report arrives: My empty cart crashes instead of showing a total.')
        phone.receive('My empty cart crashes instead of showing a total.', 'reporter@example.test')
        engine.tick(force=True)
        reports = engine.store.reports(state='filed')
        if len(reports) != 1:
            raise RuntimeError('Report was not filed.')
        ref = reports[0]['ticket_ref']
        emit('On-call: Ticket filed: ' + ref + '.')
        phone.state = 'Agent working'
        tickets.start()
        emit('Agent: Worker starts. Running the real sample test.')
        before = run(sys.executable, '-S', 'test_cart.py', check=False)
        if before.returncode == 0 or 'ZeroDivisionError' not in before.stderr:
            raise RuntimeError('Expected the empty-cart regression to fail.')
        emit('Agent: Test fails: ZeroDivisionError for an empty cart.')
        phone.state = 'Waiting for you'
        tickets.block()
        engine.tick(force=True)
        while True:
            answer = reply('zero').lower()
            if answer in ('zero', 'hide'):
                break
            emit('Choose zero or hide so the worker knows what to build.')
        phone.receive(answer)
        engine.tick(force=True)
        if not tickets.answered(answer):
            raise RuntimeError('Reply did not reach the worker.')
        phone.state = 'Agent working'
        expected = '0' if answer == 'zero' else 'None'
        (project / 'test_cart.py').write_text(
            f'from cart import total\nassert total([]) == {expected}\nassert total([2, 4]) == 3\n')
        if args.agent:
            auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
            if not shutil.which('codex') or not auth.is_file():
                raise RuntimeError('--agent needs Codex with an existing auth.json login. Retry without --agent.')
            emit('Codex is fixing the sample with your existing login. This can take longer than a minute.')
            test_source = (project / 'test_cart.py').read_bytes()
            agent_seconds = agent_fix(project, env, auth, answer)
            emit(f'Codex finished in {agent_seconds:.1f} seconds.')
            if (project / 'test_cart.py').read_bytes() != test_source:
                raise RuntimeError('Agent changed the test instead of preserving it.')
        else:
            (project / 'cart.py').write_text(
                f'def total(items):\n    return sum(items) / len(items) if items else {expected}\n')
            emit(SCRIPTED)
        run(sys.executable, '-S', 'test_cart.py')
        # A real local commit, never pushed. No repository hooks or global config.
        run('git', '-c', 'user.name=Demo', '-c', 'user.email=demo@example.test',
            '-c', 'core.hooksPath=/dev/null', 'commit', '--only', 'cart.py', 'test_cart.py', '-qm', 'fix: handle empty cart')
        commit = head_commit()
        tickets.close(commit)
        phone.state = 'Fixed'
        emit('Agent: Fix committed locally. The test passes.')
        engine.tick(force=True)
        pending = engine.store.customer_messages(state='pending')
        if len(pending) != 1 or any(m['to'] for m in phone.messages):
            raise RuntimeError('Reporter notice did not wait for approval.')
        emit('On-call: Reporter notice is waiting. Reply yes to send locally, or no to drop it.')
        while engine.store.customer_messages(state='pending'):
            approval = reply('yes')
            if approval.lower() not in ('yes', 'no'):
                emit('Reply yes or no.')
                continue
            phone.receive(approval)
            engine.tick(force=True)
        phone.state = ('Reporter told' if engine.store.customer_messages()[0]['state'] == 'sent'
                       else 'Fixed · reporter notice skipped')
        # The demonstration worker owns this low-risk local decision.
        (project / '.gitignore').write_text('__pycache__/\n')
        run('git', 'add', '.gitignore')
        run('git', '-c', 'user.name=Demo', '-c', 'user.email=demo@example.test',
            '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'chore: ignore Python cache files')
        minor_commit = head_commit()
        from .api import save_config
        save_config(engine, {'minor_decisions': {'enabled': True}})
        decision = tickets.minor(minor_commit)
        if tickets.backend == 'watchtower':
            from .api import decisions
            decision = decisions(engine)[-1]
        engine.store.log('minor', json.dumps(decision))
        emit('Agent: Decided without you: ' + decision['recommend'] + '\nUndo: ' + decision['undo'])
        emit('Try the real setup (requires a running CCC and WatchTower):\nccc oncall setup\nccc oncall minor on')
        phone.done = True
        phone.choices = []
        return dict(ok=True, worker='codex' if args.agent else 'scripted', ticket=ref,
                    backend=tickets.backend, commit=commit, test_before=before.returncode, test_after=0,
                    reporter_state=engine.store.customer_messages()[0]['state'],
                    decision=decision, messages=phone.messages,
                    commands=['ccc oncall setup', 'ccc oncall minor on'])
    finally:
        tickets.shutdown()
        phone.close()
        engine.store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ccc oncall demo', description=__doc__)
    parser.add_argument('--terminal', action='store_true')
    parser.add_argument('--fast', action='store_true', help='skip presentation pauses')
    parser.add_argument('--keep', action='store_true')
    parser.add_argument('--agent', action='store_true', help='use logged-in Codex (paid, up to three minutes)')
    parser.add_argument('--record', nargs='?', const='oncall-demo.txt', metavar='FILE')
    parser.add_argument('--json', action='store_true', help='automatic scripted replies and JSON result')
    args = parser.parse_args(argv)
    if args.json and args.agent:
        parser.error('--json runs the scripted worker; use --agent interactively')
    args.terminal |= not sys.stdout.isatty() or os.environ.get('TERM') in (None, '', 'dumb') or 'NO_COLOR' in os.environ
    root = Path(tempfile.mkdtemp(prefix='ccc-oncall-demo-'))
    start = time.monotonic()
    colour = sys.stdout.isatty() and os.environ.get('TERM') not in (None, '', 'dumb') and 'NO_COLOR' not in os.environ
    emit = Output(quiet=args.json, fast=args.fast or args.json or not sys.stdout.isatty(), colour=colour)
    lines, events = emit.lines, emit.events

    result = {}
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = walkthrough(root, args, emit, lines)
    except (Exception, KeyboardInterrupt) as exc:
        result = dict(ok=False, error=str(exc) or 'Interrupted')
        emit('Demo stopped: ' + result['error'])
    finally:
        try:
            if args.record:
                record = Path(args.record).expanduser().resolve()
                record.write_text('\n\n'.join(lines) + '\n')
                result['transcript'] = str(record)
                if shutil.which('asciinema'):
                    cast = record.with_suffix('.cast')
                    if cast == record:
                        cast = Path(str(record) + '.cast')
                    cast.write_text(json.dumps(dict(version=2, width=100, height=30)) + '\n' +
                                    '\n'.join(json.dumps(event) for event in events) + '\n')
                    result['cast'] = str(cast)
        except OSError as exc:
            result.update(ok=False, error='Could not save recording: ' + str(exc))
        result.update(elapsed_seconds=round(time.monotonic() - start, 3), kept=args.keep)
        if args.keep:
            result['workspace'] = str(root)
            if not args.json:
                print('Kept workspace: ' + str(root))
        else:
            shutil.rmtree(root)
        signal.signal(signal.SIGTERM, previous)
        if args.json:
            print(json.dumps(result))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
