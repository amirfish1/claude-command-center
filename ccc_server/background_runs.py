"""Background / harness runs: headless `claude -p` sessions run from OS temp dirs.

Eval harnesses and scripts launch `claude -p` with cwd=os.tmpdir(). Their
transcripts land in ``~/.claude/projects/<temp-dir-slug>`` where no CCC view
looks, so their tokens slipped past every dashboard. This module scans ONLY
those temp-dir project folders (never the whole projects tree), attributes each
transcript to a spawner via its spawn marker (``spawned_via`` / ``caller`` +
``kind``), and buckets markerless runs under ``unknown``.

Cost model: the directory listing is one scandir per temp project; token totals
reuse the per-file (mtime, size) cache behind the stats pipeline, so a warm poll
re-parses nothing. Totals only: session ids never leave this module.
"""

from __future__ import annotations

import os
import time

from ccc_server import core as _core

UNKNOWN = "unknown"
_TEMP_SLUG_PREFIXES = ("-private-var-folders-", "-var-folders-", "-private-tmp", "-tmp")
_HOUR = 3600
_DAY = 86400
_CACHE = {"ts": 0.0, "payload": None}
_CACHE_TTL = 15


def is_temp_project_slug(name):
    """True for a ~/.claude/projects folder whose cwd was an OS temp dir."""
    return str(name or "").startswith(_TEMP_SLUG_PREFIXES)


def _temp_transcripts(projects_root):
    """Yield (sid, path, mtime) for every transcript under temp-dir projects."""
    try:
        dirs = [e for e in os.scandir(projects_root) if is_temp_project_slug(e.name)]
    except OSError:
        return
    for d in dirs:
        try:
            files = list(os.scandir(d.path))
        except OSError:
            continue
        for f in files:
            if not f.name.endswith(".jsonl"):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            yield f.name[: -len(".jsonl")], f.path, st.st_mtime


def _estimate_cost(model, tin, tcache, tout):
    """List-price estimate; cache tokens priced at the cache-read rate, so a
    lower bound (the per-file aggregate does not split cache writes)."""
    w = _core._throughput_usage_weights(model or "", engine="claude")
    return (tin * w["rate_in"] + tcache * w["rate_cache_read"] + tout * w["rate_out"]) / 1_000_000


def _tokens(path):
    agg = _core._stats_get_file_agg(path) or {}
    total = {"in": 0, "cache": 0, "out": 0, "messages": 0, "cost": 0.0}
    models = {}
    for day in (agg.get("by_date") or {}).values():
        for m, n in (day.get("models") or {}).items():
            models[m] = models.get(m, 0) + n
    for day in (agg.get("by_date") or {}).values():
        total["in"] += day.get("in_tokens", 0)
        total["cache"] += day.get("cache_tokens", 0)
        total["out"] += day.get("out_tokens", 0)
        total["messages"] += day.get("messages", 0)
    top = max(models, key=models.get) if models else ""
    total["cost"] = _estimate_cost(top, total["in"], total["cache"], total["out"])
    return total


def _spawner_key(marker):
    marker = marker or {}
    via = marker.get("spawned_via") or marker.get("caller") or UNKNOWN
    return via, marker.get("kind") or ""


_ALERTED = set()


def _log_new_alerts(alerts):
    """One activity-log line when a spawner first crosses the threshold (no
    per-run notification; the sidebar pill carries the live signal)."""
    now_keys = {(a["spawner"], a["kind"]) for a in alerts}
    for a in alerts:
        if (a["spawner"], a["kind"]) not in _ALERTED:
            try:
                _core._log_activity("BACKGROUND_RUNS", "ALERT",
                                    f"{a['spawner']}/{a['kind']} {a['runs_last_hour']} runs in the last hour")
            except Exception:
                pass
    _ALERTED.intersection_update(now_keys)
    _ALERTED.update(now_keys)


def cpu_pressure():
    """Cheap host CPU pressure: load per core plus Linux PSI when available.

    Returns {} when nothing is readable. ``level`` is ok/busy/saturated from
    load-per-core (>=1 busy, >=2 saturated); PSI avg10 is reported verbatim.
    """
    out = {}
    try:
        load = os.getloadavg()
        cores = os.cpu_count() or 1
        per_core = load[0] / cores
        out.update(load1=round(load[0], 2), load5=round(load[1], 2), load15=round(load[2], 2),
                   cores=cores, load_per_core=round(per_core, 2),
                   level="saturated" if per_core >= 2 else "busy" if per_core >= 1 else "ok")
    except (OSError, AttributeError):
        return out
    try:
        with open("/proc/pressure/cpu") as fh:
            for line in fh:
                parts = line.split()
                if parts and parts[0] == "some":
                    vals = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
                    out["psi_some_avg10"] = float(vals.get("avg10", 0))
                    out["psi_some_avg60"] = float(vals.get("avg60", 0))
    except (OSError, ValueError):
        pass
    return out


