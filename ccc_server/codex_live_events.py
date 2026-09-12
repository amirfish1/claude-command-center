"""Map a live CODEX_CONVERSATIONS snapshot into rollout-shaped transcript events.

Slice 1 of the codex-single-renderer-merge plan (CCC-private-docs). Pure
functions: turn a `CodexConversationStore.snapshot(thread_id)` reading (the
native app-server's own turns/items, plan, diff) into the same event dicts
`ccc_server/codex_parse.py` produces for rollout rows, so the shared
transcript renderer can draw a live overlay the same way it draws rollout
history. No file/network I/O here; the caller (`codex_client_dispatch`'s
`live-transcript` action) owns fetching the snapshot and requests.

Each mapped event additionally carries:
  - `live_key` = "<turn_id>:<item_id>" (the upsert key; there is no `line`,
    since these rows have not been written to the rollout yet)
  - `provisional`: True
  - `turn_id`

Slice-1 unknowns, verified against real rollouts before this was written
(see the plan doc for the full evidence trail):

  - Rollout turn_id: present. Both on `turn_context.payload.turn_id` (once
    per turn) and directly on `event_msg/item_completed.payload.turn_id`
    (every item event). `ccc_server/codex_parse.py` now threads the
    turn_context-derived value onto every event it emits via
    `_apply_codex_turn_meta`.
  - App-server item id vs rollout call_id: they do NOT match. A single tool
    invocation showed THREE distinct ids across the two data sources: the
    model's `custom_tool_call.id` ("ctc_..."), that same call's `call_id`
    ("call_..." - what `codex_parse.py` uses as `tool_use_id`), and the
    app-server item id surfaced later via `item_completed`
    ("exec-<uuid>"). This held for CommandExecution, FileChange and
    McpToolCall items alike. Slice 2's reconciler must therefore dedupe a
    live tool row against its eventual rollout row by turn_id + ordinal (or
    text), never by id equality - matching the plan's documented risk.
"""
from __future__ import annotations

from ccc_server import core as _core

_MAX_TURNS = 4
_MAX_ITEMS_PER_TURN = 32
_MAX_TEXT = 4000
_MAX_DETAIL = 200
_MAX_TOOL_OUTPUT = 800
_MAX_COMMAND = 12000


def _text(value, limit=_MAX_TEXT):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) > limit:
        return value[: limit] + "..."
    return value


def _item_text(item):
    """Best-effort plain text for a userMessage/agentMessage item.

    Field names mirror `ccc_server/codex_conversation.py`'s `_delta` (which
    accumulates streamed text under item["text"]) plus the native pane's own
    defensive fallback chain (`static/codex-client.js`'s `textFrom`:
    item.text/content/message).
    """
    if not isinstance(item, dict):
        return ""
    for key in ("text", "message"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = item.get("content")
    if isinstance(content, list):
        parts = [
            c.get("text") for c in content
            if isinstance(c, dict) and isinstance(c.get("text"), str)
        ]
        joined = "\n\n".join(p.strip() for p in parts if p.strip()).strip()
        if joined:
            return joined
    if isinstance(content, str) and content.strip():
        return content.strip()
    return ""


def _reasoning_text(item):
    """Reasoning items accumulate under "summary" or "content" (a list of
    string chunks, one per summary/content index - see `_delta`'s
    `summaryIndex`/`contentIndex` handling)."""
    if not isinstance(item, dict):
        return ""
    for key in ("summary", "content"):
        value = item.get(key)
        if isinstance(value, list):
            parts = [p for p in value if isinstance(p, str) and p.strip()]
            joined = "\n\n".join(p.strip() for p in parts).strip()
            if joined:
                return joined
        elif isinstance(value, str) and value.strip():
            return value.strip()
    return _item_text(item)


def _command_text(item):
    command = item.get("command") or item.get("cmd") or item.get("input")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command if part)
    return command if isinstance(command, str) else ""


