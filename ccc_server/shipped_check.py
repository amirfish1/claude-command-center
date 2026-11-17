"""Pre-spawn 'already shipped?' check (MEMO-FIX-6).

WARN-only, never blocking: before a new session's prompt is finalized, ask
ship_graph.is_shipped() whether the spawn's goal text looks like work that
already shipped (a matching git commit and/or a closed WatchTower ticket).
If confidence clears the bar, the caller gets a one-line heads-up prepended
to the prompt and a machine-readable field on the spawn response -- the
spawn itself is never delayed past SHIPPED_CHECK_TIMEOUT_S and never
blocked.

Queue-drain workers use WatchTower claim-time related-work lookup instead.
Disable with CCC_DISABLE_SHIPPED_CHECK=1.
"""
from __future__ import annotations

import os
import re
import threading

SHIPPED_CHECK_TIMEOUT_S = 1.5
SHIPPED_CHECK_CONFIDENCE_MIN = 0.8


def is_queue_worker_spawn(prompt: str) -> bool:
    """Recognize the drain instruction, even when a spawn has a custom name.

    A worker's queue is known at spawn, but its actual task is chosen at claim
    time. Role labels and mentions of WatchTower alone are not drain jobs.
    """
    return bool(re.match(
        r"drain the [a-z0-9_-]+ watchtower queue\b",
        (prompt or "").strip(), re.IGNORECASE,
    ))


def _shipped_check_disabled() -> bool:
    return os.environ.get("CCC_DISABLE_SHIPPED_CHECK", "").strip().lower() in (
        "1", "true", "yes",
    )


def _is_shipped_capped(topic: str, timeout_s: float = SHIPPED_CHECK_TIMEOUT_S):
    """Run ship_graph.is_shipped(topic) on a worker thread and abandon it
    (daemon thread, never joined again) rather than block the caller past
    `timeout_s`. is_shipped() is a read-only local sqlite lookup, so an
    abandoned call has no side effect worth cancelling for.
    """
    from ccc_server import ship_graph as _ship_graph

    box: dict = {}

    def _run():
        try:
            box["value"] = _ship_graph.is_shipped(topic)
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive() or "error" in box:
        return None
    return box.get("value")


def check_shipped_for_spawn(goal: str, timeout_s: float = SHIPPED_CHECK_TIMEOUT_S):
    """Return a warning dict if `goal` looks already shipped, else None.

    Never raises and never blocks past `timeout_s`.
    """
    goal = (goal or "").strip()
    if not goal or _shipped_check_disabled():
        return None
    result = _is_shipped_capped(goal, timeout_s)
    if not result or not result.get("shipped"):
        return None
    if result.get("confidence", 0) < SHIPPED_CHECK_CONFIDENCE_MIN:
        return None
    evidence = result.get("evidence") or []
    if not evidence:
        return None
    top = evidence[0]
    return {
        "shipped": True,
        "confidence": result["confidence"],
        "repo": top.get("repo", ""),
        "commit": top.get("commit", ""),
        "subject": top.get("subject", ""),
        "tickets": result.get("tickets") or [],
    }


def shipped_warning_line(info: dict) -> str:
    """One-line heads-up meant to be prepended to a spawned prompt."""
    sha = (info.get("commit") or "")[:8]
    repo = info.get("repo") or "?"
    subject = info.get("subject") or "?"
    confidence = info.get("confidence", 0)
    return (
        f"Heads-up: this may already be shipped: {subject} ({repo} {sha}), "
        f"confidence {confidence:.2f}. Verify before rebuilding.\n\n"
    )
