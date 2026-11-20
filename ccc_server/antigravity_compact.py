"""Run Antigravity's interactive /compact command with checkpoint evidence.

Print and stream-json prompts currently send /compact to the model as text.
Only the interactive command invokes native compaction. Keep its terminal
invisible and retain the usual busy-session queue rather than interrupt work.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from ccc_server import core as _core
from ccc_server import antigravity_accounts as accounts
from ccc_server import antigravity_stream as stream

_LOCK = threading.Lock()
_ACTIVE = set()


def checkpoint_count(transcript):
    try:
        count = 0
        with open(transcript, encoding='utf-8') as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if (row.get('type') == 'CHECKPOINT' and row.get('status') == 'DONE'
                        and '# Resuming from a compaction' in row.get('content', '')):
                    count += 1
        return count
    except (OSError, TypeError):
        return 0


def run_terminal(cmd, cwd, env, transcript, timeout=180):
    """Run the interactive command; a model's text can never mean success."""
    import pty
    import select
    import fcntl
    import termios
    import struct
    import tty

    before = checkpoint_count(transcript)
    master, slave = pty.openpty()
    proc = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
        tty.setraw(slave)  # preserve CR as Enter rather than canonical LF
        # A new session needs its PTY as the controlling terminal. Without
        # this, /dev/tty users cannot reliably configure interactive input.
        bootstrap = ('import fcntl,os,sys,termios; '
                     'fcntl.ioctl(0,termios.TIOCSCTTY,0); '
                     'os.execvpe(sys.argv[1],sys.argv[1:],os.environ)')
        proc = subprocess.Popen([sys.executable, '-c', bootstrap, *cmd],
                                stdin=slave, stdout=slave, stderr=slave,
                                cwd=cwd, env=env, start_new_session=True)
        os.close(slave)
        slave = None
        tail = b''
        sent = False
        prompt_ready = False
        last_output = time.monotonic()
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if sent and checkpoint_count(transcript) > before:
                return {'ok': True, 'status': 'completed', 'via': 'antigravity-hidden-pty'}
            if proc.poll() is not None:
                break
            ready, _, _ = select.select([master], [], [], .2)
            if not ready:
                if not sent and prompt_ready and time.monotonic() - last_output >= .6:
                    # Wait for conversation/draft restoration to settle and
                    # replace any restored draft before submitting. Bracketed
                    # paste keeps similarly named skills out of autocomplete.
                    os.write(master, b'\x15\x1b[200~/compact\x1b[201~\r')
                    sent = True
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            last_output = time.monotonic()
            tail = (tail + chunk)[-16384:]
            clean = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', tail)
            clean = re.sub(rb'\x1b\][^\x07]*(?:\x07)', b'', clean)
            # The input prompt is followed by the model/status footer.
            # Do not mistake echoed history such as "> /compact" for input.
            clean = re.sub(rb'[\x00-\x08\x0e-\x1f\x7f]', b'', clean)
            prompt_ready = bool(re.search(rb'(?:^|[\r\n])>[ \t]*(?:[\r\n]|$)', clean)
                                or b'Model:' in clean[-1000:])
        return {'ok': False, 'via': 'antigravity-hidden-pty',
                'error': 'Antigravity did not write a new completed compaction checkpoint.'}
    finally:
        # This process belongs to this operation alone. Never leave an
        # invisible interactive writer attached to the user's conversation.
        try:
            if proc is not None and proc.poll() is None:
                # Let the TUI restore its terminal normally, and keep draining
                # output so shutdown cannot block on a full PTY buffer.
                try:
                    os.write(master, b'\x15/exit\r')
                except OSError:
                    pass
                deadline = time.monotonic() + 3
                while proc.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            os.read(master, 65536)
                        except OSError:
                            break
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            # An OS-delayed reap must not erase an already
                            # verified checkpoint or leak our terminal FDs.
                            threading.Thread(target=proc.wait, daemon=True).start()
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)