def _tool_output_text(item):
    for key in ("aggregatedOutput", "output", "result"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            content = value.get("content")
            if isinstance(content, list):
                parts = [
                    c.get("text") for c in content
                    if isinstance(c, dict) and isinstance(c.get("text"), str)
                ]
                joined = "\n\n".join(p.strip() for p in parts if p.strip()).strip()
                if joined:
                    return joined
    return ""


def _plan_entries(plan):
    """Same shape normalization `codex_parse.py` uses for update_plan args:
    a list of {"content", "status"} dicts."""
    steps = plan
    if isinstance(plan, dict):
        steps = plan.get("steps") or plan.get("plan")
    if not isinstance(steps, list):
        return []
    entries = []
    for item in steps:
        if not isinstance(item, dict):
            continue
        step = str(item.get("step") or item.get("content") or "").strip()
        if not step:
            continue
        status = item.get("status")
        if status is None:
            status = "completed" if item.get("completed") else "pending"
        entries.append({"content": step[:300], "status": str(status)})
    return entries


def _map_tool_item(turn_id, item, name, detail, *, command=None):
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        return []
    live_key = f"{turn_id}:{item_id}"
    block = {
        "kind": "tool_use",
        "name": name,
        "detail": _text(detail, _MAX_DETAIL),
        "id": item_id,
    }
    if command:
        block["command"] = _text(command, _MAX_COMMAND)
    events = [{
        "type": "assistant",
        "message_id": f"codex-live-tool-{item_id}",
        "blocks": [block],
        "turn_id": turn_id,
        "live_key": live_key,
        "provisional": True,
    }]
    status = str(item.get("status") or "")
    output = _tool_output_text(item)
    if status in ("completed", "failed") and output:
        events.append({
            "type": "tool_result",
            "text": _text(output, _MAX_TOOL_OUTPUT),
            "tool_use_id": item_id,
            "is_error": status == "failed",
            "turn_id": turn_id,
            "live_key": f"{live_key}:result",
            "provisional": True,
        })
    return events


def _map_item(turn_id, item):
    """One app-server item -> zero or more rollout-shaped events."""
    if not isinstance(item, dict):
        return []
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        return []
    itype = str(item.get("type") or "")
    live_key = f"{turn_id}:{item_id}"

    if itype == "userMessage":
        text = _item_text(item)
        if not text:
            return []
        return [{
            "type": "user_text", "text": text, "images": [],
            "turn_id": turn_id, "live_key": live_key, "provisional": True,
        }]
    if itype == "agentMessage":
        text = _item_text(item)
        if not text:
            return []
        return [{
            "type": "assistant", "message_id": f"codex-live-{item_id}",
            "blocks": [{"kind": "text", "text": text}],
            "turn_id": turn_id, "live_key": live_key, "provisional": True,
        }]
    if itype == "reasoning":
        text = _reasoning_text(item)
        if not text:
            return []
        return [{
            "type": "assistant", "message_id": f"codex-live-{item_id}",
            "blocks": [{"kind": "thinking", "text": text}],
            "turn_id": turn_id, "live_key": live_key, "provisional": True,
        }]
    if itype == "commandExecution":
        command = _command_text(item)
        detail = _core._shell_command_activity_label(command) if command else ""
        return _map_tool_item(turn_id, item, "Bash", detail or "Bash", command=command)
    if itype == "fileChange":
        changes = item.get("changes") or item.get("files") or []
        paths = []
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    path = str(change.get("path") or change.get("name") or "").strip()
                    if path:
                        paths.append(path)
        if paths:
            detail = paths[0] + (f" (+{len(paths) - 1} more)" if len(paths) > 1 else "")
        else:
            detail = "apply_patch"
        return _map_tool_item(turn_id, item, "apply_patch", detail)
    if itype == "mcpToolCall":
        tool = str(item.get("tool") or item.get("name") or "tool")
        name = _core._codex_tool_name(tool)
        server_name = item.get("server")
        detail = f"{server_name}.{name}" if server_name else name
        return _map_tool_item(turn_id, item, name, detail)
    if itype == "webSearch":
        query = item.get("query") or item.get("text") or _item_text(item)
        return _map_tool_item(turn_id, item, "web_search", query or "web_search")
    return []


def live_turns_from_snapshot(snapshot, *, max_turns=_MAX_TURNS, max_items=_MAX_ITEMS_PER_TURN):
    """Map a `CodexConversationStore.snapshot()` reading into
    `[{turn_id, status, events}, ...]`, oldest turn first, bounded.

    `snapshot` is the dict `CODEX_CONVERSATIONS.snapshot(thread_id)` returns:
    `{"ok", "generation", "cursor", "connected", "thread", "truncated"}`. The
    store itself already bounds turns/items/bytes (see
    `CodexConversationStore.__init__`); this adds an independent, smaller
    bound on what actually goes out over the wire for the overlay, since a
    live overlay only needs the turn(s) still running or not yet flushed to
    the rollout.
    """
    thread = snapshot.get("thread") if isinstance(snapshot, dict) else None
    if not isinstance(thread, dict):
        return []
    raw_turns = thread.get("turns")
    if not isinstance(raw_turns, list):
        return []
    out = []
    for turn in raw_turns[-max_turns:]:
        if not isinstance(turn, dict):
            continue
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            continue
        events = []
        items = turn.get("items")
        if isinstance(items, list):
            for item in items[-max_items:]:
                events.extend(_map_item(turn_id, item))
        plan_entries = _plan_entries(turn.get("plan"))
        if plan_entries:
            events.append({
                "type": "assistant", "message_id": f"codex-live-plan-{turn_id}",
                "blocks": [{"kind": "plan", "entries": plan_entries}],
                "turn_id": turn_id, "live_key": f"{turn_id}:plan", "provisional": True,
            })
        out.append({"turn_id": turn_id, "status": turn.get("status") or "", "events": events})
    return out
