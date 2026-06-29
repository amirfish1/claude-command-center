"""Model-drift advisor — recommend cheaper/stronger models per live session.

A single Claude Code session usually runs one model for both planning and
execution. Planning wants a strong model (Opus); mechanical execution (draining
a queue, applying a known fix, deploying) does not. This module reads a
session's recent transcript turns, scores them on a mechanical(0) ->
reasoning(100) axis, and emits a recommendation in BOTH directions:

  * a top-tier session (Opus/Fable) gone mechanical  -> downgrade to Sonnet,
    or — when we can see a plan->execute transition — spawn a cheap execution
    session instead of dragging the planning session down.
  * a cheap session (Sonnet/Haiku) doing hard, ambiguous reasoning -> upgrade
    to Opus, so we are never silently stuck too low.

Design goals: stdlib-only, no imports from server.py (server calls in, not the
reverse), pure scoring functions + a small atomic JSON log store so the monitor
view can show what was recommended, what was applied, and tokens saved.

The scorer is deliberately cheap (no LLM): it is right ~80% of the time on the
clear cases and abstains (returns no recommendation) in the gray zone rather
than guessing. A future Haiku tie-breaker can be layered on the abstain path.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Model tiers + price estimates
# --------------------------------------------------------------------------
# Cost/capability rank. Higher = pricier + stronger. Used to find the cheaper
# neighbor (downgrade) and the stronger neighbor (upgrade).
_TIER = {"haiku": 1, "sonnet": 2, "opus": 3, "fable": 4}

# List-price estimates, $ per 1M OUTPUT tokens. These are deliberately rough and
# overridable via CCC_MODEL_PRICES='{"opus":75,"sonnet":15}' — the monitor labels
# its dollar figure an estimate. We only ever use the *delta* between two tiers,
# so being off by a constant factor does not change which sessions look wasteful.
_DEFAULT_PRICES = {"opus": 75.0, "fable": 75.0, "sonnet": 15.0, "haiku": 4.0}


def _prices():
    raw = os.environ.get("CCC_MODEL_PRICES")
    if raw:
        try:
            over = json.loads(raw)
            if isinstance(over, dict):
                merged = dict(_DEFAULT_PRICES)
                for k, v in over.items():
                    try:
                        merged[_family(k)] = float(v)
                    except (TypeError, ValueError):
                        continue
                return merged
        except (ValueError, TypeError):
            pass
    return dict(_DEFAULT_PRICES)


def _family(model):
    """Map any model string to a tier family: opus/sonnet/haiku/fable, or ''."""
    if not model:
        return ""
    m = str(model).lower()
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if fam in m:
            return fam
    return ""


def model_tier(model):
    return _TIER.get(_family(model), 0)


# Canonical downgrade/upgrade targets. We only move one rung and only between
# the two workhorse tiers — never auto-suggest Haiku (too risky) or Fable.
_DOWNGRADE_TO = "sonnet"
_UPGRADE_TO = "opus"

# --------------------------------------------------------------------------
# Transcript reading
# --------------------------------------------------------------------------

_IMPERATIVE = re.compile(
    r"^\s*(continue|go|yes|y|ok|okay|fix|push|ship|next|do it|proceed|run it|continue from where)\b",
    re.I,
)
_OPEN_ENDED = re.compile(
    r"\b(what|why|how|should we|should i|do you think|thoughts|which|"
    r"tradeoff|trade-off|compare|approach|strategy|design|idea|opinion|"
    r"explain|recommend|vs\.?|or should)\b",
    re.I,
)
# Assistant prose that signals planning/reasoning rather than execution.
_PLANNING_PROSE = re.compile(
    r"\b(option [abc123]|approach|tradeoff|trade-off|i recommend|my rec\b|"
    r"alternativ|let'?s (think|decide|consider)|on the other hand|"
    r"pros? and cons?|the (real )?question is|two (ways|options)|"
    r"design|architecture|strategy)\b",
    re.I,
)
# Tools that imply reasoning/dialogue rather than mechanical execution.
_REASONING_TOOLS = {
    "AskUserQuestion",
    "ExitPlanMode",
    "WebSearch",
    "WebFetch",
    "Skill",
    "Task",
    "TaskCreate",
}
# Tools that imply mechanical execution.
_EXEC_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit", "ScheduleWakeup"}


def _text_of(message):
    """Flatten a message's content to plain text."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                out.append(c.get("text") or "")
    return " ".join(out)


def _tools_of(message):
    out = []
    content = message.get("content")
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                out.append(c.get("name") or "")
    return out