def _queue(sid, pid=None):
    result = _core._apply_pending_input_operations(sid, [
        {'field': 'resume', 'action': 'append_tail', 'value': '/compact'}])
    if not result.get('ok'):
        return {'ok': False, 'error': 'Could not persist queued Antigravity compaction.'}
    return {'ok': True, 'queued': True, 'pid': pid,
            'via': 'antigravity-resume-queued',
            'queued_reason': 'Compaction runs when the current Antigravity turn finishes.'}


def is_active(sid):
    with _LOCK:
        return sid in _ACTIVE


def queue_if_active(sid, text):
    """Keep normal sends from opening a competing writer during compaction."""
    with _LOCK:
        active = sid in _ACTIVE
    if not active:
        return None
    result = _core._apply_pending_input_operations(sid, [
        {'field': 'resume', 'action': 'append_tail', 'value': text}])
    return {'ok': bool(result.get('ok')), 'queued': bool(result.get('ok')),
            'via': 'antigravity-resume-queued',
            'queued_reason': 'Waiting for native Antigravity compaction to finish.'}


def compact_session(sid):
    from ccc_server import engines

    if sys.platform == 'win32':
        return {'ok': False, 'error': 'Native Antigravity compaction requires a POSIX terminal.'}
    with _LOCK:
        if sid in _ACTIVE:
            return {'ok': True, 'via': 'antigravity-hidden-pty', 'in_progress': True}
        _ACTIVE.add(sid)
    handed_off = False
    try:
        cli = _core._antigravity_cli_conversation_path(sid)
        if not (cli and cli.is_file()) and _core._antigravity_app_conversation_path(sid):
            return {'ok': False, 'code': 'compact_needs_manual',
                    'error': 'Run /compact in the Antigravity app that owns this conversation.'}
        state = stream.state(sid)
        if state and state['busy']:
            return _queue(sid, state['pid'])
        if engines._antigravity_terminal_holder_pid(sid) is not None:
            return {'ok': False, 'code': 'terminal_owned',
                    'error': 'Run /compact in the terminal that owns this conversation.'}
        if state:
            stream.retire(sid)
            deadline = time.monotonic() + 5
            while _core._is_pid_alive(state['pid']) and time.monotonic() < deadline:
                time.sleep(.1)
            if _core._is_pid_alive(state['pid']):
                return _queue(sid, state['pid'])
        pid = engines._antigravity_live_resume_pid(sid)
        if pid is not None:
            if not (engines._antigravity_turn_finished(sid, pid=pid)
                    and engines._antigravity_reap_finished_process(sid, pid)):
                return _queue(sid, pid)
        resolved = _core._resolve_antigravity_bin()
        if not resolved.get('available'):
            return {'ok': False, 'error': resolved.get('reason', 'Antigravity CLI unavailable')}
        transcript = _core._antigravity_transcript_path(sid)
        if not transcript:
            return {'ok': False, 'error': 'No Antigravity transcript is available to verify compaction.'}
        account = accounts.session_account(sid)
        override = _core._get_session_override(sid) or {}
        model = _core._antigravity_model_settings_label(override.get('model') or '')
        cmd = accounts.configure_command(_core._antigravity_command_words(resolved), account, model)
        cmd += ['--conversation', sid]
        env = accounts.launch_env(account)
        env['TERM'] = 'xterm-256color'
        cwd = _core.find_session_cwd(sid) or str(Path.cwd())

        def work():
            try:
                result = run_terminal(cmd, cwd, env, transcript)
                _core._log_activity('inject', 'AGY_COMPACT_DONE' if result['ok'] else 'AGY_COMPACT_FAILED',
                                    f'session={sid} {result.get("error", "checkpoint verified")}')
            except Exception as exc:
                _core._log_activity('inject', 'AGY_COMPACT_FAILED', f'session={sid} {exc}')
            finally:
                with _LOCK:
                    _ACTIVE.discard(sid)

        threading.Thread(target=work, daemon=True, name=f'agy-compact-{sid[:8]}').start()
        handed_off = True
        return {'ok': True, 'via': 'antigravity-hidden-pty', 'in_progress': True}
    finally:
        if not handed_off:
            with _LOCK:
                _ACTIVE.discard(sid)
