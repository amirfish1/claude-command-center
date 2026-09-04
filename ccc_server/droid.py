# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Factory Droid engine adapter — model catalog and spawn stub."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ccc_server import core as _core

_DROID_HELP_TTL_S = 30.0
_DROID_HELP_TIMEOUT_S = 10.0
_DROID_HELP_CACHE = {"ts": 0.0, "text": ""}
_DROID_HELP_LOCK = threading.Lock()

_DROID_FACTORY_MODELS = (
    {"id": "claude-fable-5", "label": "Claude Fable 5", "multiplier": 4.0, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "high", "notes": "Mythos-class; 30-day data retention"},
    {"id": "claude-opus-5", "label": "Claude Opus 5", "multiplier": 2.0, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "high"},
    {"id": "claude-opus-5-fast", "label": "Claude Opus 5 Fast", "multiplier": 4.0, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "high"},
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8", "multiplier": 2.0, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "high"},
    {"id": "claude-opus-4-8-fast", "label": "Claude Opus 4.8 Fast", "multiplier": 4.0, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "high"},
    {"id": "claude-opus-4-7", "label": "Claude Opus 4.7", "multiplier": 2.0, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "high"},
    {"id": "claude-opus-4-6", "label": "Claude Opus 4.6", "multiplier": 2.0, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "max"), "default_reasoning_effort": "high"},
    {"id": "claude-opus-4-5-20251101", "label": "Claude Opus 4.5", "multiplier": 2.0, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high"), "default_reasoning_effort": "off"},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "multiplier": 0.8, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "high"},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "multiplier": 1.2, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "max"), "default_reasoning_effort": "high"},
    {"id": "claude-sonnet-4-5-20250929", "label": "Claude Sonnet 4.5", "multiplier": 1.2, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high"), "default_reasoning_effort": "off"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "multiplier": 0.4, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high"), "default_reasoning_effort": "off"},
    {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol", "multiplier": 1.6, "base_multiplier": 2.0, "promotion_until": "2026-11-22", "droid_core": False, "reasoning_efforts": ("none", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.6-sol-fast", "label": "GPT-5.6 Sol Fast", "multiplier": 3.2, "base_multiplier": 4.0, "promotion_until": "2026-11-22", "droid_core": False, "reasoning_efforts": ("none", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra", "multiplier": 0.8, "droid_core": False, "reasoning_efforts": ("none", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna", "multiplier": 0.08, "droid_core": False, "reasoning_efforts": ("none", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.5", "label": "GPT-5.5", "multiplier": 2.0, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.5-fast", "label": "GPT-5.5 Fast", "multiplier": 5.0, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.5-pro", "label": "GPT-5.5 Pro", "multiplier": 12.0, "droid_core": False, "reasoning_efforts": ("medium", "high", "xhigh"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.4", "label": "GPT-5.4", "multiplier": 1.0, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.4-fast", "label": "GPT-5.4 Fast", "multiplier": 2.0, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini", "multiplier": 0.3, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "high"},
    {"id": "gpt-5.4-mini-fast", "label": "GPT-5.4 Mini Fast", "multiplier": 0.6, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "high"},
    {"id": "gpt-5.3-codex", "label": "GPT-5.3-Codex", "multiplier": 0.7, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.3-codex-fast", "label": "GPT-5.3-Codex Fast", "multiplier": 1.4, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "medium"},
    {"id": "gpt-5.2", "label": "GPT-5.2", "multiplier": 0.7, "droid_core": False, "reasoning_efforts": ("off", "low", "medium", "high", "xhigh"), "default_reasoning_effort": "low"},
    {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro", "multiplier": 0.8, "droid_core": False, "reasoning_efforts": ("low", "medium", "high"), "default_reasoning_effort": "high"},
    {"id": "gemini-3.7-flash", "label": "Gemini 3.7 Flash", "multiplier": 0.3, "base_multiplier": 0.6, "promotion_until": "2027-01-01", "droid_core": False, "reasoning_efforts": ("minimal", "low", "medium", "high"), "default_reasoning_effort": "high"},
    {"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash", "multiplier": 0.6, "droid_core": False, "reasoning_efforts": ("minimal", "low", "medium", "high"), "default_reasoning_effort": "high"},
    {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash", "multiplier": 0.6, "droid_core": False, "reasoning_efforts": ("minimal", "low", "medium", "high"), "default_reasoning_effort": "high"},
    {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash", "multiplier": 0.2, "droid_core": False, "reasoning_efforts": ("minimal", "low", "medium", "high"), "default_reasoning_effort": "high"},
    {"id": "grok-4.6", "label": "Grok 4.6", "multiplier": 0.8, "droid_core": False, "reasoning_efforts": ("low", "medium", "high", "xhigh"), "default_reasoning_effort": "high"},
    {"id": "grok-4.5", "label": "Grok 4.5", "multiplier": 0.8, "droid_core": False, "reasoning_efforts": ("low", "medium", "high"), "default_reasoning_effort": "high"},
    {"id": "inkling", "label": "Inkling", "multiplier": 0.4, "droid_core": True, "reasoning_efforts": ("off", "minimal", "low", "medium", "high", "xhigh", "max"), "default_reasoning_effort": "high"},
    {"id": "glm-5.2", "label": "GLM-5.2", "multiplier": 0.56, "droid_core": True, "reasoning_efforts": ("off", "high", "max"), "default_reasoning_effort": "high"},
    {"id": "glm-5.2-fast", "label": "GLM-5.2 Fast", "multiplier": 0.84, "droid_core": True, "reasoning_efforts": ("off", "high", "max"), "default_reasoning_effort": "high"},
    {"id": "kimi-k3", "label": "Kimi K3", "multiplier": 1.2, "droid_core": True, "reasoning_efforts": ("off", "low", "high", "max"), "default_reasoning_effort": "high"},
    {"id": "kimi-k2.7-code", "label": "Kimi K2.7 Code", "multiplier": 0.38, "droid_core": True, "reasoning_efforts": ("off", "high"), "default_reasoning_effort": "high"},
    {"id": "kimi-k2.6", "label": "Kimi K2.6", "multiplier": 0.4, "droid_core": True, "reasoning_efforts": ("off", "high"), "default_reasoning_effort": "high"},
    {"id": "nemotron-3-ultra", "label": "Nemotron 3 Ultra", "multiplier": 0.24, "droid_core": True, "reasoning_efforts": ("off", "high"), "default_reasoning_effort": "high"},
    {"id": "deepseek-v4-flash-0731", "label": "DeepSeek V4 Flash 0731", "multiplier": 0.176, "droid_core": True, "reasoning_efforts": ("off", "low", "high", "max"), "default_reasoning_effort": "high"},
    {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro", "multiplier": 0.528, "droid_core": True, "reasoning_efforts": ("off", "low", "high", "max"), "default_reasoning_effort": "high"},
    {"id": "minimax-m3", "label": "MiniMax M3", "multiplier": 0.12, "droid_core": True, "reasoning_efforts": ("high",), "default_reasoning_effort": "high"},
    {"id": "minimax-m2.7", "label": "MiniMax M2.7", "multiplier": 0.12, "droid_core": True, "reasoning_efforts": ("high",), "default_reasoning_effort": "high", "deprecated": True},
    {"id": "minimax-m2.5", "label": "MiniMax M2.5", "multiplier": 0.12, "droid_core": True, "reasoning_efforts": ("low", "medium", "high"), "default_reasoning_effort": "high"},
    {"id": "kimi-k2.5", "label": "Kimi K2.5", "multiplier": 0.25, "droid_core": True, "reasoning_efforts": ("off", "high"), "default_reasoning_effort": "high", "deprecated": True},
    {"id": "glm-5.1", "label": "GLM-5.1", "multiplier": 0.55, "droid_core": True, "reasoning_efforts": ("off", "high"), "default_reasoning_effort": "high", "deprecated": True},
)


def _resolve_droid_bin():
    env_bin = os.environ.get("CCC_DROID_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return {"available": True, "bin": env_bin, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "droid_unavailable",
            "reason": f"CCC_DROID_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("droid")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    return {
        "available": False,
        "bin": None,
        "code": "droid_unavailable",
        "reason": "Droid CLI not found. Install from https://factory.ai or set CCC_DROID_BIN.",
    }


def _droid_exec_help_text():
    now = time.monotonic()
    with _DROID_HELP_LOCK:
        cached = _DROID_HELP_CACHE["text"]
        if cached and now - _DROID_HELP_CACHE["ts"] < _DROID_HELP_TTL_S:
            return cached
    resolved = _resolve_droid_bin()
    if not resolved["available"]:
        return ""
    try:
        proc = subprocess.run(
            [resolved["bin"], "exec", "--help"],
            capture_output=True,
            text=True,
            timeout=_DROID_HELP_TIMEOUT_S,
        )
        text = proc.stdout or "" if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        text = ""
    with _DROID_HELP_LOCK:
        _DROID_HELP_CACHE["ts"] = now
        _DROID_HELP_CACHE["text"] = text
    return text


def _droid_help_model_ids():
    text = _droid_exec_help_text()
    if not text:
        return {}, ""

    def _parse_section(start_marker, stop_markers):
        start = text.find(start_marker)
        if start == -1:
            return {}
        section = text[start:]
        for stop in stop_markers:
            stop_at = section.find(stop)
            if stop_at != -1:
                section = section[:stop_at]
        result = {}
        for line in section.splitlines():
            if not line.startswith("  ") or not line.strip():
                continue
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            mid, label = parts[0], _core._clean_spawn_default_model(parts[1])
            result[mid] = label
        return result

    available = _parse_section("Available Models:", ("Custom Models:", "Model details:"))
    custom = _parse_section("Custom Models:", ("Model details:",))
    available.update(custom)
    bin_info = _resolve_droid_bin()
    reason = "" if bin_info["available"] else bin_info.get("reason", "Droid CLI not found")
    return available, reason


def _droid_model_catalog_records():
    cli_models, unavail_reason = _droid_help_model_ids()
    now = datetime.now(tz=timezone.utc).isoformat()
    records = []

    for row in _DROID_FACTORY_MODELS:
        mid = row["id"]
        if cli_models:
            available = mid in cli_models
            reason = None if available else "Not in this Droid CLI's model list"
        else:
            available = False
            reason = unavail_reason or "Droid CLI not found or model list unavailable"
        cost_summary = f"{row['multiplier']}x base usage"
        entitlement = None
        entitlement_summary = None
        if row.get("droid_core"):
            entitlement = "droid_core"
            entitlement_summary = "Droid Core: free after standard usage is exhausted"
        if row.get("promotion_until"):
            entitlement_summary = (
                f"Promotional pricing through {row['promotion_until']}; "
                f"normal {row['base_multiplier']}x base usage"
            )
        records.append({
            "id": mid,
            "label": row["label"],
            "source": "factory-models-docs",
            "available": available,
            "availability_reason": reason,
            "droid_core": row.get("droid_core", False),
            "multiplier": row["multiplier"],
            "cost_tier": f"{row['multiplier']}x",
            "cost_summary": cost_summary,
            "entitlement": entitlement,
            "entitlement_summary": entitlement_summary,
            "reasoning_efforts": list(row.get("reasoning_efforts", ())),
            "default_reasoning_effort": row.get("default_reasoning_effort"),
            "promotion_until": row.get("promotion_until"),
            "base_multiplier": row.get("base_multiplier"),
            "deprecated": row.get("deprecated", False),
            "notes": row.get("notes"),
            "fetched_at": now,
        })

    for mid, label in cli_models.items():
        if mid.startswith("custom:") and not any(r["id"] == mid for r in records):
            records.append({
                "id": mid,
                "label": label or mid,
                "source": "droid-cli",
                "available": True,
                "custom": True,
                "droid_core": False,
                "cost_summary": "Custom / BYOK model",
                "reasoning_efforts": [],
            })
    if "auto" in cli_models and not any(r["id"] == "auto" for r in records):
        records.append({
            "id": "auto",
            "label": cli_models["auto"] or "Auto Model",
            "source": "droid-cli",
            "available": True,
            "droid_core": False,
            "cost_summary": "Factory auto-router",
            "reasoning_efforts": ["none"],
            "default_reasoning_effort": "none",
        })

    return records


def _droid_reasoning_efforts_for_model(model):
    for row in _DROID_FACTORY_MODELS:
        if row["id"] == model:
            return set(row.get("reasoning_efforts") or ())
    return set()


def spawn_session_droid(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None, reasoning_effort=None, env=None):
    """Spawn a headless Factory Droid CLI run and return tracking info.

    ``droid exec`` is confirmed as the headless entry point (this module
    already shells out to ``droid exec --help`` for the model catalog scrape
    above); the ``--auto high`` / ``--reasoning-effort`` flags below are this
    CLI's documented non-interactive flags as of the Droid CLI version this
    was written against, matched against the model's own advertised
    reasoning-effort ladder so an unsupported value is never passed. `--auto
    high` is required, not optional, for a headless spawn: stdin is
    DEVNULL, so any lower autonomy level that pauses for an approval prompt
    would hang the subprocess forever instead of failing loudly.

    ``env``, when given, is merged over the inherited process environment --
    used by the BYOK layer (ccc_server/byok.py) to hand Droid a provider API
    key for this one spawn without touching global state.
    """
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    resolved = _resolve_droid_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved.get("reason", "Droid unavailable"), "code": resolved.get("code", "droid_unavailable")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-droid-{session_name}-{timestamp}.log"
    model_to_use = _core._spawn_model_for_engine("droid", model) or os.environ.get("CCC_DROID_MODEL", "claude-opus-5")
    if model_to_use:
        _core._set_session_model(log_filename[:-4], model_to_use, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(spawn_cwd, session_name)
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}
    cmd = [resolved["bin"], "exec", "--auto", "high"]
    if model_to_use:
        cmd.extend(["--model", model_to_use])
    effort = (reasoning_effort or "").strip().lower()
    if effort and effort in _droid_reasoning_efforts_for_model(model_to_use):
        cmd.extend(["--reasoning-effort", effort])
    cmd.append(prompt)
    log_fh = open(log_path, "w")
    if worktree_path:
        _core._run_worktree_init_hook(worktree_path, ctx["repo_path"], session_name, log_fh)
    popen_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=spawn_cwd, start_new_session=True, env=popen_env,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "code": "droid_launch_failed", "via": "droid-spawn"}
    failure = _core._spawn_early_failure_payload(proc, log_path, log_fh, engine="droid", via="droid-spawn")
    if failure:
        return failure
    entry = {
        "pid": proc.pid, "name": session_name, "log": str(log_path),
        "prompt": prompt[:200], "started": timestamp, "proc": proc,
        "log_fh": log_fh, "fifo": None, "stdin_fd": None,
        "engine": "droid", "cwd": spawn_cwd, "repo_path": repo_for_logs,
        "model": model_to_use or "", "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid, name=session_name, log_path=log_path, cwd=spawn_cwd,
        spawned_at=timestamp, command_summary=prompt[:200],
        fifo=None, engine="droid", repo_path=repo_for_logs, model=model_to_use,
        parent_session_id=parent_session_id, reasoning_effort=effort or None,
    )
    resp = {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path), "via": "droid-spawn"}
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    return _core._finalize_spawn_response(resp, entry, ctx)
