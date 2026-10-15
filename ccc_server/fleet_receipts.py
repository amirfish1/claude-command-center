"""Durable receipts for confirmed Fleet steps, independent of route-cache eviction.

Reserving a receipt is atomic across threads and CCC processes. If execution
stops before its result is saved, fail closed: an external effect and a local
file write cannot be committed as one transaction.
"""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path


def request_id(action):
    """Stable across coordinator restarts; distinct for each reviewed plan."""
    identity = [action.get(key) for key in ("coordinator_node", "plan_id", "id")]
    if not all(isinstance(value, str) and value for value in identity):
        return None  # compatibility for standalone, unplanned /api/fleet/step
    return "fleet-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def execute(directory, action, apply):
    """Replay a recorded outcome or reserve and apply one confirmed step.

    No receipt is ever expired automatically: an old plan can be resumed long
    after unrelated traffic has evicted its peer route-cache entry. Only the
    intent hash and outcome are stored, not the request's evidence/prompt.
    """
    key = request_id(action)
    if key is None:
        return apply(action)
    intent = {k: action.get(k) for k in (
        "kind", "repo_identity", "node_id", "target", "evidence")}
    fingerprint = hashlib.sha256(json.dumps(
        intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt_path = directory / (key + ".json")
    uncertain = {
        "ok": False, "error": "execution_uncertain", "request_id": key,
        "detail": "This step is still running or stopped before saving its outcome. "
                  "It will not be repeated. Inspect the target before creating a new plan.",
    }
    # O_EXCL is the cross-process claim, including on platforms where flock
    # is unavailable. Never delete an incomplete claim to permit a retry.
    try:
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return uncertain
        if not isinstance(receipt, dict):
            return uncertain
        if receipt.get("fingerprint") != fingerprint:
            return {"ok": False, "error": "idempotency_conflict", "request_id": key,
                    "detail": "This confirmed step's intent changed. Create a new plan."}
        if receipt.get("status") == "completed" and isinstance(receipt.get("result"), dict):
            return {**receipt["result"], "replayed": True}
        return uncertain
    receipt = {"fingerprint": fingerprint, "status": "running", "started_at": time.time()}
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream)
        stream.flush()
        os.fsync(stream.fileno())
    result = apply(action)
    receipt.update(status="completed", result=result, completed_at=time.time())
    temporary = receipt_path.with_suffix(".tmp-" + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, receipt_path)
    finally:
        temporary.unlink(missing_ok=True)
    return result
