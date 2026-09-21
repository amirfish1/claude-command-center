"""Plain data carriers shared by the adapters and the ingester."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UsageEvent:
    """Tokens billed for one model call, already normalized.

    ``input_tokens`` is *fresh* (uncached) input only; cache reads and writes are
    separate buckets, so ``input + cache_read + cache_creation + output`` is the
    total regardless of how the source engine reports its counters.
    ``cache_creation_1h_tokens`` is the subset of ``cache_creation_tokens`` written
    with the 1-hour TTL (Claude only; other engines leave it 0).
    ``reasoning_tokens`` is informational: it is already inside ``output_tokens``.
    """

    event_key: str
    ts: Optional[str]  # ISO-8601 UTC
    model_id: Optional[str]
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    scope: str = "call"  # 'call' | 'compaction' (Kimi session-scope summary calls)


@dataclass
class ParsedSession:
    engine: str
    source_session_id: str
    provider: Optional[str] = None
    parent_source_session_id: Optional[str] = None
    is_subagent: bool = False
    agent_label: Optional[str] = None
    working_directory: Optional[str] = None
    git_branch: Optional[str] = None
    started_at: Optional[str] = None
    last_activity_at: Optional[str] = None
    status: str = "unknown"
    user_message_count: int = 0
    assistant_message_count: int = 0
    tool_call_count: int = 0
    compaction_count: int = 0
    source_path: str = ""
    source_format_version: Optional[str] = None
    events: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    usage_complete: bool = True


@dataclass
class SourceFile:
    """A file that (fully or partly) backs one or more sessions."""

    engine: str
    path: str
    size: int
    mtime_ns: int
    # Adapter-private hints (e.g. archived flag, session/agent ids from the path).
    hint: dict = field(default_factory=dict)
