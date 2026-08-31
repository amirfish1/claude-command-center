"""Extracted from server.py (originally lines 29761-32667).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import contextlib
import fcntl
import json
import os
import queue
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from ccc_server import core as _core

_pending_queued_meta: dict = {}
_pending_queued_meta_lock = threading.Lock()


def _note_pending_queued(session_id, text, reason=None):
    """Record when and why ``text`` was queued for ``session_id``."""
    if not session_id or not text:
        return
    with _pending_queued_meta_lock:
        # Bound the table: forget entries for sessions with nothing queued.
        if len(_pending_queued_meta) > 256:
            with _core._pending_resume_lock:
                live = set(_core._pending_resume_queue.keys())
            for key in list(_pending_queued_meta.keys()):
                if key[0] not in live:
                    _pending_queued_meta.pop(key, None)
        _pending_queued_meta[(session_id, text)] = {
            "queued_at": time.time(),
            "reason": str(reason) if reason else None,
        }
_pending_resume_lock = threading.Lock()
_pending_resume_retry_after: dict = {}
_PENDING_RESUME_RETRY_DELAY_S = 60.0
_pending_terminal_input_lock = threading.Lock()
_pending_terminal_handoff_ids: dict = {}  # watcher-local handoff_id → path
_pending_inputs_lock = threading.Lock()
_pending_input_handoff_ingest_lock = threading.Lock()
_auto_resume_barrier_thread_lock = threading.RLock()
_auto_resume_barrier_local = threading.local()
_pending_inputs_watcher_lock_file = None
_pending_inputs_watcher_retry_started = False
_codex_queue_pump_locks = {}
_codex_queue_pump_locks_guard = threading.Lock()
# Per-session Devin CLI resume pump lock. The watcher drains at most one
# message at a time per session so retries cannot create duplicate concurrent
# resumes (CCC robust-devin-session design, point 6).
_devin_queue_pump_locks = {}
_devin_queue_pump_locks_guard = threading.Lock()
# Per-session opt-in for unattended auto-resume ("continue") pokes. Default
# is NOT opted in -- see CCC-863 zombie-process incident (a leaked process
# running pre-fix code injected literal "continue" into a live Codex session
# 118 times because that path used to be opt-out). Persisted alongside the
# resume/terminal queues in PENDING_INPUTS_FILE so it survives a restart.
_auto_resume_opt_in: dict = {}   # session_id → True
_auto_resume_opt_in_lock = threading.Lock()


def _auto_resume_barrier_path():
    return _core.PENDING_INPUTS_FILE.with_suffix(".auto-resume.lock")


@contextlib.contextmanager
def _auto_resume_exclusive_lock():
    """Cross-process barrier for auto-resume queueing, delivery, and cancel.

    A successful cancellation cannot overlap an unattended ``continue``
    write or delivery. Real user text never takes this lock.
    """
    with _auto_resume_barrier_thread_lock:
        depth = int(getattr(_auto_resume_barrier_local, "depth", 0) or 0)
        if depth:
            _auto_resume_barrier_local.depth = depth + 1
            try:
                yield
            finally:
                _auto_resume_barrier_local.depth = depth
            return
        lock_path = _auto_resume_barrier_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            _auto_resume_barrier_local.depth = 1
            try:
                yield
            finally:
                _auto_resume_barrier_local.depth = 0
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _deliver_with_auto_resume_barrier(session_id, text, deliver):
    """Run an unattended delivery only while its durable permission is live."""
    if not _is_unattended_auto_continue(text):
        return deliver()
    try:
        with _core._auto_resume_exclusive_lock():
            if not _core._is_auto_resume_opted_in(session_id):
                return {"ok": True, "delivered": False, "disabled": True}
            return deliver()
    except OSError:
        # Fail closed. A missing safety lock must never turn into a send.
        return {"ok": True, "delivered": False, "disabled": True}


def _acquire_pending_inputs_watcher_lock(lock_path):
    """Return an exclusive process-wide watcher lock, or None when owned."""
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        if handle is not None:
            handle.close()
        return None


def _is_unattended_auto_continue(text):
    """True for the exact auto-resume poke, not a real user follow-up."""
    return str(text or "").strip().lower() == "continue"


def _drop_unattended_auto_continues(queue):
    """Remove lone ``continue`` items from a pending-input list in place."""
    if not isinstance(queue, list):
        return False
    kept = [item for item in queue if not _is_unattended_auto_continue(item)]
    if kept == list(queue):
        return False
    queue[:] = kept
    return True


def _load_pending_inputs():
    """Load pending queues from PENDING_INPUTS_FILE into memory."""
    global _pending_resume_queue, _pending_terminal_input_queue
    try:
        with open(_core.PENDING_INPUTS_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    stripped = False
    with _core._pending_resume_lock:
        rq = data.get("resume_queue")
        if isinstance(rq, dict):
            _core._pending_resume_queue.update({k: list(v) for k, v in rq.items() if isinstance(v, list)})
        empty = []
        for sid, queue in list(_core._pending_resume_queue.items()):
            if _drop_unattended_auto_continues(queue):
                stripped = True
            if not queue:
                empty.append(sid)
        for sid in empty:
            _core._pending_resume_queue.pop(sid, None)
    with _core._pending_terminal_input_lock:
        tq = data.get("terminal_queue")
        if isinstance(tq, dict):
            _core._pending_terminal_input_queue.update({k: list(v) for k, v in tq.items() if isinstance(v, list)})
        empty = []
        for sid, queue in list(_core._pending_terminal_input_queue.items()):
            if _drop_unattended_auto_continues(queue):
                stripped = True
            if not queue:
                empty.append(sid)
        for sid in empty:
            _core._pending_terminal_input_queue.pop(sid, None)
    with _core._auto_resume_opt_in_lock:
        flags = data.get("auto_resume_opt_in")
        if isinstance(flags, dict):
            _core._auto_resume_opt_in.update({
                str(sid): True
                for sid, v in flags.items()
                if v and not _core._is_session_auto_resume_disabled(sid)
            })
    if stripped:
        _core._save_pending_inputs()
def _save_pending_inputs():
    """Save pending queues from memory to PENDING_INPUTS_FILE."""
    with _pending_inputs_lock:
        with _core._pending_resume_lock:
            rq = dict(_core._pending_resume_queue)
        with _core._pending_terminal_input_lock:
            # Worker handoff files remain the authority until delivery. Never
            # copy their in-memory string wrappers into the shared snapshot:
            # a stale sibling CCC can replace this JSON, but it cannot erase
            # the worker-owned inbox entry or create a duplicate on restart.
            tq = {
                sid: [
                    item for item in queue
                    if not isinstance(item, _PendingInputHandoff)
                ]
                for sid, queue in _core._pending_terminal_input_queue.items()
            }
            tq = {sid: queue for sid, queue in tq.items() if queue}
        with _core._auto_resume_opt_in_lock:
            opt_in = dict(_core._auto_resume_opt_in)
        payload = {
            "resume_queue": rq,
            "terminal_queue": tq,
            "auto_resume_opt_in": opt_in,
        }
        try:
            _core.PENDING_INPUTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _core.PENDING_INPUTS_FILE.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            tmp.replace(_core.PENDING_INPUTS_FILE)
        except OSError as e:
            print(f"  [pending-inputs] save failed: {e}")
            return False
    return True


def _is_auto_resume_opted_in(session_id):
    """True only when `session_id` has explicitly opted in to unattended
    auto-resume ("continue") pokes. Default is NOT opted in -- see CCC-863
    zombie-process incident (opt-out by default is how a leaked process
    running pre-fix code burned a weekly quota unattended)."""
    if not session_id:
        return False
    if _core._is_session_auto_resume_disabled(session_id):
        return False
    with _core._auto_resume_opt_in_lock:
        return bool(_core._auto_resume_opt_in.get(str(session_id)))


def _set_auto_resume_opt_in(session_id, value=True):
    """Durably set (or clear) the per-session auto-resume opt-in flag."""
    if not session_id:
        return
    with _core._auto_resume_opt_in_lock:
        if value:
            _core._auto_resume_opt_in[str(session_id)] = True
        else:
            _core._auto_resume_opt_in.pop(str(session_id), None)
    _core._save_pending_inputs()


def _apply_spawn_auto_resume_opt_in(payload, result):
    """Wire /api/sessions/spawn's `"auto_resume": true` field to the durable
    opt-in flag on the freshly spawned session. This is the main legitimate
    use case: a WatchTower queue-drain worker that is SUPPOSED to keep
    draining and should be nudged after a transient error opts in at spawn
    time, rather than every session getting unattended "continue" pokes by
    default. No-op when the spawn failed or the field was not requested."""
    if not isinstance(result, dict) or not result.get("ok"):
        return
    if not payload.get("auto_resume"):
        return
    session_id = result.get("session_id")
    if session_id:
        _core._set_auto_resume_opt_in(session_id, True)


class _PendingInputHandoff(str):
    """String-compatible queued input backed by an authoritative inbox file."""

    def __new__(cls, text, handoff_id, path):
        value = super().__new__(cls, text)
        value.handoff_id = handoff_id
        value.handoff_path = path
        return value


def _write_pending_input_handoff(session_id, text, *, front=False):
    """Write a worker handoff, gating unattended auto-resume at the barrier."""
    if _is_unattended_auto_continue(text):
        try:
            with _core._auto_resume_exclusive_lock():
                if not _core._is_auto_resume_opted_in(session_id):
                    return None
                return _write_pending_input_handoff_unlocked(
                    session_id, text, front=front,
                )
        except OSError:
            return None
    return _write_pending_input_handoff_unlocked(
        session_id, text, front=front,
    )


def _write_pending_input_handoff_unlocked(session_id, text, *, front=False):
    """Atomically hand one terminal retry from an engine worker to the watcher.

    Persistent engine workers do not own the dashboard's in-memory pending
    queues. Writing a unique inbox file avoids replacing pending-inputs.json
    from a worker's partial process-local snapshot.
    """
    session_id = str(session_id or "").strip()
    text = str(text or "")
    if not session_id or not text:
        return None
    handoff_id = str(uuid.uuid4())
    created_at = time.time()
    filename = f"{time.time_ns()}-{handoff_id}.json"
    target = _core.PENDING_INPUT_HANDOFF_DIR / filename
    tmp = _core.PENDING_INPUT_HANDOFF_DIR / f".{filename}.tmp"
    payload = {
        "id": handoff_id,
        "session_id": session_id,
        "text": text,
        "front": bool(front),
        "created_at": created_at,
    }
    try:
        _core.PENDING_INPUT_HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(target)
        return target
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"  [pending-inputs] worker handoff failed: {e}", flush=True)
        return None


def _read_pending_input_handoff(path):
    try:
        with open(path) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return None
    handoff_id = str(payload.get("id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    text = payload.get("text")
    created_at = payload.get("created_at")
    if (
        not handoff_id
        or not session_id
        or not isinstance(text, str)
        or not text
        or not isinstance(created_at, (int, float))
    ):
        return None
    return {
        "id": handoff_id,
        "session_id": session_id,
        "text": text,
        "front": bool(payload.get("front")),
        "created_at": float(created_at),
        "path": path,
    }


def _ingest_pending_input_handoffs():
    """Ingest handoffs behind the same barrier used by cancellation."""
    try:
        with _core._auto_resume_exclusive_lock():
            return _ingest_pending_input_handoffs_unlocked()
    except OSError:
        return 0


def _ingest_pending_input_handoffs_unlocked():
    """Expose authoritative worker retry files in the watcher-owned queue."""
    with _pending_input_handoff_ingest_lock:
        try:
            paths = sorted(_core.PENDING_INPUT_HANDOFF_DIR.glob("*.json"))
        except OSError:
            return 0
        if not paths:
            return 0

        events = []
        for path in paths:
            event = _read_pending_input_handoff(path)
            if event is None:
                try:
                    path.replace(path.with_suffix(".invalid"))
                except OSError:
                    pass
                print(
                    f"  [pending-inputs] invalid worker handoff: {path.name}",
                    flush=True,
                )
                continue
            if (
                _is_unattended_auto_continue(event["text"])
                and not _core._is_auto_resume_opted_in(event["session_id"])
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                _core._pending_terminal_handoff_ids.pop(event["id"], None)
                continue
            events.append(event)
        if not events:
            return 0

        # Refresh immediately before merging so unrelated messages accepted by
        # the dashboard or a sibling process remain in memory. Handoff-backed
        # strings are deliberately excluded from pending-inputs.json; their
        # unique files remain authoritative until proven delivery.
        _core._load_pending_inputs()
        new_events = [
            event for event in events
            if event["id"] not in _core._pending_terminal_handoff_ids
        ]
        with _core._pending_terminal_input_lock:
            # Inserting newest-first at index zero preserves FIFO order among
            # multiple front-of-queue retry events.
            for event in reversed([
                item for item in new_events if item["front"]
            ]):
                _core._pending_terminal_input_queue.setdefault(
                    event["session_id"], []
                ).insert(0, _PendingInputHandoff(
                    event["text"],
                    event["id"],
                    event["path"],
                ))
            for event in (
                item for item in new_events if not item["front"]
            ):
                _core._pending_terminal_input_queue.setdefault(
                    event["session_id"], []
                ).append(_PendingInputHandoff(
                    event["text"],
                    event["id"],
                    event["path"],
                ))
        for event in new_events:
            _core._pending_terminal_handoff_ids[event["id"]] = event["path"]
        return len(new_events)


def _complete_pending_input_handoff(text):
    """Acknowledge one proven-delivered (or deliberately dropped) handoff."""
    if not isinstance(text, _PendingInputHandoff):
        return False
    try:
        text.handoff_path.unlink(missing_ok=True)
    except OSError as e:
        print(
            f"  [pending-inputs] handoff cleanup failed: {e}",
            flush=True,
        )
        return False
    _core._pending_terminal_handoff_ids.pop(text.handoff_id, None)
    return True


def _queue_kimi_remote_busy_retry(session_id, text, *, front=False):
    """Return a Kimi remote-busy prompt to its process-appropriate queue."""
    if os.environ.get("CCC_WORKER_PROCESS") == "1":
        return _core._write_pending_input_handoff(
            session_id,
            text,
            front=front,
        ) is not None
    if front:
        _core._requeue_terminal_input_front(session_id, text)
    else:
        _core._queue_terminal_input(session_id, text, {"status": "running"})
    return True


def _get_queued_events_for_session(session_id):
    """Get synthetic events for any queued messages of this session."""
    events = []
    if not session_id:
        return events
    with _core._pending_resume_lock:
        resume_queue = list(_core._pending_resume_queue.get(session_id, []))
    with _core._pending_terminal_input_lock:
        term_queue = list(_core._pending_terminal_input_queue.get(session_id, []))
    # Every other event carries an ISO timestamp. This used to emit epoch
    # seconds, which the browser parsed as epoch milliseconds and rendered
    # as "Jan 21 1970" on queued rows.
    now = time.time()

    def _queued_event(text):
        visible_text = _core._strip_mode3_instruction(text)
        if not visible_text:
            return None
        with _pending_queued_meta_lock:
            meta = dict(_pending_queued_meta.get((session_id, text)) or {})
        since = float(meta.get("queued_at") or now)
        ev = {
            "line": None,
            "ts": datetime.fromtimestamp(since, timezone.utc).isoformat().replace("+00:00", "Z"),
            "type": "user_text",
            "text": visible_text,
            "images": [],
            "pending": True,
            "queued_for_s": max(0, int(now - since)),
        }
        reason = meta.get("reason")
        if reason:
            ev["queued_reason"] = str(reason)
        return ev

    for text in resume_queue:
        ev = _queued_event(text)
        if ev:
            events.append(ev)
    for text in term_queue:
        ev = _queued_event(text)
        if ev:
            events.append(ev)
    # Codex threads also surface durable desktop↔CCC coordination events
    # (external turn detected, input queued, CCC turn boundaries) so the
    # ownership story is part of the conversation, not just transient status.
    try:
        if _core._detect_session_engine(session_id) == "codex":
            events.extend(_core._get_codex_coordination_events_for_session(session_id))
            events.extend(_core._get_codex_app_server_item_events_for_session(session_id))
    except Exception:
        pass
    return events


def _merge_synthetic_conversation_events(events, synthetic_events):
    """Chronologically merge transcript and CCC-generated overlay events."""
    combined = list(events or []) + list(synthetic_events or [])

    def _epoch(event):
        value = event.get("ts") if isinstance(event, dict) else None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return float("inf")

    # Python's sort is stable, so equal/missing timestamps retain their source
    # order while old coordination rows move beside the turn that produced them.
    combined.sort(key=_epoch)
    return combined

_BUSY_SESSION_STATUSES = {"busy", "running"}


def _session_status_is_busy(status):
    return (status.get("status") or "").lower() in _BUSY_SESSION_STATUSES


def _terminal_queue_waits_for_active_acp(status):
    """Keep FIFO input parked while an ACP turn is running."""
    return status.get("kind") == "acp" and _core._session_status_is_busy(status)


def _pending_question_option_matches_text(session_id, text):
    """True when text is exactly one of the current terminal question options."""
    clean = str(text or "").strip()
    if not clean:
        return False
    pending = _core._pending_ask_user_question_for_session(session_id)
    if not isinstance(pending, dict):
        return False
    candidates = []
    options = pending.get("options")
    if isinstance(options, list):
        candidates.extend(options)
    option_details = pending.get("option_details")
    if isinstance(option_details, list):
        for detail in option_details:
            if isinstance(detail, dict):
                candidates.append(detail.get("label"))
    return any(str(candidate or "").strip() == clean for candidate in candidates)


def _drop_matching_terminal_queue_entries(session_id, text):
    """Remove stale duplicate answer clicks already parked in the terminal queue."""
    clean = str(text or "").strip()
    if not session_id or not clean:
        return 0
    removed = 0
    removed_items = []
    with _core._pending_terminal_input_lock:
        queue = _core._pending_terminal_input_queue.get(session_id)
        if not queue:
            return 0
        kept = []
        for item in queue:
            if str(item or "").strip() == clean:
                removed += 1
                removed_items.append(item)
            else:
                kept.append(item)
        if removed:
            if kept:
                _core._pending_terminal_input_queue[session_id] = kept
            else:
                _core._pending_terminal_input_queue.pop(session_id, None)
    if removed:
        _core._save_pending_inputs()
        for item in removed_items:
            _core._complete_pending_input_handoff(item)
    return removed


def _consume_matching_pending_input(session_id, text):
    """Remove one queued copy before re-routing it through Steer.

    Duplicate prompts are valid, so consume at most one item. Resume-queued
    input is checked first because it is the queue used by dormant Codex turns;
    the terminal queue is the fallback used by live terminal sessions.
    """
    clean = str(text or "").strip()
    if not session_id or not clean:
        return 0
    for queue, lock in (
        (_core._pending_resume_queue, _core._pending_resume_lock),
        (_core._pending_terminal_input_queue, _core._pending_terminal_input_lock),
    ):
        removed = False
        removed_item = None
        with lock:
            items = queue.get(session_id)
            if not items:
                continue
            for index, item in enumerate(items):
                if str(item or "").strip() != clean:
                    continue
                removed_item = items.pop(index)
                if not items:
                    queue.pop(session_id, None)
                removed = True
                break
        if removed:
            _core._save_pending_inputs()
            _core._complete_pending_input_handoff(removed_item)
            return 1
    return 0


def _finalize_queued_steer_result(session_id, text, result):
    """Commit a queued-row Steer only when Codex confirmed delivery.

    The durable FIFO remains untouched for unavailable/failed steering. This
    prevents a retry from silently moving the selected item to the queue tail.
    """
    result = dict(result or {})
    if result.get("ok") and result.get("via") == "codex-steer":
        consumed = _core._consume_matching_pending_input(session_id, text)
        if consumed:
            result["queued_consumed"] = consumed
        return result
    if result.get("code") == "codex_no_active_turn":
        # Steer is meaningful only while a turn is running. If the turn ended
        # between rendering the queued row and the click, leave every durable
        # item in place and wake the normal pump. The pump owns FIFO selection,
        # delivery confirmation, and removal, so clicking a later row can never
        # jump it ahead of an older message.
        _core._schedule_codex_queue_pump(session_id)
        result.update({
            "ok": True,
            "queued": True,
            "queued_preserved": True,
            "queue_pump_started": True,
        })
        return result
    result["queued"] = True
    result["queued_preserved"] = True
    return result


_CCC_HOOK_SCRIPTS = _core.CCC_HOOK_SCRIPT_NAMES


def _is_ccc_hook_command(command):
    """True if a child process is one of our own Claude Code hook scripts.

    The PreToolUse hook blocks (in its own process group) while a relayed
    AskUserQuestion waits for an answer. That hook is NOT a tool the agent is
    running — counting it as one flips the sidecar to a phantom "Bash" command,
    which masks the question and suppresses the answer modal.
    """
    return any(name in (command or "") for name in _CCC_HOOK_SCRIPTS)


def _spawn_entry_active_tool_child(entry):
    """Return metadata for a transient child tool process under a spawned agent.

    Claude's long-lived MCP servers stay in the Claude process group. Bash/tool
    subprocesses are launched as their own process group, so a direct child with
    a different PGID is a strong signal that the session is still busy even when
    the sidecar says "waiting". Our own hook processes are skipped — a blocked
    question-relay hook is not a running tool.
    """
    try:
        parent_pid = int((entry or {}).get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if parent_pid <= 0:
        return None
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,stat=,etime=,command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    for raw in (proc.stdout or "").splitlines():
        parts = raw.strip().split(None, 5)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            pgid = int(parts[2])
        except ValueError:
            continue
        if ppid != parent_pid or pgid == parent_pid:
            continue
        command = parts[5] if len(parts) > 5 else ""
        if _core._is_ccc_hook_command(command):
            continue
        # Derive the child's real start time from elapsed (ps etime) so the
        # UI shows the true tool age. Without this the caller stamps
        # time.time() every poll, freezing a hung child at "running <1s"
        # forever and dodging the frontend's 5-minute stale-tool cap.
        elapsed = _parse_ps_etime(parts[4])
        started_at = (time.time() - elapsed) if elapsed is not None else None
        return {
            "pid": pid,
            "pgid": pgid,
            "stat": parts[3],
            "command": command,
            "started_at": started_at,
        }
    return None


# How long an active tool child may hold QUEUED input before the drain loop
# stops waiting for it. A tool child normally means "mid-turn, input will land
# at the next boundary" — but a turn can wedge indefinitely on a child that
# outlives it (an agent-spawned `while true; ...; sleep 120` poll loop is the
# case seen in the wild, holding one session's queue for 4h22m). Past this
# bound the "it'll land shortly" assumption is simply false, and holding user
# text hostage is worse than delivering it a turn late — Claude queues
# mid-turn writes itself, so delivery here is safe either way.
_INJECT_TOOL_CHILD_MAX_HOLD_S = 600


def _tool_child_blocks_inject(spawn, now=None):
    """True if `spawn` has an active tool child young enough to justify holding
    queued input for it.

    Deliberately NOT used by the retire/staleness call sites: those kill the
    process, where a false "not busy" would destroy real work. Here the only
    cost of being wrong is a message arriving at the next turn boundary.
    """
    child = _core._spawn_entry_active_tool_child(spawn)
    if not child:
        return False
    started_at = child.get("started_at")
    if not started_at:
        return True
    age = (now if now is not None else time.time()) - started_at
    if age > _core._INJECT_TOOL_CHILD_MAX_HOLD_S:
        print(
            f"[terminal-queue] tool child pid={child.get('pid')} has held input for "
            f"{int(age)}s (> {_core._INJECT_TOOL_CHILD_MAX_HOLD_S}s); delivering anyway: "
            f"{str(child.get('command') or '')[:100]}",
            flush=True,
        )
        return False
    return True


def _parse_ps_etime(text):
    """Parse BSD ps ``etime`` ([[DD-]HH:]MM:SS) into elapsed seconds.

    Returns None on anything unparseable so callers can fall back.
    """
    text = (text or "").strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        day_part, _, text = text.partition("-")
        try:
            days = int(day_part)
        except ValueError:
            return None
    bits = text.split(":")
    try:
        nums = [int(b) for b in bits]
    except ValueError:
        return None
    if len(nums) == 3:
        h, m, s = nums
    elif len(nums) == 2:
        h, m, s = 0, nums[0], nums[1]
    else:
        return None
    return days * 86400 + h * 3600 + m * 60 + s


# CCC-1002: distinct queued_reason for "queued only to preserve order behind
# an earlier queued message" — as opposed to the default reason, which says
# the LIVE turn is the obstacle and is wrong (and misleading enough to drive
# a destructive steer/interrupt) when the actual live turn has nothing to do
# with this message.
_TERMINAL_QUEUE_ORDER_REASON = (
    "waiting behind an earlier queued message for this session"
)


def _queue_terminal_input(session_id, text, status=None, reason_hint=None):
    """Queue terminal input, gating the unattended marker at the barrier."""
    if _is_unattended_auto_continue(text):
        try:
            with _core._auto_resume_exclusive_lock():
                if not _core._is_auto_resume_opted_in(session_id):
                    return {
                        "ok": False,
                        "queued": False,
                        "error": "auto-resume not opted in for this session",
                        "auto_resume_opt_in_required": True,
                    }
                return _core._queue_terminal_input_unlocked(session_id, text, status, reason_hint)
        except OSError:
            return {
                "ok": False,
                "queued": False,
                "error": "auto-resume safety barrier unavailable",
            }
    return _core._queue_terminal_input_unlocked(session_id, text, status, reason_hint)


def _terminal_queue_dedupe_key(text):
    """Collapse key for slash commands that must not stack in one session's
    queue (/compact, /clear); None for ordinary text, which may repeat."""
    if not isinstance(text, str):
        return None
    if _core._COMPACT_TRIGGER_RE.match(text):
        return "/compact"
    if _core._CLEAR_TRIGGER_RE.match(text):
        return "/clear"
    return None


def _queue_terminal_input_unlocked(session_id, text, status=None, reason_hint=None):
    """Queue input until a live Claude session reports it is idle again."""
    deduped = False
    with _core._pending_terminal_input_lock:
        queue = _core._pending_terminal_input_queue.setdefault(session_id, [])
        # A second /compact (or /clear) queued while the first is still
        # pending would land on the already-compacted session and be refused
        # ("Not enough messages to compact."). One copy per session is the
        # whole intent; collapse the duplicate instead of stacking it.
        if _terminal_queue_dedupe_key(text) is not None and any(
            _terminal_queue_dedupe_key(item) == _terminal_queue_dedupe_key(text)
            for item in queue
        ):
            deduped = True
        else:
            queue.append(text)
        queued_count = len(queue)
    if deduped:
        # A worker-handoff copy is str-compatible; release its inbox file
        # now, since it will never be popped by the drain loop.
        _core._complete_pending_input_handoff(text)
    else:
        _core._save_pending_inputs()
    payload = {

        "ok": True,
        "queued": True,
        "via": "terminal-queued",
        "session_id": session_id,
        "queued_count": queued_count,
    }
    if deduped:
        payload["deduped"] = True
        payload["note"] = "Already queued for this session; not queued twice."
    if status:
        payload["pid"] = status.get("pid")
        payload["status"] = status.get("status")
        if (status.get("status") or "").lower() in _BUSY_SESSION_STATUSES | {"headless"}:
            payload["queued_reason"] = (
                "the current turn is still running; your message will send next"
            )
    # CCC-1002: a message queued only to preserve order behind an EARLIER
    # queued entry is not blocked by "the current turn is still running" —
    # that text told the user their live turn was the obstacle and drove
    # them to steer/interrupt it, which then aborted a turn that had nothing
    # to do with this message. Say what's actually true instead.
    if reason_hint:
        payload["queued_reason"] = reason_hint
    # CCC-796: a session already stuck as a foreign-writer hold (live
    # process, no recognized delivery channel) at submission time will NOT
    # clear on its own -- say so distinctly instead of the generic busy-turn
    # message, which implies it'll send "next" when nothing will deliver it
    # until a human opens a real terminal to the session.
    if _core._foreign_writer_hold_for_sid(session_id):
        payload["queued_reason"] = (
            "no delivery channel found for this session (it's live, but CCC lost its "
            "spawn registry entry -- open a real terminal to it to unblock)"
        )
        payload["queued_no_channel"] = True
    return payload


def _terminal_input_queue_has_pending(session_id):
    with _core._pending_terminal_input_lock:
        return bool(_core._pending_terminal_input_queue.get(session_id))


def _worker_owned_claude_input_state(session_id):
    """Ask the persistent engine owner about its live Claude FIFO."""
    result = _core._control_plane_engine_call(
        "claude", "input_state", {"session_id": session_id}, mutate=False,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return {}
    return result


def _terminal_queue_waits_for_headless_turn(session_id, status):
    """Hold durable input while a CCC-owned Claude turn owes a result."""
    if isinstance(status, dict) and _core._is_real_tty(status.get("tty")):
        return False
    spawn = _core._find_live_spawn_entry_for_session(session_id)
    if spawn is not None and (spawn.get("engine") or "claude") == "claude":
        return _core._headless_turn_in_progress(spawn)
    return bool(_worker_owned_claude_input_state(session_id).get("busy"))


def _load_daemon_roster():
    """Read Claude Code's background-agent roster, if the daemon is running."""
    try:
        data = json.loads(_core.DAEMON_ROSTER_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _find_live_bg_agent_entry_for_session(session_id):
    """Return the daemon roster worker for a live background Claude session."""
    if not session_id:
        return None
    workers = (_load_daemon_roster().get("workers") or {})
    if not isinstance(workers, dict):
        return None
    for short, worker in workers.items():
        if not isinstance(worker, dict):
            continue
        if worker.get("sessionId") != session_id:
            continue
        entry = dict(worker)
        entry["short"] = short
        return entry
    return None


def _bg_agent_state_for_session(session_id, status=None):
    """Read ~/.claude/jobs/<short>/state.json for a background agent."""
    if not session_id:
        return None
    status = status or {}
    candidates = []
    job_id = status.get("job_id") or status.get("jobId") or ""
    if job_id:
        candidates.append(_core.CLAUDE_JOBS_ROOT / str(job_id) / "state.json")
    candidates.append(_core.CLAUDE_JOBS_ROOT / session_id[:8] / "state.json")

    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("sessionId") in (None, session_id) or data.get("resumeSessionId") == session_id:
            return data

    try:
        state_files = list(_core.CLAUDE_JOBS_ROOT.glob("*/state.json"))
    except OSError:
        return None
    for path in state_files:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("sessionId") == session_id or data.get("resumeSessionId") == session_id:
            return data
    return None


def _bg_agent_ready_for_input(session_id, status=None):
    """Whether a live background agent has a prompt ready for user text.

    Claude's registry can report `busy` while the main agent is blocked for
    user input but a child local agent is still running. The jobs state is the
    better signal for background-agent input readiness.
    """
    sc = _core._read_sidecar_state(session_id)
    if sc and (sc.get("status") or "").lower() == "waiting":
        return True

    job_state = _bg_agent_state_for_session(session_id, status)
    if job_state:
        state = (job_state.get("state") or "").lower()
        if state in {"blocked", "done", "idle", "waiting"}:
            return True
        if state in {"active", "busy", "running", "working"}:
            return False

    status_text = ((status or {}).get("status") or "").lower()
    if status_text in {"idle", "waiting"}:
        return True
    if status_text in _BUSY_SESSION_STATUSES:
        return False
    return True


def _daemon_socket_path_allowed(path):
    """Clamp daemon socket use to Claude's per-user temp daemon directory."""
    if not path:
        return False
    try:
        real = os.path.realpath(path)
        daemon_dir = f"cc-daemon-{os.getuid()}"
        base_parents = {tempfile.gettempdir(), "/tmp", "/private/tmp"}
        for base_parent in base_parents:
            base = os.path.realpath(os.path.join(base_parent, daemon_dir))
            if real == base or real.startswith(base + os.sep):
                return True
        return False
    except (TypeError, ValueError, OSError):
        return False


def _clean_pty_prompt_text(text):
    """Drop terminal-control characters before writing into a background PTY."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in text
        if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )


def _inject_bg_agent_via_pty_socket(worker, text, session_id=None):
    """Send text to a live `claude agents` background session.

    The daemon PTY socket uses framed messages:
      4-byte big-endian payload length, 1-byte channel, payload bytes.
    Channel 0 is PTY data. We bracket-paste the body so multi-line prompts
    land as text, then send Return to submit.
    """
    if not worker:
        return {
            "ok": False,
            "via": "bg-agent-pty",
            "error": "background agent is live, but its daemon worker was not found",
        }
    pty_sock = worker.get("ptySock") or ""
    if not _core._daemon_socket_path_allowed(pty_sock):
        return {
            "ok": False,
            "via": "bg-agent-pty",
            "error": "background agent PTY socket is outside the expected daemon directory",
        }
    try:
        mode = os.stat(pty_sock).st_mode
    except OSError as e:
        return {"ok": False, "via": "bg-agent-pty", "error": str(e)}
    if not stat.S_ISSOCK(mode):
        return {
            "ok": False,
            "via": "bg-agent-pty",
            "error": "background agent PTY path is not a socket",
        }

    clean_text = _clean_pty_prompt_text(text)
    if not clean_text.strip():
        return {"ok": False, "via": "bg-agent-pty", "error": "missing text"}
    payload = ("\x1b[200~" + clean_text + "\x1b[201~").encode("utf-8")
    frame = len(payload).to_bytes(4, "big") + b"\x00" + payload
    submit = b"\r"
    submit_frame = len(submit).to_bytes(4, "big") + b"\x00" + submit

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect(pty_sock)
        sock.sendall(frame)
        time.sleep(0.05)
        sock.sendall(submit_frame)
    except (OSError, socket.timeout) as e:
        return {"ok": False, "via": "bg-agent-pty", "error": str(e)}
    finally:
        try:
            sock.close()
        except OSError:
            pass
    # A successful socket write is NOT delivery (CCC-113): app-managed
    # bg-pty daemons accept the connection and silently discard the input
    # frames, so "ok" here used to mean "vanished without a trace". Confirm
    # the text actually lands in the transcript before claiming success.
    sid = session_id or worker.get("sessionId") or ""
    if sid and not _core._transcript_gains_text(sid, clean_text):
        _bg_pty_inject_failures[sid] = time.time()
        return {
            "ok": False,
            "via": "bg-agent-pty",
            "delivery_unconfirmed": True,
            "pid": worker.get("pid"),
            "error": (
                "The input was written to the session's pty socket but never "
                "appeared in its transcript — this app-managed terminal "
                "ignores socket input. Type in its window instead."
            ),
        }
    return {
        "ok": True,
        "via": "bg-agent-pty",
        "pid": worker.get("pid"),
        "session_id": worker.get("sessionId"),
    }


# sid → epoch of the last pty inject whose delivery could not be confirmed.
# The queue watcher backs off such sessions so parked messages don't churn
# through a known-broken channel every tick.
_bg_pty_inject_failures: dict = {}


def _transcript_gains_text(session_id, text, timeout_s=6.0):
    """True once `text`'s first line shows up in the session transcript tail.

    Polls the last 128KB of the JSONL for up to `timeout_s`. Callers queue
    ahead of this when the session is busy, so a working channel lands the
    text within a second or two of an idle inject.
    """
    needle = ""
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            needle = line[:60]
            break
    if not needle:
        return True
    # JSONL stores the message JSON-encoded; escape so multi-byte and
    # quote-bearing needles match the on-disk form.
    needle_json = json.dumps(needle, ensure_ascii=False)[1:-1]
    path = _core._resolve_conversation_path(session_id)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 131072))
                tail = fh.read().decode("utf-8", "replace")
            if needle_json in tail:
                return True
        except OSError:
            pass
        time.sleep(1.0)
    return False


def _transcript_peer_receipt(session_id, msg_id, body, start_offset=0, timeout_s=2.0):
    """Delivery receipt for a peer-socket send: "delivered", "held", "queued",
    "unknown".

    Scans only bytes appended to the transcript AT OR AFTER `start_offset`
    (the file's size measured right before the frame was sent), never the
    whole tail. A row is only evidence of THIS send if it landed after we
    sent it: scanning from file start (or an unbounded tail) lets a stale
    "Held peer message" row from an earlier message with the same 40-char
    preview match and report "held" for a frame that was never even sent
    yet, which is why start_offset exists.

    Four verdicts, checked in this order on every poll:
    - "delivered": Claude echoed our msg_id into origin.msg_id (idle user
      row, or an absorbed mid-turn attachment). The frame reached its
      destination.
    - "held": Claude logged a "Held peer message" system row quoting the
      body's preview. The frame was received but explicitly not delivered.
    - "queued": no delivered/held row yet, but a queue-operation/enqueue row
      for this body appeared. The frame sits in the receiver's inbox and
      Claude will deliver it at the next tool boundary or turn end, so the
      caller should NOT fall through to a legacy transport (that would
      duplicate it). Remembered once seen and re-checked at the deadline
      alongside any later delivered/held row.
    - "unknown": none of the above showed up before the deadline.

    A successful socket write is NOT a delivery; only this scan is.
    """
    msg_id = str(msg_id or "")
    if not msg_id:
        return "unknown"
    needle_id = json.dumps(msg_id, ensure_ascii=False)[1:-1]
    id_forms = ('"msg_id": "%s"' % needle_id, '"msg_id":"%s"' % needle_id)
    preview = ""
    for line in str(body or "").splitlines():
        line = line.strip()
        if line:
            preview = line[:40]
            break
    preview_json = json.dumps(preview, ensure_ascii=False)[1:-1] if preview else ""
    path = _core._resolve_conversation_path(session_id)
    start_offset = max(0, int(start_offset or 0))
    deadline = time.time() + timeout_s
    queued = False
    while True:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                if size > start_offset:
                    fh.seek(start_offset)
                    tail = fh.read().decode("utf-8", "replace")
                else:
                    tail = ""  # file shorter than start_offset: nothing appended yet
            if any(form in tail for form in id_forms):
                return "delivered"
            if preview_json and "Held peer message" in tail and preview_json in tail:
                return "held"
            if preview_json and not queued:
                for line in tail.splitlines():
                    if (
                        ('"type": "queue-operation"' in line or '"type":"queue-operation"' in line)
                        and ('"operation": "enqueue"' in line or '"operation":"enqueue"' in line)
                        and preview_json in line
                    ):
                        queued = True
                        break
        except OSError:
            pass
        if time.time() >= deadline:
            return "queued" if queued else "unknown"
        time.sleep(0.25)


def _resume_queue_engine_busy(sid):
    if any(
        s.get("resumed_sid") == sid and _core._poll_spawn_entry(s) is None
        for s in _core._spawned_sessions
        if s.get("engine") in ("codex", "gemini", "cursor", "antigravity", "hermes", "opencode", "devin")
    ):
        return True
    if _core._is_codex_session(sid):
        # Preserve proven external writers on the cheap stat/lsof path. Only an
        # ownership-unknown turn needs the authoritative reattachment that
        # opening a CLI would otherwise trigger for the user.
        snap = {}
        try:
            snap = _core._codex_thread_writer_snapshot(sid)
            _core._codex_note_external_writer_transition(sid, snap)
            if snap.get("external_active") and snap.get("writer") != "unknown":
                return True
        except Exception:
            snap = {}
        if _core._codex_app_server_thread_is_active(sid, start_if_needed=True):
            return True
        if snap.get("external_active"):
            # Re-check after idle reconciliation. A real CLI is still visible
            # through rollout activity; a phantom in-memory owner disappears.
            try:
                snap = _core._codex_thread_writer_snapshot(sid)
                _core._codex_note_external_writer_transition(sid, snap)
                if snap.get("external_active"):
                    return True
            except Exception:
                return True
    return False


def _pending_resume_retry_due(sid, now=None):
    now = time.time() if now is None else float(now)
    return now >= float(_core._pending_resume_retry_after.get(sid, 0.0) or 0.0)


def _mark_pending_resume_retry(sid, now=None, delay=None):
    now = time.time() if now is None else float(now)
    delay = _PENDING_RESUME_RETRY_DELAY_S if delay is None else float(delay)
    _core._pending_resume_retry_after[sid] = now + max(0.0, delay)


def _codex_queue_pump_lock(session_id):
    """Return the process-local delivery lock for one Codex conversation."""
    with _codex_queue_pump_locks_guard:
        return _codex_queue_pump_locks.setdefault(session_id, threading.Lock())


def _devin_queue_pump_lock(session_id):
    """Return the process-local delivery lock for one Devin CLI session."""
    with _devin_queue_pump_locks_guard:
        return _devin_queue_pump_locks.setdefault(session_id, threading.Lock())


def _schedule_codex_queue_pump(session_id):
    """Trigger FIFO delivery for one conversation without blocking the caller."""
    if not session_id:
        return
    threading.Thread(
        target=_core._pump_codex_resume_queue,
        args=(session_id,),
        daemon=True,
        name=f"codex-queue-pump-{session_id[:8]}",
    ).start()


def _pump_codex_resume_queue(session_id):
    """Deliver at most one durable Codex message, preserving FIFO order."""
    lock = _core._codex_queue_pump_lock(session_id)
    if not lock.acquire(blocking=False):
        return {"ok": True, "waiting": "already-pumping"}
    try:
        if not _core._pending_resume_retry_due(session_id):
            return {"ok": True, "waiting": "backoff"}
        if _core._resume_queue_engine_busy(session_id):
            return {"ok": True, "waiting": "busy"}
        with _core._pending_resume_lock:
            queue = _core._pending_resume_queue.get(session_id) or []
            text = queue[0] if queue else None
        if text is None:
            return {"ok": True, "empty": True}

        if _is_unattended_auto_continue(text):
            try:
                with _core._auto_resume_exclusive_lock():
                    if not _core._is_auto_resume_opted_in(session_id):
                        with _core._pending_resume_lock:
                            queue = _core._pending_resume_queue.get(session_id) or []
                            if queue and queue[0] == text:
                                queue.pop(0)
                                if not queue:
                                    _core._pending_resume_queue.pop(session_id, None)
                        _core._save_pending_inputs()
                        return {"ok": True, "delivered": False, "disabled": True}
                    result = _core.resume_session_codex(
                        session_id, text, _from_queue=True,
                    )
            except OSError:
                _core._mark_pending_resume_retry(session_id)
                return {"ok": False, "delivered": False, "waiting": "barrier"}
        else:
            result = _core.resume_session_codex(session_id, text, _from_queue=True)
        # An accepted turn already started. Holding the same text because
        # app-server events were not observed re-sends it after the turn
        # ends. Treat accepted as delivered; retry only when not accepted.
        if (
            not result
            or not result.get("ok")
            or result.get("queued")
        ):
            _core._mark_pending_resume_retry(session_id)
            return {"ok": False, "delivered": False, "result": result}

        removed = False
        with _core._pending_resume_lock:
            queue = _core._pending_resume_queue.get(session_id) or []
            if queue and queue[0] == text:
                queue.pop(0)
                removed = True
                if not queue:
                    _core._pending_resume_queue.pop(session_id, None)
        if removed:
            _core._save_pending_inputs()
        _core._pending_resume_retry_after.pop(session_id, None)
        return {"ok": True, "delivered": removed, "result": result}
    finally:
        lock.release()


def _pump_devin_resume_queue(session_id):
    """Deliver at most one durable Devin CLI message, preserving FIFO order.

    Unlike the Codex pump, this pump does not remove the message from the
    queue immediately on a successful ``Popen``. The message stays queued
    until the Devin CLI writes it to its own ``prompt_history`` table
    (proof-of-delivery). The ``_start_devin_delivery_proof_watchdog`` thread
    handles the actual removal or requeue.
    """
    lock = _core._devin_queue_pump_lock(session_id)
    if not lock.acquire(blocking=False):
        return {"ok": True, "waiting": "already-pumping"}
    try:
        if not _core._pending_resume_retry_due(session_id):
            return {"ok": True, "waiting": "backoff"}
        if _core._resume_queue_engine_busy(session_id):
            return {"ok": True, "waiting": "busy"}
        with _core._pending_resume_lock:
            queue = _core._pending_resume_queue.get(session_id) or []
            text = queue[0] if queue else None
        if text is None:
            return {"ok": True, "empty": True}

        result = _core.resume_session_devin(session_id, text)
        # resume_session_devin already queues internally if a turn is running.
        # It starts both the startup-failure watchdog and the proof-of-delivery
        # watchdog. The durable queue is left intact until proof arrives.
        if result.get("queued"):
            _core._mark_pending_resume_retry(session_id)
            return {"ok": True, "waiting": "busy", "result": result}
        if not result or not result.get("ok"):
            _core._mark_pending_resume_retry(session_id)
            return {"ok": False, "waiting": "retry", "result": result}
        return {"ok": True, "started": True, "result": result}
    finally:
        lock.release()


def _codex_compaction_recovery_prompt(session_id, goals=None, recovery=None):
    goals = _core._codex_goals_snapshot() if goals is None else goals
    goal = goals.get(session_id) if isinstance(goals, dict) else None
    silent_turn = _core._codex_recovery_is_silent_turn(recovery)
    if isinstance(goal, dict) and str(goal.get("status") or "active").lower() == "active":
        if silent_turn:
            return (
                "Continue working toward the active goal after the previous turn went silent. "
                "Resume from the current repository and conversation state, do not "
                "repeat completed work, and finish and verify the original objective."
            )
        return (
            "Continue working toward the active goal after context compaction. "
            "Resume from the current repository and conversation state, do not "
            "repeat completed work, and finish and verify the original objective."
        )
    if silent_turn:
        return (
            "Continue the task after the previous turn went silent. Resume from the "
            "current repository and conversation state, do not repeat completed work, "
            "and finish and verify the original request."
        )
    return (
        "Continue the task that was interrupted by context compaction. Resume "
        "from the current repository and conversation state, do not repeat "
        "completed work, and finish and verify the original request."
    )


def _codex_compaction_recovery_suppress_unlocked(state, reason, now):
    recovery = state.get("compaction_recovery")
    if not isinstance(recovery, dict):
        return
    recovery["status"] = "suppressed"
    recovery["suppressed_reason"] = str(reason)
    label = (
        "Silent-turn recovery"
        if _core._codex_recovery_is_silent_turn(recovery)
        else "Compaction recovery"
    )
    recovery["reason"] = f"{label} suppressed: {reason}"
    recovery["suppressed_at"] = float(now)
    _core._codex_coordination_event_unlocked(
        state,
        _core._codex_recovery_event_kind(recovery, "suppressed"),
        detail=recovery["reason"],
        now=now,
    )


def _run_codex_compaction_recovery_once(session_id, now=None):
    """Advance one conversation's bounded post-compaction recovery episode."""
    sid = str(session_id or "").strip()
    now = time.time() if now is None else float(now)
    if not sid:
        return {"ok": False, "waiting": "missing-session"}

    goals = _core._codex_goals_snapshot()
    with _core._pending_resume_lock:
        has_user_input = bool(_core._pending_resume_queue.get(sid))

    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid)
        recovery = state.get("compaction_recovery") if isinstance(state, dict) else None
        if not isinstance(recovery, dict):
            return {"ok": True, "waiting": "not-armed"}
        recovery_status = str(recovery.get("status") or "waiting")
        if recovery_status in _core._CODEX_COMPACTION_RECOVERY_TERMINAL:
            return {"ok": True, "waiting": "terminal"}
        silent_turn = _core._codex_recovery_is_silent_turn(recovery)

        suppressed = None
        if state.get("thread_needs_approval") or _core._codex_app_server_pending_approval_item(state):
            suppressed = "approval"
        elif state.get("active_flags"):
            suppressed = "active-flag"
        elif has_user_input and not silent_turn:
            suppressed = "queued-user-input"
        else:
            goal = goals.get(sid) if isinstance(goals, dict) else None
            goal_status = str(goal.get("status") or "") if isinstance(goal, dict) else ""
            if goal_status.lower() in ("paused", "blocked", "complete", "completed"):
                suppressed = f"goal-{'complete' if goal_status.lower() == 'completed' else goal_status.lower()}"
        if suppressed:
            _codex_compaction_recovery_suppress_unlocked(state, suppressed, now)
            _core._save_codex_app_server_state_unlocked()
            return {"ok": True, "suppressed": suppressed}

        last_progress_at = float(
            max(
                float(recovery.get("last_progress_at") or 0.0),
                float(recovery.get("last_attempt_at") or 0.0),
                float(recovery.get("compacted_at") or 0.0),
            )
        )
        if (
            recovery_status == "recovering"
            and now - last_progress_at < _core._CODEX_COMPACTION_RECOVERY_STALL_S
        ):
            return {"ok": True, "waiting": "recovery-in-flight"}
        if (
            recovery_status == "waiting"
            and int(recovery.get("attempts") or 0) == 0
            and not silent_turn
            and now - last_progress_at < _core._CODEX_COMPACTION_RECOVERY_GRACE_S
        ):
            return {"ok": True, "waiting": "grace"}
        if now < float(recovery.get("next_attempt_at") or 0.0):
            return {"ok": True, "waiting": "backoff"}

        active_item = state.get("active_item") if isinstance(state.get("active_item"), dict) else None
        if active_item and active_item.get("in_flight", True):
            if active_item.get("type") == "contextCompaction":
                return {"ok": True, "waiting": "compacting"}
            return {"ok": True, "waiting": "active-tool"}

        active_turn = bool(
            state.get("active_turn_id")
            or str(state.get("status") or "").lower() == "active"
        )

    if active_turn:
        # CCC never interrupts a live turn on its own authority. File an
        # approval ask and hold the episode until the user answers it from
        # the dashboard (approve → interrupt below; dismiss → suppress).
        ask_reason = (
            "Codex turn went silent. Interrupt it so recovery can resume the task?"
            if silent_turn else
            "Codex turn stalled after context compaction. Interrupt it so recovery can resume the task?"
        )
        ask = _core._file_interrupt_ask(
            sid, "codex-recovery", ask_reason, {"kind": "codex-interrupt"})
        ask_status = (ask or {}).get("status") or "pending"
        if ask is None or ask_status == "pending":
            with _core._CODEX_APP_SERVER_LOCK:
                state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}
                recovery = state.get("compaction_recovery")
                if isinstance(recovery, dict):
                    recovery["reason"] = (
                        "Waiting for approval to interrupt the stalled turn"
                    )
                    recovery["next_attempt_at"] = now + 30.0
                    _core._save_codex_app_server_state_unlocked()
            return {"ok": True, "waiting": "interrupt-approval"}
        if ask_status == "dismissed":
            with _core._CODEX_APP_SERVER_LOCK:
                state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}
                if isinstance(state.get("compaction_recovery"), dict):
                    _codex_compaction_recovery_suppress_unlocked(
                        state, "interrupt-declined", now)
                    _core._save_codex_app_server_state_unlocked()
            return {"ok": True, "suppressed": "interrupt-declined"}
        # approved → the user said go; execute the interrupt.
        # Mark and persist before the RPC: Codex can emit turn/completed while
        # turn/interrupt is still waiting for its JSON-RPC response. Reset any
        # partial assistant delta so that interrupt completion cannot be
        # mistaken for a successful terminal reply.
        with _core._CODEX_APP_SERVER_LOCK:
            state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}
            recovery = state.get("compaction_recovery")
            if not isinstance(recovery, dict):
                return {"ok": False, "waiting": "not-armed"}
            recovery["status"] = "interrupting"
            recovery["reason"] = (
                "Interrupting silent stalled Codex turn"
                if _core._codex_recovery_is_silent_turn(recovery)
                else "Interrupting stalled Codex turn after compaction"
            )
            recovery["saw_agent_output"] = False
            recovery["last_attempt_at"] = now
            recovery["next_attempt_at"] = now + _core._CODEX_COMPACTION_RECOVERY_INTERRUPT_SETTLE_S
            _core._codex_coordination_event_unlocked(
                state,
                _core._codex_recovery_event_kind(recovery, "interrupting"),
                detail=recovery["reason"],
                now=now,
            )
            _core._save_codex_app_server_state_unlocked()
        interrupted = _core._codex_interrupt_via_app_server(sid)
        if interrupted.get("ok") and isinstance(ask, dict) and ask.get("id"):
            _core._mark_interrupt_ask(ask["id"], "executed")
        with _core._CODEX_APP_SERVER_LOCK:
            state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}
            recovery = state.get("compaction_recovery")
            if not isinstance(recovery, dict):
                return {"ok": False, "waiting": "not-armed"}
            if interrupted.get("ok"):
                # The interrupt RPC is authoritative. Clearing the volatile
                # local turn marker prevents a lost completion notification
                # from causing an unbounded interrupt loop; the normal writer
                # gate still re-checks Codex before starting the retry.
                state["status"] = "idle"
                state.pop("active_turn_id", None)
                state.pop("active_writer", None)
                queue_handoff = bool(
                    has_user_input and _core._codex_recovery_is_silent_turn(recovery)
                )
                if queue_handoff:
                    _codex_compaction_recovery_suppress_unlocked(
                        state, "queued-user-input", now
                    )
                _core._save_codex_app_server_state_unlocked()
                if queue_handoff:
                    _core._schedule_codex_queue_pump(sid)
                return {
                    "ok": True,
                    "interrupted": True,
                    "queue_handoff": queue_handoff,
                    "result": interrupted,
                }
            if str(recovery.get("status") or "") not in _core._CODEX_COMPACTION_RECOVERY_TERMINAL:
                recovery["status"] = "waiting"
                recovery["reason"] = interrupted.get("error") or "Could not interrupt stalled Codex turn"
                recovery["next_attempt_at"] = now + _core._CODEX_COMPACTION_RECOVERY_RETRY_S
                if interrupted.get("code") == "codex_no_active_turn":
                    state["status"] = "idle"
                    state.pop("active_turn_id", None)
                    recovery["next_attempt_at"] = now
            _core._save_codex_app_server_state_unlocked()
        return {"ok": False, "interrupted": False, "result": interrupted}

    if has_user_input and silent_turn:
        # Turn is already idle and a real user message is waiting: deliver
        # that instead of the synthetic "went silent" nudge. Mirrors the
        # queue_handoff done above when an active stalled turn had to be
        # interrupted first.
        with _core._CODEX_APP_SERVER_LOCK:
            state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}
            recovery = state.get("compaction_recovery")
            if not isinstance(recovery, dict):
                return {"ok": False, "waiting": "not-armed"}
            _codex_compaction_recovery_suppress_unlocked(state, "queued-user-input", now)
            _core._save_codex_app_server_state_unlocked()
        _core._schedule_codex_queue_pump(sid)
        return {"ok": True, "suppressed": "queued-user-input", "queue_handoff": True}

    with _core._CODEX_APP_SERVER_LOCK:
        recovery_snapshot = dict(
            (_core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}).get("compaction_recovery")
            or {}
        )
    prompt = _codex_compaction_recovery_prompt(
        sid, goals=goals, recovery=recovery_snapshot
    )
    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}
        recovery = state.get("compaction_recovery")
        if not isinstance(recovery, dict):
            return {"ok": False, "waiting": "not-armed"}
        attempts = int(recovery.get("attempts") or 0) + 1
        recovery["attempts"] = attempts
        recovery["status"] = "recovering"
        recovery["reason"] = _core._codex_recovery_activity_text(recovery)
        recovery["saw_agent_output"] = False
        recovery["last_attempt_at"] = now
        recovery["next_attempt_at"] = now + _core._CODEX_COMPACTION_RECOVERY_RETRY_S
        _core._save_codex_app_server_state_unlocked()

    result = _core.resume_session_codex(sid, prompt, _from_queue=True)
    started = bool(result and result.get("ok") and not result.get("queued"))
    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}
        recovery = state.get("compaction_recovery")
        if not isinstance(recovery, dict):
            return {"ok": False, "started": False, "result": result}
        if started:
            recovery["status"] = "recovering"
            recovery["reason"] = _core._codex_recovery_activity_text(recovery)
            if result.get("turn_id"):
                recovery["recovery_turn_id"] = str(result["turn_id"])
            _core._codex_coordination_event_unlocked(
                state,
                _core._codex_recovery_event_kind(recovery, "started"),
                detail=recovery["reason"],
                now=now,
            )
            _core._save_codex_app_server_state_unlocked()
            return {"ok": True, "started": True, "result": result}

        error = (result or {}).get("error") or "Codex recovery turn did not start"
        recovery["last_error"] = str(error)
        if int(recovery.get("attempts") or 0) >= _core._CODEX_COMPACTION_RECOVERY_MAX_ATTEMPTS:
            recovery["status"] = "exhausted"
            recovery["reason"] = (
                "Silent-turn recovery attempts exhausted"
                if _core._codex_recovery_is_silent_turn(recovery)
                else "Compaction recovery attempts exhausted"
            )
            _core._codex_coordination_event_unlocked(
                state,
                _core._codex_recovery_event_kind(recovery, "exhausted"),
                detail=str(error),
                now=now,
            )
            exhausted = True
        else:
            recovery["status"] = "waiting"
            recovery["reason"] = str(error)
            recovery["next_attempt_at"] = now + _core._CODEX_COMPACTION_RECOVERY_RETRY_S
            exhausted = False
        _core._save_codex_app_server_state_unlocked()
    return {
        "ok": False,
        "started": False,
        "exhausted": exhausted,
        "result": result,
    }