def read_recent_turns(path, max_turns=14):
    """Return the last ``max_turns`` real turns of a session JSONL as a list of
    {role, text, model, tools, ts}. Skips meta/synthetic lines and tool_result
    user echoes (which are not human prompts). Reads the whole file but keeps
    only a bounded tail in memory — fine for the live-session set, which is
    small and gated upstream."""
    turns = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                t = o.get("type")
                if t not in ("user", "assistant"):
                    continue
                if o.get("isMeta"):
                    continue
                msg = o.get("message") or {}
                if t == "user":
                    content = msg.get("content")
                    # Drop tool_result-only user turns (not human prompts).
                    if isinstance(content, list) and not any(
                        isinstance(c, dict) and c.get("type") == "text" for c in content
                    ):
                        continue
                    text = _text_of(msg)
                    if not text.strip():
                        continue
                    if text.lstrip().startswith("<"):  # injected system blocks
                        continue
                    turns.append(
                        {"role": "user", "text": text, "model": "", "tools": [], "ts": o.get("timestamp")}
                    )
                else:
                    turns.append(
                        {
                            "role": "assistant",
                            "text": _text_of(msg),
                            "model": msg.get("model") or "",
                            "tools": _tools_of(msg),
                            "ts": o.get("timestamp"),
                        }
                    )
                if len(turns) > max_turns * 4:
                    turns = turns[-max_turns * 2 :]
    except OSError:
        return []
    return turns[-max_turns:]


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _phase_of(turns):
    """Classify a slice of turns as 'planning' / 'executing' / 'mixed' from tool
    mix and prose, independent of the 0-100 score."""
    plan = exec_ = 0
    for tn in turns:
        tools = tn["tools"]
        plan += sum(1 for x in tools if x in _REASONING_TOOLS)
        exec_ += sum(1 for x in tools if x in _EXEC_TOOLS)
        if tn["role"] == "assistant" and _PLANNING_PROSE.search(tn["text"] or ""):
            plan += 1
        if tn["role"] == "user":
            if _IMPERATIVE.match(tn["text"] or ""):
                exec_ += 1
            elif _OPEN_ENDED.search(tn["text"] or ""):
                plan += 1
    if plan == 0 and exec_ == 0:
        return "mixed"
    if exec_ >= 2 * max(plan, 1) and exec_ >= 3:
        return "executing"
    if plan >= 2 * max(exec_, 1) and plan >= 2:
        return "planning"
    return "mixed"


def _raw_score(turns):
    """Numeric mechanical(0)->reasoning(100) score + features for a slice."""
    if not turns:
        return 50.0, {}
    user_turns = [t for t in turns if t["role"] == "user"]
    asst_turns = [t for t in turns if t["role"] == "assistant"]

    imperative = sum(1 for t in user_turns if _IMPERATIVE.match(t["text"] or ""))
    open_ended = sum(1 for t in user_turns if _OPEN_ENDED.search(t["text"] or ""))
    long_prompts = sum(1 for t in user_turns if len((t["text"] or "")) > 220)

    exec_tools = sum(sum(1 for x in t["tools"] if x in _EXEC_TOOLS) for t in asst_turns)
    reasoning_tools = sum(
        sum(1 for x in t["tools"] if x in _REASONING_TOOLS) for t in asst_turns
    )
    planning_prose = sum(1 for t in asst_turns if _PLANNING_PROSE.search(t["text"] or ""))

    # Autonomy ratio: many assistant turns per human prompt -> mechanical drain.
    autonomy = len(asst_turns) / max(len(user_turns), 1)

    score = 50.0
    score -= 9 * imperative
    score += 11 * open_ended
    score += 6 * long_prompts
    score -= 3 * exec_tools
    score += 8 * reasoning_tools
    score += 5 * planning_prose
    if autonomy >= 6:
        score -= 18
    elif autonomy >= 3:
        score -= 9
    score = max(0.0, min(100.0, score))
    return score, {
        "imperative": imperative,
        "open_ended": open_ended,
        "long_prompts": long_prompts,
        "exec_tools": exec_tools,
        "reasoning_tools": reasoning_tools,
        "planning_prose": planning_prose,
        "autonomy": round(autonomy, 1),
    }


