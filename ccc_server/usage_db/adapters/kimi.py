"""Kimi adapter.

Store: ``~/.kimi-code/sessions/<wd>/session_<uuid>/`` with ``state.json`` and one
event-sourced ``agents/<agent>/wire.jsonl`` per agent (``main`` plus ``agent-N``
sub-agents, each its own row).

Verified facts this parser depends on:
* ``usage.record`` events carry ``usage = {inputOther, output, inputCacheRead,
  inputCacheCreation}`` and the model; ``inputOther`` is fresh input. ``step.end``
  loop events repeat the same usage and are NOT counted.
* ``usageScope: "turn"`` records are per-call usage. The rare ``"session"`` record
  is a separate summary call made at compaction (its input is roughly the
  pre-compaction context), not a cumulative total, so it is added as its own event.
* Timestamps are epoch milliseconds.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Iterator

from ..types import ParsedSession, SourceFile, UsageEvent
from .common import as_int, iso_from_ms, iter_json_lines, ts_min_max

ENGINE = "kimi"
PROVIDER = "kimi"


def default_root() -> str:
    raw = os.environ.get("KIMI_CODE_HOME", "").strip()
    return os.path.expanduser(raw) if raw else os.path.join(os.path.expanduser("~"), ".kimi-code")


def discover(root: str) -> Iterator[SourceFile]:
    pat = os.path.join(root, "sessions", "*", "session_*", "agents", "*", "wire.jsonl")
    for path in sorted(glob.glob(pat)):
        try:
            st = os.stat(path)
        except OSError:
            continue
        # state.json holds status/updatedAt and changes without touching the wire
        # file, so its mtime is part of this file's identity.
        mtime = st.st_mtime_ns
        try:
            state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(path))), "state.json")
            mtime = max(mtime, os.stat(state_path).st_mtime_ns)
        except OSError:
            pass
        yield SourceFile(ENGINE, path, st.st_size, mtime)


def skipped(root: str) -> Iterator[tuple]:
    """Session directories that exist but have no transcript to read usage from."""
    for sdir in sorted(glob.glob(os.path.join(root, "sessions", "*", "session_*"))):
        if not glob.glob(os.path.join(sdir, "agents", "*", "wire.jsonl")):
            yield sdir, "no agent transcript (wire.jsonl) in session directory"


def _state(session_dir: str) -> dict:
    try:
        with open(os.path.join(session_dir, "state.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse(sf: SourceFile) -> list:
    agent_dir = os.path.dirname(sf.path)
    agent = os.path.basename(agent_dir)
    session_dir = os.path.dirname(os.path.dirname(agent_dir))
    parent_sid = os.path.basename(session_dir)
    state = _state(session_dir)
    is_sub = agent != "main"
    ps = ParsedSession(
        engine=ENGINE,
        source_session_id=f"{parent_sid}:{agent}" if is_sub else parent_sid,
        provider=PROVIDER,
        parent_source_session_id=parent_sid if is_sub else None,
        is_subagent=is_sub,
        agent_label=agent if is_sub else None,
        working_directory=state.get("cwd"),
        source_path=sf.path,
        status="archived" if state.get("archived") else "active",
    )
    bad = []

    def on_error(lineno, reason):
        bad.append((lineno, reason))

    span = (None, None)
    session_scope = 0
    for lineno, rec in iter_json_lines(sf.path, on_error):
        t = rec.get("time")
        if isinstance(t, (int, float)):
            span = ts_min_max(span, iso_from_ms(t))
        rtype = rec.get("type")
        if rtype == "metadata":
            if rec.get("protocol_version") is not None:
                ps.source_format_version = str(rec["protocol_version"])
            if isinstance(rec.get("created_at"), (int, float)):
                span = ts_min_max(span, iso_from_ms(rec["created_at"]))
        elif rtype in ("turn.prompt", "turn.steer"):
            ps.user_message_count += 1
        elif rtype == "context.apply_compaction":
            ps.compaction_count += 1
        elif rtype == "context.append_loop_event":
            ev = rec.get("event") or {}
            et = ev.get("type")
            if et == "tool.call":
                ps.tool_call_count += 1
            elif et == "step.end":
                ps.assistant_message_count += 1
        elif rtype == "usage.record":
            u = rec.get("usage")
            if not isinstance(u, dict):
                ps.usage_complete = False
                continue
            scope = rec.get("usageScope") or "turn"
            if scope != "turn":
                session_scope += 1
            ps.events.append(
                UsageEvent(
                    event_key=f"{ps.source_session_id}:{lineno}",
                    ts=iso_from_ms(t),
                    model_id=rec.get("model"),
                    input_tokens=as_int(u.get("inputOther")),
                    cache_read_tokens=as_int(u.get("inputCacheRead")),
                    cache_creation_tokens=as_int(u.get("inputCacheCreation")),
                    output_tokens=as_int(u.get("output")),
                    scope="call" if scope == "turn" else scope,
                )
            )
    ps.started_at, ps.last_activity_at = span
    if not is_sub:
        # state.updatedAt is deliberately ignored: it is rewritten by housekeeping
        # (a bulk touch moved many sessions to one date) and is not activity.
        created = iso_from_ms(state.get("createdAt"))
        if created:
            ps.started_at = min(filter(None, [ps.started_at, created]))
        if state.get("updatedAt") is not None:
            ps.metadata["state_updated_at"] = iso_from_ms(state.get("updatedAt"))
    if session_scope:
        ps.metadata["session_scope_usage_records"] = session_scope
    if not ps.events:
        ps.usage_complete = False
        ps.warnings.append("no usage.record events; usage unknown")
    if bad:
        ps.warnings.append(
            f"{len(bad)} unreadable line(s) skipped; first at line {bad[0][0]}: {bad[0][1]}"
        )
    return [ps]