def _codex_reconcile_recovery_goal_threads(goals, now):
    """Rediscover active goal turns after a CCC server restart.

    Volatile writer/turn status is intentionally not restored from disk. A
    bounded thread/resume read reconnects those goal threads to the app-server;
    the saved activity timestamp remains authoritative for the same turn.
    """
    if not isinstance(goals, dict):
        return
    for sid, goal in goals.items():
        if not isinstance(goal, dict):
            continue
        if str(goal.get("status") or "active").lower() != "active":
            continue
        with _core._CODEX_APP_SERVER_LOCK:
            state = _core._CODEX_APP_SERVER_THREAD_STATE.get(str(sid)) or {}
            status = str(state.get("status") or "").lower()
            if status in ("active", "idle") or state.get("active_turn_id"):
                continue
            last = float(_core._codex_recovery_reconciled_at.get(str(sid), 0.0) or 0.0)
            if now - last < _core._CODEX_RECOVERY_RECONCILE_S:
                continue
            _core._codex_recovery_reconciled_at[str(sid)] = float(now)
        try:
            _core._codex_app_server_thread_is_active(str(sid), start_if_needed=True)
        except Exception:
            continue


def _codex_recovery_watchdog_session_ids(goals, now):
    """Arm silent-turn episodes and return every nonterminal recovery sid."""
    with _core._pending_resume_lock:
        queued_sids = {
            str(sid) for sid, queue in _core._pending_resume_queue.items() if queue
        }
    changed = False
    session_ids = []
    with _core._CODEX_APP_SERVER_LOCK:
        for sid, state in _core._CODEX_APP_SERVER_THREAD_STATE.items():
            if not isinstance(state, dict):
                continue
            recovery = state.get("compaction_recovery")
            if (
                isinstance(recovery, dict)
                and str(recovery.get("status") or "")
                not in _core._CODEX_COMPACTION_RECOVERY_TERMINAL
            ):
                session_ids.append(str(sid))
                continue

            active = bool(
                state.get("active_turn_id")
                or str(state.get("status") or "").lower() == "active"
            )
            if not active:
                continue
            source_turn_id = str(
                state.get("active_turn_id") or state.get("last_turn_id") or ""
            )
            if not source_turn_id:
                continue
            goal = goals.get(str(sid)) if isinstance(goals, dict) else None
            active_goal = bool(
                isinstance(goal, dict)
                and str(goal.get("status") or "active").lower() == "active"
            )
            has_user_input = str(sid) in queued_sids
            if not active_goal and not has_user_input:
                continue
            if state.get("thread_needs_approval") or state.get("active_flags"):
                continue
            active_item = (
                state.get("active_item")
                if isinstance(state.get("active_item"), dict)
                else None
            )
            if active_item and active_item.get("in_flight", True):
                continue
            try:
                last_activity_at = float(
                    state.get("last_activity_at") or state.get("last_event_at") or 0.0
                )
            except (TypeError, ValueError):
                last_activity_at = 0.0
            if not last_activity_at or now - last_activity_at < _core._CODEX_SILENT_TURN_STALL_S:
                continue
            if (
                isinstance(recovery, dict)
                and _core._codex_recovery_is_silent_turn(recovery)
                and str(recovery.get("source_turn_id") or "") == source_turn_id
                and (
                    not has_user_input
                    or (
                        recovery.get("suppressed_reason") == "interrupt-declined"
                        and now - float(recovery.get("suppressed_at") or 0.0)
                        < _core._INTERRUPT_ASK_DISMISS_SNOOZE_S
                    )
                )
            ):
                # A queued user message normally justifies re-arming recovery
                # for the same stalled turn (new intent worth retrying for).
                # But when the last attempt was declined, `_file_interrupt_ask`
                # will just hand back the same dismissed entry for
                # _INTERRUPT_ASK_DISMISS_SNOOZE_S — re-arming on every 5s
                # watchdog tick during that window only spams
                # turn_recovery_armed/interrupt-declined events with no
                # chance of a different outcome (OPS-749).
                continue

            episode_id = f"silent-turn-{source_turn_id}"
            recovery = {
                "episode_id": episode_id,
                "trigger": "silent-turn",
                "source_turn_id": source_turn_id,
                # Existing bounded recovery machinery keys completion and
                # interrupt races through this compatibility field.
                "compaction_turn_id": source_turn_id,
                "triggered_at": float(now),
                "compacted_at": float(now),
                "last_progress_at": last_activity_at,
                "status": "waiting",
                "attempts": 0,
                "next_attempt_at": float(now),
                "reason": "Active Codex turn went silent without completing",
                "compaction_in_flight": False,
                "saw_agent_output": False,
            }
            state["compaction_recovery"] = recovery
            _core._codex_coordination_event_unlocked(
                state,
                "turn_recovery_armed",
                detail="Active goal turn went silent; preparing recovery",
                now=now,
            )
            session_ids.append(str(sid))
            changed = True
        if changed:
            _core._save_codex_app_server_state_unlocked()
    return session_ids


