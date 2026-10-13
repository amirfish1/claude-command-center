"""When is a session's open tool call really a permission prompt?

A transcript `tool_use` with no `tool_result` yet only means a tool is running.
A long build, test run or sub-agent looks identical to a prompt waiting for the
human, and it is routinely a session whose permission prompts are bypassed, so
no prompt can exist at all. Claude Code emits a distinct `Notification` hook
event when it really asks for permission; CCC stores it as the
`needs_approval` marker. For Claude sessions that marker is the evidence, and
a pending tool without it is "working". Engines without that hook keep the
older tool-pending heuristic (they have no better signal).
"""

from __future__ import annotations


def pending_tool_is_approval(row) -> bool:
    """True when `row`'s pending tool should be reported as an approval."""
    if row.get("needs_approval"):
        return True
    engine = str(row.get("engine") or row.get("source") or "claude").lower()
    if engine in ("claude", "interactive", "claude_code", "headless", ""):
        return False
    return True