def score_window(turns):
    """Score recent turns on mechanical(0) -> reasoning(100). Returns
    {score, recent_score, phase, transition, features}.

    Cheap, transparent, no LLM. ``score`` is over the whole window; ``recent_score``
    is over the later half — what the session is doing *now*. ``transition`` is
    True when the earlier half looks like planning and the later half like
    execution: the plan->execute drift that justifies spawning a cheap worker
    rather than dragging the planning session down."""
    if not turns:
        return {"score": 50, "recent_score": 50, "phase": "mixed",
                "transition": False, "features": {}}

    score, features = _raw_score(turns)
    half = max(1, len(turns) // 2)
    earlier = _phase_of(turns[:half])
    later = _phase_of(turns[half:])
    recent_score, _ = _raw_score(turns[half:])
    transition = earlier in ("planning", "mixed") and later == "executing" and earlier != "executing"

    return {
        "score": round(score),
        "recent_score": round(recent_score),
        "phase": _phase_of(turns),
        "recent_phase": later,
        "transition": bool(transition),
        "features": features,
    }


# Score thresholds. Outside [LOW, HIGH] we are confident; inside is the gray
# zone where we abstain rather than guess (no recommendation emitted).
_LOW = 32
_HIGH = 72


def recommend(current_model, turns):
    """Return a recommendation dict or None.

    dict: {action, from_model, to_model, score, phase, transition, reason,
           confidence}. action in {downgrade, upgrade, spawn_worker}.
    None means: no confident recommendation (keep current model)."""
    fam = _family(current_model)
    if not fam:
        return None  # non-Claude engine; out of scope
    sig = score_window(turns)
    score = sig["score"]
    tier = model_tier(current_model)

    def conf(distance, strong):
        if distance >= 22 and strong:
            return "high"
        if distance >= 12:
            return "medium"
        return "low"

    # Expensive top-tier gone mechanical -> save money.
    if tier >= _TIER["opus"]:
        strong = sig["features"]["exec_tools"] >= 4 or sig["features"]["autonomy"] >= 3
        # Plan->execute drift: the window still carries the planning history (so
        # the whole-window score is moderate) but the session is mechanical NOW.
        # Spawn a cheap worker for the execution; keep this one for thinking.
        if sig["transition"] and sig["recent_phase"] == "executing" and sig["recent_score"] <= _LOW:
            return {
                "action": "spawn_worker",
                "from_model": fam,
                "to_model": _DOWNGRADE_TO,
                "score": score,
                "phase": sig["phase"],
                "transition": True,
                "reason": "planning is done; this turned into mechanical execution — "
                "spawn a Sonnet worker for the execution and keep this session for thinking",
                "confidence": conf(_LOW - sig["recent_score"] + 10, strong),
            }
        # Steady-state mechanical: whole window is execution and cheap.
        if sig["phase"] == "executing" and score <= _LOW:
            return {
                "action": "downgrade",
                "from_model": fam,
                "to_model": _DOWNGRADE_TO,
                "score": score,
                "phase": sig["phase"],
                "transition": False,
                "reason": _downgrade_reason(sig),
                "confidence": conf(_LOW - score + 10, strong),
            }
        return None

    # Cheap tier doing hard reasoning -> never silently stuck too low.
    if tier <= _TIER["sonnet"]:
        strong = sig["features"]["reasoning_tools"] >= 2 or sig["features"]["open_ended"] >= 2
        if score >= _HIGH and sig["phase"] != "executing":
            return {
                "action": "upgrade",
                "from_model": fam,
                "to_model": _UPGRADE_TO,
                "score": score,
                "phase": sig["phase"],
                "transition": False,
                "reason": _upgrade_reason(sig),
                "confidence": conf(score - _HIGH + 10, strong),
            }
        return None
    return None


def _downgrade_reason(sig):
    f = sig["features"]
    bits = []
    if f["autonomy"] >= 3:
        bits.append("autonomous loop")
    if f["imperative"]:
        bits.append("short imperative prompts")
    if f["exec_tools"]:
        bits.append("repetitive edit/run")
    if not f["open_ended"] and not f["reasoning_tools"]:
        bits.append("no open decisions")
    return "mechanical execution: " + ", ".join(bits or ["no reasoning signals"])


def _upgrade_reason(sig):
    f = sig["features"]
    bits = []
    if f["reasoning_tools"]:
        bits.append("research/questions in play")
    if f["open_ended"]:
        bits.append("open-ended decisions")
    if f["planning_prose"]:
        bits.append("weighing tradeoffs")
    return "hard reasoning on a cheaper model: " + ", ".join(bits or ["ambiguous work"])


# --------------------------------------------------------------------------
# Recommendation log + savings (atomic JSON store)
# --------------------------------------------------------------------------

_LOG_LOCK = threading.Lock()
_DEDUP_COOLDOWN_SEC = 30 * 60  # don't re-log same sid+action within 30 min


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_log(path):
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("recommendations"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"recommendations": []}


def _save_log(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def log_recommendation(path, session_id, name, rec, baseline_out_tokens):
    """Append a recommendation unless an equivalent one is still fresh. Returns
    the stored entry (existing or new). ``baseline_out_tokens`` is the session's
    cumulative output-token count at recommend time, used later to value the
    savings actually realized (or missed)."""
    with _LOG_LOCK:
        data = _load_log(path)
        now = time.time()
        for e in reversed(data["recommendations"]):
            if (
                e.get("session_id") == session_id
                and e.get("action") == rec["action"]
                and e.get("to_model") == rec["to_model"]
                and e.get("status") in ("pending", "applied", "dismissed")
            ):
                try:
                    age = now - datetime.strptime(
                        e["ts"], "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc).timestamp()
                except (KeyError, ValueError):
                    age = 0
                if age < _DEDUP_COOLDOWN_SEC:
                    return e  # still fresh; don't spam
        entry = {
            "id": uuid.uuid4().hex[:12],
            "session_id": session_id,
            "name": name or "",
            "ts": _now_iso(),
            "from_model": rec["from_model"],
            "to_model": rec["to_model"],
            "action": rec["action"],
            "score": rec["score"],
            "phase": rec["phase"],
            "reason": rec["reason"],
            "confidence": rec["confidence"],
            "status": "pending",
            "applied_at": None,
            "baseline_out_tokens": int(baseline_out_tokens or 0),
            "current_out_tokens": int(baseline_out_tokens or 0),
        }
        data["recommendations"].append(entry)
        # Keep the log bounded.
        if len(data["recommendations"]) > 500:
            data["recommendations"] = data["recommendations"][-500:]
        _save_log(path, data)
        return entry


def mark(path, rec_id, status):
    """Set a recommendation's status (applied / dismissed)."""
    with _LOG_LOCK:
        data = _load_log(path)
        for e in data["recommendations"]:
            if e["id"] == rec_id:
                e["status"] = status
                if status == "applied":
                    e["applied_at"] = _now_iso()
                _save_log(path, data)
                return e
    return None


def refresh_savings(path, token_lookup):
    """Update realized/missed savings using a caller-supplied
    ``token_lookup(session_id) -> cumulative_output_tokens``. For an APPLIED
    downgrade we count output tokens produced since it was applied and value
    them at the tier price delta (realized savings). For a still-PENDING
    downgrade we count tokens since the recommendation (missed savings — what an
    ignored nudge is costing). Upgrades are not priced (they cost more on
    purpose); they are tracked for the applied/ignored counts only."""
    prices = _prices()
    with _LOG_LOCK:
        data = _load_log(path)
        for e in data["recommendations"]:
            try:
                cur = int(token_lookup(e["session_id"]) or 0)
            except Exception:
                cur = e.get("current_out_tokens", e.get("baseline_out_tokens", 0))
            if cur >= e.get("baseline_out_tokens", 0):
                e["current_out_tokens"] = cur
            delta_tokens = max(0, e["current_out_tokens"] - e.get("baseline_out_tokens", 0))
            if e["action"] in ("downgrade", "spawn_worker"):
                price_delta = prices.get(e["from_model"], 0) - prices.get(e["to_model"], 0)
                usd = delta_tokens / 1_000_000.0 * max(0.0, price_delta)
                if e["status"] == "applied":
                    e["realized_savings_usd"] = round(usd, 4)
                    e["missed_savings_usd"] = 0.0
                elif e["status"] == "pending":
                    e["realized_savings_usd"] = 0.0
                    e["missed_savings_usd"] = round(usd, 4)
                else:  # dismissed
                    e.setdefault("realized_savings_usd", 0.0)
                    e.setdefault("missed_savings_usd", 0.0)
        _save_log(path, data)
        return data


def expire_stale_pending(path, live_sids):
    """Mark pending recommendations 'expired' when their session is no longer live.
    Zeroes missed_savings so expired entries don't inflate 'left on table'."""
    with _LOG_LOCK:
        data = _load_log(path)
        changed = False
        for e in data.get("recommendations", []):
            if e.get("status") == "pending" and e.get("session_id") not in live_sids:
                e["status"] = "expired"
                e["missed_savings_usd"] = 0.0
                changed = True
        if changed:
            _save_log(path, data)


def summarize(data):
    """Roll the log into monitor totals."""
    recs = data.get("recommendations", [])
    by_status = {}
    realized = missed = 0.0
    for e in recs:
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
        realized += float(e.get("realized_savings_usd") or 0)
        missed += float(e.get("missed_savings_usd") or 0)
    return {
        "total": len(recs),
        "by_status": by_status,
        "applied": by_status.get("applied", 0),
        "pending": by_status.get("pending", 0),
        "dismissed": by_status.get("dismissed", 0),
        "realized_savings_usd": round(realized, 2),
        "missed_savings_usd": round(missed, 2),
    }