def _run_codex_recovery_watchdog_once(now=None):
    """Recover compaction episodes and silent active goal turns."""
    now = time.time() if now is None else float(now)
    _core._codex_load_coordination_state()
    goals = _core._codex_goals_snapshot()
    _codex_reconcile_recovery_goal_threads(goals, now)
    session_ids = _codex_recovery_watchdog_session_ids(goals, now)
    results = []
    for sid in session_ids:
        try:
            results.append((sid, _core._run_codex_compaction_recovery_once(sid, now=now)))
        except Exception as exc:
            results.append((sid, {"ok": False, "error": str(exc)}))
    return results


# ── Terminal-queue drain safety (CCC-455) ───────────────────────────────────
# A terminal-queue entry must NEVER be silently consumed: the 2026-07-02
# incident (dd6f2efe) popped an entry while a foreign `claude --resume` still
# owned the session, handed it to `wt send` (rc 0, transport=resume), and wt's
# then-broken resume adapter lost the text — queue empty, transcript never
# advanced. Three defenses below, all used by the watcher drain loop:
#   1. failed deliveries re-queue at the FRONT with a retry backoff;
#   2. wt-send deliveries are only PROVISIONALLY consumed — the receipt
#      (WT-77) is polled until the transcript proves `landed`, and a verified
#      `lost` re-queues the text and forces the retry to bypass wt;
#   3. undeliverable-forever drops (dead session) log loudly instead of
#      vanishing.
_pending_terminal_retry_after = {}
_TERMINAL_QUEUE_RETRY_DELAY_S = 60.0
_terminal_drain_receipts = []          # [{sid, text, receipt_id, deadline, last_check}]
_TERMINAL_DRAIN_RECEIPT_DEADLINE_S = 600.0
_TERMINAL_DRAIN_RECEIPT_POLL_S = 15.0
_terminal_drain_skip_wt = set()        # sids whose next drain attempt bypasses wt

