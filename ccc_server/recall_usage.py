# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 55786-57220).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Total Recall search — optional session-level augmentation for the sidebar
# conversation search. This does NOT replace the local claude-index path above:
# Recall is broader and summary-based, so it contributes extra session hits
# while knowledge/document hits stay out of the "Search conversations" UI.
# ---------------------------------------------------------------------------

_TOTAL_RECALL_TIMEOUT_SEC = 8.0
_TOTAL_RECALL_SESSION_SOURCES = {"claude-code", "codex"}


def _total_recall_command():
    """Return the Total Recall CLI command prefix, or None when unavailable."""
    candidates = []
    for env_name in ("CCC_TOTAL_RECALL_BRAIN", "TOTAL_RECALL_BRAIN"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw:
            candidates.append(Path(raw).expanduser())

    home = Path.home()
    tr_home = (os.environ.get("TOTAL_RECALL_HOME") or "").strip()
    bases = []
    if tr_home:
        bases.append(Path(tr_home).expanduser())
    bases.extend([home / ".claude" / "total-recall", home / ".total-recall"])
    for base in bases:
        candidates.append(base / "plugins" / "total-recall" / "brain.dist" / "brain")

    for path in candidates:
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return [str(path)]
        except OSError:
            continue

    tr = shutil.which("total-recall")
    return [tr] if tr else None


def _unquote_total_recall_scalar(value):
    s = (value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _read_total_recall_summary_meta(path):
    """Parse the small YAML-ish front matter Total Recall writes to summaries.

    PyYAML is intentionally not a dependency here; CCC's runtime remains
    stdlib-only. We only need scalar keys such as session_id, source_harness,
    project, date, and time_start.
    """
    try:
        p = Path(path).expanduser()
    except (TypeError, ValueError):
        return {}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(65536)
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not m:
            continue
        meta[m.group(1)] = _unquote_total_recall_scalar(m.group(2))
    return meta


def _total_recall_snippet(text):
    s = str(text or "")
    # Markdown headings/list markers read poorly inside the compact row preview.
    s = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", s)
    s = re.sub(r"(?m)^\s*[-*]\s+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > 360:
        s = s[:357].rstrip() + "…"
    return html.escape(s) if s else ""


def _total_recall_ts_unix(meta, item):
    date_s = (meta.get("date") or item.get("date") or "").strip()
    if not date_s:
        return 0
    time_s = (meta.get("time_start") or "00:00").strip() or "00:00"
    try:
        if "T" in date_s:
            dt = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(f"{date_s}T{time_s}")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return 0


def search_total_recall_sessions(query, limit=20, cwd_like=None):
    """Search Total Recall and return only openable session-backed hits.

    The result shape mirrors search_conversation_history enough for the
    existing sidebar augmentation to consume it: session_id, cwd, ts_unix,
    snippet, and _source. Knowledge/document matches are filtered out here.
    """
    q = (query or "").strip()
    if not q:
        return {"results": []}
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 20
    cmd = _core._total_recall_command()
    if not cmd:
        return {"results": []}

    try:
        proc = subprocess.run(
            cmd + ["recall", "--query", q, "--limit", str(limit), "--format", "json"],
            capture_output=True,
            text=True,
            timeout=_TOTAL_RECALL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return {"error": "total recall search timed out", "results": []}
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"total recall search failed: {e}", "results": []}

    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as e:
        return {"error": f"total recall returned invalid json: {e}", "results": []}
    if proc.returncode != 0 and not data:
        return {"error": (proc.stderr or "total recall search failed").strip(), "results": []}

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raw_results = [{"path": p} for p in data.get("paths", []) if isinstance(p, str)]

    out = []
    seen = set()
    cwd_filter = (cwd_like or "").strip()
    for idx, item in enumerate(raw_results):
        if not isinstance(item, dict):
            continue
        item_source = str(item.get("source_harness") or "").strip()
        if item_source == "knowledge":
            continue
        meta = _read_total_recall_summary_meta(item.get("path"))
        source = (
            meta.get("source_harness")
            or item_source
            or ""
        ).strip()
        if source == "knowledge":
            continue
        if source and source not in _TOTAL_RECALL_SESSION_SOURCES:
            continue
        session_id = (meta.get("session_id") or item.get("session_id") or "").strip()
        if not session_id or session_id in seen:
            continue
        cwd = (meta.get("project") or item.get("project") or "").strip()
        if cwd_filter and cwd_filter not in cwd:
            continue
        snippet = _total_recall_snippet(item.get("summary") or item.get("title") or "")
        if not snippet:
            snippet = _total_recall_snippet(session_id)
        ts_unix = _total_recall_ts_unix(meta, item)
        out.append({
            "uuid": f"recall:{session_id}",
            "session_id": session_id,
            "type": "recall",
            "cwd": cwd,
            "git_branch": "",
            "timestamp": meta.get("date") or item.get("date") or "",
            "ts_unix": ts_unix,
            "snippet": snippet,
            "score": idx,
            "_source": "recall",
        })
        seen.add(session_id)
        if len(out) >= limit:
            break
    return {"results": out}


_plan_usage_cache = None
_plan_usage_cache_time = 0
_plan_usage_cache_lock = threading.Lock()
_USAGE_SNAPSHOT_MAX_LINES = 50_000
_USAGE_SNAPSHOT_MAX_AGE_DAYS = 90
_USAGE_NATIVE_FRESH_SECS = 15 * 60
# A provider snapshot older than this (or whose weekly window already reset)
# is surfaced with an additive `stale: true` marker so the UI can render it
# in a visibly-stale style instead of the live visual language. Kimi's access
# token only lives ~15 minutes after its own CLI runs, so its snapshots can
# legitimately sit cached for days — the marker is the honest presentation.
_USAGE_PROVIDER_STALE_SECS = 24 * 3600
_PLAN_USAGE_POLL_SECS = 5 * 60
_RESET_DETECT_JITTER_SECS = 5 * 60
_RESET_DETECT_MAX_PREV_AGE_SECS = 30 * 60
_CODEX_USAGE_SCAN_DAYS = 9
_usage_snapshots_lock = threading.Lock()
_reset_events_lock = threading.Lock()
_codex_usage_file_cache_lock = threading.Lock()
_codex_usage_file_cache = {}
_plan_usage_poller_lock = threading.Lock()
_plan_usage_poller_started = False


def _get_claude_credentials():
    # 1. Environment variable
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return {"oauth_token": token}

    # 2. macOS System Keychain (cookie header)
    try:
        res = subprocess.run(
            ["security", "find-generic-password", "-s", "com.steipete.codexbar.cache", "-a", "cookie.claude", "-w"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0 and res.stdout.strip():
            try:
                data = json.loads(res.stdout.strip())
                cookie = data.get("cookieHeader") or ""
                if "sessionKey=" in cookie:
                    for part in cookie.split(";"):
                        part = part.strip()
                        if part.startswith("sessionKey="):
                            return {"session_key": part.split("=", 1)[1]}
                return {"session_key": cookie}
            except Exception:
                pass
    except Exception:
        pass

    # 3. macOS System Keychain fallback for standard OAuth (Claude Code token)
    try:
        res = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0 and res.stdout.strip():
            val = res.stdout.strip()
            if val.startswith("{"):
                try:
                    data = json.loads(val)
                    token = data.get("accessToken") or data.get("oauthToken") or data.get("token")
                    if token:
                        return {"oauth_token": token}
                except Exception:
                    pass
            else:
                return {"oauth_token": val}
    except Exception:
        pass

    # 4. Linux/Windows/macOS credentials file fallback
    cred_file = Path.home() / ".claude" / ".credentials.json"
    if cred_file.is_file():
        try:
            with open(cred_file, "r") as f:
                data = json.load(f)
                token = data.get("oauthToken") or data.get("accessToken") or data.get("token")
                if token:
                    return {"oauth_token": token}
                session_key = data.get("sessionKey")
                if session_key:
                    return {"session_key": session_key}
        except Exception:
            pass

    return None


_CLAUDE_ACCOUNT_FILE = Path.home() / ".claude.json"
_CLAUDE_ORG_ID_STATE_FILE = Path.home() / ".claude" / "command-center" / "claude-org-id.json"
_claude_org_id_last_diagnostic = None


def _valid_claude_org_id(value):
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _load_last_claude_org_id():
    try:
        with open(_CLAUDE_ORG_ID_STATE_FILE, "r") as f:
            return _valid_claude_org_id(json.load(f).get("org_id"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _save_last_claude_org_id(org_id):
    try:
        _CLAUDE_ORG_ID_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = _CLAUDE_ORG_ID_STATE_FILE.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump({"org_id": org_id}, f)
        os.replace(tmp_file, _CLAUDE_ORG_ID_STATE_FILE)
    except OSError:
        pass


def _claude_org_id_diagnostic():
    return _claude_org_id_last_diagnostic


def _get_claude_org_id():
    global _claude_org_id_last_diagnostic
    _claude_org_id_last_diagnostic = None

    # ~/.claude.json carries the org UUID directly — instant, no subprocess.
    try:
        with open(_CLAUDE_ACCOUNT_FILE, "r") as f:
            account_org_id = _valid_claude_org_id(
                (json.load(f).get("oauthAccount") or {}).get("organizationUuid")
            )
        if account_org_id:
            _save_last_claude_org_id(account_org_id)
            return account_org_id
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    # The CLI can cold-start slowly. A cached UUID is only safe on that
    # transient timeout; auth failures and malformed output must stay visible.
    try:
        claude_info = _core._resolve_claude_bin()
        cmd = claude_info.get("bin") if claude_info.get("available") else "claude"
        res = subprocess.run([cmd, "auth", "status"], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        cached_org_id = _load_last_claude_org_id()
        if cached_org_id:
            _claude_org_id_last_diagnostic = (
                "claude auth status timed out after 15 seconds; using cached organization ID"
            )
            return cached_org_id
        _claude_org_id_last_diagnostic = "claude auth status timed out after 15 seconds"
        return None
    except (FileNotFoundError, OSError) as e:
        _claude_org_id_last_diagnostic = f"claude auth status failed: {type(e).__name__}"
        return None

    if res.returncode != 0:
        _claude_org_id_last_diagnostic = f"claude auth status exited {res.returncode}"
        return None
    try:
        cli_org_id = _valid_claude_org_id(json.loads(res.stdout.strip()).get("orgId"))
    except (TypeError, ValueError, json.JSONDecodeError):
        cli_org_id = None
    if not cli_org_id:
        _claude_org_id_last_diagnostic = "claude auth status returned an invalid organization ID"
        return None
    _save_last_claude_org_id(cli_org_id)
    return cli_org_id


def _fetch_plan_usage():
    creds = _get_claude_credentials()
    if not creds:
        return {"ok": False, "error": "No credentials found. Please run `claude auth login` in your terminal."}

    org_id = _get_claude_org_id()
    if not org_id:
        diagnostic = _claude_org_id_diagnostic()
        error = "Could not determine organization ID. Ensure `claude` is logged in."
        return {"ok": False, "error": f"{error} {diagnostic}" if diagnostic else error}

    url = f"https://claude.ai/api/organizations/{org_id}/usage"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

    if "session_key" in creds:
        req.add_header("Cookie", f"sessionKey={creds['session_key']}")
    elif "oauth_token" in creds:
        req.add_header("Authorization", f"Bearer {creds['oauth_token']}")

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return {"ok": True, "usage": res_data}
    except Exception as e:
        return {"ok": False, "error": f"Failed to fetch from Anthropic: {str(e)}"}


def _plan_usage_with_codex(res):
    if not isinstance(res, dict):
        return res
    out = dict(res)
    out["codex"] = _core._latest_codex_usage_from_snapshots()
    return out


def get_cached_plan_usage():
    global _plan_usage_cache, _plan_usage_cache_time
    now = time.time()
    with _plan_usage_cache_lock:
        if _plan_usage_cache is not None and now - _plan_usage_cache_time < 30:
            return _plan_usage_with_codex(_plan_usage_cache)
        res = _fetch_plan_usage()
        _plan_usage_cache = res
        _plan_usage_cache_time = now
        return _plan_usage_with_codex(res)


def _usage_snapshot_iso(now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    return datetime.fromtimestamp(now_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _scoped_weekly_limit_from_usage(usage, model_name):
    if not isinstance(usage, dict):
        return {"utilization": None, "resets_at": None}
    wanted = str(model_name or "").strip().lower()
    explicit = usage.get(f"seven_day_{wanted}")
    if isinstance(explicit, dict):
        return {
            "utilization": explicit.get("utilization"),
            "resets_at": explicit.get("resets_at"),
        }
    limits = usage.get("limits")
    if not isinstance(limits, list):
        return {"utilization": None, "resets_at": None}
    for limit in limits:
        if not isinstance(limit, dict):
            continue
        if limit.get("kind") != "weekly_scoped" or limit.get("group") != "weekly":
            continue
        scope = limit.get("scope") or {}
        model = scope.get("model") if isinstance(scope, dict) else {}
        if not isinstance(model, dict):
            model = {}
        names = {
            str(model.get("display_name") or "").strip().lower(),
            str(model.get("id") or "").strip().lower(),
        }
        if wanted not in names:
            continue
        return {
            "utilization": limit.get("percent"),
            "resets_at": limit.get("resets_at"),
        }
    return {"utilization": None, "resets_at": None}


def _native_usage_snapshot_from_plan_usage(plan_usage, now_epoch=None, codex=None, kimi=None):
    """Compact the direct Claude usage response into the persisted JSONL shape."""
    if not isinstance(plan_usage, dict) or not plan_usage.get("ok"):
        return None
    usage = plan_usage.get("usage")
    if not isinstance(usage, dict):
        return None
    snap = {"ts": _core._usage_snapshot_iso(now_epoch), "source": "native"}
    for key in ("five_hour", "seven_day", "seven_day_sonnet"):
        block = usage.get(key)
        if isinstance(block, dict):
            snap[key] = {
                "utilization": block.get("utilization"),
                "resets_at": block.get("resets_at"),
            }
        else:
            snap[key] = {"utilization": None, "resets_at": None}
    snap["seven_day_fable"] = _scoped_weekly_limit_from_usage(usage, "fable")
    snap["codex"] = codex
    snap["kimi"] = kimi
    return snap


def _codex_usage_iso_from_epoch(epoch):
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def _codex_usage_value(data, snake_key, camel_key):
    if not isinstance(data, dict):
        return None
    if snake_key in data:
        return data.get(snake_key)
    return data.get(camel_key)


def _codex_usage_window_block(block):
    if not isinstance(block, dict):
        return None
    pct = _codex_usage_value(block, "used_percent", "usedPercent")
    resets_at = _codex_usage_value(block, "resets_at", "resetsAt")
    if pct is None or resets_at is None:
        return None
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pct) or pct < 0 or pct > 100:
        return None
    resets_iso = _codex_usage_iso_from_epoch(resets_at)
    if not resets_iso:
        return None
    window_minutes = _codex_usage_value(
        block, "window_minutes", "windowDurationMins"
    )
    try:
        window_minutes = int(window_minutes) if window_minutes is not None else None
    except (TypeError, ValueError):
        window_minutes = None
    return {"pct": pct, "resets_at": resets_iso, "window_minutes": window_minutes}


def _codex_usage_window(rate_limits, key):
    return _codex_usage_window_block((rate_limits or {}).get(key))


def _codex_usage_from_rate_limit_snapshot(
    rate_limits,
    *,
    snapshot_ts=None,
    now_epoch=None,
):
    if not isinstance(rate_limits, dict):
        return None
    if now_epoch is None:
        now_epoch = time.time()
    primary = _codex_usage_window_block(rate_limits.get("primary"))
    secondary = _codex_usage_window_block(rate_limits.get("secondary"))
    windows = [window for window in (primary, secondary) if window]

    weekly = None
    session = None
    for window in windows:
        duration = window.get("window_minutes")
        if isinstance(duration, int):
            if duration >= 7 * 24 * 60:
                if weekly is None or duration > weekly.get("window_minutes", 0):
                    weekly = window
            elif session is None or duration < session.get("window_minutes", duration + 1):
                session = window

    # Older events did not always include durations. Preserve their historical
    # primary=session / secondary=weekly meaning as a compatibility fallback.
    if (
        weekly is None
        and secondary is not None
        and secondary.get("window_minutes") is None
    ):
        weekly = secondary
    if (
        session is None
        and primary is not None
        and primary is not weekly
        and primary.get("window_minutes") is None
    ):
        session = primary
    if weekly is None:
        return None

    plan_type = _codex_usage_value(rate_limits, "plan_type", "planType")
    return {
        "weekly": weekly,
        "session": session,
        "plan_type": plan_type,
        "snapshot_ts": snapshot_ts or _core._usage_snapshot_iso(now_epoch),
        "fetched_at": _core._usage_snapshot_iso(now_epoch),
        "from_cache": False,
    }


def _codex_usage_from_account_rate_limits(response, now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        return None
    by_id = result.get("rateLimitsByLimitId")
    rate_limits = by_id.get("codex") if isinstance(by_id, dict) else None
    if not isinstance(rate_limits, dict):
        legacy = result.get("rateLimits")
        limit_id = _codex_usage_value(legacy, "limit_id", "limitId")
        if isinstance(legacy, dict) and limit_id in (None, "", "codex"):
            rate_limits = legacy
    return _codex_usage_from_rate_limit_snapshot(
        rate_limits,
        snapshot_ts=_core._usage_snapshot_iso(now_epoch),
        now_epoch=now_epoch,
    )


def _iter_recent_codex_rollouts(now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    cutoff = now_epoch - _CODEX_USAGE_SCAN_DAYS * 86400
    root = _core.CODEX_SESSIONS_ROOT
    if not root.is_dir():
        return []
    out = []
    try:
        for path in root.glob("*/*/*/rollout-*.jsonl"):
            try:
                if path.stat().st_mtime >= cutoff:
                    out.append(path)
            except OSError:
                continue
    except OSError:
        return []
    return out


def _codex_file_latest_rate_limits(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = str(path)
    sig = (st.st_mtime, st.st_size)
    with _codex_usage_file_cache_lock:
        cached = _core._codex_usage_file_cache.get(key)
        if cached and cached.get("sig") == sig:
            return cached.get("snapshot")
    best = None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"rate_limits"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (TypeError, ValueError):
                    continue
                payload = rec.get("payload") or {}
                rate_limits = payload.get("rate_limits")
                ts = rec.get("timestamp")
                if not isinstance(rate_limits, dict) or not ts:
                    continue
                limit_id = rate_limits.get("limit_id")
                if limit_id not in (None, "", "codex"):
                    continue
                if best is None or str(ts) > str(best.get("timestamp")):
                    best = {"timestamp": ts, "rate_limits": rate_limits}
    except OSError:
        return None
    with _codex_usage_file_cache_lock:
        _core._codex_usage_file_cache[key] = {"sig": sig, "snapshot": best}
    return best


def _read_codex_usage_from_rollouts(now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    best = None
    for path in _core._iter_recent_codex_rollouts(now_epoch=now_epoch):
        snap = _core._codex_file_latest_rate_limits(path)
        if not snap:
            continue
        if best is None or str(snap.get("timestamp")) > str(best.get("timestamp")):
            best = snap
    if not best:
        return None
    return _codex_usage_from_rate_limit_snapshot(
        best.get("rate_limits"),
        snapshot_ts=best.get("timestamp"),
        now_epoch=now_epoch,
    )


# ---------------------------------------------------------------------------
# Kimi (Moonshot "Kimi Code" CLI) weekly/session usage via the usages API.
# Mirrors the Codex block above: a live fetch normalized into the same
# {weekly, session, plan_type, snapshot_ts, fetched_at, from_cache} shape,
# with the persisted usage-snapshots.jsonl history as the fallback when the
# fetch fails (401, offline, malformed response) — there is no token-refresh
# flow here; the Kimi CLI refreshes its own stored token whenever it runs.
# ---------------------------------------------------------------------------

_KIMI_CREDENTIALS_FILE = Path(
    os.environ.get("KIMI_CREDENTIALS_FILE")
    or (Path.home() / ".kimi-code" / "credentials" / "kimi-code.json")
)
_KIMI_USAGE_BASE_URL = os.environ.get(
    "KIMI_CODE_BASE_URL", "https://api.kimi.com/coding/v1"
).rstrip("/")
_KIMI_USAGE_TIMEOUT_SECS = 8
_KIMI_UNIT_MINUTES = {
    "TIME_UNIT_MINUTE": 1,
    "TIME_UNIT_HOUR": 60,
    "TIME_UNIT_DAY": 1440,
}


def _kimi_usage_int(value):
    """API numeric fields arrive as strings; accept ints/floats too."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def _kimi_usage_reset_epoch(value):
    """ISO 8601 (fractional seconds, Z) or epoch → epoch float, or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        # fromisoformat caps fractional seconds at 6 digits — truncate longer
        m = re.match(r"^(.*\.\d{6})\d+(.*)$", text)
        if m:
            text = m.group(1) + m.group(2)
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _kimi_usage_window_block(block, window_minutes):
    """{pct, resets_at, window_minutes} from a usage/detail object, or None."""
    if not isinstance(block, dict):
        return None
    limit = _kimi_usage_int(block.get("limit"))
    if not limit:
        return None
    used = _kimi_usage_int(block.get("used"))
    if used is None:
        remaining = _kimi_usage_int(block.get("remaining"))
        if remaining is None:
            return None
        used = limit - remaining
    pct = used / limit * 100
    if not math.isfinite(pct) or pct < 0 or pct > 100:
        return None
    resets_at = None
    for key in ("resetTime", "reset_at", "resetAt", "reset_time"):
        if block.get(key) is not None:
            resets_at = block.get(key)
            break
    resets_iso = _codex_usage_iso_from_epoch(_kimi_usage_reset_epoch(resets_at))
    if not resets_iso:
        return None
    return {"pct": pct, "resets_at": resets_iso, "window_minutes": window_minutes}


def _kimi_window_minutes(window):
    duration = _kimi_usage_int((window or {}).get("duration"))
    if duration is None:
        return None
    unit = str((window or {}).get("timeUnit") or "").upper()
    return duration * _KIMI_UNIT_MINUTES.get(unit, 1)


def _kimi_session_window(limits):
    """The 5h (300-minute) entry from limits[], else the smallest window."""
    candidates = []
    for entry in limits if isinstance(limits, list) else []:
        if not isinstance(entry, dict):
            continue
        minutes = _kimi_window_minutes(entry.get("window"))
        if not minutes:
            continue
        parsed = _kimi_usage_window_block(entry.get("detail"), minutes)
        if parsed:
            candidates.append((minutes, parsed))
    if not candidates:
        return None
    for minutes, parsed in candidates:
        if minutes == 300:
            return parsed
    return min(candidates, key=lambda c: c[0])[1]


def _kimi_plan_type(level):
    """"LEVEL_ADVANCED" → "Advanced"."""
    if not level:
        return None
    text = str(level)
    if text.startswith("LEVEL_"):
        text = text[len("LEVEL_"):]
    return text.replace("_", " ").title() or None


def _kimi_load_access_token():
    creds = json.loads(_core._KIMI_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    token = creds.get("access_token") if isinstance(creds, dict) else None
    if not token:
        raise ValueError("no access_token in Kimi credentials")
    return token


def _kimi_fetch_usages(token):
    req = urllib.request.Request(
        f"{_KIMI_USAGE_BASE_URL}/usages",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=_KIMI_USAGE_TIMEOUT_SECS) as res:
        return json.loads(res.read().decode("utf-8"))


def _kimi_usage_from_response(data, now_epoch=None):
    """Normalize the /usages response, or None if weekly can't be derived."""
    if now_epoch is None:
        now_epoch = time.time()
    if not isinstance(data, dict):
        return None
    weekly = _kimi_usage_window_block(data.get("usage"), 10080)
    if weekly is None:
        return None
    return {
        "weekly": weekly,
        "session": _kimi_session_window(data.get("limits")),
        "plan_type": _kimi_plan_type(
            ((data.get("user") or {}).get("membership") or {}).get("level")
        ),
        "snapshot_ts": _core._usage_snapshot_iso(now_epoch),
        "fetched_at": _core._usage_snapshot_iso(now_epoch),
        "from_cache": False,
    }


def _read_kimi_usage(now_epoch=None):
    """Freshest Kimi usage snapshot, or the last persisted one. Shape mirrors
    _read_codex_usage: {weekly, session, plan_type, snapshot_ts, fetched_at,
    from_cache}, or None if Kimi usage has never been fetched successfully."""
    if now_epoch is None:
        now_epoch = time.time()
    try:
        token = _kimi_load_access_token()
        data = _core._kimi_fetch_usages(token)
        usage = _core._kimi_usage_from_response(data, now_epoch=now_epoch)
        if usage:
            return usage
    except Exception:
        pass
    cached = _core._latest_kimi_usage_from_snapshots(now_epoch=now_epoch)
    if cached:
        cached = dict(cached)
        cached["from_cache"] = True
        return cached
    return None


def _read_codex_usage(now_epoch=None):
    # An explicit timestamp asks for a deterministic historical view, which a
    # current account snapshot cannot provide. Live callers omit it and prefer
    # the complete multi-bucket app-server response.
    if now_epoch is None:
        now_epoch = time.time()
        try:
            response = _core._codex_app_server_request(
                "account/rateLimits/read", {}, timeout=5
            )
        except Exception:
            response = None
        usage = _core._codex_usage_from_account_rate_limits(
            response, now_epoch=now_epoch
        )
        if usage:
            return usage
    return _read_codex_usage_from_rollouts(now_epoch=now_epoch)


def _codex_nonnegative_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str) or not value.isdigit():
        return None
    try:
        value = int(value)
    except (ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def _codex_account_usage_from_response(response, now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        return None

    summary_raw = result.get("summary")
    summary_raw = summary_raw if isinstance(summary_raw, dict) else {}
    summary_fields = {
        "lifetimeTokens": "lifetime_tokens",
        "peakDailyTokens": "peak_daily_tokens",
        "longestRunningTurnSec": "longest_running_turn_sec",
        "currentStreakDays": "current_streak_days",
        "longestStreakDays": "longest_streak_days",
    }
    summary = {}
    for source_key, public_key in summary_fields.items():
        value = _codex_nonnegative_int(summary_raw.get(source_key))
        if value is not None:
            summary[public_key] = value

    daily_by_day = {}
    buckets = result.get("dailyUsageBuckets")
    for bucket in buckets if isinstance(buckets, list) else []:
        if not isinstance(bucket, dict):
            continue
        day = bucket.get("startDate")
        try:
            valid_day = datetime.strptime(str(day), "%Y-%m-%d").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        if valid_day != day:
            continue
        tokens = _codex_nonnegative_int(bucket.get("tokens"))
        if tokens is None:
            continue
        daily_by_day[day] = tokens

    if not summary and not daily_by_day:
        return None
    return {
        "source": "codex_app_server",
        "fetched_at": _core._usage_snapshot_iso(now_epoch),
        "summary": summary,
        "daily": [
            {"day": day, "tokens": daily_by_day[day]}
            for day in sorted(daily_by_day)
        ],
    }


def _read_codex_account_usage(now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    try:
        response = _core._codex_app_server_request("account/usage/read", {}, timeout=5)
    except Exception:
        return None
    return _core._codex_account_usage_from_response(response, now_epoch=now_epoch)


def _usage_snapshot_epoch(snapshot):
    if not isinstance(snapshot, dict):
        return None
    dt = _core._stats_parse_ts(snapshot.get("ts"))
    if dt is None:
        return None
    return dt.timestamp()


def _read_native_usage_snapshots_unlocked():
    try:
        with _core._USAGE_SNAPSHOTS_FILE.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    snapshots = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            snapshots.append(item)
    return snapshots


def _write_native_usage_snapshots_unlocked(snapshots):
    _core._USAGE_SNAPSHOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _core._USAGE_SNAPSHOTS_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for item in snapshots:
            f.write(json.dumps(item, separators=(",", ":")) + "\n")
    tmp.replace(_core._USAGE_SNAPSHOTS_FILE)


def _append_native_usage_snapshot(snapshot, now_epoch=None):
    if not isinstance(snapshot, dict):
        return False
    if now_epoch is None:
        now_epoch = time.time()
    cutoff_epoch = now_epoch - (_USAGE_SNAPSHOT_MAX_AGE_DAYS * 86400)
    with _usage_snapshots_lock:
        snapshots = []
        for item in _read_native_usage_snapshots_unlocked():
            ts_epoch = _usage_snapshot_epoch(item)
            if ts_epoch is None or ts_epoch >= cutoff_epoch:
                snapshots.append(item)
        snapshots.append(snapshot)
        if len(snapshots) > _USAGE_SNAPSHOT_MAX_LINES:
            snapshots = snapshots[-_USAGE_SNAPSHOT_MAX_LINES:]
        try:
            _write_native_usage_snapshots_unlocked(snapshots)
        except OSError:
            return False
    return True


def _usage_reset_detected_at(curr, now_epoch=None):
    if isinstance(curr, dict):
        ts = curr.get("ts")
        if ts:
            return ts
    return _core._usage_snapshot_iso(now_epoch)


def _usage_snapshot_block(snapshot, window):
    codex = (snapshot or {}).get("codex") or {}
    if window == "codex_five_hour":
        block = codex.get("session") if isinstance(codex, dict) else None
        return {
            "utilization": (block or {}).get("pct"),
            "resets_at": (block or {}).get("resets_at"),
        }
    if window == "codex_weekly":
        block = codex.get("weekly") if isinstance(codex, dict) else None
        return {
            "utilization": (block or {}).get("pct"),
            "resets_at": (block or {}).get("resets_at"),
        }
    kimi = (snapshot or {}).get("kimi") or {}
    if window == "kimi_five_hour":
        block = kimi.get("session") if isinstance(kimi, dict) else None
        return {
            "utilization": (block or {}).get("pct"),
            "resets_at": (block or {}).get("resets_at"),
        }
    if window == "kimi_weekly":
        block = kimi.get("weekly") if isinstance(kimi, dict) else None
        return {
            "utilization": (block or {}).get("pct"),
            "resets_at": (block or {}).get("resets_at"),
        }
    block = (snapshot or {}).get(window)
    return block if isinstance(block, dict) else {}


def _usage_reset_event(window, kind, prev, curr, now_epoch=None):
    prev_block = _usage_snapshot_block(prev, window)
    curr_block = _usage_snapshot_block(curr, window)
    return {
        "detected_at": _usage_reset_detected_at(curr, now_epoch=now_epoch),
        "window": window,
        "kind": kind,
        "prev_pct": prev_block.get("utilization"),
        "new_pct": curr_block.get("utilization"),
        "old_resets_at": prev_block.get("resets_at"),
        "new_resets_at": curr_block.get("resets_at"),
        "source": "auto",
    }


def _detect_usage_reset_events(prev, curr, now_epoch=None):
    if not isinstance(prev, dict) or not isinstance(curr, dict):
        return []
    if now_epoch is None:
        now_epoch = time.time()
    prev_epoch = _usage_snapshot_epoch(prev)
    prev_age = None if prev_epoch is None else max(0, now_epoch - prev_epoch)
    events = []
    for window in ("five_hour", "seven_day", "codex_five_hour", "codex_weekly", "kimi_five_hour", "kimi_weekly"):
        prev_block = _usage_snapshot_block(prev, window)
        curr_block = _usage_snapshot_block(curr, window)
        old_reset = _core._stats_parse_ts(prev_block.get("resets_at"))
        new_reset = _core._stats_parse_ts(curr_block.get("resets_at"))
        if old_reset and new_reset:
            delta = abs((new_reset - old_reset).total_seconds())
            if delta > _RESET_DETECT_JITTER_SECS:
                events.append(_usage_reset_event(window, "scheduled", prev, curr, now_epoch=now_epoch))
        prev_pct = prev_block.get("utilization")
        new_pct = curr_block.get("utilization")
        if not isinstance(prev_pct, (int, float)) or not isinstance(new_pct, (int, float)):
            continue
        if prev_age is None or prev_age > _RESET_DETECT_MAX_PREV_AGE_SECS:
            continue
        if not old_reset:
            continue
        early = now_epoch < (old_reset.timestamp() - _RESET_DETECT_JITTER_SECS)
        if prev_pct >= 15 and (prev_pct - new_pct) >= 15 and new_pct < 5 and early:
            events.append(_usage_reset_event(window, "unscheduled", prev, curr, now_epoch=now_epoch))
    return events


def _read_usage_reset_events_unlocked():
    try:
        lines = _core._RESET_EVENTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            events.append(_usage_reset_event_with_id(item))
    return events


def _usage_reset_event_id(event):
    explicit = str((event or {}).get("id") or "").strip()
    if explicit:
        return explicit
    stable = {
        "detected_at": (event or {}).get("detected_at"),
        "window": (event or {}).get("window"),
        "kind": (event or {}).get("kind"),
        "old_resets_at": (event or {}).get("old_resets_at"),
        "new_resets_at": (event or {}).get("new_resets_at"),
        "source": (event or {}).get("source"),
    }
    digest = hashlib.sha1(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    return f"reset-{digest}"


def _usage_reset_event_with_id(event):
    out = dict(event or {})
    out["id"] = _usage_reset_event_id(out)
    return out


def _write_usage_reset_events_unlocked(events):
    _core._RESET_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _core._RESET_EVENTS_FILE.with_suffix(".tmp")
    body = "".join(json.dumps(_usage_reset_event_with_id(event), separators=(",", ":")) + "\n" for event in events)
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(_core._RESET_EVENTS_FILE)


def _rebuild_week_start_override_from_events(events):
    candidates = [
        _usage_reset_event_with_id(event)
        for event in (events or [])
        if event.get("window") == "seven_day" and event.get("kind") in ("unscheduled", "manual")
    ]
    candidates.sort(
        key=lambda event: (_core._stats_parse_ts(event.get("detected_at")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp()
    )
    if candidates:
        return _refresh_week_start_override_from_event(candidates[-1])
    try:
        _core._WEEK_START_OVERRIDE_FILE.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _write_week_start_override(week_start, resets_at_iso):
    if week_start is None or not resets_at_iso:
        return False
    try:
        _core._WEEK_START_OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "week_start": week_start.isoformat(),
            "applies_to_resets_week": _core._usage_resets_week_key(resets_at_iso),
            "set_at": time.time(),
        }
        tmp = _core._WEEK_START_OVERRIDE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
        tmp.replace(_core._WEEK_START_OVERRIDE_FILE)
        return True
    except OSError:
        return False


def _refresh_week_start_override_from_event(event):
    if not isinstance(event, dict):
        return False
    if event.get("window") != "seven_day" or event.get("kind") not in ("unscheduled", "manual"):
        return False
    detected = _core._stats_parse_ts(event.get("detected_at"))
    if detected is None:
        return False
    resets_at = event.get("new_resets_at") or event.get("old_resets_at")
    if not resets_at:
        live = _core._live_weekly_usage()
        resets_at = (live or {}).get("weekly_resets_at")
    return _write_week_start_override(detected.astimezone(), resets_at)


def _append_usage_reset_event(event):
    if not isinstance(event, dict):
        return False
    try:
        _core._RESET_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, separators=(",", ":")) + "\n"
        with _reset_events_lock:
            with _core._RESET_EVENTS_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
        _refresh_week_start_override_from_event(event)
        return True
    except OSError:
        return False


def record_usage_reset_event(window, reset_at=None, source="user"):
    if window not in ("five_hour", "seven_day"):
        return {"ok": False, "error": "window must be five_hour or seven_day"}
    detected_at = reset_at or _core._usage_snapshot_iso()
    event = {
        "id": f"reset-{uuid.uuid4().hex}",
        "detected_at": detected_at,
        "window": window,
        "kind": "manual",
        "prev_pct": None,
        "new_pct": None,
        "old_resets_at": None,
        "new_resets_at": None,
        "source": source or "user",
    }
    ok = _core._append_usage_reset_event(event)
    return {"ok": ok, "event": event}


def update_usage_reset_event(event_id, reset_at=None, window=None):
    event_id = str(event_id or "").strip()
    if not event_id:
        return {"ok": False, "error": "event id is required"}
    if window is not None and window not in ("five_hour", "seven_day"):
        return {"ok": False, "error": "window must be five_hour or seven_day"}
    if reset_at is not None:
        parsed = _core._stats_parse_ts(reset_at)
        if parsed is None:
            return {"ok": False, "error": "reset_at must be an ISO timestamp"}
        reset_at = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        with _reset_events_lock:
            events = _read_usage_reset_events_unlocked()
            updated = None
            for idx, event in enumerate(events):
                event = _usage_reset_event_with_id(event)
                if event.get("id") != event_id:
                    events[idx] = event
                    continue
                if event.get("kind") != "manual":
                    return {"ok": False, "error": "only manual reset events can be edited"}
                if reset_at is not None:
                    event["detected_at"] = reset_at
                if window is not None:
                    event["window"] = window
                event["source"] = event.get("source") or "user"
                events[idx] = event
                updated = event
            if updated is None:
                return {"ok": False, "error": "reset event not found"}
            _write_usage_reset_events_unlocked(events)
        _rebuild_week_start_override_from_events(events)
        return {"ok": True, "event": updated}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def delete_usage_reset_event(event_id):
    event_id = str(event_id or "").strip()
    if not event_id:
        return {"ok": False, "error": "event id is required"}
    try:
        with _reset_events_lock:
            events = _read_usage_reset_events_unlocked()
            remaining = []
            deleted = None
            for event in events:
                event = _usage_reset_event_with_id(event)
                if event.get("id") == event_id:
                    if event.get("kind") != "manual":
                        return {"ok": False, "error": "only manual reset events can be deleted"}
                    deleted = event
                    continue
                remaining.append(event)
            if deleted is None:
                return {"ok": False, "error": "reset event not found"}
            _write_usage_reset_events_unlocked(remaining)
        _rebuild_week_start_override_from_events(remaining)
        return {"ok": True, "event": deleted}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def usage_reset_events_payload(days=30, now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    try:
        days = float(days)
    except (TypeError, ValueError):
        days = 30
    if not math.isfinite(days) or days <= 0:
        days = 30
    cutoff = now_epoch - days * 86400
    with _reset_events_lock:
        events = _read_usage_reset_events_unlocked()
    out = []
    for event in events:
        dt = _core._stats_parse_ts(event.get("detected_at"))
        if dt is None or dt.timestamp() < cutoff:
            continue
        out.append(event)
    out.sort(key=lambda event: (_core._stats_parse_ts(event.get("detected_at")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp())
    return {"ok": True, "days": days, "events": out}


def _latest_native_usage_snapshot(now_epoch=None, max_age_secs=None):
    if now_epoch is None:
        now_epoch = time.time()
    newest = None
    newest_epoch = None
    with _usage_snapshots_lock:
        snapshots = _read_native_usage_snapshots_unlocked()
    for item in snapshots:
        ts_epoch = _usage_snapshot_epoch(item)
        if ts_epoch is None:
            continue
        if newest_epoch is None or ts_epoch > newest_epoch:
            newest = item
            newest_epoch = ts_epoch
    if newest is None:
        return None
    age_secs = max(0, now_epoch - newest_epoch)
    if max_age_secs is not None and age_secs > max_age_secs:
        return None
    out = dict(newest)
    out["_age_secs"] = age_secs
    return out


def _live_usage_from_snapshot(snapshot):
    sd = snapshot.get("seven_day") or {}
    fh = snapshot.get("five_hour") or {}
    sonnet = snapshot.get("seven_day_sonnet") or {}
    fable = snapshot.get("seven_day_fable") or {}
    return {
        "weekly_pct": sd.get("utilization"),
        "weekly_resets_at": sd.get("resets_at"),
        "session_pct": fh.get("utilization"),
        "session_resets_at": fh.get("resets_at"),
        "sonnet_pct": sonnet.get("utilization"),
        "fable_pct": fable.get("utilization"),
        "fable_resets_at": fable.get("resets_at"),
        "fetched_at": snapshot.get("ts"),
        "source": snapshot.get("source") or "native",
    }


def usage_snapshots_payload(hours=24, now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        hours = 24
    if not math.isfinite(hours) or hours <= 0:
        hours = 24
    cutoff_epoch = now_epoch - (hours * 3600)
    with _usage_snapshots_lock:
        snapshots = _read_native_usage_snapshots_unlocked()
    recent = []
    for item in snapshots:
        ts_epoch = _usage_snapshot_epoch(item)
        if ts_epoch is None or ts_epoch < cutoff_epoch:
            continue
        recent.append(item)
    recent.sort(key=lambda item: _usage_snapshot_epoch(item) or 0)
    return {"ok": True, "hours": hours, "snapshots": recent}


def _usage_provider_stale(usage, now_epoch):
    """True when a provider usage snapshot (codex/kimi shape) should render as
    stale: its own snapshot_ts/fetched_at is older than
    _USAGE_PROVIDER_STALE_SECS, or its weekly resets_at has already passed
    (the number describes a window that is over)."""
    if not isinstance(usage, dict):
        return False
    ts_epoch = _usage_snapshot_epoch({
        "ts": usage.get("snapshot_ts") or usage.get("fetched_at")
    })
    if ts_epoch is None or now_epoch - ts_epoch > _USAGE_PROVIDER_STALE_SECS:
        return True
    resets = _core._stats_parse_ts((usage.get("weekly") or {}).get("resets_at"))
    if resets is not None and resets.timestamp() < now_epoch:
        return True
    return False


def usage_current_payload(now_epoch=None):
    if now_epoch is None:
        now_epoch = time.time()
    snap = _latest_native_usage_snapshot(now_epoch=now_epoch)
    live = _core._live_weekly_usage(now_epoch=now_epoch)
    five_hour = (snap or {}).get("five_hour") or {}
    seven_day = (snap or {}).get("seven_day") or {}
    sonnet = (snap or {}).get("seven_day_sonnet") or {}
    fable = (snap or {}).get("seven_day_fable") or {}
    codex = _core._latest_codex_usage_from_snapshots(now_epoch=now_epoch)
    kimi = _core._latest_kimi_usage_from_snapshots(now_epoch=now_epoch)
    cal = _core._weekly_pct_calibration()
    reset_events = _core.usage_reset_events_payload(days=30, now_epoch=now_epoch).get("events", [])
    codex_stale = _usage_provider_stale(codex, now_epoch)
    kimi_stale = _usage_provider_stale(kimi, now_epoch)
    codex_pace = _core.codex_usage_pace_payload(codex=codex, now_epoch=now_epoch)
    kimi_pace = _core.kimi_usage_pace_payload(kimi=kimi, now_epoch=now_epoch)
    if isinstance(codex_pace, dict) and codex_stale:
        codex_pace["stale"] = True
    if isinstance(kimi_pace, dict) and kimi_stale:
        kimi_pace["stale"] = True
    return {
        "ok": True,
        "claude": {
            "five_hour": {
                "pct": five_hour.get("utilization"),
                "resets_at": five_hour.get("resets_at"),
            },
            "seven_day": {
                "pct": seven_day.get("utilization") if seven_day else (live or {}).get("weekly_pct"),
                "resets_at": seven_day.get("resets_at") if seven_day else (live or {}).get("weekly_resets_at"),
            },
            "seven_day_sonnet": {
                "pct": sonnet.get("utilization"),
                "resets_at": sonnet.get("resets_at"),
            },
            "seven_day_fable": {
                "pct": fable.get("utilization"),
                "resets_at": fable.get("resets_at"),
            },
            "pace": _core.usage_pace_payload(live=live, now_epoch=now_epoch),
        },
        "codex": {
            "session": (codex or {}).get("session"),
            "weekly": (codex or {}).get("weekly"),
            "pace": codex_pace,
            "plan_type": (codex or {}).get("plan_type"),
            "snapshot_ts": (codex or {}).get("snapshot_ts") or (codex or {}).get("fetched_at"),
            "from_cache": bool((codex or {}).get("from_cache")),
            "stale": codex_stale,
        },
        "kimi": {
            "session": (kimi or {}).get("session"),
            "weekly": (kimi or {}).get("weekly"),
            "pace": kimi_pace,
            "plan_type": (kimi or {}).get("plan_type"),
            "snapshot_ts": (kimi or {}).get("snapshot_ts") or (kimi or {}).get("fetched_at"),
            "from_cache": bool((kimi or {}).get("from_cache")),
            "stale": kimi_stale,
        },
        "calibration": {
            "pct_per_token": (cal or {}).get("pct_per_token"),
            "calibrated_at": (cal or {}).get("calibrated_at"),
        },
        "last_reset_events": reset_events[-5:],
        "fetched_at": (snap or {}).get("ts") or _core._usage_snapshot_iso(now_epoch),
    }


def _poll_plan_usage_once():
    global _plan_usage_cache, _plan_usage_cache_time
    res = _fetch_plan_usage()
    codex = _core._read_codex_usage()
    kimi = _core._read_kimi_usage()
    if not isinstance(res, dict) or not res.get("ok"):
        if codex or kimi:
            snap = {"ts": _core._usage_snapshot_iso(), "source": "native", "codex": codex, "kimi": kimi}
            prev = _latest_native_usage_snapshot()
            _core._append_native_usage_snapshot(snap)
            for event in _core._detect_usage_reset_events(prev, snap):
                _core._append_usage_reset_event(event)
        return False
    snap = _core._native_usage_snapshot_from_plan_usage(res, codex=codex, kimi=kimi)
    if snap:
        prev = _latest_native_usage_snapshot()
        _core._append_native_usage_snapshot(snap)
        for event in _core._detect_usage_reset_events(prev, snap):
            _core._append_usage_reset_event(event)
    with _plan_usage_cache_lock:
        _plan_usage_cache = res
        _plan_usage_cache_time = time.time()
    return True


def _plan_usage_poller_loop():
    while True:
        try:
            _poll_plan_usage_once()
        except Exception:
            pass
        try:
            time.sleep(_PLAN_USAGE_POLL_SECS)
        except Exception:
            return


def _start_plan_usage_poller():
    global _plan_usage_poller_started
    with _plan_usage_poller_lock:
        if _plan_usage_poller_started:
            return False
        _plan_usage_poller_started = True
    threading.Thread(
        target=_plan_usage_poller_loop,
        daemon=True,
        name="ccc-plan-usage-poller",
    ).start()
    return True

