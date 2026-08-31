"""Extracted from server.py (originally lines 17669-19519).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import json
import os
import re
import threading
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Log parsing (mirrors the bash viewer filter logic)
# ---------------------------------------------------------------------------

def extract_session_id(path):
    """Scan the first ~60 lines of an agent log file for a session UUID."""
    try:
        with open(path, "r") as f:
            for i, line in enumerate(f):
                if i >= 60:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    # `codex exec` writes a short human-readable header before
                    # its output instead of a stream-json session event.
                    match = re.fullmatch(
                        r"session id:\s*([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",
                        line,
                        flags=re.IGNORECASE,
                    )
                    if match:
                        return match.group(1)
                    continue
                sid = ev.get("session_id") or ev.get("sessionId")
                if sid and len(sid) >= 32:
                    return sid
    except (OSError, UnicodeDecodeError):
        pass
    return None


# Cache of session_id -> cwd so we don't rescan ~/.claude/projects on every request
_session_cwd_cache = {}
# CCC-128: user-supplied cwd overrides (sid -> abs path). When CCC can't resolve
# a session's folder (recorded cwd moved/gone → "CWD MISSING"), the user points
# it at the real directory via the workspace pill; this wins over all automatic
# resolution. Persisted so it survives restart. Display/resolution only — the
# session's JSONL is never rewritten (same philosophy as the visual /move).
_session_cwd_override = {}
_SESSION_CWD_OVERRIDE_FILE = (
    Path.home() / ".claude" / "command-center" / "session-cwd-overrides.json"
)
_session_cwd_relocation_cache = {}
_session_cwd_relocation_cache_lock = threading.Lock()
_CWD_RELOCATION_CACHE_FILE = (
    Path.home() / ".claude" / "command-center" / "cwd-relocation-cache.json"
)
_CWD_RELOCATION_CACHE_SCHEMA = 1
# Per-request budget for _relocate_missing_session_cwd's filesystem walks.
# A single cold scan of a worktree-heavy repo (e.g. BYM+Finie with 128 missing
# cwds) used to burn ~40s here. Once exceeded, individual relocations return
# None and the row falls back to the recorded cwd; subsequent requests pick
# up the slack as the cache fills.
_relocation_budget = threading.local()
_session_cwd_cache_mtime = 0

SESSIONS_REGISTRY = Path.home() / ".claude" / "sessions"  # per-pid {sessionId, cwd, ...}
DAEMON_ROSTER_FILE = Path.home() / ".claude" / "daemon" / "roster.json"
CLAUDE_JOBS_ROOT = Path.home() / ".claude" / "jobs"
# Backwards-compat alias — older code / forks may import the previous name.
SESSION_NAMES_FILE = _core.COMMAND_CENTER_STATE_DIR / "session-names.json"  # side-car overrides
# Cap session-name overrides defensively. Annotation prompts and other
# multi-kilobyte text occasionally end up flowing into the name slot
# (codex's SQLite `title`, a stray paste, etc.) and a row title that
# wide breaks the sidebar layout for every session it scrolls past.
# 120 matches `summarize_session_title`'s self-cap.
SESSION_NAME_MAX_CHARS = 120
CONVERSATION_ORDER_FILE = _core.COMMAND_CENTER_STATE_DIR / "conversation-order.json"  # [session_id,...]
ARCHIVED_CONVERSATIONS_FILE = _core.COMMAND_CENTER_STATE_DIR / "archived-conversations.json"  # [session_id,...]
TRASHED_CONVERSATIONS_FILE = _core.COMMAND_CENTER_STATE_DIR / "trashed-conversations.json"  # [session_id,...] — subset of archived
ARCHIVE_GRACE_FILE = _core.COMMAND_CENTER_STATE_DIR / "archive-sticky.json"  # {session_id: archived_at_epoch} — manual archives, sticky vs auto-unarchive
_conversation_lifecycle_lock = threading.RLock()
ARCHIVE_EVENTS_LOG = _core.COMMAND_CENTER_STATE_DIR / "archive-events.log"  # append-only: every archive/unarchive state change, so "why did X get unarchived" is answerable without re-deriving candidacy internals after the fact (CCC-445)


def _log_archive_event(action, sid, reason=""):
    try:
        _core.COMMAND_CENTER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        line = f"{datetime.now(timezone.utc).isoformat()} {action} {sid} {reason}".rstrip()
        with ARCHIVE_EVENTS_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
VERIFIED_CONVERSATIONS_FILE = _core.COMMAND_CENTER_STATE_DIR / "verified-conversations.json"  # [session_id,...]
SESSION_LANE_OVERRIDES_FILE = _core.COMMAND_CENTER_STATE_DIR / "session-lane-overrides.json"  # {session_id: coding|workers|messages}
# {session_id: epoch_seconds} — last time the user interacted with this card
# from the UI (typed a message, clicked Approve/Deny, etc.). Drag-drop and
# auto-events do NOT count.
LAST_INTERACTIONS_FILE = _core.COMMAND_CENTER_STATE_DIR / "last-interactions.json"
SESSION_ISSUES_FILE = _core.COMMAND_CENTER_STATE_DIR / "session-issues.json"  # {session_id: issue_number}
FIX_DEPLOY_SPAWNED_FILE = _core.COMMAND_CENTER_STATE_DIR / "fix-deploy-spawned.json"  # {commit_sha: {pid, spawned_at, name}}
# {bind_host, allowed_origins[], trust_tailnet} — persisted same-origin
# allowlist + bind config so the user doesn't have to re-export env vars on
# every restart. Empty/missing = loopback-only (the safe default). Loaded by
# `_load_network_config`, written by `_save_network_config`. See SECURITY.md.
NETWORK_CONFIG_FILE = _core.COMMAND_CENTER_STATE_DIR / "network.json"
# Car Mode (hands-free voice operator) config: optional API keys the user can
# store from the UI instead of hand-editing ccc-voice/.env. Plaintext on disk
# like the rest of the state dir (see SECURITY.md — chmod 700 is the mitigation).
# GET /api/car-mode/status returns only booleans, never the key values. Loaded by
# `_load_car_mode_config`, written by `_save_car_mode_config`.
CAR_MODE_CONFIG_FILE = _core.COMMAND_CENTER_STATE_DIR / "car-mode.json"
# Server-side defaults for new sessions spawned from the UI or by
# ccc-orchestration. Kept out of browser localStorage so scripted callers
# inherit the same engine/model choices the UI shows.

# ── Preview feature flags ────────────────────────────────────────────────
# Developer construct for merging a feature to `main` while keeping its
# surface hidden until it's ready. Two layers:
#   1. `default` (below)  -> what the WHOLE WORLD sees. Flip False->True and
#      push to ship a feature to everyone.
#   2. per-machine override -> what THIS daemon shows. Toggled from Settings
#      (writes FEATURE_FLAGS_FILE) or via env `CCC_FF_<NAME>=1`. Never
#      committed, so dogfooding on your box doesn't leak to strangers.
# Add a preview surface by (a) adding one entry here, (b) gating the UI on
# `ff('name')` in app.js, and (c) gating any server behavior on
# `_feature_flag('name')`. The Settings "Experimental" section renders itself
# from this registry — no extra markup per flag.
_PREVIEW_FLAGS = {
    "presentation": {
        "default": False,
        "label": "Conversation presentation",
        "desc": "Show the Present controls that turn assistant answers into local slides.",
    },
    "auto_handover_pill": {
        "default": False,
        "label": "Auto handover toggle",
        "desc": "Show the Auto handover ON/OFF pill in the status bar of Claude sessions.",
    },
    "bottom_bar_cost": {
        "default": False,
        "label": "$ cost in bottom bar",
        "desc": "Show the API list-price cost pill next to token usage in the input bar.",
    },
    # "flow_v2": {
    #     "default": False,
    #     "label": "Flow v2 canvas",
    #     "desc": "Rebuilt Flow workspace with the new edge engine.",
    # },
}
FEATURE_FLAGS_FILE = _core.COMMAND_CENTER_STATE_DIR / "feature-flags.json"


def _load_feature_flag_overrides():
    """Per-machine overrides as {name: bool}. Missing/corrupt file -> {}."""
    try:
        data = json.loads(FEATURE_FLAGS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: bool(v) for k, v in data.items() if k in _PREVIEW_FLAGS}


def _resolved_feature_flags():
    """{name: bool} after applying default -> env -> file override, in order."""
    overrides = _load_feature_flag_overrides()
    out = {}
    for name, spec in _PREVIEW_FLAGS.items():
        value = bool(spec.get("default"))
        env = os.environ.get("CCC_FF_" + name.upper())
        if env is not None:
            value = env.strip().lower() in ("1", "true", "yes", "on")
        if name in overrides:
            value = overrides[name]
        out[name] = value
    return out


def _feature_flag(name):
    """Server-side gate for a single preview flag."""
    return _resolved_feature_flags().get(name, False)


def _feature_flags_meta():
    """Registry + resolved state for the Settings UI to render toggles from."""
    resolved = _resolved_feature_flags()
    return [
        {
            "name": name,
            "label": spec.get("label", name),
            "desc": spec.get("desc", ""),
            "default": bool(spec.get("default")),
            "on": resolved.get(name, False),
        }
        for name, spec in _PREVIEW_FLAGS.items()
    ]


def _save_feature_flag(name, on):
    """Persist a single per-machine override. Returns {ok, ...}."""
    if name not in _PREVIEW_FLAGS:
        return {"ok": False, "error": "unknown flag"}
    overrides = _load_feature_flag_overrides()
    overrides[name] = bool(on)
    try:
        _core.COMMAND_CENTER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(FEATURE_FLAGS_FILE, "w") as f:
            json.dump(overrides, f, indent=2, sort_keys=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "flags": _resolved_feature_flags()}
# Persistent registry of spawned headless `claude -p` PIDs, so a server restart
# can re-discover orphans instead of leaving them unreachable. See
# _reattach_spawned_orphans() for the boot-time sweep. Schema is a list of
# {pid, session_id, parent_session_id, cwd, spawned_at, name, log,
# command_summary, model}.
# Persistent mapping from Codex thread_id -> parent Claude session_id. Written
# when a CCC-spawned Codex session's thread_id is first discovered (the
# thread_id isn't known at spawn time, so we can't write it to the spawn
# registry entry before launch). Survives spawn-registry pruning so
# parent-child nesting works even after the Codex session exits. (CCC-465)
CODEX_PARENT_LINKS_FILE = _core.COMMAND_CENTER_STATE_DIR / "codex-parent-links.json"
# Durable record of sessions currently waiting out a usage/rate-limit wall,
# keyed by session_id -> {engine, detected_at, resume_at, source_text_snippet,
# fired}. See _usage_limit_watcher() (CCC-863).
# Unified parent-child session graph. Combines all four edge sources
# (Codex spawn edges, durable parent links, spawn registry, thread registry)
# plus Claude Task-tool subagent transcripts into one adjacency list so
# family-tree queries are O(1) instead of re-scanning four stores per call.
SESSION_GRAPH_FILE = _core.COMMAND_CENTER_STATE_DIR / "session-graph.json"

# Per-session model + context override. Set by the click-to-switch picker
# in the session card (see /api/session/<sid>/model). Schema:
#   { "<session_id>": {"model": "...", "context_1m": bool,
#                       "engine": "claude|codex|gemini|antigravity",
#                       "set_at": "ISO-8601"} }
# Sticky: stays until the user changes it again or hits "Reset to default".
# Read by resume_session_{headless,codex,gemini,antigravity} so a queued
# change actually lands on the next ask.

# Per-session opt-in for the auto-handover watchdog (see _run_auto_handover_watchdog_once).
# Schema: { "<session_id>": {"enabled": bool, "set_at": "ISO-8601",
#           "last_fired_mtime": float|null, "last_fired_at": "ISO-8601"|null} }
# enabled=False deletes the key entirely rather than writing enabled:false, so
# the watchdog's iteration never has to filter dead entries.
MODEL_PICKER_HISTORY_FILE = _core.COMMAND_CENTER_STATE_DIR / "model-picker-history.json"


def _mine_real_model_history_last_7_days():
    """Extract real session launches from the last 7 to 30 days on disk."""
    import datetime
    from collections import Counter

    now_ts = time.time()
    thirty_days_ago_ts = now_ts - 30 * 86400

    counts = Counter()
    last_seen = {}

    def _normalize_and_add(eng, mod, eff, ts):
        eng = str(eng or "").strip().lower()
        mod = str(mod or "").strip()
        eff = str(eff or "").strip()
        if not eng:
            return
        if mod.startswith("claude-"):
            mod = mod[7:]
        if mod.startswith("gpt-") and eng == "claude":
            eng = "codex"
        key = (eng, mod, eff)
        counts[key] += 1
        if ts > last_seen.get(key, 0):
            last_seen[key] = ts

    # 1. Read spawn-timeline.json (has precise spawn timestamps and models)
    tl_path = _core.COMMAND_CENTER_STATE_DIR / "spawn-timeline.json"
    if tl_path.exists():
        try:
            with open(tl_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for sid, entry in data.items():
                    if isinstance(entry, dict):
                        t0 = float(entry.get("t0") or 0)
                        if t0 > thirty_days_ago_ts:
                            _normalize_and_add(
                                entry.get("engine"),
                                entry.get("model"),
                                entry.get("reasoning_effort"),
                                t0,
                            )
        except Exception:
            pass

    # 2. Read session-overrides.json (has explicit user model choices with timestamps)
    ov_path = _core.COMMAND_CENTER_STATE_DIR / "session-overrides.json"
    if ov_path.exists():
        try:
            with open(ov_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for sid, entry in data.items():
                    if isinstance(entry, dict):
                        set_at = entry.get("set_at")
                        if set_at:
                            try:
                                dt = datetime.datetime.fromisoformat(
                                    set_at.replace("Z", "+00:00")
                                )
                                ts = dt.timestamp()
                                if ts > thirty_days_ago_ts:
                                    _normalize_and_add(
                                        entry.get("engine"),
                                        entry.get("model"),
                                        entry.get("reasoning_effort"),
                                        ts,
                                    )
                            except Exception:
                                pass
        except Exception:
            pass

    # 3. Read spawned-pids.json (has active/recent engine/model runs)
    pids_path = _core.COMMAND_CENTER_STATE_DIR / "spawned-pids.json"
    if pids_path.exists():
        try:
            with open(pids_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict):
                        _normalize_and_add(
                            entry.get("engine"),
                            entry.get("model"),
                            entry.get("reasoning_effort"),
                            now_ts,
                        )
        except Exception:
            pass

    ranked = sorted(
        counts.items(), key=lambda x: (x[1], last_seen.get(x[0], 0)), reverse=True
    )
    picks = []
    for (eng, mod, eff), cnt in ranked:
        picks.append({
            "engine": eng,
            "model": mod,
            "effort": eff,
            "count": cnt,
            "last_used": last_seen.get((eng, mod, eff), now_ts),
        })
    return picks


def record_model_picker_pick(engine: str, model: str, effort: str = "") -> None:
    eng = str(engine or "").strip().lower()
    mod = str(model or "").strip()
    eff = str(effort or "").strip()
    if not eng:
        return
    if mod.startswith("claude-"):
        mod = mod[7:]
    if mod.startswith("gpt-") and eng == "claude":
        eng = "codex"

    entry = {
        "engine": eng,
        "model": mod,
        "effort": eff,
        "timestamp": time.time(),
    }
    history = []
    if _core.MODEL_PICKER_HISTORY_FILE.exists():
        try:
            with open(_core.MODEL_PICKER_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

    if not history:
        mined = _core._mine_real_model_history_last_7_days()
        for p in mined:
            history.append({
                "engine": p["engine"],
                "model": p["model"],
                "effort": p["effort"],
                "timestamp": p["last_used"],
            })

    history.append(entry)
    thirty_days_ago = time.time() - 30 * 86400
    history = [
        h
        for h in history
        if isinstance(h, dict) and h.get("timestamp", 0) > thirty_days_ago
    ]
    if len(history) > 500:
        history = history[-500:]

    try:
        tmp = _core.MODEL_PICKER_HISTORY_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
        tmp.replace(_core.MODEL_PICKER_HISTORY_FILE)
    except Exception:
        pass


def get_model_picker_picks() -> list:
    """Return top 7-8 model picks based on real disk data from the last 7-30 days."""
    history = []
    if _core.MODEL_PICKER_HISTORY_FILE.exists():
        try:
            with open(_core.MODEL_PICKER_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

    if not history:
        mined = _core._mine_real_model_history_last_7_days()
        if mined:
            history = [
                {
                    "engine": p["engine"],
                    "model": p["model"],
                    "effort": p["effort"],
                    "timestamp": p["last_used"],
                }
                for p in mined
            ]
            try:
                with open(_core.MODEL_PICKER_HISTORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(history, f, indent=2)
            except Exception:
                pass
            return mined[:8]

    from collections import Counter

    now = time.time()
    thirty_days_ago = now - 30 * 86400
    counts = Counter()
    last_seen = {}
    for item in history:
        if not isinstance(item, dict):
            continue
        ts = float(item.get("timestamp") or 0)
        if ts > thirty_days_ago:
            key = (
                item.get("engine", ""),
                item.get("model", ""),
                item.get("effort", ""),
            )
            counts[key] += 1
            if ts > last_seen.get(key, 0):
                last_seen[key] = ts

    ranked = sorted(
        counts.items(), key=lambda x: (x[1], last_seen.get(x[0], 0)), reverse=True
    )
    picks = [
        {
            "engine": eng,
            "model": mod,
            "effort": eff,
            "count": cnt,
            "last_used": last_seen.get((eng, mod, eff), now),
        }
        for (eng, mod, eff), cnt in ranked
    ]

    if not picks:
        return _core._mine_real_model_history_last_7_days()[:8]

    return picks[:8]



# {path: {mtime, custom_title, last_prompt, agent_name, ...}}
# Persistent across restarts via _CONV_META_CACHE_FILE — without it, every
# repo switch on a project with hundreds of large JSONLs (BYM+Finie has
# 1.8 GB of conversation logs) re-walks every file and the API stalls
# for a minute or more. The cache is mtime-keyed so admin writes
# (custom-title, /rename) correctly invalidate the entry; bump
# _CONV_META_SCHEMA_VERSION when the extracted shape changes so old
# entries are dropped on load.
_conv_meta_cache = {}
_conv_meta_cache_dirty = False
_conv_meta_cache_lock = threading.Lock()
_CONV_META_SCHEMA_VERSION = 17

# Dedup set for transcript-scanned interrupts — prevents re-firing the same
# [Request interrupted by user] event on every cache-invalidating re-parse.
# Thread-safe via _SEEN_INTERRUPTS_LOCK. Seeded at startup from the resume
# ledger (Change 2) so dedup survives restarts. Unbounded — interrupts are
# rare (~37/day measured), and the durable seed means the set is populated
# once, not grown incrementally.
_SEEN_INTERRUPTS = set()
_SEEN_INTERRUPTS_LOCK = threading.Lock()

# Most-recent interrupt timestamp per sid, for a transient "Interrupted"
# badge on the session row (_add_sidecar_fields reads this). Small and
# unbounded-but-tiny: only sids that have ever been interrupted get an
# entry, and the badge itself only shows within _RECENT_INTERRUPT_WINDOW_S.
_RECENT_INTERRUPT_BY_SID = {}
_RECENT_INTERRUPT_LOCK = threading.Lock()
_RECENT_INTERRUPT_WINDOW_S = 10 * 60

# Interrupt event emission is disabled by default and enabled only in the
# dashboard server's main() (and only when not CCC_EPHEMERAL). This makes the
# worker process, archive-refresh subprocess, pytest, and any ad-hoc
# `import server` fail-safe by construction — no emission unless explicitly
# enabled. The guard check lives inside _emit_interrupt_event, not at call
# sites; when disabled, the helper returns WITHOUT modifying _SEEN_INTERRUPTS
# (marking-seen-while-disabled would silently swallow later real emission).

# In-memory cache of the head-parse (first ~20 lines: session_id, timestamp,
# git_branch, first_message, head_cwd) keyed by str(path) -> (cache_key, tuple),
# where cache_key is (st_mtime_ns, st_size) — same invalidation as
# _extract_tail_meta. The all-folders archive build opens and parses the head of
# every conversation (~1390 files) on each load; archived transcripts never
# change, so this collapses that to only the files that actually changed.
_conv_head_cache = {}
# Separate cache for the 5-field head shape (session_id, timestamp, git_branch,
# first_message, head_cwd) used by the live-sessions scan. It MUST NOT share
# _conv_head_cache, which holds a 4-field shape keyed by the same file path —
# sharing one dict made the two scans clobber each other's entries and unpack
# the wrong arity (ValueError: too many values to unpack).
_conv_head5_cache = {}
# DROP 16: schema 16 payloads lack the "interrupted" field in meta entries.
# Keeping 16 loadable would mean cached entries from before the interrupt
# feature silently skip emit-on-cache-hit — the silent-drop window stays
# open. Dropping 16 forces a one-time cold reparse; the cache is then warm
# again with the new shape including "interrupted". First boot after upgrade
# will visibly stall on the first conversation list render (~1.8GB corpus,
# "API stalls for a minute or more" per the cache's own comment above).
_CONV_META_COMPAT_SCHEMA_VERSIONS = {17}
_CONV_META_CACHE_FILE = (
    Path.home() / ".claude" / "command-center" / "conv_meta_cache.json"
)


def _load_cwd_relocation_cache():
    """Persist cwd-relocation results across restarts.

    `_relocate_missing_session_cwd` walks the filesystem when a session's
    recorded cwd no longer exists (deleted worktree, moved repo). On a
    worktree-heavy repo with many old sessions the walk can take ~40s per
    cold scan. Persisting the result — positive AND negative — drops the
    second-and-subsequent cold start to near-zero. Cache entries are
    revalidated lazily in _relocate_missing_session_cwd.
    """
    if not _CWD_RELOCATION_CACHE_FILE.is_file():
        return
    try:
        with _CWD_RELOCATION_CACHE_FILE.open("r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    if data.get("schema_version") != _CWD_RELOCATION_CACHE_SCHEMA:
        return
    entries = data.get("entries")
    if not isinstance(entries, list):
        return
    loaded = {}
    for row in entries:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        raw = row.get("recorded_cwd")
        if not sid or not isinstance(raw, str):
            continue
        target = row.get("resolved_cwd")
        if target is not None and not isinstance(target, str):
            continue
        loaded[(sid, raw)] = target
    with _session_cwd_relocation_cache_lock:
        _session_cwd_relocation_cache.update(loaded)


def _save_cwd_relocation_cache():
    """Atomic write of _session_cwd_relocation_cache when dirty."""
    global _session_cwd_relocation_cache_dirty
    with _session_cwd_relocation_cache_lock:
        if not _core._session_cwd_relocation_cache_dirty:
            return
        snapshot = {
            "schema_version": _CWD_RELOCATION_CACHE_SCHEMA,
            "entries": [
                {
                    "session_id": sid,
                    "recorded_cwd": raw,
                    "resolved_cwd": resolved,
                }
                for (sid, raw), resolved in _session_cwd_relocation_cache.items()
            ],
        }
        _core._session_cwd_relocation_cache_dirty = False
    try:
        _CWD_RELOCATION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CWD_RELOCATION_CACHE_FILE.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(snapshot, f)
        tmp.replace(_CWD_RELOCATION_CACHE_FILE)
    except OSError as e:
        with _session_cwd_relocation_cache_lock:
            _core._session_cwd_relocation_cache_dirty = True
        print(f"  [cwd-relocation-cache] save failed: {e}")


def _relocation_budget_begin(seconds=None):
    """Open a per-request time budget for _relocate_missing_session_cwd.

    On budget exhaustion, in-flight relocations short-circuit to None
    (the row falls back to the recorded cwd verbatim); the cache is not
    populated for skipped entries so a future request can still resolve
    them. Defaults to 1.5s — high enough that one cold scan can resolve a
    handful of missing cwds, low enough that no single request stalls.
    """
    if seconds is None:
        try:
            seconds = float(os.environ.get("CCC_CWD_RELOCATION_BUDGET_S", "1.5"))
        except (TypeError, ValueError):
            seconds = 1.5
    _relocation_budget.deadline = time.monotonic() + max(0.0, seconds)
    _relocation_budget.exhausted_count = 0


def _relocation_budget_end():
    _relocation_budget.deadline = None


def _relocation_budget_exhausted():
    deadline = getattr(_relocation_budget, "deadline", None)
    if deadline is None:
        return False
    if time.monotonic() > deadline:
        _relocation_budget.exhausted_count = getattr(_relocation_budget, "exhausted_count", 0) + 1
        return True
    return False


def _load_conv_meta_cache():
    """Best-effort load of _conv_meta_cache from disk on startup.

    Drops the entire payload (and re-extracts on demand) when the schema
    version doesn't match — small one-time cost in exchange for forward
    compatibility on shape changes.
    """
    if not _core._CONV_META_CACHE_FILE.is_file():
        return
    try:
        with _core._CONV_META_CACHE_FILE.open("r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    if data.get("schema_version") not in _CONV_META_COMPAT_SCHEMA_VERSIONS:
        return
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return
    keep = {
        k: v for k, v in entries.items()
        if isinstance(v, dict) and "mtime" in v
    }
    # JSON has no tuple type, so a cache_key saved as (mtime_ns, size) comes
    # back as a list. The freshness check in _extract_tail_meta compares it
    # against a freshly built tuple, and [a, b] == (a, b) is always False —
    # so without this coercion the disk cache NEVER hits and every restart
    # re-parses all ~1k+ transcripts (tens of seconds at 100% CPU, which
    # starves every other request thread via the GIL and wedges the server).
    for v in keep.values():
        ck = v.get("cache_key")
        if isinstance(ck, list):
            v["cache_key"] = tuple(ck)
    with _core._conv_meta_cache_lock:
        _core._conv_meta_cache.update(keep)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _session_scan_cutoff_ts(include_old):
    if include_old:
        return 0
    max_age_days = _env_int("CCC_MAX_CONV_AGE_DAYS", 30)
    if max_age_days <= 0:
        return 0
    return time.time() - (max_age_days * 86400)


def _session_scan_file_limit(include_old):
    if include_old:
        return 0
    return max(0, _env_int("CCC_INITIAL_SESSION_SCAN_LIMIT", 180))


def _filter_conversation_jsonls(
    jsonl_files,
    *,
    include_old=False,
    always_include_sids=None,
    last_interactions=None,
    cutoff_ts=None,
    max_files=None,
):
    """Return JSONL paths to scan for the first sessions response.

    The board needs live/recent sessions immediately, not every historical
    transcript. Old rows remain available through ?include_old=1.
    """
    files = list(jsonl_files)
    total = len(files)
    if include_old:
        return files, {"total": total, "skipped_old": 0, "limited": False}

    if cutoff_ts is None:
        cutoff_ts = _session_scan_cutoff_ts(False)
    if max_files is None:
        max_files = _session_scan_file_limit(False)
    always_include_sids = set(always_include_sids or [])
    last_interactions = last_interactions or {}

    required = []
    optional = []
    skipped_old = 0
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        sid = f.name[:-6] if f.name.endswith(".jsonl") else f.stem
        interacted = last_interactions.get(sid) or 0
        freshness = max(st.st_mtime, interacted)
        keep_always = sid in always_include_sids
        is_recent = cutoff_ts <= 0 or freshness >= cutoff_ts
        if not keep_always and not is_recent:
            skipped_old += 1
            continue
        row = (freshness, f)
        if keep_always:
            required.append(row)
        else:
            optional.append(row)

    required.sort(key=lambda x: x[0], reverse=True)
    optional.sort(key=lambda x: x[0], reverse=True)

    limited = False
    if max_files > 0 and len(required) + len(optional) > max_files:
        remaining = max(0, max_files - len(required))
        optional = optional[:remaining]
        limited = True

    selected = [f for _, f in required + optional]
    return selected, {
        "total": total,
        "skipped_old": skipped_old,
        "limited": limited,
    }


def _gc_scratch_jsonls(max_age_days=7):
    """Delete throwaway JSONLs older than max_age_days from our scratch
    project dir. Called once at server startup so the scratch dir
    self-empties without any background thread or cron — the next
    `./run.sh` or upgrade is the trigger.

    Only operates on `~/.claude/projects/<slug>/` where <slug> is derived
    from `_SCRATCH_DIR`; never touches any user-repo project dir.
    """
    try:
        cutoff_days = int(os.environ.get("CCC_SCRATCH_GC_DAYS", str(max_age_days)))
    except ValueError:
        cutoff_days = max_age_days
    if cutoff_days <= 0:
        return
    cutoff = time.time() - cutoff_days * 86400
    scratch_slug = _core._encode_project_slug(_core._SCRATCH_DIR)
    scratch_proj = Path.home() / ".claude" / "projects" / scratch_slug
    if not scratch_proj.is_dir():
        return
    deleted = 0
    bytes_freed = 0
    for p in scratch_proj.glob("*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_mtime > cutoff:
            continue
        try:
            p.unlink()
            deleted += 1
            bytes_freed += st.st_size
        except OSError as e:
            print(f"  [scratch-gc] could not delete {p.name}: {e}")
    if deleted:
        print(
            f"  [scratch-gc] deleted {deleted} throwaway JSONL(s), "
            f"freed {bytes_freed/1024:.0f} KB (older than {cutoff_days}d)"
        )


def _save_conv_meta_cache():
    """Atomic write of _conv_meta_cache to disk if dirty since last save.

    Called at the end of /api/conversations so saves are amortized over
    user actions, never blocking the response (already-built rows have
    been sent by then in the streaming-friendly write path; for the
    current send_json path, the extra <50 ms write is fine).
    """
    global _conv_meta_cache_dirty
    with _core._conv_meta_cache_lock:
        if not _conv_meta_cache_dirty:
            return
        snapshot = {
            "schema_version": _CONV_META_SCHEMA_VERSION,
            "entries": dict(_core._conv_meta_cache),
        }
        _conv_meta_cache_dirty = False
    try:
        _core._CONV_META_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _core._CONV_META_CACHE_FILE.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(snapshot, f)
        tmp.replace(_core._CONV_META_CACHE_FILE)
    except OSError as e:
        # Restore the dirty flag so we'll retry on the next call.
        with _core._conv_meta_cache_lock:
            _conv_meta_cache_dirty = True
        print(f"  [conv-meta-cache] save failed: {e}")


_META_MARKERS = (
    '"type":"custom-title"',
    '"type":"agent-name"',
    '"type":"last-prompt"',
    '"type":"queued_command"',
    '"type":"ai-title"',
)

# Markers for session signals — only lines with these need full JSON parse
_SIGNAL_MARKERS = (
    '"tool_use"',     # Edit/Write/Bash tool calls
    '"type":"result"',  # turn completion
)

# The per-line prefilter in _extract_tail_meta runs over every line of every
# transcript (1.4M+ lines on a real corpus), so it is measured, not guessed.
# `any(m in line for m in MARKERS)` pays a Python generator frame per marker
# per line (~10M frames, 11s of a cold scan). A compiled alternation is worse
# still — the regex engine loses to a plain substring scan on long literals.
# An unrolled `or` chain of `in` tests is the fast form: pure C, short-circuit,
# no frames. Keep these in sync with the tuples above by hand; the assert
# below fails loudly if a marker is added to one and not the other.
_check_markers = lambda line: (
    '"type":"custom-title"' in line
    or '"type":"agent-name"' in line
    or '"type":"last-prompt"' in line
    or '"type":"queued_command"' in line
    or '"type":"ai-title"' in line
)
assert all(_check_markers('x' + m + 'y') for m in _META_MARKERS), "meta marker drift"
del _check_markers


def _claude_usage_cost_breakdown(model, usage):
    """Return API-list-price cost components for one Claude assistant turn."""
    zero = {"input": 0.0, "cache_creation": 0.0, "cache_read": 0.0,
            "output": 0.0, "total": 0.0}
    if not isinstance(usage, dict):
        return zero
    rates, known = _core._rates_for_model_known(model)
    if not known:
        return zero
    rate_in, rate_cache_write, rate_cache_read, rate_out = rates
    cache_creation = _core._codex_int(usage.get("cache_creation_input_tokens"))
    cache_read = _core._codex_int(usage.get("cache_read_input_tokens"))
    cache_meta = usage.get("cache_creation")
    cache_1h = _core._codex_int(cache_meta.get("ephemeral_1h_input_tokens")) \
        if isinstance(cache_meta, dict) else 0
    cache_1h = min(cache_creation, cache_1h)
    cache_5m = cache_creation - cache_1h
    out = {
        "input": _core._codex_int(usage.get("input_tokens")) * rate_in / 1_000_000,
        "cache_creation": (
            cache_5m * rate_cache_write + cache_1h * rate_in * 2.0
        ) / 1_000_000,
        "cache_read": cache_read * rate_cache_read / 1_000_000,
        "output": _core._codex_int(usage.get("output_tokens")) * rate_out / 1_000_000,
    }
    out["total"] = sum(out.values())
    return out


def _extract_tail_meta(path):
    """Extract metadata + session signals from a jsonl in a single pass.

    Metadata: custom-title, agent-name, last-prompt (from /rename etc.)
    Signals:  stage (planning→coding→committed→pushed), last event type,
              activity status (working/waiting/idle).

    Uses string pre-filters to skip the vast majority of lines without
    JSON-parsing them. Cached by (st_mtime_ns, st_size) — plain st_mtime
    is 1-second-resolution, so two writes inside the same wall second
    keep returning the stale snapshot. The context-pct badge in the
    sidebar (latest_input_tokens / live_context_percent) was visibly
    stuck on the prior turn's value for users typing fast. Size is part
    of the key so a same-second truncate-and-overwrite (mtime unchanged,
    size different) still invalidates.
    """
    try:
        st = path.stat()
        cache_key = (st.st_mtime_ns, st.st_size)
        mtime = st.st_mtime
    except OSError:
        return {}
    cached = _core._conv_meta_cache.get(str(path))
    if cached and cached.get("cache_key") == cache_key:
        # Emit interrupts from cached meta (Change 1: emit-on-cache-hit).
        # Without this, an interrupt whose transcript was cached by a
        # non-emitting process (archive worker) would never be emitted by
        # the dashboard, because the cache hit skips the parse loop.
        _core._emit_interrupts_from_meta(cached, path.stem)
        return cached
    meta = {
        "mtime": mtime,
        # last_meaningful_ts: timestamp of the most recent user/assistant/result
        # event. Administrative writes (custom-title, agent-name, etc.) don't
        # bump this, so renames don't artificially push cards to "just now".
        "last_meaningful_ts": 0,
        "custom_title": None,
        "agent_name": None,
        "ai_title": None,
        "last_prompt": None,
        "goal": "",
        "goal_status": "",
        # Session signals — positions track ordering so stage can regress
        "has_edit": False,
        "has_commit": False,
        "has_push": False,
        "last_edit_pos": 0,
        "last_commit_pos": 0,
        "last_push_pos": 0,
        "last_event_type": None,  # "assistant", "result", "user", etc.
        "pending_tool": None,     # tool awaiting approval (last assistant had tool_use, no result yet)
        "pending_file": None,     # file path from pending tool
        "last_assistant_text": None,  # last text block from an assistant message (the "outcome")
        "model": None,
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "lifetime_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "total_output_tokens": 0,
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
        "live_context_tokens": 0,
        "live_context_limit": 0,
        "live_context_percent": 0,
        # Issue number detected from Bash/commit content — covers sessions where the
        # issue wasn't in the spawn prompt (e.g. Claude ran `gh issue create` mid-session).
        "tail_issue_number": None,
        # PR number detected from `gh pr create` output — sidebar surfaces this on
        # worktree rows in place of the generic committed/pushed chip.
        "tail_pr_number": None,
        # Full PR URL (https://github.com/<owner>/<repo>/pull/<n>). Captured so
        # the merge button can pass it to `gh pr merge` directly — `gh` resolves
        # the repo from the URL, which avoids cross-repo lookups when the
        # session's cwd has drifted to a different repo than where the PR lives.
        "tail_pr_url": None,
        # Did the session ever issue `cd <path>` or `git -C <path>` from Bash?
        # If False, the session never relocated and `_infer_effective_repo`
        # has nothing to find — caller can skip the JSONL re-walk + git
        # subprocesses for this row.
        "has_external_cd": False,
        # Subagent visibility — Claude Code's Task tool spawns child agents.
        # We count both total Task tool_use blocks ever issued and how many
        # are still in flight (no matching tool_result yet). subagent_recent
        # surfaces the last 8 with their description + status for the
        # status-rail panel.
        "subagent_count": 0,
        "subagent_in_flight_count": 0,
        "subagent_recent": [],
    }
    # Regexes compiled once per call; order matters — earlier = higher confidence.
    _gh_issue_cmd_re = re.compile(r'gh\s+issue\s+(?:view|edit|close|comment|reopen|create)\s+(?:.*?)(?<!\d)(\d{1,6})(?!\d)')
    _closes_re = re.compile(r'(?i)\bClos(?:es|e|ed|ing)\s+#(\d{1,6})\b')
    _gh_url_re = re.compile(r'github\.com/[^/\s]+/[^/\s]+/issues/(\d{1,6})')
    _gh_pr_url_re = re.compile(r'github\.com/([^/\s]+/[^/\s]+)/pull/(\d{1,7})')
    _pending_pr_ids = set()
    # Per-Task tracking: id → {description, status, ts_pos}. We mutate this
    # in place when a tool_result for the same tool_use_id lands, then
    # serialize the last 8 into meta["subagent_recent"] at the end.
    _subagent_by_id = {}
    _subagent_order = []  # insertion order so we can pull the last N
    _usage_message_ids = set()
    _pos = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                _pos += 1
                # Unrolled on purpose — see the note by _META_MARKERS. These
                # three tests run ~1.4M times per cold scan.
                is_meta = (
                    '"type":"custom-title"' in line
                    or '"type":"agent-name"' in line
                    or '"type":"last-prompt"' in line
                    or '"type":"queued_command"' in line
                    or '"type":"ai-title"' in line
                )
                is_signal = not is_meta and (
                    '"tool_use"' in line or '"type":"result"' in line
                )
                # User/assistant events may not start with "type" (parentUuid first).
                # Check for a timestamp + user/assistant marker to catch them.
                is_typed = not is_meta and not is_signal and (
                    line.startswith('{"type":')
                    or '"type":"user"' in line
                    or '"type":"assistant"' in line
                    or '"type":"result"' in line
                    or '"type":"system"' in line
                )
                if not (is_meta or is_signal or is_typed):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type", "")
                # Track last event type for activity detection
                if t in ("assistant", "result", "user"):
                    meta["last_event_type"] = t
                    # Clear pending tool when a result or user msg arrives
                    if t in ("result", "user"):
                        meta["pending_tool"] = None
                        meta["pending_file"] = None
                    # Record meaningful-activity timestamp (ISO 8601 → epoch)
                    ts = ev.get("timestamp", "")
                    if ts:
                        try:
                            from datetime import datetime as _dt
                            # Format like "2026-04-12T20:42:58.123Z" (UTC)
                            dt = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                            meta["last_meaningful_ts"] = dt.timestamp()
                        except (ValueError, ImportError):
                            pass
                    if t == "user":
                        msg = _core._safe_parse_message(ev.get("message", {}))
                        raw_text = _core._extract_text_from_content(msg.get("content", ""))
                        command_text = _core._extract_command_invocation_text(raw_text)
                        if command_text:
                            _core._apply_claude_goal_command(meta, command_text)
                        prompt_text = _core._extract_user_prompt_text(ev)
                        if prompt_text:
                            meta["last_prompt"] = prompt_text
                        # Detect [Request interrupted by user] — Claude writes
                        # this when SIGTERM'd mid-turn. Usually CCC's kill, not
                        # the user. Standardized to startswith (not substring
                        # `in`) to match the renderer's rule at line 7889 and
                        # avoid false positives when a user pastes a log that
                        # mentions the marker mid-message.
                        # Collect interrupt data in meta for emit-on-cache-hit
                        # (Change 1). The helper owns dedup + freshness cutoff
                        # + the three sinks; this site just detects and calls.
                        if raw_text and raw_text.strip().startswith("[Request interrupted by user"):
                            ev_uuid = ev.get("uuid", "")
                            if ev_uuid:
                                # Capture event timestamp for freshness cutoff
                                event_ts = None
                                ts = ev.get("timestamp", "")
                                if ts:
                                    try:
                                        from datetime import datetime as _dt
                                        dt = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                                        event_ts = dt.timestamp()
                                    except (ValueError, ImportError):
                                        pass
                                # Store in meta for emit-on-cache-hit
                                if "interrupted" not in meta:
                                    meta["interrupted"] = []
                                meta["interrupted"].append(
                                    {"uuid": ev_uuid, "ts": event_ts}
                                )
                                _core._emit_interrupt_event(
                                    path.stem, ev_uuid,
                                    source="transcript-scan",
                                    agent_name=meta.get("agent_name") or meta.get("custom_title") or "",
                                    event_ts=event_ts,
                                )
                # Metadata
                if t == "custom-title":
                    meta["custom_title"] = ev.get("customTitle") or meta["custom_title"]
                elif t == "agent-name":
                    meta["agent_name"] = ev.get("agentName") or meta["agent_name"]
                elif t == "ai-title":
                    # Claude Code rewrites ai-title throughout a session as the
                    # conversation evolves; the latest non-empty value wins.
                    meta["ai_title"] = ev.get("aiTitle") or meta["ai_title"]
                elif t == "last-prompt":
                    meta["last_prompt"] = meta["last_prompt"] or ev.get("lastPrompt")
                elif t == "attachment":
                    queued = _core._extract_queued_command_prompt(ev)
                    if queued:
                        meta["last_event_type"] = "user"
                        meta["last_prompt"] = queued.get("text") or meta["last_prompt"]
                        ts = ev.get("timestamp", "")
                        if ts:
                            try:
                                from datetime import datetime as _dt
                                dt = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                                meta["last_meaningful_ts"] = dt.timestamp()
                            except (ValueError, ImportError):
                                pass
                elif t == "pr-link":
                    pr_url = ev.get("prUrl") or ev.get("pr_url") or ""
                    mp = _gh_pr_url_re.search(pr_url)
                    if mp:
                        meta["tail_pr_number"] = int(mp.group(2))
                        meta["tail_pr_url"] = (
                            "https://github.com/" + mp.group(1)
                            + "/pull/" + mp.group(2)
                        )
                    else:
                        pr_number = ev.get("prNumber") or ev.get("pr_number")
                        repo = ev.get("prRepository") or ev.get("pr_repository") or ""
                        try:
                            n = int(pr_number)
                        except (TypeError, ValueError):
                            n = None
                        if n:
                            meta["tail_pr_number"] = n
                            if repo and "/" in repo:
                                meta["tail_pr_url"] = (
                                    "https://github.com/" + repo.strip("/")
                                    + "/pull/" + str(n)
                                )
                # Session signals from tool calls
                elif t == "assistant":
                    msg = _core._safe_parse_message(ev.get("message", {}))
                    if msg.get("model"):
                        meta["model"] = msg.get("model")
                    # Effort rides on the assistant record itself, not inside
                    # `message`. Free here because this tail is already being
                    # parsed for the model, which is the only reason a list row
                    # can state it without the per-row read the perf gate bans.
                    if ev.get("effort"):
                        meta["reasoning_effort"] = str(ev.get("effort")).strip()
                    u = msg.get("usage") or {}
                    if isinstance(u, dict):
                        ti = u.get("input_tokens") or 0
                        tcw = u.get("cache_creation_input_tokens") or 0
                        tcr = u.get("cache_read_input_tokens") or 0
                        if ti or tcw or tcr:
                            meta["latest_input_tokens"] = ti + tcw + tcr
                            meta["peak_input_tokens"] = max(
                                meta["peak_input_tokens"], meta["latest_input_tokens"],
                            )
                        mid = msg.get("id") if isinstance(msg.get("id"), str) else ""
                        if not mid or mid not in _usage_message_ids:
                            meta["lifetime_tokens"] += (
                                _core._codex_int(ti)
                                + _core._codex_int(tcw)
                                + _core._codex_int(tcr)
                                + _core._codex_int(u.get("output_tokens"))
                            )
                            meta["total_input_tokens"] += _core._codex_int(ti)
                            meta["total_cache_creation_tokens"] += _core._codex_int(tcw)
                            meta["total_cache_read_tokens"] += _core._codex_int(tcr)
                            meta["total_output_tokens"] += _core._codex_int(u.get("output_tokens"))
                            cost = _core._claude_usage_cost_breakdown(msg.get("model"), u)
                            meta["cost_usd"] += cost["total"]
                            for key in meta["cost_breakdown_usd"]:
                                meta["cost_breakdown_usd"][key] += cost[key]
                            if mid:
                                _usage_message_ids.add(mid)
                    content = msg.get("content", [])
                    if not isinstance(content, list):
                        content = []
                    last_tool_name = None
                    last_tool_file = None
                    # Capture last text block from this assistant turn as the "outcome"
                    for block in content:
                        if block.get("type") == "text":
                            txt = (block.get("text") or "").strip()
                            if txt:
                                meta["last_assistant_text"] = txt
                    for block in content:
                        if block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        last_tool_name = name
                        if name == "AskUserQuestion":
                            last_tool_file = _core._tool_use_detail(name, inp, max_len=120) or None
                        else:
                            last_tool_file = inp.get("file_path") or inp.get("command", "")[:60] or None
                        if name in ("Edit", "Write", "NotebookEdit"):
                            meta["has_edit"] = True
                            meta["last_edit_pos"] = _pos
                        elif name == "Task" or name == "Agent":
                            # Claude Code calls the subagent-spawn tool "Task"
                            # in its current tool list, but the on-disk JSONL
                            # records the name as "Agent" (legacy alias kept
                            # for transcript compatibility). Catch both so
                            # counts work across sessions of any age.
                            tu_id = block.get("id")
                            if tu_id:
                                desc = (inp.get("description") or "").strip()[:80]
                                subagent = (inp.get("subagent_type") or "").strip()[:40]
                                entry = {
                                    "id": tu_id,
                                    "description": desc or "(unnamed task)",
                                    "subagent_type": subagent,
                                    "status": "in-flight",
                                    "pos": _pos,
                                }
                                _subagent_by_id[tu_id] = entry
                                _subagent_order.append(tu_id)
                                meta["subagent_count"] += 1
                        elif name == "Bash":
                            cmd = inp.get("command", "")
                            signals = _core._shell_command_signals(cmd)
                            # `signals["edit"]` is true for shell-driven edits
                            # like `sed -i`, `tee`, `apply_patch`, `cat > file`,
                            # `printf … > file`, `perl -pi`. Without it,
                            # sessions that edited via Bash (instead of the
                            # Edit/Write/NotebookEdit tools) showed a
                            # misleading "no edits" lifecycle chip. Every
                            # other engine's parser already consumed this
                            # signal — only the Claude branch was missing it.
                            if signals["edit"]:
                                meta["has_edit"] = True
                                meta["last_edit_pos"] = _pos
                            if signals["commit"]:
                                meta["has_commit"] = True
                                meta["last_commit_pos"] = _pos
                            if signals["push"]:
                                meta["has_push"] = True
                                meta["last_push_pos"] = _pos
                            # Drift indicator: any `cd <path>` or `git -C <path>`
                            # means the session may have moved across repos.
                            # Used by find_conversations() to skip the
                            # _infer_effective_repo walk when there's nothing
                            # to find.
                            if signals["external_cd"]:
                                meta["has_external_cd"] = True
                            # Detect issue number from high-confidence signals
                            mi = (_gh_issue_cmd_re.search(cmd)
                                  or _closes_re.search(cmd)
                                  or _gh_url_re.search(cmd))
                            if mi:
                                meta["tail_issue_number"] = mi.group(1)
                            # Track gh-pr-create tool_use_ids; the matching
                            # tool_result will carry the PR URL we want.
                            if signals["pr"]:
                                tu_id = block.get("id")
                                if tu_id:
                                    _pending_pr_ids.add(tu_id)
                        elif name == "EnterWorktree":
                            # Same drift signal as external_cd above, but for
                            # the native worktree tool: it relocates
                            # tool-execution cwd without ever running a Bash
                            # `cd`/`git -C`, so the signal above never fires
                            # for it on its own. Any call is enough to gate
                            # _infer_effective_repo — see _scan_session_tool_paths
                            # for how the resulting path itself gets resolved.
                            meta["has_external_cd"] = True
                    # The last assistant message's tool_use is "pending" until
                    # a tool_result or user message clears it
                    if last_tool_name:
                        meta["pending_tool"] = last_tool_name
                        meta["pending_file"] = last_tool_file
                elif t == "system":
                    subtype = ev.get("subtype") or ""
                    if subtype == "compact_boundary":
                        meta_cb = ev.get("compactMetadata") or {}
                        post_tokens = _core._codex_int(meta_cb.get("postTokens"))
                        meta["latest_input_tokens"] = post_tokens
                        # /compact rewrites the JSONL much smaller, so the
                        # live_context_* fields captured from a pre-compact
                        # `/context` invocation no longer reflect reality.
                        # Reset them so the badge falls back to the fresh
                        # post-compact latest_input_tokens (calc path).
                        # Next `/context` run will repopulate live_context_*
                        # with the real new percentage.
                        meta["live_context_tokens"] = 0
                        meta["live_context_limit"] = 0
                        meta["live_context_percent"] = 0
                    elif subtype == "local_command":
                        parsed_context = _core._local_command_context_usage(ev)
                        if parsed_context:
                            meta["live_context_tokens"] = parsed_context["tokens"]
                            meta["live_context_limit"] = parsed_context["limit"]
                            meta["live_context_percent"] = parsed_context["percent"]
                # Tool results land as a user-role event; scan for PR URLs
                # when we're matching a `gh pr create` we already saw, and
                # flip any Task tool_result we're tracking from in-flight
                # → done so subagent_in_flight_count stays accurate.
                elif t == "user" and (_pending_pr_ids or _subagent_by_id):
                    msg_content = ev.get("message", {}).get("content")
                    if isinstance(msg_content, list):
                        for sub in msg_content:
                            if not isinstance(sub, dict) or sub.get("type") != "tool_result":
                                continue
                            tu_id = sub.get("tool_use_id", "")
                            if not tu_id:
                                continue
                            if tu_id in _subagent_by_id:
                                entry = _subagent_by_id[tu_id]
                                if entry.get("status") == "in-flight":
                                    entry["status"] = "done"
                            if tu_id not in _pending_pr_ids:
                                continue
                            _pending_pr_ids.discard(tu_id)
                            rc = sub.get("content")
                            text = ""
                            if isinstance(rc, str):
                                text = rc
                            elif isinstance(rc, list):
                                text = "\n".join(
                                    b.get("text", "") for b in rc
                                    if isinstance(b, dict) and b.get("type") == "text"
                                )
                            mp = _gh_pr_url_re.search(text)
                            if mp:
                                meta["tail_pr_number"] = int(mp.group(2))
                                meta["tail_pr_url"] = (
                                    "https://github.com/" + mp.group(1)
                                    + "/pull/" + mp.group(2)
                                )
    except OSError:
        pass
    # Finalize subagent meta now that we've walked the file: count what's
    # still in-flight and snapshot the most recent 8 for the rail panel.
    if _subagent_by_id:
        meta["subagent_in_flight_count"] = sum(
            1 for e in _subagent_by_id.values() if e.get("status") == "in-flight"
        )
        last_ids = _subagent_order[-8:]
        meta["subagent_recent"] = [
            {
                "description": _subagent_by_id[tid]["description"],
                "subagent_type": _subagent_by_id[tid]["subagent_type"],
                "status": _subagent_by_id[tid]["status"],
            }
            for tid in last_ids if tid in _subagent_by_id
        ]
    meta["cache_key"] = cache_key
    global _conv_meta_cache_dirty
    with _core._conv_meta_cache_lock:
        _core._conv_meta_cache[str(path)] = meta
        _conv_meta_cache_dirty = True
    return meta


def _truncate_session_name(name):
    """Clamp a session-name override to SESSION_NAME_MAX_CHARS. Whitespace is
    collapsed first so a multi-line paste reads as a single sentence in the
    sidebar instead of stretching the column or breaking layout."""
    if name is None:
        return None
    s = re.sub(r"\s+", " ", str(name)).strip()
    if not s:
        return s
    if len(s) <= _core.SESSION_NAME_MAX_CHARS:
        return s
    return s[: _core.SESSION_NAME_MAX_CHARS - 1].rstrip() + "…"


def _load_session_name_overrides():
    """Load user-set names from the side-car file. Returns {session_id: name}.

    Values are truncated to SESSION_NAME_MAX_CHARS on read so that legacy
    entries (annotation context dumped as a "name") cannot inflate the
    sidebar even before the next write rewrites the file."""
    try:
        data = json.loads(SESSION_NAMES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: _core._truncate_session_name(v)
        for k, v in data.items()
        if isinstance(k, str) and v
    }


def _load_conversation_order():
    """Load user-set conversation order. Returns list of session_ids (or []) ."""
    try:
        data = json.loads(CONVERSATION_ORDER_FILE.read_text())
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_conversation_order(order):
    """Persist custom conversation order (list of session_ids)."""
    _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(order, list):
        order = []
    CONVERSATION_ORDER_FILE.write_text(json.dumps(order, indent=2))
    return order




def _load_archive_grace() -> dict:
    """Load the sticky manual-archive map (sid → archived-at epoch).

    A session in this map was deliberately archived by the user and must NOT
    be auto-unarchived even while it's live and streaming. Persisted so the
    stickiness survives a server restart ("archive refuses to work" on live
    rows — CCC-149 follow-up).
    """
    try:
        data = json.loads(ARCHIVE_GRACE_FILE.read_text())
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items()
                    if isinstance(k, str) and isinstance(v, (int, float))}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _save_archive_grace() -> None:
    try:
        _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        ARCHIVE_GRACE_FILE.write_text(json.dumps(_core._archive_grace, indent=2))
    except OSError:
        pass


_archive_grace: dict = _load_archive_grace()  # sid → epoch; manual archives, sticky vs auto-unarchive


_codex_pool_candidates_cache = {"ts": 0.0, "sids": frozenset()}
_CODEX_POOL_CANDIDATES_TTL = 15.0


def _codex_pool_candidate_sids(now=None):
    """Thread ids Codex's sqlite index has touched inside the archive
    freshness window — a candidacy set for pool-model Codex.app threads
    (CCC-435), which never appear in _discover_live_session_ids()'s
    resume-arg scan because `codex app-server` puts no per-session id on
    any command line.

    Cost model: ONE cached, bulk `_codex_fetch_threads()` SQL query (already
    ordered newest-first, LIMIT-bounded) — never a per-sid sqlite connect and
    never a filesystem glob across CODEX_SESSIONS_ROOT. Also gated behind the
    already-cached _codex_pool_alive(), so a machine with no Codex.app pool
    process running pays nothing at all.
    """
    now = now if now is not None else time.time()
    cached = _core._codex_pool_candidates_cache
    if now - cached["ts"] < _CODEX_POOL_CANDIDATES_TTL:
        return cached["sids"]
    sids = set()
    if _core._codex_pool_alive(now):
        try:
            for row in _core._codex_fetch_threads(limit=100):
                ts = _core._codex_ts_seconds(row, prefix="updated") or _core._codex_ts_seconds(row, prefix="created")
                if ts and (now - ts) < 300:
                    tid = row.get("id")
                    if tid:
                        sids.add(tid)
        except Exception:
            sids = set()
    sids = frozenset(sids)
    _core._codex_pool_candidates_cache["ts"] = now
    _core._codex_pool_candidates_cache["sids"] = sids
    return sids


_recently_auto_unarchived: dict = {}  # sid -> epoch dropped from Archived; drives the "[unarchived]" row prefix
_RECENTLY_UNARCHIVED_WINDOW_S = 600  # 10 min — long enough to notice across a few polls, short enough to not become permanent chrome


def _is_recently_unarchived(sid, now=None) -> bool:
    ts = _recently_auto_unarchived.get(sid)
    if ts is None:
        return False
    now = now if now is not None else time.time()
    return (now - ts) < _RECENTLY_UNARCHIVED_WINDOW_S


def _auto_unarchive_live_sessions(archived):
    """Drop archived sids showing fresh activity (CCC-117).

    A session that's actively writing its transcript doesn't belong in the
    Archived bucket — auto-unarchive it. Perf gate: runs at most every 30s,
    AND only probes the (heavier, engine-classifying) _archive_session_is_live
    for sids that are plausible liveness candidates — never for the whole
    archived list. A per-sid _archive_session_is_live probe for every
    archived sid regressed this sweep to hundreds of sqlite connects/globs on
    a real archive (CCC-435 follow-up); the candidacy set below keeps the
    sweep O(candidates), not O(all archived), while still catching pool-model
    Codex.app threads. The grace map keeps a just-archived-by-the-user
    session from bouncing straight back (its transcript mtime is fresh from
    the kill/final writes).
    """
    now = time.time()
    if not archived or now - _core._archive_auto_sweep_last < 30:
        return archived
    _core._archive_auto_sweep_last = now
    # Candidacy gate, two cheap membership sources unioned:
    #   - _discover_live_session_ids(): Claude registry + engine resume-arg
    #     scan + fresh sidecar markers — the original CCC-117 gate, and
    #     already used the same way by _rehydrate_archive_cached_rows.
    #   - _codex_pool_candidate_sids(): pool-model Codex.app threads the
    #     resume-arg scan can never see (CCC-435), via one cached bulk query.
    candidates = _core._discover_live_session_ids() | _core._codex_pool_candidate_sids(now)
    keep = []
    changed = False
    for sid in archived:
        fresh = False
        # Sticky manual archives are exempt: a session the user deliberately
        # archived stays archived even while it streams (CCC-149 follow-up).
        # Only sessions that entered the archive WITHOUT a manual marker (none
        # today, but kept for CCC-117's resume-brings-back intent) can bounce.
        if sid not in _core._archive_grace and sid in candidates:
            try:
                live = _core._archive_session_is_live(sid)
            except Exception:
                live = False
            if live:
                try:
                    path, _parser = _core._resolve_conversation_reader(sid)
                    fresh = path and path.is_file() and now - path.stat().st_mtime < 300
                except (OSError, Exception):
                    fresh = False
        if fresh:
            changed = True
            _recently_auto_unarchived[sid] = now
            _core._log_archive_event(
                "auto-unarchive", sid,
                f"live+fresh transcript (mtime age {now - path.stat().st_mtime:.0f}s)",
            )
            continue
        keep.append(sid)
    if changed:
        for stale_sid, ts in list(_recently_auto_unarchived.items()):
            if now - ts >= _RECENTLY_UNARCHIVED_WINDOW_S:
                del _recently_auto_unarchived[stale_sid]
        try:
            _core._save_archived_conversations(keep)
        except Exception:
            return archived
    return keep


def _load_archived_conversations(*, sweep=True):
    """Load list of archived session_ids from the side-car file."""
    with _conversation_lifecycle_lock:
        try:
            data = json.loads(_core.ARCHIVED_CONVERSATIONS_FILE.read_text())
            archived = [s for s in data if isinstance(s, str)] if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            archived = []
        # Trash is a terminal subset of Archive. Repair older split-brain
        # sidecars here so every caller — including refresh-time row builders —
        # observes the invariant even if a previous concurrent write lost the
        # archive entry while preserving the trash marker.
        trashed = _core._load_trashed_conversations(sweep=False)
        repaired = archived + [sid for sid in trashed if sid not in archived]
        if repaired != archived:
            _write_archived_conversations(repaired)
        if not sweep:
            return repaired
        trashed_set = set(trashed)
        sweepable = [sid for sid in repaired if sid not in trashed_set]
        swept = _core._auto_unarchive_live_sessions(sweepable)
        swept_set = set(swept)
        return [sid for sid in repaired if sid in trashed_set or sid in swept_set]


def _write_archived_conversations(archived):
    """Write an already-normalized archive list while the lifecycle lock is held."""
    _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    _core.ARCHIVED_CONVERSATIONS_FILE.write_text(json.dumps(archived, indent=2))
    return archived


def _save_archived_conversations(archived):
    """Persist list of archived session_ids."""
    with _conversation_lifecycle_lock:
        if not isinstance(archived, list):
            archived = []
        archived = [sid for sid in archived if isinstance(sid, str)]
        trashed = _core._load_trashed_conversations(sweep=False)
        normalized = archived + [sid for sid in trashed if sid not in archived]
        return _write_archived_conversations(normalized)


def _load_trashed_conversations(*, sweep=True):
    """Load trashed session ids. `sweep` exists for archive-helper parity."""
    with _conversation_lifecycle_lock:
        try:
            data = json.loads(_core.TRASHED_CONVERSATIONS_FILE.read_text())
            if isinstance(data, list):
                return [sid for sid in data if isinstance(sid, str)]
        except (OSError, json.JSONDecodeError):
            pass
        return []


def _save_trashed_conversations(trashed):
    """Persist trashed session ids."""
    with _conversation_lifecycle_lock:
        _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        if not isinstance(trashed, list):
            trashed = []
        _core.TRASHED_CONVERSATIONS_FILE.write_text(json.dumps(trashed, indent=2))
        return trashed


def _load_conversation_lifecycle_state():
    """Return repaired Archive and Trash lists as one consistent snapshot."""
    with _conversation_lifecycle_lock:
        trashed = _core._load_trashed_conversations(sweep=False)
        archived = _core._load_archived_conversations(sweep=False)
        return archived, trashed


def _load_conversation_lifecycle_sets(*, sweep=False):
    """Return one atomic row-building snapshot, preserving Trash ⊆ Archive."""
    with _conversation_lifecycle_lock:
        archived = set(_core._load_archived_conversations(sweep=sweep))
        trashed = set(_core._load_trashed_conversations(sweep=False))
        archived.update(trashed)
        return archived, trashed


def _clear_trashed_on_unarchive(sid):
    """Remove Trash membership when a session moves back to Active."""
    _clear_trashed_on_unarchive_many({sid})


def _clear_trashed_on_unarchive_many(session_ids):
    """Atomically remove sessions from Trash during an Active transition."""
    remove = {sid for sid in session_ids if isinstance(sid, str) and sid}
    if not remove:
        return
    with _conversation_lifecycle_lock:
        trashed = _core._load_trashed_conversations(sweep=False)
        remaining = [sid for sid in trashed if sid not in remove]
        if remaining != trashed:
            _core._save_trashed_conversations(remaining)


def _set_conversation_archived(sid, desired=None, *, source="manual"):
    """Atomically set/toggle one Archive state and its Trash/grace invariants."""
    with _conversation_lifecycle_lock:
        archived = _core._load_archived_conversations()
        is_archived = sid in archived
        want_archived = desired if isinstance(desired, bool) else not is_archived
        changed = want_archived != is_archived

        if want_archived and not is_archived:
            archived.append(sid)
            _core._archive_grace[sid] = time.time()
            _core._save_archive_grace()
            _core._log_archive_event("archive", sid, source)
        elif not want_archived and is_archived:
            archived.remove(sid)
            _core._archive_grace.pop(sid, None)
            _core._save_archive_grace()
            _core._log_archive_event("unarchive", sid, source)

        if not want_archived:
            _core._clear_trashed_on_unarchive(sid)
        _core._save_archived_conversations(archived)
        return want_archived, changed


def _set_conversations_archived(session_ids, want_archived):
    """Atomically set Archive state for a deduplicated batch."""
    with _conversation_lifecycle_lock:
        archived = _core._load_archived_conversations()
        archived_set = set(archived)
        changed, unchanged, to_add, to_remove = [], [], [], set()
        for sid in session_ids:
            is_archived = sid in archived_set
            if want_archived and not is_archived:
                to_add.append(sid)
                archived_set.add(sid)
                changed.append(sid)
            elif not want_archived and is_archived:
                to_remove.add(sid)
                archived_set.discard(sid)
                changed.append(sid)
            else:
                unchanged.append(sid)

        if changed:
            new_list = [sid for sid in archived if sid not in to_remove] + to_add
            now = time.time()
            for sid in to_add:
                _core._archive_grace[sid] = now
                _core._log_archive_event("archive", sid, "bulk")
            for sid in to_remove:
                _core._archive_grace.pop(sid, None)
                _core._log_archive_event("unarchive", sid, "bulk")
            if to_remove:
                _clear_trashed_on_unarchive_many(to_remove)
            _core._save_archived_conversations(new_list)
            _core._save_archive_grace()
        return changed, unchanged, to_add, to_remove


def _find_descendant_sessions(sid, max_depth=6):
    """Return all known child/descendant session_ids of ``sid``.

    Uses the unified SessionGraph as the primary path (O(1) per edge),
    falling back to the original four-source merge if the graph is empty
    (e.g. before startup build has run, or in a test context).
    """
    if not sid:
        return []
    # Primary path: SessionGraph (built at startup, incrementally updated).
    graph_descendants = _core._session_graph.descendants(sid, max_depth=max_depth)
    # An empty result is a valid answer once the graph has any edges.
    # `if graph_descendants:` treated "this sid has no children" as "graph
    # isn't ready" and fell back to scanning Codex sqlite + the thread
    # registry on every trash of a leaf session.
    if graph_descendants or _core._session_graph.stats().get("edges"):
        return graph_descendants
    # Fallback: original four-source merge (used before the graph is built
    # or if it somehow has no edges for this session).
    parent_by_child = {}
    try:
        parent_by_child.update(_core._codex_spawn_parent_by_child())
    except Exception:
        pass
    try:
        parent_by_child.update(_core._load_codex_parent_links())
    except Exception:
        pass
    try:
        for entry in _core._load_spawn_registry():
            if not isinstance(entry, dict):
                continue
            child = str(entry.get("session_id") or "").strip()
            parent = str(entry.get("parent_session_id") or "").strip()
            if child and parent and child != parent:
                parent_by_child.setdefault(child, parent)
    except Exception:
        pass
    try:
        for child_id, entry in _core._codex_thread_registry_entries().items():
            if not isinstance(entry, dict):
                continue
            parent = str(entry.get("parent_session_id") or "").strip()
            if child_id and parent and child_id != parent:
                parent_by_child.setdefault(child_id, parent)
    except Exception:
        pass
    # Walk down from sid, collecting all descendants.
    descendants = []
    seen = {sid}
    frontier = [sid]
    for _ in range(max_depth):
        next_frontier = []
        for child, parent in parent_by_child.items():
            if parent in seen and child not in seen:
                seen.add(child)
                descendants.append(child)
                next_frontier.append(child)
        if not next_frontier:
            break
        frontier = next_frontier
    return descendants


def _find_continuation_ancestors(sid, max_depth=20):
    """Return the chain of continuation-origin ancestors of ``sid``.

    A "Continue in a new session" / auto-resume session records its origin
    in its transcript's first user prompt as "Origin session id: <sid>".
    This walks that link upward: resolve ``sid``'s jsonl (via
    ``_find_session_jsonl``, falling back to
    ``_find_session_jsonl_any_project``), read its continuation origin (via
    ``_continued_from_session_id_from_transcript``), then repeat for that
    origin. Cycle-guarded and capped at ``max_depth`` hops — cheap, since
    each hop is one transcript-head read plus the jsonl finders' existing
    project-dir scan.

    Returns ancestors nearest-first (immediate origin first, oldest last),
    excluding ``sid`` itself. Never raises; returns whatever was collected
    so far if anything goes wrong.
    """
    ancestors = []
    if not sid:
        return ancestors
    seen = {sid}
    current = sid
    for _ in range(max_depth):
        try:
            path = _core._find_session_jsonl(current)
            if path is None:
                path = _core._find_session_jsonl_any_project(current)
            if path is None:
                break
            origin = _core._continued_from_session_id_from_transcript(path)
        except Exception:
            break
        origin = str(origin or "").strip()
        if not origin or origin in seen:
            break
        ancestors.append(origin)
        seen.add(origin)
        current = origin
    return ancestors