# P0b: scoped 2-tick escalation for the foreign-live-writer hold (a live
# session with a pid CCC has no recognized channel to, after the WT-worker
# fifo fast path above also found nothing). {(sid, pid): {"first_seen",
# "escalated"}}. Keyed on pid too so a session whose process was replaced
# starts a fresh incident rather than inheriting stale escalated state.
_foreign_writer_hold_incidents = {}
_foreign_writer_hold_lock = threading.Lock()


def _note_foreign_writer_hold(sid, pid):
    """Track consecutive watcher observations of `sid` being held as a
    foreign live writer. Returns True exactly once per incident, on the
    SECOND consecutive observation (the watcher ticks every 5s, so this
    lands ~5-10s after the message was first queued, depending on tick
    phase) -- never on the very first. The WT-129/WT-131 postmortem this
    exists for was a message that queued for 15+ minutes with zero visible
    signal; escalating on a single momentary read would also false-alarm on
    a fresh worker whose workers.json entry hasn't landed on disk yet. A
    second observation ~5s later costs almost nothing and rules that out."""
    key = (sid, pid)
    with _core._foreign_writer_hold_lock:
        # A previous incident for this sid under a DIFFERENT pid (the
        # process was replaced) must not leave the new one pre-escalated.
        for stale in [k for k in _core._foreign_writer_hold_incidents if k[0] == sid and k[1] != pid]:
            _core._foreign_writer_hold_incidents.pop(stale, None)
        state = _core._foreign_writer_hold_incidents.get(key)
        if state is None:
            _core._foreign_writer_hold_incidents[key] = {
                "first_seen": time.time(), "escalated": False,
            }
            return False
        if state["escalated"]:
            return False
        state["escalated"] = True
        return True


