"""Codex adapter.

Store: ``~/.codex/sessions/**/rollout-*.jsonl`` and ``~/.codex/archived_sessions``.

Verified facts this parser depends on:
* One rollout file per thread; ``session_meta.payload.id`` is the stable id.
  Sub-agent threads carry ``parent_thread_id`` / ``source.subagent``.
* Usage arrives as ``event_msg`` / ``token_count`` events. ``last_token_usage`` is
  that API call's usage; ``total_token_usage`` is a running total. The running
  total is *not* reliable (it can drop mid-file), so usage is the sum of
  ``last_token_usage`` over distinct events. Where the running total is monotonic
  this sum equals its final value in 1,621 of 1,633 sampled files.
* ``input_tokens`` INCLUDES ``cached_input_tokens`` (and ``cache_write_input_tokens``
  when present), and ``reasoning_output_tokens`` is a subset of ``output_tokens``;
  fresh input is therefore ``input - cached - cache_write``.
* The model is per turn (``turn_context.model``) and can change mid-session, so
  each event takes the model of the most recent turn.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Iterator

from ..types import ParsedSession, SourceFile, UsageEvent
from .common import as_int, iter_json_lines, norm_ts, ts_min_max

ENGINE = "codex"

_TOOL_ITEMS = {
    "function_call",
    "custom_tool_call",
    "local_shell_call",
    "tool_search_call",
    "web_search_call",
}


def default_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".codex")


def discover(root: str) -> Iterator[SourceFile]:
    for sub, archived in (("sessions", False), ("archived_sessions", True)):
        base = os.path.join(root, sub)
        for path in sorted(glob.glob(os.path.join(base, "**", "rollout-*.jsonl"), recursive=True)):
            try:
                st = os.stat(path)
            except OSError:
                continue
            yield SourceFile(ENGINE, path, st.st_size, st.st_mtime_ns, {"archived": archived})


def parse(sf: SourceFile) -> list:
    bad = []

    def on_error(lineno, reason):
        bad.append((lineno, reason))

    ps = None
    span = (None, None)
    model = None
    first_model = None
    seen = set()
    prev_total = 0
    monotonic = True
    final_total = 0
    clamped = False
    for lineno, rec in iter_json_lines(sf.path, on_error):
        ts = norm_ts(rec.get("timestamp"))
        span = ts_min_max(span, ts)
        rtype = rec.get("type")
        p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        if rtype == "session_meta" and ps is None:
            src = p.get("source")
            parent = p.get("parent_thread_id")
            if isinstance(src, dict) and isinstance(src.get("subagent"), dict):
                spawn = src["subagent"].get("thread_spawn") or {}
                parent = parent or spawn.get("parent_thread_id")
            is_sub = bool(parent) or (isinstance(src, dict) and "subagent" in src)
            git = p.get("git") if isinstance(p.get("git"), dict) else {}
            ps = ParsedSession(
                engine=ENGINE,
                source_session_id=str(p.get("id") or os.path.basename(sf.path)),
                provider=p.get("model_provider"),
                parent_source_session_id=parent,
                is_subagent=is_sub,
                agent_label=p.get("agent_nickname") or p.get("agent_role"),
                working_directory=p.get("cwd"),
                git_branch=git.get("branch"),
                started_at=norm_ts(p.get("timestamp")),
                source_format_version=p.get("cli_version"),
                source_path=sf.path,
                status="archived" if sf.hint.get("archived") else "active",
            )
            for k in ("originator", "thread_source", "forked_from_id", "agent_role"):
                if p.get(k) is not None:
                    ps.metadata[k] = p[k]
            src_kind = src if isinstance(src, str) else ("subagent" if is_sub else None)
            if src_kind:
                ps.metadata["source"] = src_kind
            continue
        if ps is None:
            continue
        if rtype == "turn_context":
            m = p.get("model")
            if m:
                model = m
                first_model = first_model or m
        elif rtype == "compacted":
            ps.compaction_count += 1
        elif rtype == "response_item":
            it = p.get("type")
            if it == "message" and p.get("role") == "assistant":
                ps.assistant_message_count += 1
            elif it in _TOOL_ITEMS:
                ps.tool_call_count += 1
        elif rtype == "event_msg":
            et = p.get("type")
            if et == "user_message":
                ps.user_message_count += 1
            elif et == "token_count" and isinstance(p.get("info"), dict):
                info = p["info"]
                last = info.get("last_token_usage") or {}
                total = info.get("total_token_usage") or {}
                sig = (json.dumps(total, sort_keys=True), json.dumps(last, sort_keys=True))
                cur_total = as_int(total.get("total_tokens"))
                if cur_total < prev_total:
                    monotonic = False
                prev_total = cur_total
                final_total = cur_total
                if sig in seen:
                    continue  # the same event re-emitted
                seen.add(sig)
                cached = as_int(last.get("cached_input_tokens"))
                write = as_int(last.get("cache_write_input_tokens"))
                fresh = as_int(last.get("input_tokens")) - cached - write
                if fresh < 0:
                    clamped = True
                    fresh = 0
                ps.events.append(
                    UsageEvent(
                        event_key=f"{ps.source_session_id}:{len(ps.events)}",
                        ts=ts,
                        model_id=model or first_model,
                        input_tokens=fresh,
                        cache_read_tokens=cached,
                        cache_creation_tokens=write,
                        output_tokens=as_int(last.get("output_tokens")),
                        reasoning_tokens=as_int(last.get("reasoning_output_tokens")),
                    )
                )
    if ps is None:
        return []
    ps.started_at = ps.started_at or span[0]
    ps.last_activity_at = span[1]
    if clamped:
        ps.warnings.append("input_tokens < cached+cache_write on some event; fresh input clamped to 0")
    if not ps.events:
        ps.usage_complete = False
        ps.warnings.append("no token_count events; usage unknown")
    elif not monotonic:
        ps.usage_complete = False
        ps.warnings.append(
            "source running total is not monotonic; usage is the per-call sum and is unreconciled"
        )
    else:
        summed = sum(
            e.input_tokens + e.cache_read_tokens + e.cache_creation_tokens + e.output_tokens
            for e in ps.events
        )
        if summed != final_total:
            ps.warnings.append(
                f"per-call sum ({summed}) differs from source running total ({final_total})"
                + ("; forked thread inherits its parent's total" if ps.metadata.get("forked_from_id") else "")
            )
    # Calls logged before the thread's first turn_context carry no model; they belong to
    # the model the thread then starts with, so price them at that rather than as unknown.
    early = [e for e in ps.events if e.model_id is None]
    if early and first_model:
        for e in early:
            e.model_id = first_model
        ps.warnings.append(f"{len(early)} usage event(s) before the first turn assumed to use {first_model}")
    elif early:
        ps.warnings.append("some usage events have no model")
    if bad:
        ps.warnings.append(
            f"{len(bad)} unreadable line(s) skipped; first at line {bad[0][0]}: {bad[0][1]}"
        )
    return [ps]
