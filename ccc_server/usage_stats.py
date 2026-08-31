"""Extracted from server.py (originally lines 41765-45087).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import json
import os
import sqlite3
import stat
import threading
import time
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Global usage stats — aggregated across every transcript under PROJECTS_ROOT.
# Powers the /api/stats endpoint and the "Stats" overlay in the UI.
#
# Cold-scanning hundreds of JSONL files on every request is too slow, so we
# memoise per-file aggregates keyed by (path, mtime, size). Subsequent calls
# only re-read transcripts that were appended to or replaced.
# ---------------------------------------------------------------------------

_STATS_FILE_CACHE = {}        # str(path) -> {"mtime", "size", "agg"}
_STATS_CACHE_LOCK = threading.Lock()
_STATS_FILE_CACHE_DIRTY = False
_STATS_FILE_CACHE_SCHEMA = 1
_STATS_FILE_CACHE_FILE = (
    Path.home() / ".claude" / "command-center" / "stats_file_cache.json"
)


def _load_stats_file_cache():
    """Load per-transcript stats aggregates from disk on startup.

    Without this the in-memory cache is empty after every restart, so the
    first /api/stats (Stats overlay) cold-parses every transcript (~1200 files
    ≈ 40s) AND that CPU-bound parse holds the GIL, freezing the whole server
    for its duration. Persisting means a restart only re-parses the handful of
    transcripts that changed; entries are (mtime,size)-keyed so stale ones
    self-invalidate in _stats_get_file_agg.
    """
    if not _core._STATS_FILE_CACHE_FILE.is_file():
        return
    try:
        with _core._STATS_FILE_CACHE_FILE.open("r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict) or data.get("schema_version") != _STATS_FILE_CACHE_SCHEMA:
        return
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return
    keep = {
        k: v for k, v in entries.items()
        if isinstance(v, dict) and "mtime" in v and "size" in v and "agg" in v
    }
    with _STATS_CACHE_LOCK:
        _core._STATS_FILE_CACHE.update(keep)


def _save_stats_file_cache():
    """Atomic write of _STATS_FILE_CACHE when dirty. Must be called OUTSIDE
    _STATS_CACHE_LOCK (it acquires the lock; compute_global_stats holds it for
    the whole build, so call this after that returns)."""
    global _STATS_FILE_CACHE_DIRTY
    with _STATS_CACHE_LOCK:
        if not _STATS_FILE_CACHE_DIRTY:
            return
        snapshot = {
            "schema_version": _STATS_FILE_CACHE_SCHEMA,
            "entries": dict(_core._STATS_FILE_CACHE),
        }
        _STATS_FILE_CACHE_DIRTY = False
    try:
        _core._STATS_FILE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _core._STATS_FILE_CACHE_FILE.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(snapshot, f)
        tmp.replace(_core._STATS_FILE_CACHE_FILE)
    except OSError as e:
        with _STATS_CACHE_LOCK:
            _STATS_FILE_CACHE_DIRTY = True
        print(f"  [stats-file-cache] save failed: {e}")

# Token equivalent of "The Lord of the Rings" — ~576k words × ~1.25 tokens/word.
# Used for the whimsical comparison line at the bottom of the stats overlay.
# Off by ±20% is fine; the line is for fun, not accuracy.
_STATS_LOTR_TOKENS = 720_000

# Models that show up in transcripts but aren't real assistant runs we want
# users to see in the "Favorite model" tile or Models tab.
_STATS_MODEL_BLOCKLIST = {"<synthetic>"}


def _stats_parse_ts(ts_str):
    """Parse an ISO-8601 timestamp from the JSONL into an aware datetime in
    the server's local timezone. Returns None on failure."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        if ts_str.endswith("Z"):
            dt = datetime.fromisoformat(ts_str[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(ts_str)
    except ValueError:
        return None
    return dt.astimezone()


def _stats_aggregate_file(path):
    """Walk a single transcript and return its date-bucketed aggregate.

    Shape:
      {
        "session_id": str|None,
        "by_date": {
          "YYYY-MM-DD": {
            "messages": int,            # user + assistant turns (no sidechain)
            "in_tokens": int,           # input_tokens (no cache)
            "cache_tokens": int,        # cache_creation + cache_read
            "out_tokens": int,
            "hours": {"0".."23": int},  # message count per hour-of-day (local)
            "models": {model: int},     # assistant turns per model
          }, ...
        },
      }
    """
    agg = {"session_id": None, "by_date": {}}
    # Dedupe assistant usage by `message.id` for the same reason as
    # extract_session_usage — resumes replay each API response under
    # fresh event uuids but the same message.id. See issue #60.
    seen_message_ids = set()
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype not in ("user", "assistant"):
                    continue
                if ev.get("isSidechain"):
                    continue
                local = _stats_parse_ts(ev.get("timestamp"))
                if local is None:
                    continue
                if agg["session_id"] is None and ev.get("sessionId"):
                    agg["session_id"] = ev["sessionId"]
                date_str = local.strftime("%Y-%m-%d")
                hour_str = str(local.hour)
                day = agg["by_date"].setdefault(date_str, {
                    "messages": 0,
                    "in_tokens": 0,
                    "cache_tokens": 0,
                    "out_tokens": 0,
                    "hours": {},
                    "models": {},
                })
                day["messages"] += 1
                day["hours"][hour_str] = day["hours"].get(hour_str, 0) + 1
                if etype == "assistant":
                    msg = _core._safe_parse_message(ev.get("message", {}))
                    model = msg.get("model")
                    if model and model not in _STATS_MODEL_BLOCKLIST:
                        day["models"][model] = day["models"].get(model, 0) + 1
                    mid = msg.get("id") if isinstance(msg.get("id"), str) else None
                    if mid:
                        if mid in seen_message_ids:
                            continue
                        seen_message_ids.add(mid)
                    u = msg.get("usage")
                    if isinstance(u, dict):
                        in_tok = u.get("input_tokens") or 0
                        cache_tok = ((u.get("cache_creation_input_tokens") or 0)
                                     + (u.get("cache_read_input_tokens") or 0))
                        out_tok = u.get("output_tokens") or 0
                        if isinstance(in_tok, int):
                            day["in_tokens"] += in_tok
                        if isinstance(cache_tok, int):
                            day["cache_tokens"] += cache_tok
                        if isinstance(out_tok, int):
                            day["out_tokens"] += out_tok
    except OSError:
        pass
    return agg


def _stats_get_file_agg(path):
    """Return cached aggregate for `path`, recomputing if mtime/size changed."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = str(path)
    cached = _core._STATS_FILE_CACHE.get(key)
    if cached and cached["mtime"] == st.st_mtime and cached["size"] == st.st_size:
        return cached["agg"]
    agg = _core._stats_aggregate_file(path)
    _core._STATS_FILE_CACHE[key] = {"mtime": st.st_mtime, "size": st.st_size, "agg": agg}
    global _STATS_FILE_CACHE_DIRTY
    _STATS_FILE_CACHE_DIRTY = True
    return agg


def _stats_pretty_model(m):
    """`claude-opus-4-7` → `Opus 4.7`. Best-effort, falls back to the raw id."""
    if not m:
        return "Unknown"
    s = m.lower().replace("[1m]", "").strip()
    if s.startswith("claude-"):
        s = s[len("claude-"):]
    parts = [p for p in s.split("-") if p]
    if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
        return f"{parts[0].capitalize()} {parts[1]}.{parts[2]}"
    if len(parts) >= 2 and parts[1].isdigit():
        return f"{parts[0].capitalize()} {parts[1]}"
    return parts[0].capitalize() if parts else m


def _stats_compute_streaks(active_dates, today):
    """Return (current_streak_days, longest_streak_days) over a date set.

    `current_streak` only counts if the user was active today OR yesterday —
    a gap of >1 day from today resets it to 0."""
    if not active_dates:
        return 0, 0
    dates = sorted(active_dates)
    longest = 1
    run = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            run += 1
            if run > longest:
                longest = run
        else:
            run = 1
    last = dates[-1]
    current = 0
    if last == today or last == today - timedelta(days=1):
        current = 1
        for i in range(len(dates) - 2, -1, -1):
            if (dates[i + 1] - dates[i]).days == 1:
                current += 1
            else:
                break
    return current, longest


def _stats_pick_comparison(total_tokens):
    """Whimsical line for the bottom of the stats overlay.

    Always compares to The Lord of the Rings (matches the design mock).
    Hidden when the user hasn't yet exceeded one LotR — saying
    "you've used 0.3× a LotR" lands flatter than no line at all."""
    if total_tokens <= 0:
        return None
    mult = total_tokens / _STATS_LOTR_TOKENS
    if mult < 1.5:
        return None
    return f"You've used ~{int(round(mult))}× more tokens than The Lord of the Rings."


def compute_global_stats(days=None):
    """Aggregate transcript stats across ~/.claude/projects.

    days=None → "All". days=7 / 30 → last N days inclusive of today (local)."""
    today = datetime.now().astimezone().date()
    cutoff = None if days is None else today - timedelta(days=days - 1)

    out = {
        "range_days": days,
        "sessions": 0,
        "messages": 0,
        "total_tokens": 0,        # input + output (excludes cache replays)
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_tokens": 0,        # cache_creation + cache_read, mostly replays
        "active_days": 0,
        "current_streak": 0,
        "longest_streak": 0,
        "peak_hour": None,
        "favorite_model": None,
        "favorite_model_id": "",
        "models": [],
        "heatmap": [[0] * 24 for _ in range(7)],  # rows = Mon..Sun, cols = 0..23
        "per_date": {},
        "comparison": None,
    }

    if not _core.PROJECTS_ROOT.is_dir():
        return out

    sessions = set()
    active_dates = set()
    by_dow_hour = [[0] * 24 for _ in range(7)]
    hour_totals = [0] * 24
    by_model = {}
    per_date = {}

    with _STATS_CACHE_LOCK:
        for project_dir in _core.PROJECTS_ROOT.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl in project_dir.iterdir():
                if not jsonl.name.endswith(".jsonl"):
                    continue
                agg = _stats_get_file_agg(jsonl)
                if not agg:
                    continue
                file_in_range = False
                for date_str, day in agg["by_date"].items():
                    try:
                        d = datetime.strptime(date_str, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    if cutoff and d < cutoff:
                        continue
                    if d > today:  # ignore clock-skew futures
                        continue
                    file_in_range = True
                    active_dates.add(d)
                    out["messages"] += day["messages"]
                    out["input_tokens"] += day["in_tokens"]
                    out["output_tokens"] += day["out_tokens"]
                    out["cache_tokens"] += day.get("cache_tokens", 0)
                    pd = per_date.setdefault(date_str, {"messages": 0, "tokens": 0})
                    pd["messages"] += day["messages"]
                    pd["tokens"] += day["in_tokens"] + day["out_tokens"]
                    dow = d.weekday()  # 0=Mon..6=Sun
                    for hour_str, c in day["hours"].items():
                        try:
                            h = int(hour_str)
                        except (TypeError, ValueError):
                            continue
                        if 0 <= h < 24:
                            by_dow_hour[dow][h] += c
                            hour_totals[h] += c
                    for m, c in day["models"].items():
                        by_model[m] = by_model.get(m, 0) + c
                if file_in_range and agg.get("session_id"):
                    sessions.add(agg["session_id"])

    out["sessions"] = len(sessions)
    out["total_tokens"] = out["input_tokens"] + out["output_tokens"]
    out["active_days"] = len(active_dates)
    out["current_streak"], out["longest_streak"] = _stats_compute_streaks(
        active_dates, today
    )
    if any(hour_totals):
        peak = max(range(24), key=lambda h: hour_totals[h])
        # Format like the screenshot: "7 PM"
        if peak == 0:
            out["peak_hour"] = "12 AM"
        elif peak < 12:
            out["peak_hour"] = f"{peak} AM"
        elif peak == 12:
            out["peak_hour"] = "12 PM"
        else:
            out["peak_hour"] = f"{peak - 12} PM"
    if by_model:
        top_id = max(by_model, key=by_model.get)
        out["favorite_model_id"] = top_id
        out["favorite_model"] = _stats_pretty_model(top_id)
        total = sum(by_model.values()) or 1
        out["models"] = sorted(
            [
                {
                    "id": mid,
                    "label": _stats_pretty_model(mid),
                    "messages": c,
                    "share": round(c / total, 4),
                }
                for mid, c in by_model.items()
            ],
            key=lambda r: r["messages"],
            reverse=True,
        )
    out["heatmap"] = by_dow_hour
    out["per_date"] = per_date
    out["comparison"] = _stats_pick_comparison(out["total_tokens"])
    return out


def _model_rates_known(model):
    return _core._rates_for_model_known(model)


def _throughput_scope(session_id, range_key=None):
    """Resolve throughput scope.

    Returns (is_aggregate, cutoff_epoch, label). A cutoff of None means all
    available turns for the selected session/scope.
    """
    sid = str(session_id or "").strip()
    rk = str(range_key or "").strip().lower()
    now = time.time()
    is_all = sid in ("all", "all_time") or sid.startswith("all_")
    # Explicit rolling ranges are shared by the aggregate dashboard and its
    # sidebar. Check them before legacy aggregate IDs so `all_7_days?range=2d`
    # cannot accidentally retain the seven-day scope.
    if rk in ("1d", "24h", "last_1_day"):
        return is_all, now - 86400, "Last day"
    if rk in ("2d", "48h", "last_2_days"):
        return is_all, now - 2 * 86400, "Last 2 days"
    if rk in ("7d", "week", "last_7_days"):
        return is_all, now - 7 * 86400, "Last 7 days"
    if sid == "all_1_hour" or rk in ("1h", "hour", "last_hour"):
        return is_all, now - 3600, "Last hour"
    if sid == "all_today" or rk in ("today", "day"):
        local_midnight = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return is_all, local_midnight.timestamp(), "Today"
    if sid == "all_7_days":
        return is_all, now - 7 * 86400, "Last 7 days"
    if sid == "all_56_days":
        return True, now - 56 * 86400, "Last 56 days"
    if sid in ("all", "all_time"):
        return True, None, "All time"
    return False, None, "Session"


def _throughput_engine_filter(value):
    v = str(value or "claude").strip().lower()
    return v if v in ("codex", "kimi") else "claude"


def _throughput_trigger_preview(tev):
    text = tev.get("text") or ""
    if tev.get("type") == "tool_result":
        use_id = tev.get("tool_use_id") or ""
        prefix = f"Tool Result ({use_id}): " if use_id else "Tool Result: "
        return prefix + text[:300] + ("..." if len(text) > 300 else "")
    return text[:300] + ("..." if len(text) > 300 else "")


def _throughput_assistant_preview(aev):
    blocks = aev.get("blocks") or []
    parts = []
    for block in blocks:
        kind = block.get("kind") or ""
        if kind == "text":
            parts.append(block.get("text") or "")
        elif kind == "thinking":
            parts.append(f"[Thinking: {block.get('text') or ''}]")
        elif kind == "tool_use":
            parts.append(
                f"[Tool Use: {block.get('name') or ''}({block.get('detail') or ''})]"
            )
    full_text = "\n".join(parts)
    return full_text[:4000] + ("..." if len(full_text) > 4000 else "")


def _throughput_usage_weights(model, engine=""):
    rates, known = _model_rates_known(model)
    rate_in, rate_cw, rate_cr, rate_out = rates
    if rate_in <= 0:
        cache_write_weight = 1.25
        cache_write_1h_weight = 2.0
        cache_read_weight = 0.10
    else:
        cache_write_weight = rate_cw / rate_in
        cache_write_1h_weight = 2.0
        cache_read_weight = rate_cr / rate_in
    engine_l = (engine or "").lower()
    can_price = known or engine_l == "claude"
    if known:
        cost_basis = "api_list_price"
    elif can_price:
        cost_basis = "fallback_sonnet"
    else:
        cost_basis = "unpriced"
    if not can_price:
        # Keep cache-adjusted token math available for all engines, but avoid
        # pretending Claude list prices are Codex/Gemini prices.
        rate_in = rate_cw = rate_cr = rate_out = 0.0
    return {
        "cache_write_weight": cache_write_weight,
        "cache_write_1h_weight": cache_write_1h_weight,
        "cache_read_weight": cache_read_weight,
        "rate_in": rate_in,
        "rate_cache_write": rate_cw,
        "rate_cache_write_1h": rate_in * cache_write_1h_weight,
        "rate_cache_read": rate_cr,
        "rate_out": rate_out,
        "cost_basis": cost_basis,
        "cost_available": can_price,
    }


def _throughput_usage_int(usage, *keys):
    if not isinstance(usage, dict):
        return 0
    for key in keys:
        if key in usage:
            return _core._codex_int(usage.get(key))
    return 0


def _throughput_normalize_usage(usage, *, engine="", model=""):
    """Normalize per-turn usage across Claude/Codex/Gemini/Antigravity.

    Claude/Anthropic and Antigravity expose cache reads/writes as separate
    buckets in addition to uncached input. Codex/OpenAI and Gemini expose
    cached input as a subset of input_tokens/candidate input. This function
    produces both raw context-size tokens and cache-adjusted input-equivalent
    tokens so the UI can show throughput and financial burn separately.
    """
    if not isinstance(usage, dict):
        usage = {}
    engine_l = (engine or "").lower()
    raw_context = _throughput_usage_int(
        usage, "raw_context_tokens", "context_tokens", "tokens_in"
    )
    input_tokens = _throughput_usage_int(usage, "input_tokens", "input", "in")
    cache_read = _throughput_usage_int(
        usage,
        "cache_read_input_tokens",
        "cached_input_tokens",
        "cache_read_tokens",
        "cached_tokens",
        "cached",
    )
    cache_write = _throughput_usage_int(
        usage,
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
        "cache_creation_tokens",
        "cache_write_tokens",
        "cache_create",
    )
    cache_write_5m = _throughput_usage_int(
        usage,
        "cache_creation_5m_input_tokens",
        "cache_create_5m_tokens",
        "ephemeral_5m_input_tokens",
    )
    cache_write_1h = _throughput_usage_int(
        usage,
        "cache_creation_1h_input_tokens",
        "cache_create_1h_tokens",
        "ephemeral_1h_input_tokens",
    )
    cache_write_split = cache_write_5m + cache_write_1h
    cache_write_unspecified = 0
    if cache_write_split:
        cache_write_unspecified = max(cache_write - cache_write_split, 0)
        cache_write = cache_write_split + cache_write_unspecified
    else:
        cache_write_unspecified = cache_write
    output = _throughput_usage_int(usage, "output_tokens", "output", "out")
    reasoning = _throughput_usage_int(
        usage,
        "reasoning_output_tokens",
        "thinking_tokens",
        "thinking",
        "thoughts",
    )
    tool_tokens = _throughput_usage_int(usage, "tool_tokens", "tool")
    output_total = output + reasoning + tool_tokens

    has_subset_cached = any(
        key in usage for key in ("cached_input_tokens", "cached_tokens", "cached")
    )
    if raw_context:
        fresh_input = max(raw_context - cache_read - cache_write, 0)
    elif has_subset_cached or engine_l in ("codex", "gemini", "cursor", "kilo", "opencode"):
        raw_context = input_tokens
        fresh_input = max(input_tokens - cache_read - cache_write, 0)
    else:
        fresh_input = input_tokens
        raw_context = input_tokens + cache_read + cache_write

    weights = _throughput_usage_weights(model, engine=engine)
    effective_input = (
        fresh_input
        + (cache_write_5m + cache_write_unspecified) * weights["cache_write_weight"]
        + cache_write_1h * weights["cache_write_1h_weight"]
        + cache_read * weights["cache_read_weight"]
    )
    cost_usd = (
        fresh_input * weights["rate_in"]
        + (cache_write_5m + cache_write_unspecified) * weights["rate_cache_write"]
        + cache_write_1h * weights["rate_cache_write_1h"]
        + cache_read * weights["rate_cache_read"]
        + output_total * weights["rate_out"]
    ) / 1_000_000

    return {
        "model": model or "",
        "engine": engine or "",
        "fresh_input_tokens": fresh_input,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cache_write_5m_tokens": cache_write_5m,
        "cache_write_1h_tokens": cache_write_1h,
        "raw_context_tokens": raw_context,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "tool_tokens": tool_tokens,
        "output_total_tokens": output_total,
        "effective_input_tokens": effective_input,
        "effective_total_tokens": effective_input + output_total,
        "cache_write_weight": weights["cache_write_weight"],
        "cache_write_1h_weight": weights["cache_write_1h_weight"],
        "cache_read_weight": weights["cache_read_weight"],
        "cost_usd": cost_usd,
        "cost_available": weights["cost_available"],
        "cost_basis": weights["cost_basis"],
    }


def _throughput_event_usage(ev, *, engine="", model_hint=""):
    model = ev.get("model") or model_hint or ""
    usage = ev.get("token_usage")
    if not isinstance(usage, dict):
        usage = None
    if usage is None and (
        ev.get("tokens_in")
        or ev.get("tokens_out")
        or ev.get("tokens_thinking")
    ):
        usage = {
            "raw_context_tokens": ev.get("tokens_in") or 0,
            "output_tokens": ev.get("tokens_out") or 0,
            "reasoning_output_tokens": ev.get("tokens_thinking") or 0,
        }
    return _core._throughput_normalize_usage(usage or {}, engine=engine, model=model)


def _throughput_is_claude_turn(turn):
    engine = (turn.get("engine") or "").lower()
    model = (turn.get("model") or "").lower()
    mid = turn.get("message_id") or ""
    return engine == "claude" or model.startswith("claude-") or str(mid).startswith("msg_")


def _throughput_turn_usage_total(turn):
    return (
        (turn.get("raw_context_tokens") or turn.get("tokens_in") or 0)
        + (turn.get("tokens_out") or 0)
    )


def _throughput_should_replace_duplicate(candidate, existing):
    candidate_sidechain = bool(candidate.get("is_sidechain"))
    existing_sidechain = bool(existing.get("is_sidechain"))
    if candidate_sidechain != existing_sidechain:
        return existing_sidechain

    candidate_total = _throughput_turn_usage_total(candidate)
    existing_total = _throughput_turn_usage_total(existing)
    if candidate_total != existing_total:
        return candidate_total > existing_total

    candidate_end = _stats_parse_ts(candidate.get("t_end"))
    existing_end = _stats_parse_ts(existing.get("t_end"))
    if candidate_end and existing_end and candidate_end != existing_end:
        return candidate_end > existing_end
    return False


def _throughput_duplicate_match(turn, existing):
    if not _throughput_is_claude_turn(turn) or not _throughput_is_claude_turn(existing):
        return False
    message_id = turn.get("message_id") or ""
    if not message_id or existing.get("message_id") != message_id:
        return False
    request_id = turn.get("request_id") or ""
    existing_request_id = existing.get("request_id") or ""
    if request_id == existing_request_id:
        return True
    if not request_id or not existing_request_id:
        return True
    return bool(turn.get("is_sidechain") or existing.get("is_sidechain"))


def _throughput_dedupe_turns(turns):
    deduped = []
    by_message = {}
    for turn in turns:
        message_id = turn.get("message_id") or ""
        if not message_id or not _throughput_is_claude_turn(turn):
            deduped.append(turn)
            continue

        request_id = turn.get("request_id") or ""
        match_index = None
        for index in by_message.get(message_id, []):
            if _throughput_duplicate_match(turn, deduped[index]):
                match_index = index
                break

        if match_index is not None:
            if _throughput_should_replace_duplicate(turn, deduped[match_index]):
                deduped[match_index] = turn
            continue

        by_message.setdefault(message_id, []).append(len(deduped))
        deduped.append(turn)

    deduped.sort(key=lambda t: t.get("t_start") or "")
    for idx, turn in enumerate(deduped, 1):
        turn["turn_index"] = idx
    return deduped


def _throughput_attach_result_usage(events, *, engine="", model_hint=""):
    """Attach result token_usage to the preceding assistant event.

    Codex/Gemini expose per-turn usage on a synthetic result event. Throughput
    is rendered by assistant turns, so mirror that usage onto the last
    assistant before the result unless another assistant/user turn intervenes.
    """
    for i, ev in enumerate(events):
        if ev.get("type") != "assistant" or isinstance(ev.get("token_usage"), dict):
            continue
        result_ev = None
        has_later_assistant = False
        for nxt in events[i + 1:]:
            if nxt.get("type") == "user_text":
                break
            if nxt.get("type") == "assistant":
                has_later_assistant = True
                break
            if nxt.get("type") == "result":
                result_ev = nxt
                break
        usage = result_ev.get("token_usage") if result_ev and not has_later_assistant else None
        if not isinstance(usage, dict):
            continue
        ev["token_usage"] = dict(usage)
        norm = _throughput_event_usage(ev, engine=engine, model_hint=model_hint)
        if not ev.get("tokens_in"):
            ev["tokens_in"] = norm["raw_context_tokens"]
        if not ev.get("tokens_out"):
            ev["tokens_out"] = norm["output_total_tokens"]


def _throughput_turns_from_events(
    events,
    *,
    session_id="",
    session_name="",
    engine="",
    model_hint="",
    cutoff_epoch=None,
):
    events = [dict(ev) for ev in (events or []) if isinstance(ev, dict)]
    _throughput_attach_result_usage(events, engine=engine, model_hint=model_hint)
    turns = []
    for i, ev in enumerate(events):
        if ev.get("type") != "assistant":
            continue
        trigger_ev = None
        for prev in reversed(events[:i]):
            if prev.get("type") in ("user_text", "tool_result"):
                trigger_ev = prev
                break
        if not trigger_ev:
            continue
        t_start = _stats_parse_ts(trigger_ev.get("ts"))
        t_end = _stats_parse_ts(ev.get("ts"))
        if not t_start or not t_end:
            continue
        if cutoff_epoch is not None and t_end.timestamp() < cutoff_epoch:
            continue
        dur_sec = (t_end - t_start).total_seconds()
        if dur_sec < 1.0:
            dur_sec = 1.0
        usage = _throughput_event_usage(ev, engine=engine, model_hint=model_hint)
        tokens_in = usage["raw_context_tokens"]
        tokens_out = usage["output_total_tokens"]
        effective_input = usage["effective_input_tokens"]
        effective_total = usage["effective_total_tokens"]
        in_tps = tokens_in / dur_sec if dur_sec > 0 else 0.0
        out_tps = tokens_out / dur_sec if dur_sec > 0 else 0.0
        total_tps = (tokens_in + tokens_out) / dur_sec if dur_sec > 0 else 0.0
        effective_input_tps = effective_input / dur_sec if dur_sec > 0 else 0.0
        effective_total_tps = effective_total / dur_sec if dur_sec > 0 else 0.0
        turn = {
            "turn_index": len(turns) + 1,
            "session_id": session_id,
            "session_name": session_name,
            "engine": engine or usage["engine"],
            "model": usage["model"] or model_hint or "",
            "message_id": ev.get("message_id") or "",
            "request_id": ev.get("request_id") or "",
            "is_sidechain": bool(ev.get("is_sidechain") or ev.get("isSidechain")),
            "trigger_type": trigger_ev.get("type"),
            "trigger_preview": _throughput_trigger_preview(trigger_ev),
            "assistant_preview": _throughput_assistant_preview(ev),
            "t_start": trigger_ev.get("ts"),
            "t_end": ev.get("ts"),
            "dur_sec": round(dur_sec, 2),
            # Backward-compatible raw context/output fields.
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "in_tps": round(in_tps, 2),
            "out_tps": round(out_tps, 2),
            "total_tps": round(total_tps, 2),
            "in_tpm": round(in_tps * 60.0, 2),
            "out_tpm": round(out_tps * 60.0, 2),
            "total_tpm": round(total_tps * 60.0, 2),
            # Cache-aware fields.
            "fresh_input_tokens": usage["fresh_input_tokens"],
            "cache_read_tokens": usage["cache_read_tokens"],
            "cache_write_tokens": usage["cache_write_tokens"],
            "cache_write_5m_tokens": usage["cache_write_5m_tokens"],
            "cache_write_1h_tokens": usage["cache_write_1h_tokens"],
            "raw_context_tokens": tokens_in,
            "effective_input_tokens": round(effective_input, 2),
            "effective_total_tokens": round(effective_total, 2),
            "effective_input_tps": round(effective_input_tps, 2),
            "effective_input_tpm": round(effective_input_tps * 60.0, 2),
            "cache_adjusted_total_tps": round(effective_total_tps, 2),
            "cache_adjusted_total_tpm": round(effective_total_tps * 60.0, 2),
            "reasoning_output_tokens": usage["reasoning_output_tokens"],
            "tool_tokens": usage["tool_tokens"],
            "cost_usd": round(usage["cost_usd"], 6),
            "cost_available": usage["cost_available"],
            "cost_basis": usage["cost_basis"],
            "cache_write_weight": usage["cache_write_weight"],
            "cache_write_1h_weight": usage["cache_write_1h_weight"],
            "cache_read_weight": usage["cache_read_weight"],
        }
        turns.append(turn)
    return turns


def _throughput_codex_payload_model(payload):
    if not isinstance(payload, dict):
        return ""
    for key in ("model", "model_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("model")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _throughput_iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _throughput_codex_turns_from_file(
    session_id,
    *,
    session_name="",
    model_hint="",
    cutoff_epoch=None,
):
    path = _core._resolve_codex_rollout_path(session_id)
    if not path:
        return []
    turns = []
    current_model = model_hint or ""
    previous_totals = None
    last_boundary_ts = None
    line_num = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_num += 1
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _core._codex_event_timestamp(ev)
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                if ev.get("type") == "turn_context":
                    current_model = _throughput_codex_payload_model(payload) or current_model
                    if ts and last_boundary_ts is None:
                        last_boundary_ts = ts
                    continue
                if ev.get("type") == "event_msg" and payload.get("type") == "user_message":
                    if ts:
                        last_boundary_ts = ts
                    continue

                usage_raw, previous_totals = _core._codex_usage_delta_from_event(ev, previous_totals)
                if not usage_raw:
                    continue
                t_end = _stats_parse_ts(ts)
                if not t_end:
                    continue
                if cutoff_epoch is not None and t_end.timestamp() < cutoff_epoch:
                    last_boundary_ts = ts or last_boundary_ts
                    continue
                t_start = _stats_parse_ts(last_boundary_ts) if last_boundary_ts else None
                if not t_start or t_start > t_end:
                    t_start = t_end - timedelta(seconds=1)
                dur_sec = max((t_end - t_start).total_seconds(), 1.0)

                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                model = (
                    _throughput_codex_payload_model(info)
                    or _throughput_codex_payload_model(payload)
                    or current_model
                    or model_hint
                    or ""
                )
                usage = _core._throughput_normalize_usage(
                    usage_raw,
                    engine="codex",
                    model=model,
                )
                tokens_in = usage["raw_context_tokens"]
                tokens_out = usage["output_total_tokens"]
                effective_input = usage["effective_input_tokens"]
                effective_total = usage["effective_total_tokens"]
                in_tps = tokens_in / dur_sec if dur_sec > 0 else 0.0
                out_tps = tokens_out / dur_sec if dur_sec > 0 else 0.0
                total_tps = (tokens_in + tokens_out) / dur_sec if dur_sec > 0 else 0.0
                effective_input_tps = effective_input / dur_sec if dur_sec > 0 else 0.0
                effective_total_tps = effective_total / dur_sec if dur_sec > 0 else 0.0
                turns.append({
                    "turn_index": len(turns) + 1,
                    "session_id": session_id,
                    "session_name": session_name,
                    "engine": "codex",
                    "model": usage["model"] or model,
                    "message_id": f"codex-token-{line_num}",
                    "request_id": "",
                    "is_sidechain": False,
                    "trigger_type": "token_count",
                    "trigger_preview": "Codex model call",
                    "assistant_preview": "Codex token usage event",
                    "t_start": _throughput_iso(t_start),
                    "t_end": ts,
                    "dur_sec": round(dur_sec, 2),
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "in_tps": round(in_tps, 2),
                    "out_tps": round(out_tps, 2),
                    "total_tps": round(total_tps, 2),
                    "in_tpm": round(in_tps * 60.0, 2),
                    "out_tpm": round(out_tps * 60.0, 2),
                    "total_tpm": round(total_tps * 60.0, 2),
                    "fresh_input_tokens": usage["fresh_input_tokens"],
                    "cache_read_tokens": usage["cache_read_tokens"],
                    "cache_write_tokens": usage["cache_write_tokens"],
                    "cache_write_5m_tokens": usage["cache_write_5m_tokens"],
                    "cache_write_1h_tokens": usage["cache_write_1h_tokens"],
                    "raw_context_tokens": tokens_in,
                    "effective_input_tokens": round(effective_input, 2),
                    "effective_total_tokens": round(effective_total, 2),
                    "effective_input_tps": round(effective_input_tps, 2),
                    "effective_input_tpm": round(effective_input_tps * 60.0, 2),
                    "cache_adjusted_total_tps": round(effective_total_tps, 2),
                    "cache_adjusted_total_tpm": round(effective_total_tps * 60.0, 2),
                    "reasoning_output_tokens": usage["reasoning_output_tokens"],
                    "tool_tokens": usage["tool_tokens"],
                    "cost_usd": round(usage["cost_usd"], 6),
                    "cost_available": usage["cost_available"],
                    "cost_basis": usage["cost_basis"],
                    "cache_write_weight": usage["cache_write_weight"],
                    "cache_write_1h_weight": usage["cache_write_1h_weight"],
                    "cache_read_weight": usage["cache_read_weight"],
                })
                last_boundary_ts = ts
    except OSError:
        return []
    return turns


def _kimi_record_timestamp(rec):
    """Epoch-ms `time`/`created_at` from a Kimi wire record → aware datetime."""
    raw = rec.get("time")
    if raw is None:
        raw = rec.get("created_at")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 100000000000:  # epoch milliseconds
        value = value / 1000.0
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _throughput_kimi_turns_from_wire(
    path,
    session_id,
    *,
    session_name="",
    model_hint="",
    cutoff_epoch=None,
):
    """Per-turn token deltas from one Kimi wire.jsonl transcript.

    Each `usage.record` line (usageScope "turn") carries the token counts for
    one model call: inputOther (uncached input), inputCacheRead,
    inputCacheCreation and output. Those map onto the Claude-style buckets
    (cache read/write are additive, not a subset of input), so the shared
    normalizer produces raw-context and cache-adjusted totals the same way it
    does for Codex turns. `usageScope: "session"` records are skipped — they
    restate session totals and would double-count."""
    turns = []
    current_model = model_hint or ""
    last_boundary_ts = None
    line_num = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_num += 1
                if '"usage"' not in line and '"model"' not in line and '"turn.prompt"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rtype = rec.get("type")
                t_end = _kimi_record_timestamp(rec)
                if rtype == "turn.prompt":
                    if t_end:
                        last_boundary_ts = t_end
                    continue
                if rtype == "llm.request":
                    model = rec.get("modelAlias") or rec.get("model")
                    if isinstance(model, str) and model.strip():
                        current_model = model.strip()
                    continue
                if rtype != "usage.record":
                    continue
                if rec.get("usageScope") not in (None, "turn"):
                    continue
                usage_raw = rec.get("usage")
                if not isinstance(usage_raw, dict) or not usage_raw or not t_end:
                    continue
                if cutoff_epoch is not None and t_end.timestamp() < cutoff_epoch:
                    last_boundary_ts = t_end
                    continue
                t_start = last_boundary_ts
                if not t_start or t_start > t_end:
                    t_start = t_end - timedelta(seconds=1)
                dur_sec = max((t_end - t_start).total_seconds(), 1.0)

                model = rec.get("model") or current_model or model_hint or "kimi"
                usage = _core._throughput_normalize_usage(
                    {
                        "input_tokens": _core._codex_int(usage_raw.get("inputOther")),
                        "cache_read_input_tokens": _core._codex_int(usage_raw.get("inputCacheRead")),
                        "cache_creation_input_tokens": _core._codex_int(usage_raw.get("inputCacheCreation")),
                        "output_tokens": _core._codex_int(usage_raw.get("output")),
                    },
                    engine="kimi",
                    model=model,
                )
                tokens_in = usage["raw_context_tokens"]
                tokens_out = usage["output_total_tokens"]
                effective_input = usage["effective_input_tokens"]
                effective_total = usage["effective_total_tokens"]
                in_tps = tokens_in / dur_sec if dur_sec > 0 else 0.0
                out_tps = tokens_out / dur_sec if dur_sec > 0 else 0.0
                total_tps = (tokens_in + tokens_out) / dur_sec if dur_sec > 0 else 0.0
                effective_input_tps = effective_input / dur_sec if dur_sec > 0 else 0.0
                effective_total_tps = effective_total / dur_sec if dur_sec > 0 else 0.0
                turns.append({
                    "turn_index": len(turns) + 1,
                    "session_id": session_id,
                    "session_name": session_name,
                    "engine": "kimi",
                    "model": usage["model"] or model,
                    "message_id": f"kimi-usage-{line_num}",
                    "request_id": "",
                    "is_sidechain": False,
                    "trigger_type": "usage_record",
                    "trigger_preview": "Kimi model call",
                    "assistant_preview": "Kimi token usage event",
                    "t_start": _throughput_iso(t_start),
                    "t_end": _throughput_iso(t_end),
                    "dur_sec": round(dur_sec, 2),
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "in_tps": round(in_tps, 2),
                    "out_tps": round(out_tps, 2),
                    "total_tps": round(total_tps, 2),
                    "in_tpm": round(in_tps * 60.0, 2),
                    "out_tpm": round(out_tps * 60.0, 2),
                    "total_tpm": round(total_tps * 60.0, 2),
                    "fresh_input_tokens": usage["fresh_input_tokens"],
                    "cache_read_tokens": usage["cache_read_tokens"],
                    "cache_write_tokens": usage["cache_write_tokens"],
                    "cache_write_5m_tokens": usage["cache_write_5m_tokens"],
                    "cache_write_1h_tokens": usage["cache_write_1h_tokens"],
                    "raw_context_tokens": tokens_in,
                    "effective_input_tokens": round(effective_input, 2),
                    "effective_total_tokens": round(effective_total, 2),
                    "effective_input_tps": round(effective_input_tps, 2),
                    "effective_input_tpm": round(effective_input_tps * 60.0, 2),
                    "cache_adjusted_total_tps": round(effective_total_tps, 2),
                    "cache_adjusted_total_tpm": round(effective_total_tps * 60.0, 2),
                    "reasoning_output_tokens": usage["reasoning_output_tokens"],
                    "tool_tokens": usage["tool_tokens"],
                    "cost_usd": round(usage["cost_usd"], 6),
                    "cost_available": usage["cost_available"],
                    "cost_basis": usage["cost_basis"],
                    "cache_write_weight": usage["cache_write_weight"],
                    "cache_write_1h_weight": usage["cache_write_1h_weight"],
                    "cache_read_weight": usage["cache_read_weight"],
                })
                last_boundary_ts = t_end
    except OSError:
        return []
    return turns


def _throughput_kimi_turns_from_file(
    session_id,
    *,
    session_name="",
    model_hint="",
    cutoff_epoch=None,
):
    """All turns for a Kimi session — every agents/*/wire.jsonl in its dir."""
    session_dir = _core._kimi_session_dir(session_id)
    turns = []
    for wire_path in _core._throughput_kimi_wire_files(session_dir):
        turns.extend(_throughput_kimi_turns_from_wire(
            wire_path,
            session_id,
            session_name=session_name,
            model_hint=model_hint,
            cutoff_epoch=cutoff_epoch,
        ))
    turns.sort(key=lambda t: t.get("t_start") or "")
    for idx, turn in enumerate(turns, 1):
        turn["turn_index"] = idx
    return turns


def _throughput_empty_bucket():
    return {
        "turns": 0,
        "active_duration_sec": 0.0,
        "raw_context_tokens": 0,
        "fresh_input_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "effective_input_tokens": 0.0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "unpriced_turns": 0,
    }


def _throughput_add_bucket(bucket, turn):
    bucket["turns"] += 1
    bucket["active_duration_sec"] += turn.get("dur_sec") or 0
    bucket["raw_context_tokens"] += turn.get("raw_context_tokens") or turn.get("tokens_in") or 0
    bucket["fresh_input_tokens"] += turn.get("fresh_input_tokens") or 0
    bucket["cache_read_tokens"] += turn.get("cache_read_tokens") or 0
    bucket["cache_write_tokens"] += turn.get("cache_write_tokens") or 0
    bucket["effective_input_tokens"] += turn.get("effective_input_tokens") or 0
    bucket["output_tokens"] += turn.get("tokens_out") or 0
    bucket["total_tokens"] += (turn.get("tokens_in") or 0) + (turn.get("tokens_out") or 0)
    bucket["cost_usd"] += turn.get("cost_usd") or 0.0
    if not turn.get("cost_available"):
        bucket["unpriced_turns"] += 1


def _throughput_finalize_bucket(bucket):
    dur = bucket.get("active_duration_sec") or 0.0
    raw_context = bucket.get("raw_context_tokens") or 0
    effective = bucket.get("effective_input_tokens") or 0.0
    output = bucket.get("output_tokens") or 0
    cache_tokens = (bucket.get("cache_read_tokens") or 0) + (bucket.get("cache_write_tokens") or 0)
    out = dict(bucket)
    out["active_duration_sec"] = round(dur, 2)
    out["effective_input_tokens"] = round(effective, 2)
    out["cost_usd"] = round(bucket.get("cost_usd") or 0.0, 6)
    out["avg_raw_context_tpm"] = round((raw_context / dur) * 60.0, 2) if dur else 0.0
    out["avg_effective_input_tpm"] = round((effective / dur) * 60.0, 2) if dur else 0.0
    out["avg_output_tpm"] = round((output / dur) * 60.0, 2) if dur else 0.0
    out["avg_cache_adjusted_total_tpm"] = (
        round(((effective + output) / dur) * 60.0, 2) if dur else 0.0
    )
    out["cache_hit_ratio"] = round((bucket.get("cache_read_tokens") or 0) / raw_context, 4) if raw_context else 0.0
    out["cache_token_ratio"] = round(cache_tokens / raw_context, 4) if raw_context else 0.0
    return out


def _throughput_summary(turns, stat_cutoff_epoch=None):
    # stat_turns: the scoped subset used for aggregate card totals (e.g. last 7d).
    # `turns` covers a wider window so hourly/daily buckets reach back further
    # (e.g. previous billing period for the chart overlay).
    stat_turns = (
        [t for t in turns if _throughput_turn_after_cutoff(t, stat_cutoff_epoch)]
        if stat_cutoff_epoch is not None else turns
    )
    total_turns = len(stat_turns)
    turns_with_tokens = sum(
        1 for t in stat_turns if (t.get("tokens_in") or 0) > 0 or (t.get("tokens_out") or 0) > 0
    )
    total_bucket = _throughput_empty_bucket()
    per_model = {}
    hourly = {}
    daily = {}
    for t in stat_turns:
        _throughput_add_bucket(total_bucket, t)
        model_key = t.get("model") or t.get("engine") or "unknown"
        model_bucket = per_model.setdefault(model_key, _throughput_empty_bucket())
        model_bucket["model"] = t.get("model") or ""
        model_bucket["engine"] = t.get("engine") or ""
        _throughput_add_bucket(model_bucket, t)
    for t in turns:
        t_end = _stats_parse_ts(t.get("t_end"))
        if t_end:
            hour_key = t_end.strftime("%Y-%m-%d %H:00")
            hour_bucket = hourly.setdefault(hour_key, _throughput_empty_bucket())
            hour_bucket["hour"] = hour_key
            _throughput_add_bucket(hour_bucket, t)
            # Daily buckets too — the 7-day view is far too crowded at hourly
            # granularity (168 points); the frontend picks daily for wide ranges.
            day_key = t_end.strftime("%Y-%m-%d")
            day_bucket = daily.setdefault(day_key, _throughput_empty_bucket())
            day_bucket["hour"] = day_key  # reuse the "hour" field as the bucket label
            day_bucket["day"] = day_key
            _throughput_add_bucket(day_bucket, t)

    finalized_total = _throughput_finalize_bucket(total_bucket)
    total_active_sec = finalized_total["active_duration_sec"]
    total_in = finalized_total["raw_context_tokens"]
    total_out = finalized_total["output_tokens"]
    total_tok = total_in + total_out
    avg_in_tps = total_in / total_active_sec if total_active_sec > 0 else 0.0
    avg_out_tps = total_out / total_active_sec if total_active_sec > 0 else 0.0
    avg_total_tps = total_tok / total_active_sec if total_active_sec > 0 else 0.0
    out = {
        "total_turns": total_turns,
        "turns_with_tokens": turns_with_tokens,
        "total_active_duration_sec": total_active_sec,
        # Backward-compatible raw context/output totals.
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_tokens": total_tok,
        "avg_input_tps": round(avg_in_tps, 2),
        "avg_output_tps": round(avg_out_tps, 2),
        "avg_total_tps": round(avg_total_tps, 2),
        "avg_input_tpm": round(avg_in_tps * 60.0, 2),
        "avg_output_tpm": round(avg_out_tps * 60.0, 2),
        "avg_total_tpm": round(avg_total_tps * 60.0, 2),
        # Cache-aware totals.
        "total_raw_context_tokens": finalized_total["raw_context_tokens"],
        "total_fresh_input_tokens": finalized_total["fresh_input_tokens"],
        "total_cache_read_tokens": finalized_total["cache_read_tokens"],
        "total_cache_write_tokens": finalized_total["cache_write_tokens"],
        "total_effective_input_tokens": finalized_total["effective_input_tokens"],
        "avg_effective_input_tpm": finalized_total["avg_effective_input_tpm"],
        "avg_effective_input_tps": round(
            finalized_total["effective_input_tokens"] / total_active_sec, 2
        ) if total_active_sec > 0 else 0.0,
        "avg_cache_adjusted_total_tpm": finalized_total["avg_cache_adjusted_total_tpm"],
        "cache_hit_ratio": finalized_total["cache_hit_ratio"],
        "cache_token_ratio": finalized_total["cache_token_ratio"],
        "cost_usd": finalized_total["cost_usd"],
        "unpriced_turns": finalized_total["unpriced_turns"],
        "per_model": sorted(
            (_throughput_finalize_bucket(v) for v in per_model.values()),
            key=lambda r: r.get("effective_input_tokens") or 0,
            reverse=True,
        ),
        "hourly": [
            _throughput_finalize_bucket(hourly[k])
            for k in sorted(hourly)
        ],
        "daily": [
            _throughput_finalize_bucket(daily[k])
            for k in sorted(daily)
        ],
    }
    return out


def _throughput_session_hints(session_id):
    engine = ""
    model = ""
    try:
        engine = _core._detect_session_engine(session_id) or ""
    except Exception:
        engine = ""
    try:
        usage = _core.extract_session_usage(session_id)
        model = usage.get("model") or ""
        engine = usage.get("engine") or engine
    except Exception:
        pass
    return engine, model


# --- Per-transcript throughput-turn cache (in-memory + lazy SQLite disk) -----
# Aggregate ranges (esp. "Last 7 days") parse every transcript in the window on
# each load — ~40s cold. But a transcript's past turns never change once
# written, so we cache the FULL extracted turn list per file keyed by
# (mtime,size). A reload then re-parses only the handful of files that actually
# changed (the live sessions); everything else is a dict/DB hit (~20x faster).
# The cutoff is applied AFTER the cache lookup (never baked into a cached entry)
# so a 1h load can't poison the 7d cache and vice-versa.
#
# Disk layer: a SQLite DB at ~/.cache/ccc-throughput-cache/turns.db.
# Entries are loaded LAZILY — only when that specific transcript is requested,
# not all at once at startup. This avoids the 81MB-parsed-on-boot problem the
# prior flat-file approach had. SQLite is stdlib; no extra deps.
_THROUGHPUT_TURN_CACHE = {}        # str(path) -> {"mtime", "size", "turns"}
_THROUGHPUT_CACHE_LOCK = threading.Lock()
_THROUGHPUT_AGG_CACHE = {}         # cache_key -> {"ts": float, "payload": dict, "status": int}
_THROUGHPUT_AGG_CACHE_TTL = 300    # seconds
_THROUGHPUT_RANKINGS_CACHE = {}    # "week" -> {"ts": float, "rankings": list}
_THROUGHPUT_RANKINGS_CACHE_TTL = 60
_THROUGHPUT_BOOTSTRAP_SCHEMA = 1
_THROUGHPUT_REFRESH_JOBS = {}
_THROUGHPUT_REFRESH_LAST_SUCCESS = {}
_THROUGHPUT_REFRESH_LOCK = threading.Lock()

_THROUGHPUT_DISK_CACHE_DIR = Path.home() / ".cache" / "ccc-throughput-cache"
_THROUGHPUT_DISK_CACHE_DB = _THROUGHPUT_DISK_CACHE_DIR / "turns.db"
_throughput_disk_conn = None       # sqlite3.Connection, None until first use
_throughput_disk_conn_lock = threading.Lock()
_throughput_disk_available = None  # True/False/None (None = not yet tried)
_THROUGHPUT_DISCOVERY_DAYS = 14


def _throughput_aggregate_cache_key(session_id, engine_filter=None):
    return f"{session_id}:{engine_filter or 'claude'}"


def _throughput_recent_conversations(
    engine_filter,
    cutoff_epoch,
    *,
    progress=None,
    projects_root=None,
):
    """Return only transcript candidates needed by throughput.

    Unlike the archive scanner, this applies the mtime cutoff before opening a
    transcript and never discovers unrelated engines or archive metadata.
    """
    engine = _core._throughput_engine_filter(engine_filter)
    if progress:
        progress("phase", "discovering")
        progress("sessions_discovered", 0)
    rows = []

    if engine == "claude":
        root = Path(projects_root) if projects_root is not None else Path.home() / ".claude" / "projects"
        try:
            project_dirs = [path for path in root.iterdir() if path.is_dir()]
        except OSError:
            project_dirs = []
        try:
            names = _core._load_session_name_overrides()
        except Exception:
            names = {}
        for folder_index, project_dir in enumerate(project_dirs, 1):
            try:
                candidates = []
                for path in project_dir.iterdir():
                    if not path.is_file() or not path.name.endswith(".jsonl"):
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if cutoff_epoch is not None and stat.st_mtime < cutoff_epoch:
                        continue
                    candidates.append((path, stat))
            except OSError:
                candidates = []
            for path, stat in candidates:
                sid = path.stem
                rows.append({
                    "session_id": sid,
                    "jsonl_path": str(path),
                    "modified": stat.st_mtime,
                    "mtime": stat.st_mtime,
                    "engine": "claude",
                    "source": "interactive",
                    "display_name": names.get(sid) or sid[:8],
                    "model": "",
                    "folder_path": str(_core._decode_project_slug(project_dir.name) or project_dir.name),
                })
            if progress:
                progress("folders_scanned", folder_index)
                progress("sessions_discovered", len(rows))
    elif engine == "kimi":
        # A Kimi "conversation" is one session_<uuid> dir; its transcript is
        # the agents/*/wire.jsonl set (main agent first). Rows point at the
        # main agent wire so (mtime,size) caching keys on a real file.
        root = _core.KIMI_SESSIONS_ROOT
        try:
            wd_dirs = [p for p in root.iterdir() if p.is_dir()]
        except OSError:
            wd_dirs = []
        for wd_dir in wd_dirs:
            try:
                session_dirs = [
                    p for p in wd_dir.iterdir()
                    if p.is_dir() and p.name.startswith("session_")
                ]
            except OSError:
                continue
            for session_dir in session_dirs:
                wire_files = _core._throughput_kimi_wire_files(session_dir)
                if not wire_files:
                    continue
                try:
                    modified = max(p.stat().st_mtime for p in wire_files)
                except OSError:
                    continue
                if cutoff_epoch is not None and modified < cutoff_epoch:
                    continue
                sid = session_dir.name[len("session_"):]
                rows.append({
                    "session_id": sid,
                    "jsonl_path": str(wire_files[0]),
                    "modified": modified,
                    "mtime": modified,
                    "engine": "kimi",
                    "source": "kimi",
                    "display_name": sid[:8],
                    "model": "",
                    "folder_path": _core._kimi_wd_folder_path(wd_dir.name),
                })
                if progress and (len(rows) == 1 or len(rows) % 25 == 0):
                    progress("sessions_discovered", len(rows))
        if progress:
            progress("folders_scanned", 1)
            progress("sessions_discovered", len(rows))
    else:
        try:
            threads = _core._codex_fetch_threads(limit=None) or []
        except Exception:
            threads = []
        for row in threads:
            sid = str(row.get("id") or "").strip()
            if not sid:
                continue
            # The thread index timestamp is cheap and authoritative. Apply the
            # bound before resolving a rollout path because that fallback may
            # recursively search the full Codex sessions tree.
            indexed_modified = _core._codex_ts_seconds(row, "updated")
            if (
                cutoff_epoch is not None
                and indexed_modified
                and indexed_modified < cutoff_epoch
            ):
                continue
            path = _core._codex_rollout_path_from_row(row)
            if not path or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            modified = indexed_modified or stat.st_mtime
            if cutoff_epoch is not None and modified < cutoff_epoch:
                continue
            rows.append({
                "session_id": sid,
                "jsonl_path": str(path),
                "modified": modified,
                "mtime": modified,
                "engine": "codex",
                "source": "codex",
                "display_name": row.get("title") or sid[:8],
                "model": row.get("model") or "",
                "folder_path": row.get("cwd") or "",
            })
            if progress and (len(rows) == 1 or len(rows) % 25 == 0):
                progress("sessions_discovered", len(rows))
        if progress:
            progress("folders_scanned", 1)
            progress("sessions_discovered", len(rows))

    rows.sort(key=lambda row: row.get("modified") or 0, reverse=True)
    return rows


def _throughput_snapshot_path(session_id, engine_filter=None):
    safe = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in str(session_id or "")
    )
    engine = _core._throughput_engine_filter(engine_filter)
    suffix = "" if engine == "claude" else f"-{engine}"
    return _core._THROUGHPUT_DISK_CACHE_DIR / f"aggregate-{safe or 'unknown'}{suffix}.json"


def _throughput_bootstrap_path(session_id, engine_filter=None):
    safe = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in str(session_id or "")
    )
    engine = _core._throughput_engine_filter(engine_filter)
    suffix = "" if engine == "claude" else f"-{engine}"
    return _core._THROUGHPUT_DISK_CACHE_DIR / f"bootstrap-{safe or 'unknown'}{suffix}.json"