def _clear_foreign_writer_hold(sid):
    """Call once `sid` leaves the held state (delivered, queue drained, or a
    channel became reachable) so a later, unrelated hold on the same sid
    starts its own fresh 2-tick window instead of inheriting stale state."""
    with _core._foreign_writer_hold_lock:
        for key in [k for k in _core._foreign_writer_hold_incidents if k[0] == sid]:
            _core._foreign_writer_hold_incidents.pop(key, None)


# P0c: a global, server-backed incident feed for the delivery-health banner.
# Two sources, both in-process (no `wt` subprocess anywhere in this path):
#   (a) active P0b foreign-writer holds -- auto-resolve, no ack needed.
#   (b) receipts.json rows newly transitioned to "lost" -- these are
#       permanent historical facts, not self-resolving, so they need a
#       durable per-ID ack. The very first read baselines every
#       already-lost receipt as acked ONCE EVER (never per-restart, so a
#       loss that happens while CCC is down still surfaces on the next
#       start) -- otherwise every one of the ~73 pre-existing losses would
#       paint the banner red immediately on first boot after this ships.
def _wt_receipts_path():
    """Mirrors watchtower.receipts._receipts_file(): lives next to the
    outbox. WatchTower has no dedicated env var for this file of its own --
    it derives the directory from $WATCHTOWER_OUTBOX_FILE, so honor that
    override too when set, for the same reason WATCHTOWER_WORKERS_FILE is
    honored above."""
    outbox_env = os.environ.get("WATCHTOWER_OUTBOX_FILE")
    if outbox_env:
        return Path(outbox_env).expanduser().parent / "receipts.json"
    return _core._WT_HOME / "receipts.json"


