"""Explicit, cross-process ownership for CCC versus native Codex queues."""
import hashlib
import json
import os
from ccc_server import core as _core


def _owner_file(thread_id):
    digest = hashlib.sha256(str(thread_id).encode()).hexdigest()
    return _core.PENDING_INPUTS_FILE.parent / "codex-queue-locks" / (digest + ".native-owner")


def native_queue_owned(thread_id):
    return _owner_file(thread_id).is_file()


def claim_native_queue(thread_id):
    """Transfer only an empty, recovered CCC queue; callers never copy inputs."""
    with _core._codex_queue_pump_lock("native-owner-switch:" + thread_id), _core._codex_queue_pump_lock(thread_id):
        if native_queue_owned(thread_id):
            return
        if not _core._retry_pending_input_recovery(thread_id):
            raise ValueError("Finish CCC queued-message recovery before switching queues")
        refreshed = _core._refresh_pending_inputs_for_session(thread_id)
        if not refreshed or isinstance(refreshed, dict) and not refreshed.get("ok"):
            raise ValueError("Cannot verify the CCC message queue")
        snapshot = _core._pending_inputs_session_snapshot(thread_id)
        if snapshot.get("resume") or snapshot.get("terminal"):
            raise ValueError("Send or remove CCC queued messages before using the Codex queue")
        # This runs on an explicit ownership change, never on a session-list row.
        for candidate in _core.PENDING_INPUT_HANDOFF_DIR.glob("*.json"):
            try:
                if json.loads(candidate.read_text()).get("session_id") == thread_id:
                    raise ValueError("Finish CCC queued-message handoff before switching queues")
            except (OSError, json.JSONDecodeError):
                raise ValueError("Cannot verify queued-message handoffs") from None
        target = _owner_file(thread_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write("native\n")
            out.flush()
            os.fsync(out.fileno())


def release_native_queue(thread_id, read_native_queue):
    """Return to CCC only after native queue emptiness is confirmed."""
    # Keep writers out using a distinct ownership lock. The notification reader
    # may acquire the normal delivery lock while this RPC is in flight.
    with _core._codex_queue_pump_lock("native-owner-switch:" + thread_id):
        with _core._codex_queue_pump_lock(thread_id):
            if not native_queue_owned(thread_id):
                return
            if _inflight_file(thread_id).exists():
                raise ValueError("A Codex queue action is pending or unconfirmed; keep using the Codex queue")
        result = read_native_queue()
        if not result.get("ok"):
            raise ValueError("Cannot verify the Codex queue; queue ownership was kept")
        output = result.get("result")
        if not isinstance(output, dict) or not isinstance(output.get("data"), list):
            raise ValueError("Cannot verify the Codex queue; queue ownership was kept")
        if output["data"] or output.get("nextCursor"):
            raise ValueError("Send or remove Codex queued messages before switching to the CCC queue")
        with _core._codex_queue_pump_lock(thread_id):
            _owner_file(thread_id).unlink(missing_ok=True)


def _inflight_file(thread_id):
    return _owner_file(thread_id).with_suffix(".native-inflight")


def begin_native_queue_action(thread_id, action_id):
    with _core._codex_queue_pump_lock("native-owner-switch:" + thread_id), _core._codex_queue_pump_lock(thread_id):
        claim_native_queue(thread_id)
        target = _inflight_file(thread_id)
        receipts = json.loads(target.read_text()) if target.exists() else []
        if action_id not in receipts:
            receipts.append(action_id)
        temporary = target.with_suffix(".tmp")
        with temporary.open("w") as out:
            json.dump(receipts, out)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(target)


def finish_native_queue_action(thread_id, action_id):
    with _core._codex_queue_pump_lock(thread_id):
        target = _inflight_file(thread_id)
        if not target.exists():
            return
        receipts = [receipt for receipt in json.loads(target.read_text()) if receipt != action_id]
        if not receipts:
            target.unlink()
            return
        temporary = target.with_suffix(".tmp")
        with temporary.open("w") as out:
            json.dump(receipts, out)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(target)
