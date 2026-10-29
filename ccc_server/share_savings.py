"""Share-card savings stats: per-day $0-run aggregates for /api/throughput/share.

A "free" session was spawned with ``runtime="free"`` (the $0 router runtime).
Its turns are still priced at API rates by the throughput pipeline
(``fallback_sonnet`` when the free model id is not in the price table), so a
free turn's ``cost_usd`` IS the amount saved versus list prices.

Everything here is totals-only: session ids are used to join turn -> session
and are never emitted in the output.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from ccc_server import core as _core

_FREE = "free"


def _entry_sids(entry):
    for key in ("session_id", "resumed_sid"):
        sid = str(entry.get(key) or "").strip()
        if sid:
            yield sid


def _is_free(value):
    return str(value or "").strip().lower() == _FREE


def _free_sids_from_registry():
    """Spawn-registry + in-memory spawn entries stamped runtime="free"."""
    sids = set()
    try:
        spawned = list(_core._spawned_sessions or [])
    except Exception:
        spawned = []
    try:
        registry = list(_core._load_spawn_registry() or [])
    except Exception:
        registry = []
    for entry in spawned + registry:
        if isinstance(entry, dict) and _is_free(entry.get("runtime")):
            sids.update(_entry_sids(entry))
    return sids


def _free_sids_from_markers():
    """Spawn-marker files carrying a free flag.

    The decoded marker map only keeps lane/kind/spawned_via, so for
    ``runtime`` we read the raw files (tiny JSON, one pass per share build,
    inside the TTL'd background job).
    """
    sids = set()
    markers_dir = getattr(_core, "SPAWN_MARKERS_DIR", None)
    if markers_dir is None:
        return sids
    try:
        files = list(markers_dir.glob("*.json"))
    except OSError:
        return sids
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if (
            _is_free(data.get("runtime"))
            or _is_free(data.get("kind"))
            or _is_free(data.get("spawned_via"))
        ):
            sid = path.name[: -len(".json")].strip()
            if sid:
                sids.add(sid)
    return sids


def free_session_ids():
    """session_ids known to have run on the $0 runtime.

    Best-effort across every marker surface: whichever lane lands the flag
    first (spawn registry entry, marker file kind/via) feeds the card. An
    install with no free runs yields an empty set, not an error.
    """
    try:
        return _free_sids_from_registry() | _free_sids_from_markers()
    except Exception:
        return set()


def _window_start_day(now=None):
    now = time.time() if now is None else now
    days = getattr(_core, "_THROUGHPUT_SHARE_DAYS", 365)
    return (
        datetime.fromtimestamp(now).astimezone() - timedelta(days=days - 1)
    ).strftime("%Y-%m-%d")


def savings_block(payload, turns_by_engine, free_sids=None, now=None):
    """Return the ``savings`` section for a share payload.

    Each free turn adds its API-priced ``cost_usd`` (the saved amount) and
    token count to its end-day inside the same window the aggregate used;
    a day counts a $0 run once per session active that day.
    """
    if free_sids is None:
        free_sids = free_session_ids()
    start_day = _window_start_day(now)
    saved = {}
    tokens = {}
    runs = {}
    seen = set()
    for _engine, turns in (turns_by_engine or {}).items():
        for t in turns or []:
            sid = str(t.get("session_id") or "").strip()
            if not sid or sid not in free_sids:
                continue
            t_end = _core._stats_parse_ts(t.get("t_end"))
            if not t_end:
                continue
            day = t_end.strftime("%Y-%m-%d")
            if day < start_day:
                continue
            saved[day] = saved.get(day, 0.0) + (t.get("cost_usd") or 0.0)
            tokens[day] = tokens.get(day, 0) + (
                (t.get("tokens_in") or 0) + (t.get("tokens_out") or 0)
            )
            seen.add(sid)
            runs.setdefault(day, set()).add(sid)
    daily = [
        {
            "day": day,
            "free_saved_usd": round(saved.get(day, 0.0), 4),
            "free_tokens": tokens.get(day, 0),
            "free_runs": len(runs.get(day) or ()),
        }
        for day in sorted(set(list(saved) + list(tokens) + list(runs)))
    ]
    return {
        "available": bool(free_sids),
        "daily": daily,
        "free_saved_usd": round(sum(saved.values()), 4),
        "free_tokens": sum(tokens.values()),
        "free_runs": len(seen),
    }


def annotate_share_payload(payload, turns_by_engine, free_sids=None, now=None):
    """Attach the savings section to a share payload (mutates and returns it).

    Never raises: a half-merged tree or a marker-read hiccup must not take
    the share card down with it.
    """
    try:
        payload["savings"] = savings_block(
            payload, turns_by_engine, free_sids=free_sids, now=now
        )
    except Exception:
        payload["savings"] = {
            "available": False,
            "daily": [],
            "free_saved_usd": 0.0,
            "free_tokens": 0,
            "free_runs": 0,
        }
    return payload
