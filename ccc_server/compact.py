"""Extracted from server.py (originally lines 44789-45801).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

from ccc_server import core as _core

def _backup_jsonl_before_compact(session_id):
    """Copy the session's JSONL to ~/.claude/command-center/compact-backups/
    before Claude Code rewrites it during /compact. Returns the backup path
    or None on failure. Best-effort — never blocks the inject path.

    Claude Code's /compact replaces the on-disk transcript with the compacted
    summary, deleting the original message history. Without a snapshot the
    user loses everything before the compact boundary permanently.
    """
    try:
        path = _core._find_session_jsonl(session_id)
        if not path:
            return None
        src = Path(path)
        if not src.is_file():
            return None
        backup_dir = Path.home() / ".claude" / "command-center" / "compact-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        dest = backup_dir / f"{session_id}-{stamp}.jsonl"
        shutil.copy2(str(src), str(dest))
        # Keep at most 10 backups per session — older ones rotate out.
        backups = sorted(backup_dir.glob(f"{session_id}-*.jsonl"))
        for stale in backups[:-10]:
            try:
                stale.unlink()
            except OSError:
                pass
        return str(dest)
    except Exception as e:
        print(f"  [compact-backup] failed for {session_id}: {e}", file=sys.stderr)
        return None


_COMPACT_TRIGGER_RE = re.compile(r"^\s*/compact(?:\s|$)", re.IGNORECASE)
# CCC-935: a queued "/clear" used to be delivered as plain FIFO text with no
# special-casing — Claude does execute it, but CCC never re-keyed the spawn
# entry/UI the way _clear_via_live_spawn_stdin does, so the conversation went
# dead and the fresh session appeared as an unrelated stranger.
_CLEAR_TRIGGER_RE = re.compile(r"^\s*/clear(?:\s|$)", re.IGNORECASE)
_SLASH_COMMAND_TRIGGER_RE = re.compile(
    r"^\s*/[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z0-9_-]+)*(?=\s|$)"
)


def _compact_result(payload, backup_path=None):
    payload = dict(payload or {})
    payload["compact"] = True
    if backup_path:
        payload["backup_path"] = backup_path
    return payload


def _tail_count(path, needle, n=262144):
    """Count occurrences of `needle` (bytes) in the last `n` bytes of a file.

    `/compact` rewrites the JSONL much smaller, so a new compact_boundary
    always lands inside the tail whether the transcript was rewritten or
    appended. Counting (not just presence) lets a caller detect a NEW boundary
    even when older ones already exist.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().count(needle)
    except OSError:
        return 0


_HIDDEN_PTY_COMPACT_TIMEOUT_S = 300.0


def _pty_prompt_visible(tail_text):
    """True when Claude Code's interactive input prompt is on screen — the
    `❯` prompt glyph (or a bare `>` line on terminals without it). Only then
    is the TUI in raw mode and ready for typed input."""
    txt = tail_text or ""
    return "❯" in txt[-400:] or bool(re.search(r"(^|\s)>\s*$", txt[-120:]))