def memory_pressure():
    """Cheap host memory pressure from /proc/meminfo plus memory PSI (Linux).

    Returns {} when /proc is unavailable (macOS). ``level`` is ok/busy/saturated
    from used% (>=80 busy, >=90 saturated) or PSI some avg10 (>=10 / >=25).
    """
    out = {}
    try:
        info = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, _, rest = line.partition(":")
                if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                    info[k] = int(rest.split()[0])
        total, avail = info["MemTotal"], info["MemAvailable"]
        used_pct = round(100.0 * (total - avail) / total, 1) if total else 0.0
        out.update(total_mb=total // 1024, available_mb=avail // 1024, used_pct=used_pct)
        if info.get("SwapTotal"):
            out["swap_used_pct"] = round(100.0 * (info["SwapTotal"] - info.get("SwapFree", 0)) / info["SwapTotal"], 1)
    except (OSError, ValueError, KeyError, IndexError):
        return {}
    psi = 0.0
    try:
        with open("/proc/pressure/memory") as fh:
            for line in fh:
                parts = line.split()
                if parts and parts[0] == "some":
                    vals = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
                    psi = float(vals.get("avg10", 0))
                    out["psi_some_avg10"] = psi
    except (OSError, ValueError):
        pass
    out["level"] = ("saturated" if used_pct >= 90 or psi >= 25
                    else "busy" if used_pct >= 80 or psi >= 10 else "ok")
    return out


def _with_pressure(payload):
    return dict(payload, cpu=cpu_pressure(), memory=memory_pressure())


def background_runs_payload(now=None, projects_root=None, markers=None, use_cache=True):
    now = time.time() if now is None else now
    if use_cache and _CACHE["payload"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return _with_pressure(_CACHE["payload"])
    root = projects_root or _core.PROJECTS_ROOT
    markers = _core._load_spawn_markers() if markers is None else markers
    groups = {}
    for sid, path, mtime in _temp_transcripts(root):
        via, kind = _spawner_key(markers.get(sid))
        g = groups.setdefault((via, kind), {
            "spawner": via, "kind": kind, "runs": 0, "runs_last_hour": 0,
            "runs_last_day": 0, "in_tokens": 0, "cache_tokens": 0,
            "out_tokens": 0, "tokens_last_day": 0, "cost_usd": 0.0,
            "cost_usd_last_day": 0.0, "last_run_ts": 0.0,
        })
        t = _tokens(path)
        age = now - mtime
        g["runs"] += 1
        g["in_tokens"] += t["in"]
        g["cache_tokens"] += t["cache"]
        g["out_tokens"] += t["out"]
        g["cost_usd"] += t["cost"]
        if age <= _HOUR:
            g["runs_last_hour"] += 1
        if age <= _DAY:
            g["runs_last_day"] += 1
            g["tokens_last_day"] += t["in"] + t["cache"] + t["out"]
            g["cost_usd_last_day"] += t["cost"]
        g["last_run_ts"] = max(g["last_run_ts"], mtime)
    for g in groups.values():
        g["cost_usd"] = round(g["cost_usd"], 4)
        g["cost_usd_last_day"] = round(g["cost_usd_last_day"], 4)
    rows = sorted(groups.values(), key=lambda g: (-g["runs_last_hour"], -g["tokens_last_day"]))
    payload = {
        "ok": True,
        "groups": rows,
        "totals": {
            "runs": sum(g["runs"] for g in rows),
            "runs_last_hour": sum(g["runs_last_hour"] for g in rows),
            "runs_last_day": sum(g["runs_last_day"] for g in rows),
            "tokens_last_day": sum(g["tokens_last_day"] for g in rows),
            "cost_usd_last_day": round(sum(g["cost_usd_last_day"] for g in rows), 4),
        },
        "alerts": [
            {"spawner": g["spawner"], "kind": g["kind"], "runs_last_hour": g["runs_last_hour"]}
            for g in rows if g["runs_last_hour"] >= _core.BACKGROUND_RUNS_HOURLY_ALERT
        ],
    }
    _log_new_alerts(payload["alerts"])
    if use_cache:
        _CACHE.update(ts=now, payload=payload)
    return _with_pressure(payload)