def _wt_read_receipts():
    """Receipt rows read straight from receipts.json, in-process. No
    subprocess, no `wt receipts` CLI call -- WT's daemon remains the
    sweeper/owner of this file; CCC only ever reads it."""
    try:
        with open(_core._wt_receipts_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    rows = data.get("receipts") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def _injection_health_state_path():
    if "pytest" in sys.modules:
        return Path(tempfile.gettempdir()) / "ccc-test-injection-health.json"
    return _core.COMMAND_CENTER_STATE_DIR / "injection-health.json"


def _load_injection_health_state():
    try:
        with open(_core._injection_health_state_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("receipts_baselined", False)
    data.setdefault("acked_lost_receipt_ids", [])
    return data


def _save_injection_health_state(state):
    path = _core._injection_health_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def _active_foreign_writer_holds():
    """Escalated (2nd-consecutive-tick) P0b incidents still open. Auto-
    resolving -- there's no ack list for these, _clear_foreign_writer_hold
    already removes an entry the moment it stops being true."""
    with _core._foreign_writer_hold_lock:
        return [
            {"sid": sid, "pid": pid, "first_seen": state["first_seen"]}
            for (sid, pid), state in _core._foreign_writer_hold_incidents.items()
            if state.get("escalated")
        ]


def _foreign_writer_hold_for_sid(sid):
    """The escalated hold for this session, if any (CCC-796): a session with
    a live process but no recognized delivery channel (no spawn-registry
    entry, no tty, not a WatchTower-worker FIFO) gets queued input parked
    here forever -- there's no auto-recovery, only a human opening a real
    terminal to the session (which attaches a tty and unblocks it). Exposed
    per-sid on /api/session-status so a queued mode=send bubble can say so,
    instead of the generic "current turn is still running" (which implies
    it'll clear on its own)."""
    with _core._foreign_writer_hold_lock:
        for (hold_sid, pid), state in _core._foreign_writer_hold_incidents.items():
            if hold_sid == sid and state.get("escalated"):
                return {"pid": pid, "first_seen": state["first_seen"]}
    return None


def _build_injection_health():
    """The banner's single source of truth. Baselines historical losses on
    its very first call ever (persisted, not per-restart), then reports only
    NEWLY lost receipts (not yet acked) plus any active foreign-writer hold."""
    state = _load_injection_health_state()
    receipts = _wt_read_receipts()
    lost = [r for r in receipts if r.get("status") == "lost"]
    if not state["receipts_baselined"]:
        state["acked_lost_receipt_ids"] = list({
            str(r.get("id") or "") for r in lost if r.get("id")
        })
        state["receipts_baselined"] = True
        _save_injection_health_state(state)
    acked = set(state["acked_lost_receipt_ids"])
    new_lost = [r for r in lost if str(r.get("id") or "") not in acked]
    active_holds = _active_foreign_writer_holds()
    return {
        "active_holds": active_holds,
        "new_lost_receipts": [
            {
                "id": r.get("id"),
                "sid": r.get("sid"),
                "engine": r.get("engine"),
                "transport": r.get("transport"),
                "sent_at": r.get("sent_at"),
                # The message's own first ~60 chars, so a lost receipt reads
                # as "this specific message to this session" instead of a
                # bare id -- needed to tell which of several sends is at
                # fault when the same session shows up more than once.
                "preview": r.get("needle"),
                # A blank at_send.path means the receipt couldn't snapshot a
                # baseline at send time -- that's a proof-path gap, not
                # necessarily proof the message itself never landed.
                "proof_available": bool((r.get("at_send") or {}).get("path")),
            }
            for r in new_lost
        ],
        "any_active": bool(active_holds or new_lost),
    }


def _ack_injection_health(receipt_id=None, ack_all=False):
    """Durably dismiss one lost-receipt incident (or all currently known
    ones) so it doesn't keep alarming across reloads. Active foreign-writer
    holds aren't ack-able -- they clear themselves."""
    state = _load_injection_health_state()
    acked = set(state["acked_lost_receipt_ids"])
    if ack_all:
        for r in _wt_read_receipts():
            if r.get("status") == "lost" and r.get("id"):
                acked.add(str(r["id"]))
    elif receipt_id:
        acked.add(str(receipt_id))
    state["acked_lost_receipt_ids"] = sorted(acked)
    _save_injection_health_state(state)
    return _core._build_injection_health()


def _terminal_queue_retry_due(sid, now=None):
    now = time.time() if now is None else float(now)
    return now >= float(_core._pending_terminal_retry_after.get(sid, 0.0) or 0.0)


def _mark_terminal_queue_retry(sid, now=None, delay=None):
    now = time.time() if now is None else float(now)
    delay = _TERMINAL_QUEUE_RETRY_DELAY_S if delay is None else float(delay)
    _core._pending_terminal_retry_after[sid] = now + max(0.0, delay)


# Outcomes the SESSION ITSELF refused, as opposed to CCC failing to deliver.
# `compact_failed` is Claude Code answering a delivered /compact with
# `compact_result: "failed"` ("Not enough messages to compact." on a freshly
# compacted session). Retrying cannot change any of these, so the watcher
# must consume the entry instead of requeueing it at the front: observed
# 2026-08-26 (twice), a queued /compact that landed right after a successful
# compaction was re-sent every 60s until the dashboard restarted.
_TERMINAL_QUEUE_TERMINAL_CODES = frozenset({
    "compact_failed",
    "compact_unsupported_engine",
    "compact_needs_manual",
    "clear_unsupported_engine",
})


def _terminal_queue_result_is_terminal(result):
    """True when a drained entry's failure is final (drop it, don't retry)."""
    if not isinstance(result, dict):
        return False
    return str(result.get("code") or "") in _TERMINAL_QUEUE_TERMINAL_CODES


# CCC-984: several terminal-queue gates above (ask-question, active-acp,
# tty-busy, headless-turn-wait, bg-not-ready) `continue` with NO log line —
# a stuck item is invisible until it eventually resolves or falls into one
# of the few paths that DO log (INJECT_STALLED, "dropping ... dead
# session"). A session whose busy/ready check stays wrong indefinitely (the
# actual failure mode is elsewhere) then looks like total silence: no
# error, no retry evidence, nothing to grep — exactly what made CCC-984
# ("messages are not sent into the conversation") impossible to diagnose
# from logs alone. Throttle to once per sid+reason per 60s so a stuck item
# leaves a paper trail without adding log volume to the normal 5s poll.
_terminal_queue_hold_log_at = {}
_TERMINAL_QUEUE_HOLD_LOG_INTERVAL_S = 60.0


def _log_terminal_queue_hold(sid, reason):
    now = time.time()
    key = (sid, reason)
    last = _terminal_queue_hold_log_at.get(key, 0.0)
    if now - last < _TERMINAL_QUEUE_HOLD_LOG_INTERVAL_S:
        return
    _terminal_queue_hold_log_at[key] = now
    try:
        _core._log_activity(
            "inject", "Q_HELD",
            f"session={sid} reason={reason} — queued terminal input held, "
            "will keep retrying every 5s",
        )
    except Exception:
        pass


# CCC-1002: a held entry (ask-question / active-acp / tty-busy / headless-
# turn / bg-not-ready / tool-child-blocks-inject) previously retried every 5s
# forever. Observed holding 12 minutes, then firing the now-stale command
# into a live turn that started well after the command was queued, aborting
# it. Past this ceiling the head-of-queue entry is dropped instead of kept
# armed — the context it was queued for no longer exists.
_terminal_queue_hold_since: dict = {}
_TERMINAL_QUEUE_HOLD_TTL_S = 600.0  # 10 min


def _terminal_queue_clear_hold(sid):
    _core._terminal_queue_hold_since.pop(sid, None)


def _terminal_queue_hold_or_expire(sid, reason):
    """Record another tick of holding `sid`'s head entry, or — once held past
    `_TERMINAL_QUEUE_HOLD_TTL_S` — drop that stale entry instead. Either way
    the caller should `continue` to the next sid; returns nothing."""
    now = time.time()
    started = _core._terminal_queue_hold_since.setdefault(sid, now)
    if now - started < _core._TERMINAL_QUEUE_HOLD_TTL_S:
        _core._log_terminal_queue_hold(sid, reason)
        return
    _core._terminal_queue_hold_since.pop(sid, None)
    dropped_text = None
    with _core._pending_terminal_input_lock:
        queue = _core._pending_terminal_input_queue.get(sid, [])
        if queue:
            dropped_text = queue.pop(0)
            if not queue:
                _core._pending_terminal_input_queue.pop(sid, None)
    if dropped_text is None:
        return
    _core._save_pending_inputs()
    _core._complete_pending_input_handoff(dropped_text)
    _core._pending_terminal_retry_after.pop(sid, None)
    try:
        _core._log_activity(
            "inject", "Q_DROP",
            f"session={sid} code=held_ttl_expired "
            f"text={str(dropped_text)[:40]!r} — queued input held "
            f"{_core._TERMINAL_QUEUE_HOLD_TTL_S:.0f}s (reason={reason}) with no "
            "delivery window; dropped as stale instead of firing into an "
            "unrelated later turn",
        )
    except Exception:
        pass


def _requeue_terminal_input_front(sid, text):
    """Put a popped-but-undelivered entry back where it came from (front,
    preserving order relative to anything queued behind it)."""
    if _is_unattended_auto_continue(text):
        try:
            with _core._auto_resume_exclusive_lock():
                if not _core._is_auto_resume_opted_in(sid):
                    _core._complete_pending_input_handoff(text)
                    return False
                return _requeue_terminal_input_front_unlocked(sid, text)
        except OSError:
            _core._complete_pending_input_handoff(text)
            return False
    return _requeue_terminal_input_front_unlocked(sid, text)


def _requeue_terminal_input_front_unlocked(sid, text):
    with _core._pending_terminal_input_lock:
        _core._pending_terminal_input_queue.setdefault(sid, []).insert(0, text)
    _core._save_pending_inputs()
    return True


def _verify_terminal_drain_receipts(now=None):
    """Poll in-flight wt-send receipts for drained terminal-queue entries.

    Bounded work: only entries this watcher actually handed to wt are tracked
    (normally zero), each polled at most every _TERMINAL_DRAIN_RECEIPT_POLL_S.
    `landed` = delivery proven against the transcript, done. `lost` = wt
    verified the text never arrived — re-queue it and make the retry skip wt.
    Deadline expiry with the receipt still unverified does NOT re-send (the
    transcript may have absorbed the text in a form the needle can't match;
    re-sending would double-deliver) — but it logs instead of vanishing."""
    now = time.time() if now is None else float(now)
    for item in list(_core._terminal_drain_receipts):
        if now - float(item.get("last_check") or 0.0) < _TERMINAL_DRAIN_RECEIPT_POLL_S:
            continue
        item["last_check"] = now
        rec = None
        try:
            proc = subprocess.run(
                ["wt", "receipts", "get", item["receipt_id"]],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                rec = json.loads(proc.stdout or "")
        except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            rec = None
        rec_status = (rec or {}).get("status") if isinstance(rec, dict) else None
        if rec_status == "landed":
            _core._terminal_drain_receipts.remove(item)
            _core._complete_pending_input_handoff(item["text"])
        elif rec_status == "lost":
            _core._terminal_drain_receipts.remove(item)
            _core._terminal_drain_skip_wt.add(item["sid"])
            _core._requeue_terminal_input_front(item["sid"], item["text"])
            _core._mark_terminal_queue_retry(item["sid"], delay=5.0)
            print(
                f"[terminal-queue] wt-send receipt {item['receipt_id']} LOST — "
                f"re-queued input for {item['sid']} (next attempt bypasses wt)",
                flush=True,
            )
        elif now > float(item.get("deadline") or 0.0):
            _core._terminal_drain_receipts.remove(item)
            _core._complete_pending_input_handoff(item["text"])
            print(
                f"[terminal-queue] wt-send receipt {item['receipt_id']} still "
                f"unverified after {int(_TERMINAL_DRAIN_RECEIPT_DEADLINE_S)}s — "
                f"treating as delivered for {item['sid']}",
                flush=True,
            )


# ── Stray process reaper ────────────────────────────────────────────────────
# A leaked server.py/ccc_worker.py process (e.g. escaped from a test harness
# with a faked $HOME) can sit around for days running stale code — and if it
# wins the pending-inputs watcher flock, that stale code runs with authority
# over live sessions. Incident: a 5-day-old orphan ran pre-fix auto-resume
# code and injected "continue" into a live Codex session 118 times before
# anyone noticed. This reaper runs inside the same watcher tick that already
# owns that flock, throttled to once per _STRAY_REAPER_INTERVAL_S so it never
# slows the 5s tick, and kills any candidate PID that: matches this repo's
# absolute server.py/ccc_worker.py path, is not this process, is not the
# dashboard's or worker's own launchd-managed PID, is not an
# --archive-refresh-worker child (this dashboard's own short-lived helper),
# and has been alive longer than _STRAY_REAPER_AGE_THRESHOLD_S.
_STRAY_REAPER_INTERVAL_S = 60
_STRAY_REAPER_AGE_THRESHOLD_S = 600
_STRAY_REAPER_LAST_RUN = {"ts": 0.0}
_STRAY_REAPER_LOG = []
_STRAY_REAPER_LOG_LOCK = threading.Lock()
_STRAY_REAPER_LAUNCHD_LABELS = (
    "com.github.claude-command-center",
    "com.github.claude-command-center.worker",
)
_STRAY_REAPER_PID_RE = re.compile(r'"PID"\s*=\s*(\d+);')


def _stray_reaper_recent_entries():
    """Last N reaped-process entries for /api/health. Never raises."""
    with _STRAY_REAPER_LOG_LOCK:
        return list(_core._STRAY_REAPER_LOG[-20:])


def _stray_reaper_target_paths():
    """Absolute paths of this repo's server.py and ccc_worker.py."""
    base = os.path.dirname(os.path.abspath(__file__))
    return (
        os.path.join(base, "server.py"),
        os.path.join(base, "ccc_worker.py"),
    )


def _stray_reaper_candidate_pids():
    """PIDs of any process whose command line mentions this repo's
    server.py or ccc_worker.py by absolute path. Never raises."""
    pids = set()
    for path in _core._stray_reaper_target_paths():
        try:
            out = subprocess.run(
                ["pgrep", "-f", path],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except Exception:
            continue
        for line in (out or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pids.add(int(line))
            except ValueError:
                pass
    return pids


def _stray_reaper_pid_args(pid):
    """Full command line for `pid`, or "" if the process is already gone."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return ""
    return (out or "").strip()


def _stray_reaper_pid_age_s(pid, now):
    """Seconds since `pid` started, or None if unknown (never guess)."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return None
    raw = (out or "").strip()
    if not raw:
        return None
    try:
        started = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return now - started.timestamp()


def _stray_reaper_legitimate_pids():
    """PIDs launchd already recognizes as this dashboard's or worker's own,
    plus this process itself. A failed/absent launchctl lookup just means
    nothing is excluded from that label -- never raises."""
    legit = {os.getpid()}
    for label in _STRAY_REAPER_LAUNCHD_LABELS:
        try:
            out = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except Exception:
            continue
        m = _STRAY_REAPER_PID_RE.search(out or "")
        if m:
            try:
                legit.add(int(m.group(1)))
            except ValueError:
                pass
    return legit


def _stray_reaper_wait_for_death(pid, checks=6, interval=0.5):
    """Poll for up to ~checks*interval seconds for `pid` to exit."""
    for _ in range(checks):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _stray_reaper_reap(pid, args, age_s):
    """SIGTERM, then SIGKILL if still alive after the poll window. Never
    raises -- a kill race (already gone) or a permission failure (not ours
    to kill) just means no log entry."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    if not _stray_reaper_wait_for_death(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            return
    entry = {
        "pid": pid,
        "args": args,
        "age_s": round(age_s),
        "killed_at": time.time(),
    }
    with _STRAY_REAPER_LOG_LOCK:
        _core._STRAY_REAPER_LOG.append(entry)
        del _core._STRAY_REAPER_LOG[:-20]
    print(f"[stray-reaper] killed pid {pid} (alive {round(age_s)}s): {args}", flush=True)


def _stray_reaper_scan_and_reap(now):
    legitimate = _stray_reaper_legitimate_pids()
    for pid in _stray_reaper_candidate_pids():
        if pid in legitimate:
            continue
        args = _stray_reaper_pid_args(pid)
        if not args:
            continue  # race: already gone
        if "--archive-refresh-worker" in args:
            continue  # this dashboard's own short-lived archive-refresh child
        age_s = _stray_reaper_pid_age_s(pid, now)
        if age_s is None:
            continue  # unknown age: never guess, skip
        if age_s <= _STRAY_REAPER_AGE_THRESHOLD_S:
            continue
        _stray_reaper_reap(pid, args, age_s)


def _run_stray_process_reaper_once(now=None):
    """Throttled entry point for the resume-queue watcher tick. Internally
    caps itself to once per _STRAY_REAPER_INTERVAL_S so the caller's 5s tick
    stays fast; never raises."""
    now = now if now is not None else time.time()
    if now - _core._STRAY_REAPER_LAST_RUN["ts"] < _STRAY_REAPER_INTERVAL_S:
        return
    _core._STRAY_REAPER_LAST_RUN["ts"] = now
    try:
        _stray_reaper_scan_and_reap(now)
    except Exception:
        pass


def _start_resume_queue_watcher() -> None:
    """Drain queued prompts once fire-and-watch engines or live terminal sessions go idle."""
    global _pending_inputs_watcher_lock_file, _pending_inputs_watcher_retry_started
    if _pending_inputs_watcher_lock_file is not None:
        return
    lock_path = _core.PENDING_INPUTS_FILE.with_suffix(".watcher.lock")
    lock_file = _core._acquire_pending_inputs_watcher_lock(lock_path)
    if lock_file is None:
        print("[pending-inputs] resume watcher owned by another CCC process", flush=True)
        # Do not permanently give up when an older sibling holds the lock. If
        # its watcher thread has died, its process still keeps the flock until
        # restart; this process must be ready to take over as soon as it drops.
        if not _pending_inputs_watcher_retry_started:
            _pending_inputs_watcher_retry_started = True

            def _retry_watcher_lock():
                global _pending_inputs_watcher_retry_started
                while _pending_inputs_watcher_lock_file is None:
                    time.sleep(5)
                    _core._start_resume_queue_watcher()
                _pending_inputs_watcher_retry_started = False

            threading.Thread(
                target=_retry_watcher_lock,
                daemon=True,
                name="resume-queue-watcher-lock-retry",
            ).start()
        return
    # Keep the lock handle local to the watcher wrapper. A global handle keeps
    # the flock alive if the thread exits unexpectedly, stranding every later
    # server behind a stale sibling process.
    _pending_inputs_watcher_lock_file = True
    _core._load_pending_inputs()
    _core._ingest_pending_input_handoffs()
    # Restore durable Codex coordination events before any app-server
    # notification persists (and would otherwise clobber) the state file.
    try:
        _core._codex_load_coordination_state()
    except Exception:
        pass
    def _watcher():
        while True:
            time.sleep(5)
            # Another local CCC may have accepted a message since the previous
            # pass. The single watcher owns delivery but always refreshes this
            # durable queue before draining it.
            _core._load_pending_inputs()
            _core._ingest_pending_input_handoffs()
            # Recovery is lower priority than every durable user message. Run
            # this before FIFO draining so a queued message suppresses its
            # conversation's automatic continuation instead of racing it.
            _core._run_codex_recovery_watchdog_once()
            _core._run_auto_handover_watchdog_once()
            # Throttled internally to once per _STRAY_REAPER_INTERVAL_S; safe
            # to call every tick.
            _core._run_stray_process_reaper_once()
            with _core._pending_resume_lock:
                queued_sids = list(_core._pending_resume_queue.keys())
            for sid in queued_sids:
                if _core._is_codex_session(sid):
                    _core._pump_codex_resume_queue(sid)
                    continue
                if not _core._pending_resume_retry_due(sid):
                    continue
                if _core._resume_queue_engine_busy(sid):
                    continue
                with _core._pending_resume_lock:
                    queue = _core._pending_resume_queue.get(sid, [])
                    if not queue:
                        _core._pending_resume_queue.pop(sid, None)
                        _core._pending_resume_retry_after.pop(sid, None)
                        text = None
                    else:
                        text = queue.pop(0)
                        if not queue:
                            _core._pending_resume_queue.pop(sid, None)
                _core._save_pending_inputs()
                if text is None:
                    continue
                result = None
                try:
                    def _deliver_resume_queue_text():
                        if _core._is_codex_session(sid):
                            return _core.resume_session_codex(sid, text)
                        if _core._is_gemini_session(sid):
                            return _core.resume_session_gemini(sid, text)
                        if _core._is_cursor_session(sid):
                            return _core.resume_session_cursor(sid, text)
                        if _core._is_antigravity_session(sid):
                            return _core.resume_session_antigravity(sid, text)
                        if _core._is_hermes_session(sid):
                            return _core.resume_session_hermes(sid, text)
                        if _core._is_opencode_session(sid):
                            return _core.resume_session_opencode(sid, text)
                        if _core._is_devin_cli_session(sid):
                            return _core._pump_devin_resume_queue(sid)
                        return {"ok": False}

                    result = _core._deliver_with_auto_resume_barrier(
                        sid, text, _deliver_resume_queue_text,
                    )
                except Exception:
                    result = {"ok": False}
                if result and result.get("blocked"):
                    # Circuit-breaker trip: drop, never requeue (see the
                    # terminal-queue watcher below for the same rule).
                    _core._pending_resume_retry_after.pop(sid, None)
                elif not result or not result.get("ok"):
                    with _core._pending_resume_lock:
                        _core._pending_resume_queue.setdefault(sid, []).insert(0, text)
                    _core._save_pending_inputs()
                    _core._mark_pending_resume_retry(sid)
                elif result.get("queued") or result.get("started"):
                    # "queued" means the engine already queued it internally.
                    # "started" (Devin) means the process launched but the
                    # durable queue is kept until prompt_history proves delivery.
                    _core._mark_pending_resume_retry(sid)
                else:
                    _core._pending_resume_retry_after.pop(sid, None)
            with _core._pending_terminal_input_lock:
                terminal_sids = list(_core._pending_terminal_input_queue.keys())
            for sid in terminal_sids:
                text = None
                try:
                    # CHEAP LIVENESS PRE-GATE (perf + stuck-queue cleanup):
                    # terminal input can only ever be injected into a LIVE
                    # session (tty / bg agent / live spawn). If the session
                    # isn't even a live candidate — no fresh sidecar, no live
                    # engine process — it is dead and its queued input is
                    # undeliverable forever. Without this gate the watcher ran
                    # the EXPENSIVE session_live_status() probe (~0.5s each) on
                    # every such dead sid every 5s, pinning a core on a session
                    # that closed weeks ago (observed: a 26-day-dead sid with 13
                    # stuck items). Drop the queue and skip the probe entirely.
                    # The memoized _archive_session_is_live is ~free and is a
                    # strict superset of "injectable", so no live session is lost.
                    if not _core._archive_session_is_live(sid):
                        dropped = None
                        with _core._pending_terminal_input_lock:
                            dropped = _core._pending_terminal_input_queue.pop(sid, None)
                        if dropped:
                            # Loud, not silent (CCC-455): this is the one place
                            # queued user text is deliberately discarded.
                            print(
                                f"[terminal-queue] dropping {len(dropped)} queued "
                                f"input(s) for dead session {sid}: "
                                + "; ".join(repr(t[:80]) for t in dropped),
                                flush=True,
                            )
                            _core._save_pending_inputs()
                            for dropped_text in dropped:
                                _core._complete_pending_input_handoff(dropped_text)
                        _core._clear_foreign_writer_hold(sid)
                        _terminal_queue_clear_hold(sid)
                        continue
                    # Backoff gate (CCC-455): a sid whose last drain attempt
                    # failed or re-parked waits out its retry window before we
                    # spend another live-status probe on it.
                    if not _terminal_queue_retry_due(sid):
                        continue
                    # CRITICAL guard — never flush queued input while an
                    # AskUserQuestion is in-flight. The watcher previously
                    # only checked _session_status_is_busy() (status =
                    # busy/running), which can return False even when
                    # sidecar_tool == "AskUserQuestion" is still pending a
                    # user pick. Injecting queued text in that window made
                    # Claude Code synthesize a tool_result for the open
                    # AskUserQuestion (treating the queued text as the
                    # user's "answer") and continue the conversation —
                    # surfacing as "Claude continued silently past the
                    # question". The relay request file is the authoritative
                    # live signal; if a question is genuinely blocking, hold
                    # the queue until the user answers in the UI. Once the
                    # hook resolves it (deny-reply, no tool_result ever lands)
                    # the request file is gone and the queue drains — a plain
                    # transcript scan here would deadlock forever.
                    status = _core.session_live_status(sid, _core.find_session_cwd(sid))
                    if _core._ask_question_blocking_inject(sid, status):
                        _core._terminal_queue_hold_or_expire(sid, "ask_question_blocking")
                        continue
                    if _core._terminal_queue_waits_for_active_acp(status):
                        _core._terminal_queue_hold_or_expire(sid, "active_acp")
                        continue
                    if status.get("live") and status.get("tty") and _core._session_status_is_busy(status):
                        _core._terminal_queue_hold_or_expire(sid, "tty_busy")
                        continue
                    if _terminal_queue_waits_for_headless_turn(sid, status):
                        _core._terminal_queue_hold_or_expire(sid, "headless_turn")
                        continue
                    if status.get("live") and status.get("kind") == "bg":
                        if not _core._bg_agent_ready_for_input(sid, status):
                            _core._terminal_queue_hold_or_expire(sid, "bg_not_ready")
                            continue
                        # Channel recently proven broken (pty inject wrote ok
                        # but nothing landed) — hold instead of churning the
                        # queue through it every tick.
                        if time.time() - _bg_pty_inject_failures.get(sid, 0) < 600:
                            _core._terminal_queue_hold_or_expire(sid, "bg_pty_recent_failure")
                            continue
                    if status.get("live") and not status.get("tty"):
                        spawn = _core._find_live_spawn_entry_for_session(sid)
                        if spawn is not None and _core._tool_child_blocks_inject(spawn):
                            _core._terminal_queue_hold_or_expire(sid, "tool_child_blocks_inject")
                            continue
                        # A live WatchTower-tracked worker's FIFO is a known,
                        # in-process-reachable channel even though CCC never
                        # spawned it itself. Recognizing it here keeps the
                        # foreign-writer hold below from blocking a message
                        # that _inject_text_into_session's own WT-worker fifo
                        # fast path (below) is about to be able to deliver.
                        wt_worker_reachable = (
                            spawn is None
                            and _core._wt_worker_fifo_entry_for_session(sid) is not None
                        )
                        engine_worker_reachable = (
                            spawn is None
                            and _worker_owned_claude_input_state(sid).get("owned")
                        )
                        # Foreign live writer (not ours, no channel): hold the
                        # queue until that process exits — injecting now would
                        # spawn a parallel resume and fork the transcript.
                        if (spawn is None and not wt_worker_reachable
                                and not engine_worker_reachable
                                and status.get("kind") != "bg" and status.get("pid")):
                            if _core._note_foreign_writer_hold(sid, status.get("pid")):
                                _core._log_activity(
                                    "inject", "INJECT_STALLED",
                                    f"session={sid} pid={status.get('pid')} — "
                                    "live process, no recognized delivery "
                                    f"channel; wt send {sid} \"<text>\" reaches "
                                    "it directly",
                                )
                            continue
                        _core._clear_foreign_writer_hold(sid)
                    _terminal_queue_clear_hold(sid)
                    with _core._pending_terminal_input_lock:
                        queue = _core._pending_terminal_input_queue.get(sid, [])
                        if not queue:
                            removed = _core._pending_terminal_input_queue.pop(sid, None) is not None
                            text = None
                        else:
                            text = queue.pop(0)
                            removed = True
                            if not queue:
                                _core._pending_terminal_input_queue.pop(sid, None)
                    if removed:
                        _core._save_pending_inputs()
                    if text is None:
                        continue
                    # CCC-455: a popped entry is only consumed by a PROVEN
                    # delivery. Failure re-queues at the front (with backoff);
                    # a wt-send handoff is tracked by receipt until the
                    # transcript confirms it landed.
                    result = None
                    try:
                        result = _core._deliver_with_auto_resume_barrier(
                            sid,
                            text,
                            lambda: _core._inject_text_into_session(
                                sid, text, _from_terminal_queue=True,
                                skip_wt=(sid in _core._terminal_drain_skip_wt),
                                source="terminal-queue-watcher",
                            ),
                        )
                    except Exception:
                        result = None
                    _core._terminal_drain_skip_wt.discard(sid)
                    if not isinstance(result, dict):
                        result = {"ok": False}
                    if result.get("queued"):
                        # The inject re-parked it itself (foreign live writer,
                        # bg-undeliverable, invalid cwd) — entry is safe; back
                        # off so a held session isn't re-driven every 5s tick.
                        _core._complete_pending_input_handoff(text)
                        if result.get("foreign_live_writer"):
                            # CCC-799: a foreign-live-writer re-park here can be
                            # a one-tick status race, not a persistently stuck
                            # session — the watcher's own gate above already
                            # confirmed a channel (spawn/wt-fifo/tty) via its
                            # OWN session_live_status() call this same tick,
                            # but _inject_text_into_session recomputes status
                            # independently and can catch the channel mid-
                            # flicker a beat later. The generic 60s backoff
                            # meant for genuinely stuck sessions was costing
                            # every real recovery an extra ~60-90s of queued-
                            # but-undelivered time (confirmed via
                            # activity.log: INJECT_STALLED to first delivery
                            # gaps landing right at ~60s + tick slack). Retry
                            # soon, same short window already used for the
                            # wt-receipt-lost case below.
                            _core._mark_terminal_queue_retry(sid, delay=5.0)
                        else:
                            _core._mark_terminal_queue_retry(sid)
                    elif result.get("blocked"):
                        # Circuit-breaker trip: TERMINAL, not a delivery
                        # failure. Requeueing at the front (the branch below)
                        # would re-offer the same blocked text every tick and
                        # turn a rate limit into a hot loop. Drop it; the
                        # attempt is in the held bucket for the human.
                        _core._complete_pending_input_handoff(text)
                        _core._pending_terminal_retry_after.pop(sid, None)
                    elif _core._terminal_queue_result_is_terminal(result):
                        # The session REFUSED the command (e.g. /compact on a
                        # just-compacted session: "Not enough messages to
                        # compact."). It was delivered; the answer is final.
                        # Requeueing would re-send it every backoff window
                        # forever. Consume it, loudly.
                        _core._complete_pending_input_handoff(text)
                        _core._pending_terminal_retry_after.pop(sid, None)
                        try:
                            _core._log_activity(
                                "inject", "Q_DROP",
                                f"session={sid} code={result.get('code')} "
                                f"text={str(text)[:40]!r} — session refused "
                                f"the queued command, not retrying: "
                                f"{result.get('error') or ''}",
                            )
                        except Exception:
                            pass
                    elif not result.get("ok"):
                        _core._requeue_terminal_input_front(sid, text)
                        _core._mark_terminal_queue_retry(sid)
                    elif result.get("via") == "wt-send" and result.get("receipt_id"):
                        _core._terminal_drain_receipts.append({
                            "sid": sid,
                            "text": text,
                            "receipt_id": result["receipt_id"],
                            "deadline": time.time() + _TERMINAL_DRAIN_RECEIPT_DEADLINE_S,
                            "last_check": 0.0,
                        })
                    else:
                        _core._complete_pending_input_handoff(text)
                        _core._pending_terminal_retry_after.pop(sid, None)
                        _core._clear_foreign_writer_hold(sid)
                except Exception:
                    if isinstance(text, _PendingInputHandoff):
                        try:
                            _core._requeue_terminal_input_front(sid, text)
                            _core._mark_terminal_queue_retry(sid)
                        except Exception:
                            _core._pending_terminal_handoff_ids.pop(
                                text.handoff_id,
                                None,
                            )
            try:
                _core._verify_terminal_drain_receipts()
            except Exception:
                pass
    def _watcher_entry():
        global _pending_inputs_watcher_lock_file
        try:
            _watcher()
        finally:
            lock_file.close()
            _pending_inputs_watcher_lock_file = None
            # Restart locally as well: a transient watcher exception must not
            # leave this process dependent on a separate CCC instance.
            _core._start_resume_queue_watcher()

    threading.Thread(target=_watcher_entry, daemon=True, name="resume-queue-watcher").start()


# ── UX-fixes queue fixer resolution ─────────────────────────────────────────
# The old CCC-100 "continue" nudge watcher was removed: WatchTower now owns
# queue draining end-to-end (it pushes new work to a live worker's stream-json
# FIFO and resumes blocked sessions itself), so CCC injecting "continue" into
# queue workers is redundant — and worse, its loose claimer-resolution could
# match and double-wake a WT-spawned worker. The fixer-resolution helpers below
# survive because /api/ux-fixes/health still reports per-project liveness/stuck.
_UXQ_NUDGE_LOOKBACK_S = 48 * 3600
_UXQ_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# A UUID embedded anywhere in a free-form label (e.g. ``codex:<uuid>`` or
# ``codex-<uuid>``) — lets us recover a reachable id from an engine-prefixed
# claim label even when ``claimed_session_id`` was not supplied.
_UXQ_EMBEDDED_SESSION_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _uxq_resolve_fixer_sid(item, *, registry_by_name=None):
    """Resolve a queue item's claimer to a reachable CCC session UUID, or "".

    Resolution order (cheapest first, all O(1) per item):
      1. ``claimed_session_id`` — the additive real-session field a worker can
         supply at claim time (preferred; exact).
      2. A UUID-shaped ``claimed_by`` (the historical happy path).
      3. A UUID embedded in an engine-prefixed ``claimed_by`` label.
      4. ``registry_by_name`` lookup — best-effort: if the label exactly matches
         a CCC-spawned session's launch ``name``, take that session's id. The
         caller passes a prebuilt name→sid map so this stays O(1) per item and
         the spawn registry is read at most once per health/nudge pass (no
         per-row file read, no subprocess).

    Returns "" when the claimer is a genuinely unreachable label (e.g. a ref
    like ``CCC-59`` or a free-form name with no spawn-registry match)."""
    claimed_sid = str((item or {}).get("claimed_session_id") or "").strip()
    if _UXQ_SESSION_ID_RE.match(claimed_sid):
        return claimed_sid
    label = str((item or {}).get("claimed_by") or (item or {}).get("closed_by") or "").strip()
    if not label:
        return ""
    if _UXQ_SESSION_ID_RE.match(label):
        return label
    m = _UXQ_EMBEDDED_SESSION_ID_RE.search(label)
    if m:
        return m.group(0)
    if registry_by_name:
        hit = registry_by_name.get(label)
        if hit and _UXQ_SESSION_ID_RE.match(hit):
            return hit
    return ""


def _uxq_spawn_names_to_sids():
    """name → session_id map from the spawn registry (read once per pass).

    Cheap: one file read via _load_spawn_registry(), no subprocess, no
    transcript parse. Used only to resolve label-claimed fixers for projects
    that already have open tickets (the candidacy gate is applied by callers)."""
    out = {}
    try:
        for entry in _core._load_spawn_registry():
            name = str(entry.get("name") or "").strip()
            sid = str(entry.get("session_id") or "").strip()
            if name and sid and _UXQ_SESSION_ID_RE.match(sid):
                out.setdefault(name, sid)
    except Exception:
        return {}
    return out


def _uxq_parse_ts(value):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0