def _throughput_build_bootstrap(
    session_id,
    engine_filter,
    throughput,
    *,
    generated_at=None,
    refresh=None,
    weekly=None,
):
    engine = _core._throughput_engine_filter(engine_filter)
    return {
        "schema": _core._THROUGHPUT_BOOTSTRAP_SCHEMA,
        "session_id": session_id,
        "engine": engine,
        "generated_at": float(time.time() if generated_at is None else generated_at),
        "throughput": throughput,
        "weekly": _core._weekly_usage_block() if weekly is None else weekly,
        "reset_events": _core.usage_reset_events_payload(days=30).get("events", []),
        "refresh": dict(refresh or {}),
    }


def _throughput_attach_account_usage(payload, account_usage):
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return False
    summary = payload.get("summary")
    if not isinstance(summary, dict) or not isinstance(account_usage, dict):
        return False
    summary["account_usage"] = account_usage
    return True


def _throughput_bootstrap_valid(model, session_id, engine_filter=None):
    engine = _core._throughput_engine_filter(engine_filter)
    if not isinstance(model, dict):
        return False
    if model.get("schema") != _core._THROUGHPUT_BOOTSTRAP_SCHEMA:
        return False
    if model.get("session_id") != session_id or model.get("engine") != engine:
        return False
    if not isinstance(model.get("generated_at"), (int, float)):
        return False
    throughput = model.get("throughput")
    if not isinstance(throughput, dict) or throughput.get("ok") is not True:
        return False
    scope = throughput.get("scope")
    if not isinstance(scope, dict) or scope.get("aggregate") is not True:
        return False
    if throughput.get("session_id") != session_id:
        return False
    if _core._throughput_engine_filter(scope.get("engine")) != engine:
        return False
    if not isinstance(model.get("weekly"), dict):
        return False
    if not isinstance(model.get("reset_events"), list):
        return False
    return isinstance(model.get("refresh"), dict)


