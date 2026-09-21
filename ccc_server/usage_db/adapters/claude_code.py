"""Claude Code adapter.

Store: ``~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`` (top-level session)
and ``<sessionId>/subagents/agent-*.jsonl`` (sub-agent runs, each its own row).

Verified facts this parser depends on:
* Every assistant API response is written on several lines (one per content
  block) that all carry the same ``message.id`` and identical ``usage``; usage is
  therefore counted once per ``message.id``. Resumed/forked sessions can replay
  earlier messages into a new file, so the same id may appear in two files; the
  ingester keeps a single owner per ``message.id``.
* ``usage.input_tokens`` excludes cache buckets; cache creation is
  ``cache_creation_input_tokens`` with a 5m/1h split under ``usage.cache_creation``.
* ``model == "<synthetic>"`` marks client-generated messages with no billed usage.
"""

from __future__ import annotations

import glob
import os
from typing import Iterator

from ..types import ParsedSession, SourceFile, UsageEvent
from .common import as_int, iter_json_lines, norm_ts, ts_min_max

ENGINE = "claude_code"
PROVIDER = "anthropic"


def default_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def discover(root: str) -> Iterator[SourceFile]:
    pats = [
        os.path.join(root, "*", "*.jsonl"),
        os.path.join(root, "*", "*", "subagents", "*.jsonl"),
    ]
    for pat in pats:
        for path in sorted(glob.glob(pat)):
            try:
                st = os.stat(path)
            except OSError:
                continue
            yield SourceFile(ENGINE, path, st.st_size, st.st_mtime_ns)


def _is_user_message(rec: dict) -> bool:
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") in ("text", "image") for b in content
        )
    return False


def parse(sf: SourceFile) -> list:
    path = sf.path
    stem = os.path.basename(path)[: -len(".jsonl")]
    parts = path.split(os.sep)
    is_sub = len(parts) >= 3 and parts[-2] == "subagents"
    parent = parts[-3] if is_sub else None
    sid = f"{parent}:{stem}" if is_sub else stem

    ps = ParsedSession(
        engine=ENGINE,
        source_session_id=sid,
        provider=PROVIDER,
        parent_source_session_id=parent,
        is_subagent=is_sub,
        agent_label=stem if is_sub else None,
        source_path=path,
    )
    bad = []

    def on_error(lineno, reason):
        bad.append((lineno, reason))

    seen_msgs = set()
    seen_tool_uuids = set()
    span = (None, None)
    cwds = set()
    cost_state = None
    synthetic_with_tokens = 0
    for lineno, rec in iter_json_lines(path, on_error):
        ts = norm_ts(rec.get("timestamp"))
        span = ts_min_max(span, ts)
        if rec.get("cwd"):
            cwds.add(rec["cwd"])
            if ps.working_directory is None:
                ps.working_directory = rec["cwd"]
        if rec.get("gitBranch") and ps.git_branch is None:
            ps.git_branch = rec["gitBranch"]
        if rec.get("version") and ps.source_format_version is None:
            ps.source_format_version = str(rec["version"])
        rtype = rec.get("type")
        if rtype == "assistant":
            msg = rec.get("message") or {}
            uuid = rec.get("uuid")
            content = msg.get("content")
            if isinstance(content, list) and uuid and uuid not in seen_tool_uuids:
                seen_tool_uuids.add(uuid)
                ps.tool_call_count += sum(
                    1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
                )
            model = msg.get("model")
            usage = msg.get("usage")
            mid = msg.get("id")
            if model == "<synthetic>":
                if usage and any(as_int(usage.get(k)) for k in ("input_tokens", "output_tokens")):
                    synthetic_with_tokens += 1
                continue
            if mid:
                if mid in seen_msgs:
                    continue
                seen_msgs.add(mid)
            ps.assistant_message_count += 1
            if not isinstance(usage, dict):
                ps.usage_complete = False
                continue
            cc = usage.get("cache_creation") or {}
            create = as_int(usage.get("cache_creation_input_tokens"))
            one_h = min(as_int(cc.get("ephemeral_1h_input_tokens")), create)
            details = usage.get("output_tokens_details") or {}
            ps.events.append(
                UsageEvent(
                    event_key=mid or f"{sid}:{rec.get('uuid') or lineno}",
                    ts=ts,
                    model_id=model,
                    input_tokens=as_int(usage.get("input_tokens")),
                    cache_read_tokens=as_int(usage.get("cache_read_input_tokens")),
                    cache_creation_tokens=create,
                    cache_creation_1h_tokens=one_h,
                    output_tokens=as_int(usage.get("output_tokens")),
                    reasoning_tokens=as_int(details.get("thinking_tokens")),
                )
            )
        elif rtype == "user":
            if _is_user_message(rec):
                ps.user_message_count += 1
        elif rtype == "system" and rec.get("subtype") == "compact_boundary":
            ps.compaction_count += 1
        elif rtype == "cost-state":
            cost_state = rec
    ps.started_at, ps.last_activity_at = span
    if cost_state:
        ps.metadata["cost_state"] = {
            k: cost_state.get(k) for k in ("totalCostUSD", "modelUsage", "startTime")
        }
    if len(cwds) > 1:
        ps.metadata["cwd_count"] = len(cwds)
    if synthetic_with_tokens:
        ps.warnings.append(f"{synthetic_with_tokens} synthetic message(s) reported tokens; ignored")
    if bad:
        ps.warnings.append(
            f"{len(bad)} unreadable line(s) skipped; first at line {bad[0][0]}: {bad[0][1]}"
        )
    return [ps]