def _compact_via_hidden_pty(session_id, cwd):
    """Run Claude Code's `/compact` in an INVISIBLE pty — no Terminal window.

    Claude Desktop runs the interactive CLI inside an embedded, hidden
    pseudo-terminal; this does the same. We own a pty, spawn the interactive
    `claude --resume <sid>` attached to it, wait for the TUI to settle, type
    `/compact`, watch the transcript for a fresh `compact_boundary` marker, then
    `/exit` and reap. No window pops, nothing is left running — the compaction
    is durable on disk the moment the boundary lands, so the next CCC inject or
    terminal resume cold-reads the compacted transcript with zero loss.

    Returns {ok, via:"hidden-pty", ...}. Any failure returns ok=False with an
    `error`; the caller falls back to the visible-terminal launch so compaction
    is never silently dropped.
    """
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "via": "hidden-pty", "error": "missing session_id"}
    claude_bin = _core._resolve_claude_bin()
    if not claude_bin.get("available"):
        return {
            "ok": False,
            "via": "hidden-pty",
            "error": claude_bin.get("reason") or "Claude Code CLI not found",
        }

    status = _core.session_live_status(sid, cwd) or {}
    tty = status.get("tty")
    has_tty = _core._is_real_tty(tty)
    if status.get("live") and has_tty:
        # A live interactive terminal already owns this session — don't spawn a
        # competing pty (the caller's tty branch handles that case).
        return {"ok": False, "via": "hidden-pty", "error": "session has a live terminal"}
    if status.get("live") and status.get("kind") == "bg":
        return {"ok": False, "via": "hidden-pty", "error": "session is a live background agent"}

    # Stop a CCC-owned idle headless first so the pty resume isn't a second
    # writer on the transcript (CCC-96 fork guard). Only retire OUR spawn; an
    # external headless falls through to the visible path, which surfaces the
    # stop_headless warning.
    spawn = _core._find_live_spawn_entry_for_session(sid)
    if spawn is not None:
        if (
            _core._headless_turn_in_progress(spawn)
            or _core._spawn_entry_active_tool_child(spawn)
        ):
            return {"ok": False, "via": "hidden-pty", "error": "headless is mid-turn"}
        hpid = spawn.get("pid")
        _core._retire_unresponsive_spawn_entry(spawn, terminate=True, caller="hidden-pty-compact")
        if hpid is not None:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    os.kill(int(hpid), 0)
                except (ProcessLookupError, ValueError, PermissionError):
                    break
                time.sleep(0.2)
    else:
        fresh = _core.session_live_status(sid, cwd) or {}
        fresh_tty = fresh.get("tty")
        if fresh.get("live") and not _core._is_real_tty(fresh_tty):
            # External headless we don't own — leave it for the visible
            # fallback, which warns instead of silently killing it.
            return {"ok": False, "via": "hidden-pty", "error": "external headless owns this session"}

    import pty
    import select as _select
    import fcntl as _fcntl
    import termios as _termios
    import struct as _struct

    jsonl = _core._resolve_conversation_path(sid)
    n0 = _tail_count(jsonl, b'"compact_boundary"')

    try:
        master_fd, slave_fd = pty.openpty()
    except OSError as e:
        return {"ok": False, "via": "hidden-pty", "error": f"openpty failed: {e}"}
    try:
        _fcntl.ioctl(slave_fd, _termios.TIOCSWINSZ, _struct.pack("HHHH", 50, 160, 0, 0))
    except OSError:
        pass

    argv = [claude_bin["bin"], "--resume", sid, "--dangerously-skip-permissions"]
    run_cwd = cwd if (cwd and os.path.isdir(cwd)) else None
    # A server started from inside a Claude Code session inherits
    # CLAUDE_CODE_CHILD_SESSION, and the resumed TUI then runs with transcript
    # saving OFF — the compact_boundary we wait for is never written. Strip
    # the nesting markers and force persistence so the boundary lands on disk.
    env = {
        k: v for k, v in _core._question_relay_env().items()
        if not (k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE")
    }
    env.update(TERM="xterm-256color", CLAUDE_CODE_FORCE_SESSION_PERSISTENCE="1")
    try:
        proc = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=run_cwd,
            start_new_session=True,
            env=env,
            close_fds=True,
        )
    except (FileNotFoundError, OSError) as e:
        _core._close_fd_quiet(master_fd)
        _core._close_fd_quiet(slave_fd)
        return {"ok": False, "via": "hidden-pty", "error": f"claude failed to start: {e}"}
    _core._close_fd_quiet(slave_fd)  # parent keeps only the master end

    # Rolling capture of the LAST few KB of pty output. When claude exits early
    # or compaction stalls, this is the only window into WHY — without it every
    # failure collapses to a generic "claude exited" with no diagnosable cause
    # (the prior version read-and-discarded, so fallbacks were unexplainable).
    tail_buf = bytearray()
    TAIL_CAP = 8192

    def _drain(timeout):
        """Read available pty output so the child never blocks on a full pty
        buffer, keeping a rolling tail for diagnostics. Returns True if any
        bytes were seen this call."""
        saw = False
        end = time.time() + timeout
        while True:
            remaining = end - time.time()
            if remaining <= 0:
                break
            try:
                r, _, _ = _select.select([master_fd], [], [], remaining)
            except (OSError, ValueError):
                break
            if not r:
                break
            try:
                chunk = os.read(master_fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            saw = True
            tail_buf.extend(chunk)
            if len(tail_buf) > TAIL_CAP:
                del tail_buf[:-TAIL_CAP]
        return saw

    def _pty_tail():
        """De-ANSI'd, whitespace-collapsed tail of claude's pty output for logs
        and the error payload — what claude said right before it gave up."""
        raw = bytes(tail_buf)
        raw = re.sub(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", b"", raw)  # OSC
        raw = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", raw)            # CSI
        raw = re.sub(rb"\x1b[ -/]*[0-~]", b"", raw)                    # 2-byte / charset esc
        txt = raw.decode("utf-8", "replace")
        txt = re.sub(r"[\x00-\x1f\x7f]+", " ", txt)                   # stray control bytes
        txt = re.sub(r"\s{2,}", " ", txt).strip()
        return txt[-600:]

    def _fail(msg):
        """Attach the captured pty tail to a failure and log it server-side."""
        tail = _pty_tail()
        result["error"] = msg
        if tail:
            result["pty_tail"] = tail
        print(f"  [hidden-pty] compact failed for {sid}: {msg}"
              + (f" | claude said: …{tail}" if tail else " | (no pty output captured)"),
              file=sys.stderr)
        return result

    result = {"ok": False, "via": "hidden-pty"}
    try:
        # Let the TUI boot + render the resumed transcript. The old gate
        # ("0.7s of silence, capped at 25s") fired during the startup pause
        # BEFORE the TUI had switched the pty to raw mode, so "/compact\r"
        # sat in the cooked-mode line buffer and was delivered to Ink as one
        # chunk — a paste. The text landed in the prompt, the Enter was
        # swallowed, and the driver then waited 180s for a boundary that
        # never came (worker.err.log: "❯ /compact" still on screen). Ready
        # now means: output was seen, the input prompt is on screen, and it
        # has been quiet for a full second. Big transcripts render slowly,
        # so the cap is 60s.
        ready_deadline = time.time() + 60.0
        seen_output = False
        while time.time() < ready_deadline:
            if proc.poll() is not None:
                return _fail("claude exited before /compact could run")
            got = _drain(1.0)
            seen_output = seen_output or got
            if seen_output and not got and _pty_prompt_visible(_pty_tail()):
                break
        # Type the command, let the slash-command menu render, then submit.
        # The menu's first Enter can be consumed as "accept completion"; if
        # the prompt still shows the typed command a beat later, submit again.
        os.write(master_fd, b"/compact")
        _drain(0.6)
        os.write(master_fd, b"\r")
        _drain(2.0)
        if (
            _tail_count(jsonl, b'"compact_boundary"') <= n0
            and re.search(r"❯\s*/compact\s*$", _pty_tail()[-80:])
        ):
            os.write(master_fd, b"\r")
        # Done when a NEW compact_boundary appears in the transcript tail.
        # Summarising a 1M-context session takes minutes, not seconds.
        compact_deadline = time.time() + _HIDDEN_PTY_COMPACT_TIMEOUT_S
        while time.time() < compact_deadline:
            _drain(0.5)
            if _tail_count(jsonl, b'"compact_boundary"') > n0:
                result["ok"] = True
                result["note"] = "Compacted in a hidden terminal — no window opened."
                break
            if proc.poll() is not None:
                if _tail_count(jsonl, b'"compact_boundary"') > n0:
                    result["ok"] = True
                    result["note"] = "Compacted in a hidden terminal — no window opened."
                else:
                    _fail("claude exited before compaction completed")
                break
        else:
            _fail(f"compaction did not complete within {int(_HIDDEN_PTY_COMPACT_TIMEOUT_S)}s")
    finally:
        try:
            os.write(master_fd, b"/exit\r")
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            for killer in (
                lambda: os.killpg(proc.pid, signal.SIGTERM),
                lambda: proc.terminate(),
            ):
                try:
                    killer()
                    proc.wait(timeout=3)
                    break
                except Exception:
                    continue
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        _core._close_fd_quiet(master_fd)
    return result


def _watch_compact_status(spawn_entry, *, log_pos, via, n0, jsonl, timeout):
    """Poll a spawn's stdout log (from `log_pos` onward) plus the on-disk
    JSONL for the compacting/compact_result status events Claude Code emits
    after a `/compact` stream-json user message lands on stdin.

    Shared by `_compact_via_live_spawn_stdin` (already-live spawn, `/compact`
    sent mid-stream — watermark past prior turns) and `_compact_via_resume_spawn`
    (freshly resumed spawn, `/compact` sent as the first message) — both write
    `/compact` the same way and need the same wait-and-parse afterward.

    Current Claude Code (verified 2026-06-28, claude 2.1.195) executes a
    `/compact` user message inline: the spawn emits on its stdout
        {"type":"system","subtype":"status","status":"compacting", ...}
    then
        {"type":"system","subtype":"status","status":null,
         "compact_result":"success"|"failed","compact_error":"<msg>", ...}
    and (on success) writes a fresh `compact_boundary` to the JSONL.
    """
    log = (spawn_entry or {}).get("log") if isinstance(spawn_entry, dict) else None

    def _scan_new_status_events():
        """Read log lines appended since `log_pos`; return the latest
        (status, compact_result, compact_error) seen, advancing log_pos."""
        nonlocal log_pos
        seen_compacting = False
        result = None
        error = None
        if not log:
            return seen_compacting, result, error
        try:
            with open(log, "rb") as fh:
                fh.seek(log_pos)
                chunk = fh.read()
                log_pos = fh.tell()
        except OSError:
            return seen_compacting, result, error
        for raw in chunk.splitlines():
            if b'"status"' not in raw and b'"compact_result"' not in raw:
                continue
            try:
                ev = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if ev.get("type") != "system" or ev.get("subtype") != "status":
                continue
            if ev.get("status") == "compacting":
                seen_compacting = True
            if ev.get("compact_result"):
                result = ev.get("compact_result")
                error = ev.get("compact_error")
        return seen_compacting, result, error

    saw_compacting = False
    compact_result = None
    compact_error = None
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        comp, res, err = _scan_new_status_events()
        if comp:
            saw_compacting = True
        if res:
            compact_result = res
            compact_error = err
            break
        # The spawn may have died, or the stdout-log path could be unavailable;
        # fall back to the on-disk boundary as ground truth either way.
        if jsonl and _tail_count(jsonl, b'"compact_boundary"') > n0:
            compact_result = "success"
            break
        if _core._poll_spawn_entry(spawn_entry) is not None:
            # Process exited — check the boundary one last time.
            if jsonl and _tail_count(jsonl, b'"compact_boundary"') > n0:
                compact_result = "success"
            else:
                return {
                    "ok": False,
                    "via": via,
                    "code": "compact_spawn_exited",
                    "status": "compacting" if saw_compacting else None,
                    "error": "The live session exited before compaction completed.",
                }
            break
        time.sleep(0.4)

    boundary_landed = bool(jsonl and _tail_count(jsonl, b'"compact_boundary"') > n0)

    if compact_result == "success" or boundary_landed:
        return {
            "ok": True,
            "via": via,
            "status": "compacted",
            "compact_result": "success",
            "note": "Compacted in place — the live session ran /compact itself.",
        }
    if compact_result == "failed":
        return {
            "ok": False,
            "via": via,
            "code": "compact_failed",
            "status": "failed",
            "compact_result": "failed",
            "compact_error": compact_error,
            "error": compact_error or "Compaction failed.",
        }
    # Neither a result event nor a boundary within the timeout.
    return {
        "ok": False,
        "via": via,
        "code": "compact_timeout",
        "status": "compacting" if saw_compacting else None,
        "error": "Compaction did not complete in time (the live session is still up).",
    }


def _compact_via_live_spawn_stdin(spawn_entry, session_id, *, timeout=180.0):
    """Compact a LIVE CCC-owned stream-json claude spawn by sending `/compact`
    as a normal user message over its stdin FIFO — the native in-stream path.

    This replaces the old hidden-pty path for live spawns, which KILLED the
    session the user was actively using and then waited 180s for a boundary
    that never came (the slash command never ran in `--resume`). We no longer
    touch the live process — it stays up and compacts itself.

    Returns {ok, via:"live-spawn-stdin", status, compact_result, compact_error,
    ...}. Caller wraps with `_compact_result(...)` for the backup_path field.
    """
    sid = (session_id or "").strip()
    log = (spawn_entry or {}).get("log") if isinstance(spawn_entry, dict) else None
    jsonl = _core._resolve_conversation_path(sid) if sid else None
    n0 = _tail_count(jsonl, b'"compact_boundary"') if jsonl else 0

    # Watermark the log size so we only read NEW lines emitted after our send,
    # never an older compaction from a prior turn in the same long-lived spawn.
    log_pos = 0
    if log:
        try:
            log_pos = os.path.getsize(log)
        except OSError:
            log_pos = 0

    if not _core._write_stream_json_user_message(spawn_entry, "/compact"):
        return {
            "ok": False,
            "via": "live-spawn-stdin",
            "code": "compact_stdin_write_failed",
            "error": "Couldn't write /compact to the live session's stdin.",
        }

    return _watch_compact_status(
        spawn_entry, log_pos=log_pos, via="live-spawn-stdin",
        n0=n0, jsonl=jsonl, timeout=timeout,
    )


def _compact_via_resume_spawn(session_id, cwd, *, timeout=180.0):
    """Compact a DORMANT session through the same wake path a normal message
    send uses — `resume_session_headless` spawns `claude -p --resume ...
    --input-format stream-json` and this sends `/compact` as the FIRST
    stream-json message, then watches for the compacting/compact_result
    status events the same way `_compact_via_live_spawn_stdin` does for an
    already-live spawn.

    Replaces killing a hidden-pty TUI and typing `/compact` into it with one
    wake path for dormant sessions instead of two. `_compact_via_hidden_pty`
    stays as the fallback for when this can't spawn or the resume dies before
    a result lands (see call site in `_compact_session_context_impl`).
    """
    sid = (session_id or "").strip()
    jsonl = _core._resolve_conversation_path(sid) if sid else None
    n0 = _tail_count(jsonl, b'"compact_boundary"') if jsonl else 0

    resumed = _core.resume_session_headless(sid, "/compact", cwd=cwd)
    if not resumed.get("ok"):
        result = dict(resumed)
        result["via"] = "resume-stdin"
        result.setdefault("code", "compact_resume_spawn_failed")
        result.setdefault("error", "Couldn't resume the dormant session to compact it.")
        return result

    spawn_entry = _core._find_live_spawn_entry_for_session(sid)
    if spawn_entry is None:
        return {
            "ok": False,
            "via": "resume-stdin",
            "code": "compact_resume_spawn_missing",
            "error": "Session resumed for /compact but CCC lost track of the spawn.",
        }

    # This log file was just created for this resume (see
    # resume_session_headless's `resume-<sid8>-<timestamp>.log` naming) — there
    # is no prior-turn content to watermark past, so start from the top.
    return _watch_compact_status(
        spawn_entry, log_pos=0, via="resume-stdin",
        n0=n0, jsonl=jsonl, timeout=timeout,
    )


def _clear_via_live_spawn_stdin(spawn_entry, session_id, *, timeout=60.0):
    """Clear a LIVE CCC-owned stream-json claude spawn by sending `/clear` as a
    normal user message over its stdin FIFO — the native in-stream path.

    Verified empirically (2026-08-05, claude 2.1.222): current Claude Code
    executes a `/clear` user message inline even when it arrives over headless
    stream-json stdin (the old assumption that it "is just literal text and
    never runs" was stale/false — the same false assumption `/compact` had
    until it was corrected 2026-06-28, see `_compact_via_live_spawn_stdin`
    above). It emits
        {"type":"conversation_reset","session_id":"<old>","new_conversation_id":"<new>"}
    then mints a brand-new session_id and a brand-new on-disk JSONL — the
    pre-clear transcript freezes in the old file untouched. The live process
    itself is NOT killed and NOT restarted; only its self-reported session_id
    changes, so the caller must re-key the spawn entry afterward.

    Returns {ok, via:"live-spawn-clear", old_session_id, new_session_id, ...}.
    """
    sid = (session_id or "").strip()
    log = (spawn_entry or {}).get("log") if isinstance(spawn_entry, dict) else None

    # Watermark the log size so we only read NEW lines emitted after our send,
    # never an older reset from a prior turn in the same long-lived spawn.
    log_pos = 0
    if log:
        try:
            log_pos = os.path.getsize(log)
        except OSError:
            log_pos = 0

    if not _core._write_stream_json_user_message(spawn_entry, "/clear"):
        return {
            "ok": False,
            "via": "live-spawn-clear",
            "code": "clear_stdin_write_failed",
            "error": "Couldn't write /clear to the live session's stdin.",
        }

    seen_reset = False

    def _scan_for_reset():
        """Read log lines appended since `log_pos`; return the new session_id
        once one lands, advancing log_pos.

        The `conversation_reset` event's own `new_conversation_id` field is
        NOT reliable — verified empirically (2026-08-05, claude 2.1.222) that
        it does not match the session_id subsequent events (and the actual
        on-disk JSONL filename) actually use. So this only uses that event as
        a marker that a reset happened, then takes the session_id off the
        first event AFTER it that differs from the pre-clear sid — cross
        -checked against disk by the caller regardless.
        """
        nonlocal log_pos, seen_reset
        if not log:
            return None
        try:
            with open(log, "rb") as fh:
                fh.seek(log_pos)
                chunk = fh.read()
                log_pos = fh.tell()
        except OSError:
            return None
        for raw in chunk.splitlines():
            try:
                ev = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if ev.get("type") == "conversation_reset":
                seen_reset = True
                continue
            if seen_reset:
                candidate = ev.get("session_id")
                if candidate and candidate != sid:
                    return candidate
        return None

    new_sid = None
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        new_sid = _scan_for_reset()
        if new_sid:
            break
        if _core._poll_spawn_entry(spawn_entry) is not None:
            # Process exited — one last look in case the reset landed right
            # before it did, then give up.
            new_sid = _scan_for_reset()
            break
        time.sleep(0.2)

    if not new_sid:
        return {
            "ok": False,
            "via": "live-spawn-clear",
            "code": "clear_timeout",
            "error": "/clear did not complete in time (the live session is still up).",
        }

    # Belt-and-suspenders: confirm the fresh transcript actually landed on
    # disk before re-keying anything to point at it.
    confirm_deadline = time.time() + 10.0
    while time.time() < confirm_deadline and not _core._find_session_jsonl(new_sid):
        time.sleep(0.2)
    if not _core._find_session_jsonl(new_sid):
        return {
            "ok": False,
            "via": "live-spawn-clear",
            "code": "clear_no_transcript",
            "error": "/clear reset the session but its new transcript never appeared on disk.",
        }

    if isinstance(spawn_entry, dict):
        # Re-key whichever identity field(s) this entry actually carries so
        # every existing lookup that trusts them (_find_live_spawn_entry_for_session,
        # _spawn_entry_session_id, the GH #71 staleness watermark machinery)
        # picks up the new id for free.
        if "resumed_sid" in spawn_entry:
            spawn_entry["resumed_sid"] = new_sid
        if "session_id" in spawn_entry:
            spawn_entry["session_id"] = new_sid
        _core._update_spawn_session_id_in_registry(spawn_entry.get("pid"), new_sid)

    return {
        "ok": True,
        "via": "live-spawn-clear",
        "old_session_id": sid,
        "new_session_id": new_sid,
        "note": "Cleared in place — the live session ran /clear itself.",
    }


def clear_session_context(session_id, *, terminal_app=None, initial_message=None,
                           _from_terminal_queue=False):
    """Run Claude Code's `/clear` for a session, picking the right surface.

    Routing (deliberately narrower than `compact_session_context` — CCC-44):
      - LIVE CCC-owned stream-json spawn, IDLE → send /clear in-stream over
        its stdin FIFO (`_clear_via_live_spawn_stdin`); the live process is
        NOT killed, no terminal window opens. If `initial_message` was
        given, it's sent as the fresh session's first turn once the reset
        is confirmed.
      - LIVE spawn BUSY mid-turn → queue (`_queue_terminal_input`).
      - Everything else (live interactive terminal present, or fully
        dormant with no live process) is left to the CALLER's existing
        paths — this function is only reached for the live-headless,
        no-terminal case the frontend routes here.
    """
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}

    engine = _core._detect_session_engine(sid)
    if engine == "claude":
        routed = _core._control_plane_engine_call(
            "claude", "clear", {
                "session_id": sid,
                "terminal_app": terminal_app,
                "initial_message": initial_message,
                "from_terminal_queue": bool(_from_terminal_queue),
            },
            idempotency_key=_core._take_control_plane_action_id(),
        )
        if routed is not None:
            return routed
    if engine != "claude":
        return {
            "ok": False,
            "code": "clear_unsupported_engine",
            "engine": engine,
            "error": "/clear is only available for Claude Code sessions.",
        }

    cwd = _core.find_session_cwd(sid)
    status = _core.session_live_status(sid, cwd) or {}
    has_tty = _core._is_real_tty(status.get("tty"))

    live_spawn = _core._find_live_spawn_entry_for_session(sid) if not has_tty else None
    if live_spawn is not None:
        # CCC-935: was the raw (unbounded) tool-child check — a stuck
        # background tool child (e.g. a dangling `npx vercel deploy`) held
        # /clear queued forever even after the enclosing turn genuinely
        # finished. `_tool_child_blocks_inject` is the same signal capped at
        # _INJECT_TOOL_CHILD_MAX_HOLD_S, like the queue-drain gate uses.
        if not _core._tool_child_blocks_inject(live_spawn):
            result = _clear_via_live_spawn_stdin(live_spawn, sid)
            if result.get("ok") and initial_message:
                _core._write_stream_json_user_message(live_spawn, initial_message)
            return result
        queued_status = {"pid": live_spawn.get("pid"), "status": "headless"}
        result = _core._queue_terminal_input(sid, "/clear", queued_status)
        result["via"] = "terminal-queued-headless"
        result["note"] = "Queued — /clear will run when the headless turn finishes."
        return result

    return {
        "ok": False,
        "code": "clear_no_headless_spawn",
        "error": "No CCC-owned live headless session to clear.",
    }


def compact_session_context(session_id, *, terminal_app=None, _from_terminal_queue=False):
    """Run Claude Code's `/compact` for a session, picking the right surface.

    Routing:
      - LIVE CCC-owned stream-json spawn, IDLE → send /compact in-stream over
        its stdin FIFO (`_compact_via_live_spawn_stdin`); current Claude Code
        executes it natively and emits status:compacting → compact_result.
        The live session is NOT killed.
      - LIVE spawn BUSY mid-turn → queue (`_queue_terminal_input`).
      - LIVE interactive terminal → keystroke /compact into the TUI.
      - LIVE background agent → pty-socket inject.
      - DORMANT (no live stdin) → resume headlessly via the same wake path
        any other send uses (`_compact_via_resume_spawn`), sending /compact
        as the first stream-json message. Hidden-pty `claude --resume` is
        only the fallback if that resume can't spawn or times out.

    Wrapped with a per-session_id in-flight guard: nothing upstream stops two
    independent HTTP requests for the same session racing each other (a phone
    tab reloading mid-request and re-sending, two tabs/devices both pressing
    Compact), so a duplicate request arriving while one is already running
    for this sid is rejected instead of kicking off a second real compact.
    """
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}

    now = time.monotonic()
    with _core._COMPACT_INFLIGHT_LOCK:
        started_at = _core._COMPACT_INFLIGHT_SESSIONS.get(sid)
        if started_at is not None and now - started_at < _core._COMPACT_INFLIGHT_COOLDOWN_SECONDS:
            return {
                "ok": False,
                "code": "compact_already_in_progress",
                "error": "A /compact request for this session is already in progress.",
            }
        _core._COMPACT_INFLIGHT_SESSIONS[sid] = now
    try:
        return _compact_session_context_impl(
            sid, terminal_app=terminal_app, _from_terminal_queue=_from_terminal_queue,
        )
    finally:
        with _core._COMPACT_INFLIGHT_LOCK:
            _core._COMPACT_INFLIGHT_SESSIONS.pop(sid, None)