def _throughput_read_bootstrap(session_id, engine_filter=None):
    try:
        model = json.loads(
            _throughput_bootstrap_path(session_id, engine_filter).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None
    if not _throughput_bootstrap_valid(model, session_id, engine_filter):
        return None
    return model


def _throughput_write_bootstrap(session_id, engine_filter, model):
    if not _throughput_bootstrap_valid(model, session_id, engine_filter):
        return False
    try:
        _core._THROUGHPUT_DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _throughput_bootstrap_path(session_id, engine_filter)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _throughput_refresh_public(job, now=None):
    now = time.time() if now is None else now
    public = dict(job)
    started_at = public.get("started_at") or now
    completed_at = public.get("completed_at")
    end = completed_at if completed_at is not None else now
    public["elapsed_ms"] = max(0, int((end - started_at) * 1000))
    discovered = int(public.get("sessions_discovered") or 0)
    read = int(public.get("sessions_read") or 0)
    if public.get("state") == "refreshing" and read >= 10 and discovered >= read:
        # Reserve time for dedupe/summary plus weekly-context publication after
        # the transcript loop finishes; otherwise the estimate reaches zero
        # while the final (visible) phase is still running.
        projected_ms = int((public["elapsed_ms"] / read) * discovered + 1500)
        public["expected_ms"] = max(public["elapsed_ms"] + 250, projected_ms)
    public.pop("thread", None)
    return public


def _throughput_refresh_status(session_id, engine_filter=None):
    key = _core._throughput_aggregate_cache_key(
        session_id, _core._throughput_engine_filter(engine_filter)
    )
    with _core._THROUGHPUT_REFRESH_LOCK:
        job = _core._THROUGHPUT_REFRESH_JOBS.get(key)
        last = _core._THROUGHPUT_REFRESH_LAST_SUCCESS.get(key) or {}
        if job is not None:
            return _core._throughput_refresh_public(job)
        return {
            "job_id": None,
            "state": "idle",
            "session_id": session_id,
            "engine": _core._throughput_engine_filter(engine_filter),
            "started_at": None,
            "completed_at": None,
            "elapsed_ms": 0,
            "expected_ms": int(last.get("duration_ms") or 35000),
            "phase": "idle",
            "window_days": _THROUGHPUT_DISCOVERY_DAYS,
            "folders_scanned": 0,
            "sessions_discovered": 0,
            "sessions_read": 0,
            "cache_hits": 0,
            "parsed": 0,
            "last_refreshed_at": last.get("completed_at"),
            "error": None,
        }


def _throughput_refresh_scope_supported(session_id):
    return session_id == "all_7_days"


def _throughput_refresh_start(session_id, engine_filter=None):
    engine = _core._throughput_engine_filter(engine_filter)
    key = _core._throughput_aggregate_cache_key(session_id, engine)
    now = time.time()
    with _core._THROUGHPUT_REFRESH_LOCK:
        existing = _core._THROUGHPUT_REFRESH_JOBS.get(key)
        if existing and existing.get("state") == "refreshing":
            return _core._throughput_refresh_public(existing, now)
        last = _core._THROUGHPUT_REFRESH_LAST_SUCCESS.get(key) or {}
        if not last:
            persisted = _core._throughput_read_bootstrap(session_id, engine) or {}
            persisted_refresh = persisted.get("refresh") or {}
            persisted_duration = (
                persisted_refresh.get("elapsed_ms")
                or persisted_refresh.get("expected_ms")
            )
            if isinstance(persisted_duration, (int, float)) and persisted_duration > 0:
                last = {
                    "completed_at": persisted.get("generated_at"),
                    "duration_ms": int(persisted_duration),
                }
                _core._THROUGHPUT_REFRESH_LAST_SUCCESS[key] = last
        job = {
            "job_id": uuid.uuid4().hex,
            "state": "refreshing",
            "session_id": session_id,
            "engine": engine,
            "started_at": now,
            "completed_at": None,
            "expected_ms": int(last.get("duration_ms") or 35000),
            "phase": "discovering",
            "window_days": _THROUGHPUT_DISCOVERY_DAYS,
            "folders_scanned": 0,
            "sessions_discovered": 0,
            "sessions_read": 0,
            "cache_hits": 0,
            "parsed": 0,
            "last_refreshed_at": last.get("completed_at"),
            "error": None,
        }
        _core._THROUGHPUT_REFRESH_JOBS[key] = job

    def progress(event, value=None):
        with _core._THROUGHPUT_REFRESH_LOCK:
            current = _core._THROUGHPUT_REFRESH_JOBS.get(key)
            if current is not job or current.get("state") != "refreshing":
                return
            if event == "sessions_discovered":
                current["sessions_discovered"] = max(0, int(value or 0))
            elif event == "phase":
                current["phase"] = str(value or "")
            elif event == "folders_scanned":
                current["folders_scanned"] = max(0, int(value or 0))
            elif event == "session_read":
                current["sessions_read"] += 1
            elif event == "cache_hit":
                current["cache_hits"] += 1
            elif event == "parsed":
                current["parsed"] += 1

    def run():
        try:
            payload, status = _core._throughput_payload(
                session_id,
                engine_filter=engine,
                force_refresh=True,
                progress=progress,
            )
            if status != 200 or not isinstance(payload, dict) or not payload.get("ok"):
                raise RuntimeError((payload or {}).get("error") or "Throughput refresh failed")
            if engine == "codex":
                progress("phase", "account_context")
                account_usage = _core._read_codex_account_usage()
                if _core._throughput_attach_account_usage(payload, account_usage):
                    # _throughput_payload persisted before account context was
                    # available. Refresh that compatible snapshot so direct
                    # cache readers receive the enriched summary too.
                    _throughput_persist_aggregate_snapshot(
                        session_id, payload, status, engine
                    )
            progress("phase", "weekly_context")
            weekly = _core._weekly_usage_block()
            completed = time.time()
            duration_ms = max(1, int((completed - now) * 1000))
            with _core._THROUGHPUT_REFRESH_LOCK:
                current = _core._THROUGHPUT_REFRESH_JOBS.get(key)
                refresh_meta = _core._throughput_refresh_public(current or job, completed)
            refresh_meta.update({
                "state": "complete",
                "completed_at": completed,
                "elapsed_ms": duration_ms,
                "expected_ms": duration_ms,
                "last_refreshed_at": completed,
            })
            model = _core._throughput_build_bootstrap(
                session_id,
                engine,
                payload,
                generated_at=completed,
                refresh=refresh_meta,
                weekly=weekly,
            )
            progress("phase", "publishing")
            if not _core._throughput_write_bootstrap(session_id, engine, model):
                raise RuntimeError("Could not persist throughput bootstrap")
            with _core._THROUGHPUT_REFRESH_LOCK:
                current = _core._THROUGHPUT_REFRESH_JOBS.get(key)
                if current is job:
                    current.update({
                        "state": "complete",
                        "completed_at": completed,
                        "elapsed_ms": duration_ms,
                        "expected_ms": duration_ms,
                        "last_refreshed_at": completed,
                    })
                    _core._THROUGHPUT_REFRESH_LAST_SUCCESS[key] = {
                        "completed_at": completed,
                        "duration_ms": duration_ms,
                    }
        except Exception as exc:
            completed = time.time()
            with _core._THROUGHPUT_REFRESH_LOCK:
                current = _core._THROUGHPUT_REFRESH_JOBS.get(key)
                if current is job:
                    current.update({
                        "state": "failed",
                        "completed_at": completed,
                        "error": str(exc),
                    })

    thread = threading.Thread(
        target=run,
        daemon=True,
        name=f"ccc-throughput-refresh-{engine}",
    )
    with _core._THROUGHPUT_REFRESH_LOCK:
        job["thread"] = thread
    thread.start()
    return _core._throughput_refresh_public(job, now)


def _throughput_empty_initial_payload(session_id, range_key=None, engine_filter=None):
    engine_filter = _core._throughput_engine_filter(engine_filter)
    is_aggregate, cutoff_epoch, label = _core._throughput_scope(session_id, range_key)
    return {
        "ok": True,
        "session_id": session_id,
        "scope": {
            "aggregate": is_aggregate,
            "range": label,
            "cutoff_epoch": cutoff_epoch,
            "total_turns": 0,
            "engine": engine_filter or "claude",
        },
        "summary": _core._throughput_summary(
            [],
            stat_cutoff_epoch=cutoff_epoch if session_id == "all_7_days" else None,
        ),
        "turns": [],
        "snapshot": {
            "state": "empty",
            "cached": False,
            "stale": True,
            "generated_at": None,
        },
    }


def _throughput_persist_aggregate_snapshot(session_id, payload, status, engine_filter=None):
    if status != 200 or not isinstance(payload, dict) or not payload.get("ok"):
        return
    try:
        _core._THROUGHPUT_DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        body = {
            "generated_at": time.time(),
            "payload": payload,
            "status": status,
        }
        path = _throughput_snapshot_path(session_id, engine_filter)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _throughput_initial_payload(session_id, repo_path=None, range_key=None, engine_filter=None):
    engine_filter = _core._throughput_engine_filter(engine_filter)
    is_aggregate, _cutoff_epoch, _label = _core._throughput_scope(session_id, range_key)
    if not is_aggregate or repo_path:
        payload = _throughput_empty_initial_payload(session_id, range_key, engine_filter)
        payload["bootstrap"] = None
        return payload, 200
    bootstrap = _core._throughput_read_bootstrap(session_id, engine_filter)
    try:
        raw = _throughput_snapshot_path(session_id, engine_filter).read_text(encoding="utf-8")
        stored = json.loads(raw)
        payload = stored.get("payload") or {}
        status = int(stored.get("status") or 200)
        if status == 200 and payload.get("ok"):
            payload = dict(payload)
            payload["snapshot"] = {
                "state": "cached",
                "cached": True,
                "stale": True,
                "generated_at": stored.get("generated_at"),
            }
            payload["bootstrap"] = bootstrap
            return payload, 200
    except Exception:
        pass
    payload = _throughput_empty_initial_payload(session_id, range_key, engine_filter)
    payload["bootstrap"] = bootstrap
    return payload, 200


def _throughput_disk_connection():
    """Return a sqlite3 connection (shared across threads via check_same_thread=False),
    creating the DB and schema on first call. Returns None if SQLite is unavailable
    or the cache dir can't be created — all callers must handle None gracefully."""
    import sqlite3  # stdlib; import here so the module import path stays unchanged

    global _throughput_disk_conn, _throughput_disk_available
    if _throughput_disk_available is False:
        return None
    with _throughput_disk_conn_lock:
        if _throughput_disk_conn is not None:
            return _throughput_disk_conn
        try:
            _core._THROUGHPUT_DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(_THROUGHPUT_DISK_CACHE_DB),
                check_same_thread=False,
                timeout=5,
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS turns_cache ("
                "  path      TEXT    PRIMARY KEY,"
                "  mtime     REAL    NOT NULL,"
                "  size      INTEGER NOT NULL,"
                "  turns_json TEXT   NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_turns_cache_path ON turns_cache(path)"
            )
            conn.commit()
            _throughput_disk_conn = conn
            _throughput_disk_available = True
            return conn
        except Exception:
            _throughput_disk_available = False
            return None


def _throughput_disk_get(key, mtime, size):
    """Look up a cache entry in SQLite. Returns the turns list if the stored
    mtime/size match, otherwise None (stale or missing)."""
    import sqlite3

    conn = _throughput_disk_connection()
    if conn is None:
        return None
    try:
        with _throughput_disk_conn_lock:
            row = conn.execute(
                "SELECT mtime, size, turns_json FROM turns_cache WHERE path = ?",
                (key,),
            ).fetchone()
        if row and row[0] == mtime and row[1] == size:
            return json.loads(row[2])
    except Exception:
        pass
    return None


def _throughput_disk_put(key, mtime, size, turns):
    """Write or replace a cache entry in SQLite. Silently swallows errors."""
    conn = _throughput_disk_connection()
    if conn is None:
        return
    try:
        turns_json = json.dumps(turns, separators=(",", ":"))
        with _throughput_disk_conn_lock:
            conn.execute(
                "INSERT OR REPLACE INTO turns_cache(path, mtime, size, turns_json)"
                " VALUES (?, ?, ?, ?)",
                (key, mtime, size, turns_json),
            )
            conn.commit()
    except Exception:
        pass


def _throughput_file_turns(path, extract_fn, progress=None):
    """Return the FULL (unfiltered-by-cutoff) turn list for a transcript at
    `path`, recomputing only if mtime/size changed.  `extract_fn()` produces the
    fresh full turn list on a cache miss.  Returns None if the path can't be
    stat'd (caller falls back to a direct, uncached extraction).

    Cache hierarchy (fastest first):
      1. In-memory dict (_THROUGHPUT_TURN_CACHE) — O(1) dict lookup.
      2. SQLite disk cache — lazy per-entry load, not bulk startup parse.
      3. extract_fn() — full transcript parse; result written to both layers."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = str(path)
    mtime = st.st_mtime
    size = st.st_size

    # 1. In-memory hit
    with _THROUGHPUT_CACHE_LOCK:
        cached = _THROUGHPUT_TURN_CACHE.get(key)
        if cached and cached["mtime"] == mtime and cached["size"] == size:
            if progress:
                progress("cache_hit")
            return cached["turns"]

    # 2. Disk cache hit (lazy — reads only this one entry)
    disk_turns = _throughput_disk_get(key, mtime, size)
    if disk_turns is not None:
        with _THROUGHPUT_CACHE_LOCK:
            _THROUGHPUT_TURN_CACHE[key] = {"mtime": mtime, "size": size, "turns": disk_turns}
        if progress:
            progress("cache_hit")
        return disk_turns

    # 3. Full parse — write through to both layers
    turns = extract_fn()
    if progress:
        progress("parsed")
    with _THROUGHPUT_CACHE_LOCK:
        _THROUGHPUT_TURN_CACHE[key] = {"mtime": mtime, "size": size, "turns": turns}
    _throughput_disk_put(key, mtime, size, turns)
    return turns


# Weekly-usage calibration pairs this week's locally-counted tokens with the
# real Anthropic weekly-limit %, giving a tokens -> "% of weekly limit"
# multiplier for the throughput dashboard.
_weekly_cal_memo = {"path": None, "mtime": None, "value": None}


def _read_weekly_calibration_file(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    path_str = str(path)
    if _core._weekly_cal_memo["path"] == path_str and _core._weekly_cal_memo["mtime"] == st.st_mtime:
        return _core._weekly_cal_memo["value"]
    value = None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        tokens = data.get("tokens")
        real_pct = data.get("real_pct")
        if isinstance(tokens, (int, float)) and tokens > 0 and isinstance(real_pct, (int, float)):
            value = {
                "pct_per_token": real_pct / tokens,
                "real_pct": real_pct,
                "tokens": tokens,
                "week_start": data.get("week_start"),
                "calibrated_at": data.get("calibrated_at"),
            }
    except (OSError, json.JSONDecodeError, ValueError):
        value = None
    _core._weekly_cal_memo["path"] = path_str
    _core._weekly_cal_memo["mtime"] = st.st_mtime
    _core._weekly_cal_memo["value"] = value
    return value


def _weekly_pct_calibration():
    """Return {pct_per_token, real_pct, tokens, week_start, calibrated_at}.
    Prefer CCC-owned calibration; the legacy cache is read-only fallback."""
    for path in (_core._CCC_WEEKLY_CAL_FILE, _core._WEEKLY_CAL_FILE):
        value = _read_weekly_calibration_file(path)
        if value:
            return value
    return None


def _save_weekly_calibration(week_start, tokens, real_pct, now_epoch=None):
    if not week_start or not isinstance(tokens, (int, float)) or tokens <= 0:
        return False
    if not isinstance(real_pct, (int, float)) or real_pct <= 0:
        return False
    if now_epoch is None:
        now_epoch = time.time()
    try:
        _core._CCC_WEEKLY_CAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "week_start": week_start.isoformat(),
            "tokens": tokens,
            "real_pct": real_pct,
            "calibrated_at": now_epoch,
        }
        tmp = _core._CCC_WEEKLY_CAL_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
        tmp.replace(_core._CCC_WEEKLY_CAL_FILE)
        _core._weekly_cal_memo["path"] = None
        _core._weekly_cal_memo["mtime"] = None
        _core._weekly_cal_memo["value"] = None
        return True
    except OSError:
        return False


def _usage_resets_week_key(resets_at_iso):
    dt = _stats_parse_ts(resets_at_iso)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).date().isoformat()


def _active_week_start_override(resets_at_iso):
    if not resets_at_iso:
        return None
    try:
        raw = _core._WEEK_START_OVERRIDE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        if data.get("applies_to_resets_week") != _core._usage_resets_week_key(resets_at_iso):
            return None
        week_start = _stats_parse_ts(data.get("week_start"))
        return week_start.astimezone() if week_start else None
    except Exception:
        return None


def _latest_week_start_reset_event(resets_at_iso):
    resets_at = _stats_parse_ts(resets_at_iso)
    if resets_at is None:
        return None
    min_epoch = resets_at.timestamp() - 7 * 86400
    max_epoch = resets_at.timestamp() + _core._RESET_DETECT_JITTER_SECS
    with _core._reset_events_lock:
        events = _core._read_usage_reset_events_unlocked()
    newest = None
    newest_epoch = None
    for event in events:
        if event.get("window") != "seven_day" or event.get("kind") not in ("unscheduled", "manual"):
            continue
        detected = _stats_parse_ts(event.get("detected_at"))
        if detected is None:
            continue
        ts_epoch = detected.timestamp()
        if ts_epoch < min_epoch or ts_epoch > max_epoch:
            continue
        if newest_epoch is None or ts_epoch > newest_epoch:
            newest = event
            newest_epoch = ts_epoch
    return newest


def _usage_week_start(resets_at_iso):
    if not resets_at_iso:
        return None
    resets_at = _stats_parse_ts(resets_at_iso)
    if resets_at is None:
        return None
    event = _latest_week_start_reset_event(resets_at_iso)
    if event:
        detected = _stats_parse_ts(event.get("detected_at"))
        if detected is not None:
            week_start = detected.astimezone()
            _core._write_week_start_override(week_start, resets_at_iso)
            return week_start
    override = _active_week_start_override(resets_at_iso)
    if override is not None:
        return override
    return resets_at.astimezone() - timedelta(days=7)


def _usage_work_window():
    def _hour_from_env(name, default):
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return default
        if val < 0 or val > 24:
            return default
        return val

    start = _hour_from_env("CCC_WORK_START", 7)
    end = _hour_from_env("CCC_WORK_END", 20)
    if end <= start:
        start, end = 7, 20
    return start, end


def _elapsed_work_hours(start_local, end_local, h_start, h_end):
    if start_local is None or end_local is None or end_local <= start_local:
        return 0.0
    total = 0.0
    cur = start_local.date()
    end = end_local.date()
    tz = start_local.tzinfo
    while cur <= end:
        ws = datetime.combine(cur, datetime_time(h_start, 0), tzinfo=tz)
        we = datetime.combine(cur, datetime_time(h_end, 0), tzinfo=tz)
        s = max(ws, start_local)
        e = min(we, end_local)
        if e > s:
            total += (e - s).total_seconds() / 3600.0
        cur += timedelta(days=1)
    return total


def usage_pace_payload(live=None, now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    if live is None:
        live = _core._live_weekly_usage(now_epoch=now_epoch)
    weekly_pct = (live or {}).get("weekly_pct")
    resets_at_iso = (live or {}).get("weekly_resets_at")
    week_start_source = "reset"
    if _latest_week_start_reset_event(resets_at_iso):
        week_start_source = "event"
    elif _active_week_start_override(resets_at_iso):
        week_start_source = "override"
    week_start = _core._usage_week_start(resets_at_iso)
    resets_at = _stats_parse_ts(resets_at_iso)
    if not isinstance(weekly_pct, (int, float)) or week_start is None or resets_at is None:
        return {
            "ok": False,
            "weekly_pct": weekly_pct,
            "projected_pct": None,
            "week_start": week_start.isoformat() if week_start else None,
            "week_start_source": week_start_source,
            "elapsed_h": 0.0,
            "total_h": 0.0,
            "source": (live or {}).get("source"),
        }
    now_local = datetime.fromtimestamp(now_epoch, tz=timezone.utc).astimezone(week_start.tzinfo)
    resets_local = resets_at.astimezone(week_start.tzinfo)
    h_start, h_end = _usage_work_window()
    total_h = _elapsed_work_hours(week_start, resets_local, h_start, h_end)
    elapsed_h = _elapsed_work_hours(week_start, now_local, h_start, h_end)
    expected_pct = (elapsed_h / total_h) * 100 if total_h else 0.0
    projected_pct = (weekly_pct / elapsed_h) * total_h if elapsed_h > 0 else None
    return {
        "ok": True,
        "weekly_pct": weekly_pct,
        "projected_pct": projected_pct,
        "week_start": week_start.isoformat(),
        "week_start_source": week_start_source,
        "weekly_resets_at": resets_at_iso,
        "elapsed_h": elapsed_h,
        "total_h": total_h,
        "hours_left": max(0.0, total_h - elapsed_h),
        "expected_pct": expected_pct,
        "delta_pp": weekly_pct - expected_pct,
        "work_start_hour": h_start,
        "work_end_hour": h_end,
        "source": (live or {}).get("source"),
        "codex": _core.codex_usage_pace_payload(now_epoch=now_epoch),
    }


def _latest_codex_usage_from_snapshots(now_epoch=None, max_age_secs=None):
    if now_epoch is None:
        now_epoch = time.time()
    newest = None
    newest_epoch = None
    with _core._usage_snapshots_lock:
        snapshots = _core._read_native_usage_snapshots_unlocked()
    for item in snapshots:
        codex = item.get("codex") if isinstance(item, dict) else None
        if not isinstance(codex, dict):
            continue
        ts_epoch = _core._usage_snapshot_epoch(item)
        if ts_epoch is None:
            continue
        if newest_epoch is None or ts_epoch > newest_epoch:
            newest = codex
            newest_epoch = ts_epoch
    if newest is None:
        return None
    if max_age_secs is not None and now_epoch - newest_epoch > max_age_secs:
        return None
    return newest


def _latest_kimi_usage_from_snapshots(now_epoch=None, max_age_secs=None):
    if now_epoch is None:
        now_epoch = time.time()
    newest = None
    newest_epoch = None
    with _core._usage_snapshots_lock:
        snapshots = _core._read_native_usage_snapshots_unlocked()
    for item in snapshots:
        kimi = item.get("kimi") if isinstance(item, dict) else None
        if not isinstance(kimi, dict):
            continue
        ts_epoch = _core._usage_snapshot_epoch(item)
        if ts_epoch is None:
            continue
        if newest_epoch is None or ts_epoch > newest_epoch:
            newest = kimi
            newest_epoch = ts_epoch
    if newest is None:
        return None
    if max_age_secs is not None and now_epoch - newest_epoch > max_age_secs:
        return None
    return newest


def codex_usage_pace_payload(codex=None, now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    if codex is None:
        codex = _latest_codex_usage_from_snapshots(now_epoch=now_epoch)
        if codex is not None:
            newest_epoch = _core._usage_snapshot_epoch({
                "ts": codex.get("snapshot_ts") or codex.get("fetched_at")
            })
            # Persisted Codex data is a fallback between rollout events. Do not
            # surface it as live usage once its source snapshot is stale.
            if newest_epoch is None or now_epoch - newest_epoch > _core._USAGE_NATIVE_FRESH_SECS:
                return {"ok": False, "weekly_pct": None, "projected_pct": None, "stale": True}
    weekly = (codex or {}).get("weekly") or {}
    weekly_pct = weekly.get("pct")
    resets_at_iso = weekly.get("resets_at")
    window_minutes = weekly.get("window_minutes") or 10080
    resets_at = _stats_parse_ts(resets_at_iso)
    if not isinstance(weekly_pct, (int, float)) or resets_at is None:
        return {"ok": False, "weekly_pct": weekly_pct, "projected_pct": None}
    week_start = resets_at.astimezone() - timedelta(minutes=window_minutes)
    now_local = datetime.fromtimestamp(now_epoch, tz=timezone.utc).astimezone(week_start.tzinfo)
    h_start, h_end = _usage_work_window()
    total_h = _elapsed_work_hours(week_start, resets_at.astimezone(week_start.tzinfo), h_start, h_end)
    elapsed_h = _elapsed_work_hours(week_start, now_local, h_start, h_end)
    projected_pct = (weekly_pct / elapsed_h) * total_h if elapsed_h > 0 else None
    return {
        "ok": True,
        "weekly_pct": weekly_pct,
        "projected_pct": projected_pct,
        "week_start": week_start.isoformat(),
        "weekly_resets_at": resets_at_iso,
        "elapsed_h": elapsed_h,
        "total_h": total_h,
        "hours_left": max(0.0, total_h - elapsed_h),
        "window_minutes": window_minutes,
        "session": (codex or {}).get("session"),
        "plan_type": (codex or {}).get("plan_type"),
    }


def kimi_usage_pace_payload(kimi=None, now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    if kimi is None:
        kimi = _latest_kimi_usage_from_snapshots(now_epoch=now_epoch)
        if kimi is not None:
            newest_epoch = _core._usage_snapshot_epoch({
                "ts": kimi.get("snapshot_ts") or kimi.get("fetched_at")
            })
            # Persisted Kimi data is a fallback between poll cycles. Do not
            # surface it as live usage once its source snapshot is stale.
            if newest_epoch is None or now_epoch - newest_epoch > _core._USAGE_NATIVE_FRESH_SECS:
                return {"ok": False, "weekly_pct": None, "projected_pct": None, "stale": True}
    weekly = (kimi or {}).get("weekly") or {}
    weekly_pct = weekly.get("pct")
    resets_at_iso = weekly.get("resets_at")
    window_minutes = weekly.get("window_minutes") or 10080
    resets_at = _stats_parse_ts(resets_at_iso)
    if not isinstance(weekly_pct, (int, float)) or resets_at is None:
        return {"ok": False, "weekly_pct": weekly_pct, "projected_pct": None}
    week_start = resets_at.astimezone() - timedelta(minutes=window_minutes)
    now_local = datetime.fromtimestamp(now_epoch, tz=timezone.utc).astimezone(week_start.tzinfo)
    h_start, h_end = _usage_work_window()
    total_h = _elapsed_work_hours(week_start, resets_at.astimezone(week_start.tzinfo), h_start, h_end)
    elapsed_h = _elapsed_work_hours(week_start, now_local, h_start, h_end)
    projected_pct = (weekly_pct / elapsed_h) * total_h if elapsed_h > 0 else None
    return {
        "ok": True,
        "weekly_pct": weekly_pct,
        "projected_pct": projected_pct,
        "week_start": week_start.isoformat(),
        "weekly_resets_at": resets_at_iso,
        "elapsed_h": elapsed_h,
        "total_h": total_h,
        "hours_left": max(0.0, total_h - elapsed_h),
        "window_minutes": window_minutes,
        "session": (kimi or {}).get("session"),
        "plan_type": (kimi or {}).get("plan_type"),
    }


def _live_weekly_usage(now_epoch=None):
    """Read the last real usage value. Prefer fresh native snapshots, falling
    back to the legacy menu-bar cache only when native data is absent or stale.
    Returns
    {weekly_pct, weekly_resets_at, session_pct, sonnet_pct} (any may be None),
    or None if the cache is missing/unreadable."""
    native = _core._latest_native_usage_snapshot(
        now_epoch=now_epoch,
        max_age_secs=_core._USAGE_NATIVE_FRESH_SECS,
    )
    if native:
        return _core._live_usage_from_snapshot(native)
    try:
        with _core._WEEKLY_PCT_FILE.open("r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    usage = (data.get("usage") or {}) if isinstance(data, dict) else {}
    sd = usage.get("seven_day") or {}
    fh = usage.get("five_hour") or {}
    sonnet = usage.get("seven_day_sonnet") or {}
    return {
        "weekly_pct": sd.get("utilization"),
        "weekly_resets_at": sd.get("resets_at"),
        "session_pct": fh.get("utilization"),
        "session_resets_at": fh.get("resets_at"),
        "sonnet_pct": sonnet.get("utilization"),
        "fable_pct": None,
        "fable_resets_at": None,
        "fetched_at": data.get("fetched_at") if isinstance(data, dict) else None,
        "source": "legacy",
    }


def _weekly_usage_block():
    """Apples-to-apples weekly usage: Claude-only token burn since the weekly
    reset, converted to "% of weekly limit" via the menu-bar calibration, paired
    with the last real scraped %. This matches the unit the user reads in their
    macOS top bar (same scope: Claude only, same window: since reset).

    Returns a dict with available=False when calibration is missing."""
    live = _core._live_weekly_usage()
    cal = _core._weekly_pct_calibration()
    week_start_epoch = None
    week_start_dt = None
    if live and live.get("weekly_resets_at"):
        week_start_dt = _core._usage_week_start(live["weekly_resets_at"])
        if week_start_dt is not None:
            week_start_epoch = week_start_dt.timestamp()
    if week_start_epoch is None and cal and cal.get("week_start"):
        try:
            week_start_dt = datetime.fromisoformat(cal["week_start"])
            week_start_epoch = week_start_dt.timestamp()
        except (ValueError, TypeError):
            week_start_epoch = None
    if week_start_epoch is None:
        week_start_epoch = time.time() - 7 * 86400
        week_start_dt = datetime.fromtimestamp(week_start_epoch).astimezone()

    # Claude-only token burn since reset, summed the same way the calibration
    # counted (raw context incl. cache + output). Uses the per-file turn cache.
    # Turns must be deduped by message_id first: resumed sessions replay the
    # same API response under fresh event uuids, so a raw sum double-counts
    # (~2x) — exactly what threw the estimate to 74% instead of ~34%.
    try:
        convs = _core._throughput_recent_conversations("claude", week_start_epoch)
    except Exception:
        convs = []
    collected = []
    for conv_row in convs:
        sid = conv_row.get("session_id")
        if not sid:
            continue
        modified = (
            conv_row.get("last_interacted")
            or conv_row.get("modified")
            or conv_row.get("mtime")
            or 0
        )
        if modified < week_start_epoch:
            continue
        engine = (conv_row.get("engine") or conv_row.get("source") or "").lower()
        if engine == "codex":
            continue
        path = conv_row.get("jsonl_path")
        if not path:
            continue
        name = conv_row.get("display_name") or conv_row.get("name") or ""
        model_hint = conv_row.get("model") or ""

        def _extract():
            parsed = _core.parse_conversation(sid)
            return _throughput_turns_from_events(
                parsed.get("events") or [],
                session_id=sid, session_name=name, engine=engine,
                model_hint=model_hint, cutoff_epoch=None,
            )

        full_turns = _core._throughput_file_turns(path, _extract)
        if not full_turns:
            continue
        for t in full_turns:
            if _throughput_turn_after_cutoff(t, week_start_epoch):
                collected.append(dict(t))

    deduped = _throughput_dedupe_turns(collected)
    claude_tokens = sum(
        (t.get("raw_context_tokens") or 0) + (t.get("tokens_out") or 0)
        for t in deduped
    )
    real_pct = (live or {}).get("weekly_pct")
    if isinstance(real_pct, (int, float)) and real_pct > 0 and claude_tokens > 0 and week_start_dt:
        if _core._save_weekly_calibration(week_start_dt, claude_tokens, real_pct):
            cal = _core._weekly_pct_calibration()
    if not cal:
        return {
            "available": False,
            "live": live,
            "claude_tokens": claude_tokens,
            "week_start_epoch": week_start_epoch,
            **_core.usage_pace_payload(live=live),
        }

    rate = cal["pct_per_token"]
    est_pct = round(claude_tokens * rate, 1)
    # Never show below the last real scrape — the local count lags the server.
    display_pct = est_pct
    if isinstance(real_pct, (int, float)):
        display_pct = max(est_pct, real_pct)
    pace = _core.usage_pace_payload(live=live)
    codex_pace = _core.codex_usage_pace_payload()
    kimi_pace = _core.kimi_usage_pace_payload()
    return {
        "available": True,
        "est_pct": est_pct,
        "real_pct": real_pct,
        "display_pct": round(display_pct, 1),
        "claude_tokens": claude_tokens,
        "pct_per_token": rate,
        "week_start_epoch": week_start_epoch,
        "weekly_resets_at": (live or {}).get("weekly_resets_at"),
        "fable_pct": (live or {}).get("fable_pct"),
        "fable_resets_at": (live or {}).get("fable_resets_at"),
        "session_pct": (live or {}).get("session_pct"),
        "source": (live or {}).get("source"),
        "calibrated_at": cal.get("calibrated_at"),
        "fetched_at": (live or {}).get("fetched_at"),
        "projected_pct": pace.get("projected_pct"),
        "week_start": pace.get("week_start"),
        "week_start_source": pace.get("week_start_source"),
        "elapsed_h": pace.get("elapsed_h"),
        "total_h": pace.get("total_h"),
        "hours_left": pace.get("hours_left"),
        "expected_pct": pace.get("expected_pct"),
        "delta_pp": pace.get("delta_pp"),
        "codex": codex_pace,
        "kimi": kimi_pace,
    }


def _throughput_turn_after_cutoff(turn, cutoff_epoch):
    """True if a turn ends at/after the cutoff (used to filter cached full
    turn lists down to the requested window)."""
    if cutoff_epoch is None:
        return True
    t_end = _stats_parse_ts(turn.get("t_end"))
    if t_end is None:
        return True
    return t_end.timestamp() >= cutoff_epoch


def _throughput_week_rankings(force_refresh=False):
    """Return per-session token contribution for the current weekly period.

    Uses the same week-start derivation as _weekly_usage_block (live
    weekly_resets_at → resets_at - 7d, falling back to now - 7d).

    Cache-only: checks in-memory then disk cache; skips sessions whose
    transcript hasn't been cached yet so the response stays fast.  Only
    sessions with week_tokens > 0 are included.  Results are sorted
    descending by week_tokens."""
    _rk = _core._THROUGHPUT_RANKINGS_CACHE.get("week")
    if (not force_refresh and _rk
            and time.time() - _rk["ts"] < _THROUGHPUT_RANKINGS_CACHE_TTL):
        return _rk["rankings"]
    # Derive week_start_epoch the same way _weekly_usage_block does.
    live = _core._live_weekly_usage()
    week_start_epoch = None
    if live and live.get("weekly_resets_at"):
        try:
            week_start_epoch = (
                _stats_parse_ts(live["weekly_resets_at"]).timestamp() - 7 * 86400
            )
        except Exception:
            week_start_epoch = None
    if week_start_epoch is None:
        week_start_epoch = time.time() - 7 * 86400

    try:
        convs = _core._throughput_recent_conversations("claude", week_start_epoch)
    except Exception:
        convs = []

    session_tokens = {}
    for conv_row in convs:
        sid = conv_row.get("session_id")
        if not sid:
            continue
        modified = (
            conv_row.get("last_interacted")
            or conv_row.get("modified")
            or conv_row.get("mtime")
            or 0
        )
        if modified < week_start_epoch:
            continue
        engine = (conv_row.get("engine") or conv_row.get("source") or "").lower()
        if engine == "codex":
            continue
        path = conv_row.get("jsonl_path")
        if not path:
            continue

        # Cache-only lookup: check in-memory cache, then disk cache.
        # Skip entirely if neither has data — don't block on a full parse.
        key = str(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        mtime = st.st_mtime
        size = st.st_size

        full_turns = None
        with _THROUGHPUT_CACHE_LOCK:
            cached = _THROUGHPUT_TURN_CACHE.get(key)
            if cached and cached["mtime"] == mtime and cached["size"] == size:
                full_turns = cached["turns"]

        if full_turns is None:
            disk_turns = _throughput_disk_get(key, mtime, size)
            if disk_turns is not None:
                with _THROUGHPUT_CACHE_LOCK:
                    _THROUGHPUT_TURN_CACHE[key] = {
                        "mtime": mtime, "size": size, "turns": disk_turns
                    }
                full_turns = disk_turns

        if full_turns is None:
            continue  # not yet cached — skip to stay fast

        week_turns = [
            dict(t) for t in full_turns
            if _throughput_turn_after_cutoff(t, week_start_epoch)
        ]
        if not week_turns:
            continue

        # Per-session dedupe handles resumed sessions that replay the same
        # message_id under a fresh event uuid (would otherwise double-count).
        deduped = _throughput_dedupe_turns(week_turns)
        week_tok = sum(
            (t.get("raw_context_tokens") or t.get("tokens_in") or 0) + (t.get("tokens_out") or 0)
            for t in deduped
        )
        if week_tok > 0:
            session_tokens[sid] = session_tokens.get(sid, 0) + week_tok

    rankings = [
        {"session_id": sid, "week_tokens": tok}
        for sid, tok in session_tokens.items()
    ]
    rankings.sort(key=lambda r: r["week_tokens"], reverse=True)
    _core._THROUGHPUT_RANKINGS_CACHE["week"] = {"ts": time.time(), "rankings": rankings}
    return rankings


def _throughput_payload(
    session_id,
    repo_path=None,
    range_key=None,
    engine_filter=None,
    *,
    force_refresh=False,
    progress=None,
):
    engine_filter = _core._throughput_engine_filter(engine_filter)
    is_aggregate, cutoff_epoch, label = _core._throughput_scope(session_id, range_key)
    if is_aggregate:
        # cutoff_epoch shifts every call (float); key on stable scope + engine.
        _cache_key = _core._throughput_aggregate_cache_key(
            f"{session_id}:{range_key or ''}", engine_filter
        )
        _cached = _core._THROUGHPUT_AGG_CACHE.get(_cache_key)
        if not force_refresh and _cached and (time.time() - _cached["ts"] < _THROUGHPUT_AGG_CACHE_TTL):
            return _cached["payload"], _cached["status"]
    turns = []
    if is_aggregate:
        bounded_recent = session_id == "all_7_days" or bool(range_key)
        discovery_cutoff = (
            time.time() - _THROUGHPUT_DISCOVERY_DAYS * 86400
            if session_id == "all_7_days" and not range_key
            else cutoff_epoch
        )
        try:
            if bounded_recent:
                # The interactive dashboard needs at most the current billing
                # week plus its prior-week overlay, both covered by 14 days.
                recent = _core._throughput_recent_conversations(
                    engine_filter,
                    discovery_cutoff,
                    progress=progress,
                )
            elif engine_filter == "kimi":
                # The archive scanner only knows Claude/Codex sessions; Kimi
                # discovery is a cheap dir scan, so run it directly for the
                # wider scopes too.
                recent = _core._throughput_recent_conversations(
                    engine_filter,
                    cutoff_epoch,
                    progress=progress,
                )
            else:
                # Preserve the public semantics of 56-day and all-time scopes.
                all_conversations = _core.find_all_conversations(
                    resolve_pr_states=False,
                    resolve_effective=False,
                    resolve_worktree_dirty=False,
                )
                recent = []
                for row in all_conversations:
                    modified = (
                        row.get("last_interacted")
                        or row.get("modified")
                        or row.get("mtime")
                        or 0
                    )
                    if cutoff_epoch is not None and modified < cutoff_epoch:
                        continue
                    recent.append(row)
        except Exception as e:
            return {"error": f"Failed to list conversations: {str(e)}"}, 500
        if repo_path:
            try:
                requested_repo = Path(repo_path).resolve(strict=False)
                recent = [
                    row for row in recent
                    if Path(row.get("folder_path") or "").resolve(strict=False) == requested_repo
                ]
            except Exception:
                recent = []
        if progress:
            progress("phase", "reading")
            progress("sessions_discovered", len(recent))

        for conv_row in recent:
            sid = conv_row.get("session_id")
            name = conv_row.get("display_name") or conv_row.get("name") or "Untitled"
            engine = conv_row.get("engine") or conv_row.get("source") or ""
            model_hint = conv_row.get("model") or ""
            is_codex = engine == "codex"
            is_kimi = engine == "kimi"
            if not bounded_recent and not is_codex and not is_kimi:
                try:
                    is_codex = _core._is_codex_session(sid)
                except Exception:
                    continue
            if engine_filter == "codex":
                if not is_codex:
                    continue
            elif engine_filter == "kimi":
                if not is_kimi:
                    continue
            elif is_codex or is_kimi:
                continue
            if progress:
                progress("session_read")

            # Resolve the transcript path so we can (mtime,size)-cache its full
            # turn list. Extraction runs with cutoff_epoch=None — the window
            # filter is applied below, never baked into the cached entry.
            if is_kimi:
                # One row per session dir, one cache entry per agent wire file.
                full_turns = []
                for wire_path in _core._throughput_kimi_wire_files(_core._kimi_session_dir(sid)):
                    def _extract(p=wire_path):
                        return _throughput_kimi_turns_from_wire(
                            p, sid, session_name=name, model_hint=model_hint,
                            cutoff_epoch=None,
                        )

                    try:
                        file_turns = _core._throughput_file_turns(
                            str(wire_path), _extract, progress=progress
                        )
                    except Exception:
                        file_turns = None
                    if file_turns is None:
                        try:
                            if progress:
                                progress("parsed")
                            file_turns = _extract()
                        except Exception:
                            continue
                    full_turns.extend(file_turns)
                full_turns.sort(key=lambda t: t.get("t_start") or "")
            else:
                if is_codex:
                    cache_path = _core._resolve_codex_rollout_path(sid)

                    def _extract():
                        return _core._throughput_codex_turns_from_file(
                            sid, session_name=name, model_hint=model_hint, cutoff_epoch=None
                        )
                else:
                    cache_path = conv_row.get("jsonl_path")

                    def _extract():
                        parsed = _core.parse_conversation(sid, repo_path=repo_path)
                        return _throughput_turns_from_events(
                            parsed.get("events") or [],
                            session_id=sid,
                            session_name=name,
                            engine=engine,
                            model_hint=model_hint,
                            cutoff_epoch=None,
                        )

                full_turns = None
                if cache_path:
                    full_turns = _core._throughput_file_turns(cache_path, _extract, progress=progress)
                if full_turns is None:
                    # No stat-able path (or cache disabled) — fall back to a direct,
                    # cutoff-scoped extraction so we never serve nothing.
                    _fb_cutoff = (
                        time.time() - 14 * 86400
                        if session_id == "all_7_days" and not range_key and cutoff_epoch is not None
                        else cutoff_epoch
                    )
                    try:
                        if progress:
                            progress("parsed")
                        if is_codex:
                            turns.extend(_core._throughput_codex_turns_from_file(
                                sid, session_name=name, model_hint=model_hint,
                                cutoff_epoch=_fb_cutoff,
                            ))
                        else:
                            parsed = _core.parse_conversation(sid, repo_path=repo_path)
                            turns.extend(_throughput_turns_from_events(
                                parsed.get("events") or [],
                                session_id=sid, session_name=name, engine=engine,
                                model_hint=model_hint, cutoff_epoch=_fb_cutoff,
                            ))
                    except Exception:
                        continue
                    continue

            # Copy each kept turn so the per-request turn_index reassignment
            # below doesn't mutate the shared cached dicts.
            _turn_cutoff = (
                time.time() - 14 * 86400
                if session_id == "all_7_days" and not range_key and cutoff_epoch is not None
                else cutoff_epoch
            )
            for t in full_turns:
                if _throughput_turn_after_cutoff(t, _turn_cutoff):
                    turns.append(dict(t))
        if progress:
            progress("phase", "finalizing")
        turns.sort(key=lambda t: t.get("t_start") or "")
        for idx, turn in enumerate(turns, 1):
            turn["turn_index"] = idx
    else:
        engine, model_hint = _throughput_session_hints(session_id)
        if engine == "codex" or _core._is_codex_session(session_id):
            turns = _core._throughput_codex_turns_from_file(
                session_id,
                session_name="",
                model_hint=model_hint,
                cutoff_epoch=cutoff_epoch,
            )
        elif engine == "kimi" or _core._kimi_session_dir(session_id) is not None or _core._is_kimi_session(session_id):
            turns = _core._throughput_kimi_turns_from_file(
                session_id,
                session_name="",
                model_hint=model_hint,
                cutoff_epoch=cutoff_epoch,
            )
        else:
            try:
                parsed = _core.parse_conversation(session_id, repo_path=repo_path)
            except Exception as e:
                return {"error": f"Failed to parse conversation: {str(e)}"}, 500
            turns = _throughput_turns_from_events(
                parsed.get("events") or [],
                session_id=session_id,
                session_name="",
                engine=engine,
                model_hint=model_hint,
                cutoff_epoch=cutoff_epoch,
            )

    turns = _throughput_dedupe_turns(turns)
    # Aggregate views: the chart uses summary.hourly/daily; sending all turns
    # (potentially 20k+) bloats the payload to 28MB and costs 130ms just in
    # JSON serialisation. Send the tail only so the per-turn list stays usable.
    serialised_turns = turns if not is_aggregate else turns[-200:]
    _payload = {
        "ok": True,
        "session_id": session_id,
        "scope": {
            "aggregate": is_aggregate,
            "range": label,
            "cutoff_epoch": cutoff_epoch,
            "total_turns": len(turns),
            "engine": engine_filter or "claude",
        },
        "summary": _core._throughput_summary(
            turns,
            stat_cutoff_epoch=cutoff_epoch if is_aggregate else None,
        ),
        "turns": serialised_turns,
    }
    _status = 200
    if is_aggregate:
        _cache_key = _core._throughput_aggregate_cache_key(
            f"{session_id}:{range_key or ''}", engine_filter
        )
        _core._THROUGHPUT_AGG_CACHE[_cache_key] = {
            "ts": time.time(),
            "payload": _payload,
            "status": _status,
        }
        if not range_key:
            _throughput_persist_aggregate_snapshot(session_id, _payload, _status, engine_filter)
    return _payload, _status


def _throughput_history_payload(cache_only=False):
    """56-day daily throughput history.

    cache_only=True is for lightweight dashboard badges: return an existing
    aggregate snapshot if one is fresh, but never compute the expensive 56-day
    aggregate just to paint a footer pill.
    """
    cached = _core._THROUGHPUT_AGG_CACHE.get(
        _core._throughput_aggregate_cache_key("all_56_days", "claude")
    )
    if cached and (time.time() - cached.get("ts", 0.0) < _THROUGHPUT_AGG_CACHE_TTL):
        payload = cached.get("payload") or {}
        if cached.get("status") == 200:
            daily = (payload.get("summary") or {}).get("daily") or []
            return {"ok": True, "daily": daily, "cached": True}, 200
    if cache_only:
        return {"ok": True, "daily": [], "cached": False}, 200

    payload, status = _core._throughput_payload("all_56_days")
    if status != 200:
        return payload, status
    daily = (payload.get("summary") or {}).get("daily") or []
    return {"ok": True, "daily": daily, "cached": False}, 200


# ---------------------------------------------------------------------------
# Throughput drill-down — bounded time-window per-session breakdown plus a
# persisted daily digest. Both gate discovery by the window start (a
# transcript untouched since then cannot contain turns inside the window) and
# extract through the (mtime,size) per-transcript turn cache, so a zoom or a
# digest never does O(all-transcripts) parse work.
# ---------------------------------------------------------------------------

_THROUGHPUT_WINDOW_CACHE = {}      # (start,end,engine,limit) -> {"ts","payload","status"}
_THROUGHPUT_WINDOW_CACHE_TTL = 60
_THROUGHPUT_WINDOW_CACHE_MAX = 64
_THROUGHPUT_WINDOW_MAX_SPAN_SEC = 7 * 86400
_THROUGHPUT_WINDOW_MAX_AGE_SEC = 56 * 86400
_THROUGHPUT_DAILY_SCHEMA = 1
_THROUGHPUT_DAILY_CACHE = {}       # (iso_date,engine) -> {"ts","payload","status"}
_THROUGHPUT_DAILY_CACHE_TTL = 180
_THROUGHPUT_DAILY_LOCK = threading.Lock()


def _throughput_daily_snapshot_path(date_str, engine_filter=None):
    engine = _core._throughput_engine_filter(engine_filter)
    suffix = "" if engine == "claude" else f"-{engine}"
    safe = "".join(ch for ch in str(date_str or "") if ch.isdigit() or ch == "-")
    return _core._THROUGHPUT_DISK_CACHE_DIR / f"daily-{safe or 'unknown'}{suffix}.json"


def _throughput_window_turns(start_epoch, end_epoch, engine_filter):
    """Deduped turns whose t_end falls inside [start_epoch, end_epoch)."""
    engine = _core._throughput_engine_filter(engine_filter)
    recent = _core._throughput_recent_conversations(engine, start_epoch)
    turns = []
    for conv_row in recent:
        sid = conv_row.get("session_id")
        name = conv_row.get("display_name") or conv_row.get("name") or "Untitled"
        row_engine = conv_row.get("engine") or conv_row.get("source") or ""
        model_hint = conv_row.get("model") or ""
        is_codex = row_engine == "codex"
        is_kimi = row_engine == "kimi"
        if engine == "codex":
            if not is_codex:
                continue
        elif engine == "kimi":
            if not is_kimi:
                continue
        elif is_codex or is_kimi:
            continue

        if is_kimi:
            file_jobs = [
                (
                    str(wire_path),
                    (lambda p=wire_path: _throughput_kimi_turns_from_wire(
                        p, sid, session_name=name, model_hint=model_hint,
                        cutoff_epoch=None,
                    )),
                )
                for wire_path in _core._throughput_kimi_wire_files(_core._kimi_session_dir(sid))
            ]
        elif is_codex:
            def _extract():
                return _core._throughput_codex_turns_from_file(
                    sid, session_name=name, model_hint=model_hint, cutoff_epoch=None
                )
            file_jobs = [(conv_row.get("jsonl_path"), _extract)]
        else:
            def _extract():
                parsed = _core.parse_conversation(sid)
                return _throughput_turns_from_events(
                    parsed.get("events") or [],
                    session_id=sid,
                    session_name=name,
                    engine=row_engine,
                    model_hint=model_hint,
                    cutoff_epoch=None,
                )
            file_jobs = [(conv_row.get("jsonl_path"), _extract)]

        full_turns = []
        for cache_path, extract_fn in file_jobs:
            try:
                file_turns = (
                    _core._throughput_file_turns(cache_path, extract_fn)
                    if cache_path else extract_fn()
                )
            except Exception:
                continue
            if file_turns is None:
                try:
                    file_turns = extract_fn()
                except Exception:
                    continue
            full_turns.extend(file_turns)

        folder = conv_row.get("folder_path") or ""
        for t in full_turns:
            t_end = _stats_parse_ts(t.get("t_end"))
            if not t_end:
                continue
            ts = t_end.timestamp()
            if ts < start_epoch or ts >= end_epoch:
                continue
            copied = dict(t)
            copied["folder_path"] = folder
            turns.append(copied)
    turns.sort(key=lambda t: t.get("t_start") or "")
    return _throughput_dedupe_turns(turns)


def _throughput_sessions_from_turns(turns, limit=50):
    """Per-session aggregates for a turn set, sorted by cache-adjusted burn.

    Returns (rows, total_session_count) with rows capped at ``limit``.
    """
    by_session = {}
    for t in turns:
        sid = t.get("session_id") or ""
        entry = by_session.get(sid)
        if entry is None:
            entry = by_session[sid] = {
                "session_id": sid,
                "session_name": t.get("session_name") or "",
                "engine": t.get("engine") or "",
                "folder_path": t.get("folder_path") or "",
                "first_turn_at": t.get("t_start") or "",
                "last_turn_at": t.get("t_end") or "",
                "_models": {},
                "_bucket": _throughput_empty_bucket(),
            }
        _throughput_add_bucket(entry["_bucket"], t)
        model = t.get("model") or ""
        if model:
            entry["_models"][model] = entry["_models"].get(model, 0) + 1
        if (t.get("t_end") or "") > (entry["last_turn_at"] or ""):
            entry["last_turn_at"] = t.get("t_end") or ""
        t_start = t.get("t_start") or ""
        if t_start and (not entry["first_turn_at"] or t_start < entry["first_turn_at"]):
            entry["first_turn_at"] = t_start
    rows = []
    for entry in by_session.values():
        bucket = _throughput_finalize_bucket(entry.pop("_bucket"))
        models = entry.pop("_models")
        row = dict(entry)
        row.update(bucket)
        row["cache_adjusted_tokens"] = round(
            (bucket.get("effective_input_tokens") or 0.0)
            + (bucket.get("output_tokens") or 0),
            2,
        )
        row["models"] = sorted(models, key=models.get, reverse=True)
        rows.append(row)
    rows.sort(key=lambda r: r.get("cache_adjusted_tokens") or 0, reverse=True)
    return rows[:limit], len(rows)


def _throughput_window_payload(start, end, engine_filter=None, limit=50):
    """Per-session breakdown of a bounded time window (the zoom drill)."""
    engine = _core._throughput_engine_filter(engine_filter)
    try:
        start_epoch = float(start)
        end_epoch = float(end)
    except (TypeError, ValueError):
        return {"error": "start and end must be epoch seconds"}, 400
    try:
        limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        limit = 50
    if end_epoch <= start_epoch:
        return {"error": "end must be after start"}, 400
    now = time.time()
    if end_epoch - start_epoch > _core._THROUGHPUT_WINDOW_MAX_SPAN_SEC:
        return {"error": "window too wide (max 26 hours)"}, 400
    if start_epoch < now - _core._THROUGHPUT_WINDOW_MAX_AGE_SEC:
        return {"error": "window too old (max 56 days)"}, 400

    key = (int(start_epoch), int(end_epoch), engine, limit)
    cached = _THROUGHPUT_WINDOW_CACHE.get(key)
    if cached and now - cached["ts"] < _THROUGHPUT_WINDOW_CACHE_TTL:
        return cached["payload"], cached["status"]

    turns = _throughput_window_turns(start_epoch, end_epoch, engine)
    sessions, session_count = _throughput_sessions_from_turns(turns, limit=limit)
    total_bucket = _throughput_empty_bucket()
    for t in turns:
        _throughput_add_bucket(total_bucket, t)
    totals = _throughput_finalize_bucket(total_bucket)
    totals["cache_adjusted_tokens"] = round(
        (totals.get("effective_input_tokens") or 0.0)
        + (totals.get("output_tokens") or 0),
        2,
    )
    payload = {
        "ok": True,
        "scope": {
            "start_epoch": start_epoch,
            "end_epoch": end_epoch,
            "engine": engine,
        },
        "totals": totals,
        "session_count": session_count,
        "sessions": sessions,
        "sessions_truncated": max(0, session_count - len(sessions)),
    }
    if len(_THROUGHPUT_WINDOW_CACHE) >= _THROUGHPUT_WINDOW_CACHE_MAX:
        oldest = min(_THROUGHPUT_WINDOW_CACHE, key=lambda k: _THROUGHPUT_WINDOW_CACHE[k]["ts"])
        _THROUGHPUT_WINDOW_CACHE.pop(oldest, None)
    _THROUGHPUT_WINDOW_CACHE[key] = {"ts": now, "payload": payload, "status": 200}
    return payload, 200


def _throughput_local_day_bounds(date_str=None):
    """(start_epoch, end_epoch, iso_date) for a local calendar day."""
    local_midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    key = str(date_str or "").strip().lower()
    if key in ("", "today"):
        day = local_midnight
    elif key == "yesterday":
        day = local_midnight - timedelta(days=1)
    else:
        parsed = datetime.strptime(key, "%Y-%m-%d")
        day = parsed.replace(tzinfo=local_midnight.tzinfo)
    return day.timestamp(), (day + timedelta(days=1)).timestamp(), day.strftime("%Y-%m-%d")


def _throughput_daily_ticket_counts(start_epoch, end_epoch):
    """WatchTower queue activity for the day: one queue read, no per-row work.

    The queue has no explicit "failed" state (open/in_progress/closed), so the
    digest reports opened/closed plus what was still open at day end.
    """
    try:
        items = _core._q.list_items()
    except Exception:
        return {"available": False, "opened": 0, "closed": 0, "still_open": 0}

    def _epoch(ts_str):
        dt = _stats_parse_ts(ts_str) if ts_str else None
        return dt.timestamp() if dt else None

    opened = closed = still_open = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        created = _epoch(item.get("created_at"))
        done = _epoch(item.get("closed_at"))
        if created is not None and start_epoch <= created < end_epoch:
            opened += 1
        if done is not None and start_epoch <= done < end_epoch:
            closed += 1
        if (
            created is not None
            and created < end_epoch
            and (done is None or done >= end_epoch)
        ):
            still_open += 1
    return {
        "available": True,
        "opened": opened,
        "closed": closed,
        "still_open": still_open,
    }


def _throughput_read_daily_snapshot(iso_date, engine_filter=None):
    try:
        raw = _throughput_daily_snapshot_path(iso_date, engine_filter).read_text(
            encoding="utf-8"
        )
        model = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(model, dict) or model.get("schema") != _THROUGHPUT_DAILY_SCHEMA:
        return None
    if model.get("date") != iso_date or not model.get("ok"):
        return None
    if _core._throughput_engine_filter(model.get("engine")) != _core._throughput_engine_filter(engine_filter):
        return None
    return model


def _throughput_persist_daily_snapshot(payload):
    """Persist a finished day's digest (same pattern as aggregate snapshots)."""
    if not isinstance(payload, dict) or not payload.get("ok") or not payload.get("final"):
        return
    try:
        _core._THROUGHPUT_DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _throughput_daily_snapshot_path(payload.get("date"), payload.get("engine"))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _throughput_daily_compare_block(iso_date, engine, now):
    """Yesterday-at-the-same-time token totals for the pace readout."""
    try:
        y_start, y_end, y_iso = _throughput_local_day_bounds(
            (datetime.strptime(iso_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        )
    except ValueError:
        return None
    y_payload, y_status = _throughput_daily_payload(y_iso, engine_filter=engine)
    if y_status != 200 or not isinstance(y_payload, dict):
        return None
    elapsed = max(0.0, now - _throughput_local_day_bounds(iso_date)[0])
    same_time_tokens = 0.0
    total_tokens = 0.0
    for row in y_payload.get("hourly") or []:
        dt = _stats_parse_ts(str(row.get("hour") or "") + ":00")
        tokens = (row.get("effective_input_tokens") or 0.0) + (row.get("output_tokens") or 0)
        total_tokens += tokens
        if dt is None:
            continue
        offset = dt.timestamp() - y_start
        if offset + 3600 <= elapsed:
            same_time_tokens += tokens
        elif offset < elapsed:
            same_time_tokens += tokens * ((elapsed - offset) / 3600.0)
    return {
        "date": y_iso,
        "cache_adjusted_tokens_to_same_time": round(same_time_tokens, 2),
        "cache_adjusted_tokens_total": round(total_tokens, 2),
    }


def _throughput_daily_payload(date_str=None, engine_filter=None, force_refresh=False):
    """Daily throughput digest: sessions, tokens by model, top lanes, tickets.

    Finished days persist to a disk snapshot (write-once) and never recompute;
    today recomputes at most every _THROUGHPUT_DAILY_CACHE_TTL seconds behind a
    single-flight lock, so strip/report polls coalesce.
    """
    engine = _core._throughput_engine_filter(engine_filter)
    try:
        start_epoch, end_epoch, iso_date = _throughput_local_day_bounds(date_str)
    except ValueError:
        return {"error": "date must be YYYY-MM-DD, 'today', or 'yesterday'"}, 400
    now = time.time()
    if start_epoch > now:
        return {"error": "date is in the future"}, 400
    if start_epoch < now - _core._THROUGHPUT_WINDOW_MAX_AGE_SEC:
        return {"error": "date too old (max 56 days)"}, 400
    is_final = end_epoch <= now
    key = (iso_date, engine)

    cached = _THROUGHPUT_DAILY_CACHE.get(key)
    if not force_refresh and cached and (
        is_final or now - cached["ts"] < _THROUGHPUT_DAILY_CACHE_TTL
    ):
        return cached["payload"], cached["status"]
    if is_final and not force_refresh:
        snapshot = _throughput_read_daily_snapshot(iso_date, engine)
        if snapshot:
            _THROUGHPUT_DAILY_CACHE[key] = {"ts": now, "payload": snapshot, "status": 200}
            return snapshot, 200

    with _core._THROUGHPUT_DAILY_LOCK:
        cached = _THROUGHPUT_DAILY_CACHE.get(key)
        if not force_refresh and cached and (
            is_final or time.time() - cached["ts"] < _THROUGHPUT_DAILY_CACHE_TTL
        ):
            return cached["payload"], cached["status"]

        turns = _throughput_window_turns(start_epoch, end_epoch, engine)
        summary = _core._throughput_summary(turns)
        top_sessions, session_count = _throughput_sessions_from_turns(turns, limit=20)
        active_cutoff = time.time() - 3600
        active_last_hour = set()
        if not is_final:
            for t in turns:
                t_end = _stats_parse_ts(t.get("t_end"))
                if t_end and t_end.timestamp() >= active_cutoff:
                    active_last_hour.add(t.get("session_id") or "")
        payload = {
            "ok": True,
            "schema": _THROUGHPUT_DAILY_SCHEMA,
            "date": iso_date,
            "engine": engine,
            "generated_at": time.time(),
            "final": is_final,
            "day_start_epoch": start_epoch,
            "day_end_epoch": end_epoch,
            "totals": {
                "turns": summary.get("total_turns") or 0,
                "sessions": session_count,
                "cache_adjusted_tokens": round(
                    (summary.get("total_effective_input_tokens") or 0.0)
                    + (summary.get("total_output_tokens") or 0),
                    2,
                ),
                "raw_context_tokens": summary.get("total_raw_context_tokens") or 0,
                "output_tokens": summary.get("total_output_tokens") or 0,
                "cost_usd": summary.get("cost_usd") or 0.0,
                "active_duration_sec": summary.get("total_active_duration_sec") or 0.0,
                "cache_hit_ratio": summary.get("cache_hit_ratio") or 0.0,
            },
            "per_model": summary.get("per_model") or [],
            "hourly": summary.get("hourly") or [],
            "top_sessions": top_sessions,
            "sessions_truncated": max(0, session_count - len(top_sessions)),
            "active_sessions_last_hour": len(active_last_hour),
            "tickets": _throughput_daily_ticket_counts(start_epoch, end_epoch),
        }
        _THROUGHPUT_DAILY_CACHE[key] = {
            "ts": time.time(), "payload": payload, "status": 200,
        }
        _throughput_persist_daily_snapshot(payload)

    if not is_final:
        compare = _throughput_daily_compare_block(iso_date, engine, now)
        if compare:
            payload["yesterday"] = compare
    return payload, 200

