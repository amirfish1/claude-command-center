"""Run Antigravity's interactive /compact command with checkpoint evidence.

Print and stream-json prompts currently send /compact to the model as text.
Only the interactive command invokes native compaction. Keep its terminal
invisible and retain the usual busy-session queue rather than interrupt work.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
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
_HIDDEN_PIDS = set()
_AUTO_ATTEMPTED = {}  # session -> latest usage sample already compacted or attempted


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


def latest_checkpoint(transcript):
    """Return the latest completed checkpoint, independent of its token usage."""
    found = None
    try:
        with open(transcript, encoding='utf-8') as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if (row.get('type') == 'CHECKPOINT' and row.get('status') == 'DONE'
                        and '# Resuming from a compaction' in row.get('content', '')):
                    found = {'index': int(row['step_index']),
                             'key': hashlib.sha256(line.strip().encode()).hexdigest()}
    except (OSError, TypeError, ValueError, KeyError):
        pass
    return found


def _context_snapshot_path(transcript):
    digest = hashlib.sha256(str(transcript).encode()).hexdigest()
    return Path(_core.COMMAND_CENTER_STATE_DIR) / 'antigravity-context' / (digest + '.json')


def save_context_estimate(transcript, tokens):
    checkpoint = latest_checkpoint(transcript)
    if not checkpoint or not tokens:
        return
    target = _context_snapshot_path(transcript)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as fh:
            temp_name = fh.name
            json.dump({**checkpoint, 'tokens': int(tokens)}, fh)
        os.replace(temp_name, target)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def context_estimate(transcript, checkpoint):
    try:
        saved = json.loads(_context_snapshot_path(transcript).read_text())
        if saved.get('key') == checkpoint['key']:
            return max(0, int(saved.get('tokens') or 0))
    except (OSError, ValueError, TypeError):
        pass
    return 0


def terminal_context_estimate(text):
    matches = re.findall(r'context [\d.,]+[kKmM]?\s*→\s*~([\d.]+)([kKmM]?)', text)
    if not matches:
        return None
    value, unit = matches[-1]
    return round(float(value) * {'': 1, 'k': 1000, 'm': 1000000}[unit.lower()])


def run_terminal(cmd, cwd, env, transcript, timeout=180, *, context_only=False):
    """Compact natively, or recover the latest context card without a model turn."""
    import pty
    import select
    import fcntl
    import termios
    import struct
    import tty

    before = checkpoint_count(transcript)
    master, slave = pty.openpty()
    proc = None
    completed = False
    try:
        cols = 120
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, cols, 0, 0))
        tty.setraw(slave)  # preserve CR as Enter rather than canonical LF
        # A new session needs its PTY as the controlling terminal. Without
        # this, /dev/tty users cannot reliably configure interactive input.
        bootstrap = ('import fcntl,os,sys,termios; '
                     'fcntl.ioctl(0,termios.TIOCSCTTY,0); '
                     'os.execvpe(sys.argv[1],sys.argv[1:],os.environ)')
        with _LOCK:
            proc = subprocess.Popen([sys.executable, '-c', bootstrap, *cmd],
                                    stdin=slave, stdout=slave, stderr=slave,
                                    cwd=cwd, env=env, start_new_session=True)
            _HIDDEN_PIDS.add(proc.pid)
        os.close(slave)
        slave = None
        tail = b''
        drafted_at = 0.0
        draft_ready = False
        sent = False
        prompt_ready = False
        dismissed_intros = 0
        redraw_pending = False
        last_output = time.monotonic()
        started = time.monotonic()
        checkpoint_seen = None
        clean = b''
        old_cards = set()
        while time.monotonic() - started < timeout:
            if sent and checkpoint_count(transcript) > before:
                completed = True
                checkpoint_seen = checkpoint_seen or time.monotonic()
            if checkpoint_seen or context_only:
                checkpoint = latest_checkpoint(transcript)
                text = clean.decode('utf-8', 'replace')
                # The step count on the card is relative, unlike the raw
                # checkpoint index. Reject startup history and accept the new
                # post-command card; read-only recovery uses the latest card.
                cards = re.findall(r'\d+ steps summarized[^\r\n]*', text)
                card = cards[-1] if cards else ''
                tokens = terminal_context_estimate(card)
                if checkpoint and tokens and (context_only or card not in old_cards):
                    completed = True
                    try:
                        save_context_estimate(transcript, tokens)
                    except OSError:
                        pass  # A missing optional estimate must not undo compaction.
                    return {'ok': True, 'status': 'completed', 'context_tokens': tokens,
                            'via': 'antigravity-hidden-pty'}
                if checkpoint_seen and time.monotonic() - checkpoint_seen >= 3:
                    return {'ok': True, 'status': 'completed', 'via': 'antigravity-hidden-pty'}
            if not context_only and not sent and time.monotonic() - started > 15:
                return {'ok': False, 'via': 'antigravity-hidden-pty',
                        'error': 'Antigravity did not become ready to submit /compact.'}
            if proc.poll() is not None:
                break
            ready, _, _ = select.select([master], [], [], .2)
            if not ready:
                if redraw_pending and time.monotonic() - last_output >= .6:
                    # Incremental terminal updates can omit unchanged text.
                    # A resize produces a fresh frame for the next check.
                    cols = 100 if cols == 120 else 120
                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 40, cols, 0, 0))
                    redraw_pending = False
                    last_output = time.monotonic()
                    continue
                if not context_only and not sent and draft_ready and time.monotonic() - last_output >= .6:
                    os.write(master, b'\r')
                    sent = True
                    old_cards = set(re.findall(r'\d+ steps summarized[^\r\n]*', clean.decode('utf-8', 'replace')))
                    tail = clean = b''
                elif (not context_only and not drafted_at and prompt_ready and time.monotonic() - started >= 4
                      and time.monotonic() - last_output >= .6):
                    # The trailing space closes autocomplete. Wait until
                    # the editor shows exactly /compact before pressing Enter.
                    os.write(master, b'\x15\x1b[200~/compact \x1b[201~')
                    drafted_at = time.monotonic()
                    redraw_pending = True
                    tail = b''
                    last_output = time.monotonic()
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            last_output = time.monotonic()
            if redraw_pending:
                continue
            tail = (tail + chunk)[-16384:]
            clean = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', tail)
            clean = re.sub(rb'\x1b\][^\x07]*(?:\x07)', b'', clean)
            # The input prompt is followed by the model/status footer.
            # Do not mistake echoed history such as "> /compact" for input.
            clean = re.sub(rb'[\x00-\x08\x0e-\x1f\x7f]', b'', clean)
            recent = clean[-5000:]
            if (not sent and b'Do you trust the contents of this project?' in recent
                    and b'Yes, I trust this folder' in recent and b'No, exit' in recent):
                return {'ok': False, 'via': 'antigravity-hidden-pty',
                        'code': 'compact_workspace_trust_required',
                        'error': 'Antigravity needs you to confirm workspace trust in its terminal before compacting.'}
            if (not sent and dismissed_intros < 6 and b'Introducing Gemini' in recent
                    and b'esc to dismiss' in recent.lower()):
                # The CLI can cover its input with a model announcement on
                # startup. Dismiss only that overlay in this private PTY.
                os.write(master, b'\x1b')
                dismissed_intros += 1
                redraw_pending = True
                tail = b''
                prompt_ready = False
                draft_ready = False
                continue
            prompts = re.findall(rb'(?:^|[\r\n])> ([^\r\n]*)(?=[\r\n])', recent)
            draft_ready = bool(drafted_at and prompts and prompts[-1].strip() == b'/compact')
            prompt_ready = bool(
                re.search(rb'(?:^|[\r\n])>[ \t]*(?:[\r\n]|$)', recent)
                or re.search(rb'(?:^|[\r\n])> [^\r\n]*shift\+tab to cycle\)', recent, re.I)
                or b'Model:' in recent[-1000:]
                or b'? for shortcuts' in recent[-1000:]
            )
        if checkpoint_seen:
            return {'ok': True, 'status': 'completed', 'via': 'antigravity-hidden-pty'}
        return {'ok': False, 'via': 'antigravity-hidden-pty',
                'error': 'Antigravity did not write a new completed compaction checkpoint.'}
    finally:
        # This process belongs to this operation alone. Never leave an
        # invisible interactive writer attached to the user's conversation.
        try:
            if completed and proc is not None and proc.poll() is None:
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
            if proc is not None and proc.poll() is None:
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
            if proc is not None:
                with _LOCK:
                    _HIDDEN_PIDS.discard(proc.pid)
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


def is_hidden_pid(pid):
    with _LOCK:
        return pid in _HIDDEN_PIDS


def queue_if_waiting(sid, text, mode):
    """Keep sends in order while the compactor or its durable queue owns delivery."""
    if is_active(sid):
        reason = 'Waiting for native Antigravity compaction to finish.'
    elif mode != 'steer' and not getattr(text, 'pending_id', None):
        _core._refresh_queued_view_if_file_changed(sid)
        with _core._pending_resume_lock:
            if not _core._pending_resume_queue.get(sid):
                return None
        reason = 'Waiting behind an earlier queued message for this session.'
    else:
        return None
    if mode == 'steer':
        return {'ok': True, 'queued_preserved': True, 'via': 'antigravity-resume-queued',
                'error': reason}
    return _defer_input(sid, text, reason)


def _defer_input(sid, text, reason):
    # The queue watcher has already popped a durable entry. Return it to the
    # front with its original identity, ahead of the sends that followed it.
    action = 'insert_front' if getattr(text, 'pending_id', None) else 'append_tail'
    result = _core._apply_pending_input_operations(sid, [
        {'field': 'resume', 'action': action, 'value': text}])
    if not result.get('ok'):
        return {'ok': False, 'error': 'Could not persist input waiting for Antigravity compaction.'}
    return {'ok': True, 'queued': True,
            'via': 'antigravity-resume-queued',
            'queued_reason': reason}


def maybe_before_send(sid, text):
    """Defer a CLI send while CCC compacts a measured over-budget context."""
    path = _core._antigravity_transcript_path(sid)
    sample = _core._extract_antigravity_tail_meta(path).get('compact_usage_sample') if path else None
    if not sample:
        return None
    threshold = _core._spawn_auto_compact_k_tokens()
    if sample[1] < threshold:
        return None
    return compact_session(sid, automatic=(tuple(sample), text))


def compact_session(sid, *, automatic=None):
    from ccc_server import engines

    if sys.platform == 'win32':
        if automatic:
            return None
        return {'ok': False, 'error': 'Native Antigravity compaction requires a POSIX terminal.'}
    with _LOCK:
        already_active = sid in _ACTIVE
        if not already_active:
            if automatic and _AUTO_ATTEMPTED.get(sid) == automatic[0]:
                return None
            _ACTIVE.add(sid)
    if already_active:
        if automatic:
            return _defer_input(sid, automatic[1], 'Waiting for Antigravity compaction to finish.')
        return {'ok': True, 'via': 'antigravity-hidden-pty', 'in_progress': True}
    handed_off = False
    try:
        cli = _core._antigravity_cli_conversation_path(sid)
        if not (cli and cli.is_file()) and _core._antigravity_app_conversation_path(sid):
            if automatic:
                return None
            return {'ok': False, 'code': 'compact_needs_manual',
                    'error': 'Run /compact in the Antigravity app that owns this conversation.'}
        state = stream.state(sid)
        if state and state['busy']:
            return None if automatic else _queue(sid, state['pid'])
        if engines._antigravity_terminal_holder_pid(sid) is not None:
            if automatic:
                return None
            return {'ok': False, 'code': 'terminal_owned',
                    'error': 'Run /compact in the terminal that owns this conversation.'}
        if state:
            stream.retire(sid)
            deadline = time.monotonic() + 5
            while _core._is_pid_alive(state['pid']) and time.monotonic() < deadline:
                time.sleep(.1)
            if _core._is_pid_alive(state['pid']):
                return None if automatic else _queue(sid, state['pid'])
        pid = engines._antigravity_live_resume_pid(sid)
        if pid is not None:
            if not (engines._antigravity_turn_finished(sid, pid=pid)
                    and engines._antigravity_reap_finished_process(sid, pid)):
                return None if automatic else _queue(sid, pid)
        resolved = _core._resolve_antigravity_bin()
        if not resolved.get('available'):
            if automatic:
                return None
            return {'ok': False, 'error': resolved.get('reason', 'Antigravity CLI unavailable')}
        transcript = _core._antigravity_transcript_path(sid)
        if not transcript:
            if automatic:
                return None
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

        worker = threading.Thread(target=work, daemon=True, name=f'agy-compact-{sid[:8]}')
        if automatic:
            sample, text = automatic
            queued = _defer_input(sid, text, 'Antigravity reached the auto-compact threshold.')
            if not queued['ok']:
                return queued
            queued['auto_compacting'] = True
            # A failed compact must let this send through. Retry only after
            # another model response produces a fresh usage sample.
            with _LOCK:
                _AUTO_ATTEMPTED.pop(sid, None)
                _AUTO_ATTEMPTED[sid] = sample
                if len(_AUTO_ATTEMPTED) > 1024:
                    _AUTO_ATTEMPTED.pop(next(iter(_AUTO_ATTEMPTED)))
            try:
                worker.start()
            except RuntimeError as exc:
                _core._log_activity('inject', 'AGY_COMPACT_FAILED', f'session={sid} {exc}')
                return queued
            handed_off = True
            return queued
        worker.start()
        handed_off = True
        return {'ok': True, 'via': 'antigravity-hidden-pty', 'in_progress': True}
    finally:
        if not handed_off:
            with _LOCK:
                _ACTIVE.discard(sid)