def _compact_session_context_impl(session_id, *, terminal_app=None, _from_terminal_queue=False):
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}

    engine = _core._detect_session_engine(sid)
    if engine == "claude":
        routed = _core._control_plane_engine_call(
            "claude", "compact", {
                "session_id": sid,
                "terminal_app": terminal_app,
                "from_terminal_queue": bool(_from_terminal_queue),
            },
            idempotency_key=_core._take_control_plane_action_id(),
            # Worker-side compaction can take up to ~300s on the hidden-pty
            # fallback path (114s on the stdin path) — the client default
            # (45s) would abandon the request as "timed out" while the worker
            # was still finishing successfully. 330s = 300s hidden-pty budget
            # + margin.
            timeout_ms=330_000,
        )
        if routed is not None:
            return routed
    if engine == "codex":
        # Codex compaction goes through the app-server `thread/compact/start`
        # RPC — no interactive TUI needed (unlike Claude). Back up the rollout
        # first because compaction is lossy.
        backup_path = _core._backup_codex_rollout_before_compact(sid)
        result = _core._codex_compact_via_app_server(sid)
        result = _compact_result(result, backup_path=backup_path)
        result.setdefault("engine", "codex")
        return result
    if engine == "kimi":
        # CCC-934: Kimi's own ACP adapter intercepts "/compact" as one of its
        # BUILTIN slash commands (MoonshotAI/kimi-code
        # packages/acp-adapter/src/{slash,builtin-commands}.ts) — it never
        # reaches the model; the adapter runs session.compact() directly and
        # reports back over session/update (compaction.started/completed/
        # blocked). Deliver it the same way normal text reaches a Kimi
        # session instead of hard-blocking with "unsupported engine". (This
        # is distinct from the kap-server daemon's REST `:compact`, which is
        # a different, out-of-band mechanism for sessions with no live ACP
        # connection at all.)
        result = _core._acp_prompt("kimi", sid, "/compact")
        if result.get("code") == "busy":
            queued = _core._queue_terminal_input(sid, "/compact", {"status": "running"})
            queued.setdefault("engine", "kimi")
            return queued
        result = _compact_result(result)
        result.setdefault("engine", "kimi")
        return result
    if engine != "claude":
        return {
            "ok": False,
            "code": "compact_unsupported_engine",
            "engine": engine,
            "error": "/compact is only available for Claude Code sessions.",
        }

    cwd = _core.find_session_cwd(sid)
    status = _core.session_live_status(sid, cwd) or {}
    tty = status.get("tty")
    term_app = terminal_app or status.get("terminal_app") or "Terminal"
    has_tty = _core._is_real_tty(tty)
    # A live headless spawn blocks /compact only when there's NO terminal to run
    # it in (headless-only — there's no TUI for the slash command). When the
    # session ALSO has a terminal (the concurrent case), we no longer block:
    # /compact runs in the terminal below, and the now-redundant headless can't
    # be reused with a stale pre-compact view because the staleness machinery
    # (GH #71) retires it the moment CCC would route to it. (/compact is also
    # append-only on disk, so there's no truncation race either.)
    # User-visible policy (per 2026-06-07): never bounce /compact with a
    # "wait then click again" error — queue it instead. The standard
    # terminal-input queue drains the moment the headless run finishes
    # and an interactive terminal opens, so /compact will run
    # automatically. UX matches a regular injection: "queued" not
    # "rejected".
    # Headless /compact (CORRECTED 2026-06-28): current Claude Code DOES execute
    # `/compact` when it arrives as a normal stream-json user message on a live
    # spawn's stdin — it emits status:"compacting" then a compact_result event
    # and writes a fresh compact_boundary. (The old assumption that stdin
    # /compact "is just literal text and never runs" was stale/false.) So for a
    # CCC-owned LIVE spawn that is IDLE we now send /compact in-stream via
    # `_compact_via_live_spawn_stdin` and watch the spawn's stdout log for the
    # outcome — WITHOUT killing the live session the user is using. Only queue
    # when BUSY (mid-turn) so a running turn isn't abandoned. The hidden-pty
    # path below stays ONLY as the fallback for genuinely dormant sessions.
    def _launch_terminal_compact(note):
        backup_path = _core._backup_jsonl_before_compact(sid)
        # SILENT path first: drive /compact in an invisible pty so no Terminal
        # window pops (the Claude-Desktop approach). Falls through to the
        # visible launch on any failure so compaction is never silently dropped.
        silent = _core._compact_via_hidden_pty(sid, cwd)
        if silent.get("ok"):
            return _compact_result(silent, backup_path)
        # Hidden-pty failed. The only remaining automatic path opens a NEW
        # terminal and AppleScript-types /compact into it. When other terminals
        # are opening that keystroke lands in the wrong window — jarring and a
        # bad outcome — so we no longer do it automatically (CCC-300). Return a
        # "needs manual" status; the client surfaces how to run /compact by hand
        # and offers to open a terminal WITHOUT typing into it.
        return _compact_result({
            "ok": False,
            "code": "compact_needs_manual",
            "via": "manual",
            "error": "Couldn't compact in the background. Resume the session and run /compact yourself.",
            "fallback_from": silent.get("error"),
            "fallback_detail": silent.get("pty_tail"),
        }, backup_path)

    live_spawn = _core._find_live_spawn_entry_for_session(sid) if not has_tty else None
    if live_spawn is not None:
        # CCC-935: bounded check (see clear_session_context above) — a stuck
        # tool child should not hold /compact queued past _INJECT_TOOL_CHILD_MAX_HOLD_S.
        if not _core._tool_child_blocks_inject(live_spawn):
            # IDLE live spawn: send /compact natively in-stream and watch the
            # spawn's stdout for status:compacting → compact_result. Back up
            # first (compaction is lossy). The live session stays up.
            backup_path = _core._backup_jsonl_before_compact(sid)
            return _compact_result(
                _compact_via_live_spawn_stdin(live_spawn, sid),
                backup_path,
            )
        queued_status = {"pid": live_spawn.get("pid"), "status": "headless"}
        result = _compact_result(_core._queue_terminal_input(sid, "/compact", queued_status))
        result["via"] = "terminal-queued-headless"
        result["note"] = "Queued — /compact will run when the headless turn finishes."
        return result
    if status.get("live") and not has_tty and status.get("kind") != "bg":
        # Live headless that CCC does NOT own a spawn entry for (live_spawn was
        # None above) — we have no stdin FIFO to send /compact in-stream. The
        # hidden-pty fallback refuses to kill an external headless (it returns
        # "external headless owns this session"), so this degrades to a
        # needs-manual status rather than disrupting a session we don't own.
        if not _core._session_status_is_busy(status):
            return _launch_terminal_compact(
                "Couldn't compact this externally-owned live session automatically.")
        queued_status = {
            "pid": status.get("pid"),
            "status": status.get("status") or "headless",
        }
        result = _compact_result(_core._queue_terminal_input(sid, "/compact", queued_status))
        result["via"] = "terminal-queued-headless"
        result["note"] = "Queued — /compact will run when the headless turn finishes."
        return result

    pending_question = _core._pending_ask_user_question_for_session(sid)

    if status.get("live") and has_tty:
        should_queue = (
            pending_question
            or _core._terminal_input_queue_has_pending(sid)
            or _core._session_status_is_busy(status)
        )
        if should_queue:
            if _from_terminal_queue:
                return {
                    "ok": False,
                    "code": "compact_session_busy",
                    "error": "Claude is still busy; compact was not submitted.",
                }
            return _compact_result(_core._queue_terminal_input(sid, "/compact", status))
        backup_path = _core._backup_jsonl_before_compact(sid)
        return _compact_result(
            _core.inject_input_via_keystroke(tty, term_app, "/compact"),
            backup_path,
        )

    if status.get("live") and status.get("kind") == "bg":
        should_queue = (
            pending_question
            or _core._terminal_input_queue_has_pending(sid)
            or not _core._bg_agent_ready_for_input(sid, status)
        )
        if should_queue:
            if _from_terminal_queue:
                return {
                    "ok": False,
                    "code": "compact_session_busy",
                    "error": "Claude background agent is still busy; compact was not submitted.",
                }
            queued_status = dict(status or {})
            queued_status["status"] = queued_status.get("status") or "busy"
            return _compact_result(_core._queue_terminal_input(sid, "/compact", queued_status))
        worker = _core._find_live_bg_agent_entry_for_session(sid)
        backup_path = _core._backup_jsonl_before_compact(sid)
        return _compact_result(_core._inject_bg_agent_via_pty_socket(worker, "/compact"), backup_path)

    if pending_question:
        return {
            "ok": False,
            "code": "compact_question_pending",
            "error": "This session is waiting for an answer. Answer it before compacting.",
        }

    if status.get("live"):
        return {
            "ok": False,
            "code": "compact_interactive_target_missing",
            "error": "CCC found a live Claude session but no interactive terminal to receive /compact.",
        }

    if _from_terminal_queue:
        return {
            "ok": False,
            "code": "compact_interactive_target_lost",
            "error": "The queued compact was not submitted because the interactive Claude terminal is gone.",
        }

    backup_path = _core._backup_jsonl_before_compact(sid)
    # Standard wake path first: resume headlessly the same way any other send
    # would, sending /compact as the first stream-json message: one wake path
    # instead of a second, fragile one that drives the TUI through a hidden pty.
    resumed = _compact_via_resume_spawn(sid, cwd)
    if resumed.get("ok"):
        return _compact_result(resumed, backup_path)
    sys.stderr.write(
        f"[compact] resume-stdin failed for {sid}: {resumed.get('error')!r} "
        "— falling back to hidden-pty\n"
    )
    # SILENT fallback: no Terminal window pops.
    silent = _core._compact_via_hidden_pty(sid, cwd)
    if silent.get("ok"):
        return _compact_result(silent, backup_path)
    # Hidden-pty failed too. We no longer auto-open a terminal and type
    # /compact — that keystroke injection lands in the wrong window when other
    # terminals are opening (CCC-300). Surface a manual-needed status instead;
    # the client explains how to run /compact and can open a terminal WITHOUT
    # typing.
    return _compact_result({
        "ok": False,
        "code": "compact_needs_manual",
        "via": "manual",
        "error": "Couldn't compact in the background. Resume the session and run /compact yourself.",
        "fallback_from": silent.get("error"),
        "resume_stdin_error": resumed.get("error"),
    }, backup_path)


