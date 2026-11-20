# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Headless single-turn runner (`ccc run`) that follows CCC session defaults.

Scheduled jobs and automation scripts often need a bounded, non-interactive
model turn (tool-free JSON synthesis, read-only repo inspection, or strict
MCP-only fact checking) without hardcoding `claude -p` or a specific model.

This module resolves CCC's saved session defaults (`engine`, `models[engine]`,
`reasoning_effort`) from the running server's `GET /api/spawn-defaults` (with
fallback to `~/.claude/command-center/spawn-defaults.json`) and dispatches to
the active engine's native headless CLI while preserving tool, sandbox, MCP,
and JSON Schema contracts.

Stdlib-only by design.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from ccc_server.paths import SPAWN_DEFAULTS_FILE

PORT_FILE = Path.home() / ".claude" / "command-center" / "port.txt"

SUBSCRIPTION_STRIP_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDECODE",
    "CLAUDE_PROVIDER",
    "CLAUDE_CODE_SESSION_ID",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _discover_server_url(server_url=None):
    cand = (server_url or os.environ.get("CCC_SERVER") or "").strip()
    if not cand:
        try:
            cand = PORT_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            cand = ""
    if not cand:
        return ""
    if not cand.startswith(("http://", "https://")):
        cand = f"http://127.0.0.1:{cand}"
    return cand.rstrip("/")


def resolve_session_defaults(server_url=None, defaults_file=None):
    """Return CCC's saved session defaults: {engine, model, effort, source}."""
    data = None
    source = "fallback"
    base = _discover_server_url(server_url)
    if base:
        try:
            req = urllib.request.Request(
                f"{base}/api/spawn-defaults",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(parsed, dict) and parsed.get("engine"):
                data = parsed
                source = "api"
        except (OSError, ValueError, urllib.error.URLError):
            data = None

    if data is None:
        path = Path(defaults_file) if defaults_file else SPAWN_DEFAULTS_FILE
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = parsed
                source = "file"
        except (OSError, ValueError):
            data = {}

    engine = str(data.get("engine") or "claude").strip().lower()
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    model = str(models.get(engine) or "").strip()
    effort = str(data.get("reasoning_effort") or "").strip()
    return {
        "engine": engine,
        "model": model,
        "effort": effort,
        "source": source,
    }


def _resolve_binary(name):
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / ".local" / "bin" / name
    if local.is_file():
        return str(local)
    return name


def _toml_value(val):
    """Serialize a Python primitive/list/dict as a TOML value for `codex -c`."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return json.dumps(val)
    if isinstance(val, list):
        return "[" + ", ".join(_toml_value(item) for item in val) + "]"
    if isinstance(val, dict):
        pairs = []
        for k, v in val.items():
            key = str(k)
            if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                key = json.dumps(key)
            pairs.append(f"{key} = {_toml_value(v)}")
        return "{" + ", ".join(pairs) + "}"
    return json.dumps(str(val))


def _load_json_arg(raw):
    """Accept a dict, a JSON string, or a path to a JSON file."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    if text.startswith("@"):
        text = Path(text[1:]).read_text(encoding="utf-8")
    elif not text.startswith("{") and Path(text).is_file():
        text = Path(text).read_text(encoding="utf-8")
    return json.loads(text)


def _extract_json_object(text):
    raw = _ANSI_RE.sub("", text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S).strip()
    try:
        return json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            return json.loads(raw[start : end + 1])
        raise


def _run_process_group(argv, stdin_text, cwd, env, timeout_s, max_output_bytes):
    """Run argv in its own process group with hard wall-clock and output caps."""
    with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        if stdin_text:
            inp.write(stdin_text.encode("utf-8"))
            inp.seek(0)
        proc = subprocess.Popen(
            argv,
            stdin=inp,
            stdout=out,
            stderr=err,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
        deadline = time.monotonic() + max(1.0, float(timeout_s))
        timed_out = False
        capped = False
        try:
            while proc.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > max_output_bytes:
                    capped = True
                    break
                time.sleep(0.05)
            if timed_out or capped:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
            out.seek(0)
            err.seek(0)
            stdout = out.read(max_output_bytes).decode("utf-8", errors="replace")
            stderr = err.read(max_output_bytes).decode("utf-8", errors="replace")
            return proc.returncode, stdout, stderr, timed_out, capped
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()


def _build_env(subscription_only, base_env=None):
    env = dict(os.environ if base_env is None else base_env)
    env.pop("CLAUDECODE", None)
    if subscription_only:
        for key in SUBSCRIPTION_STRIP_ENV:
            env.pop(key, None)
    return env


def _error_envelope(engine, model, effort, message, *, subtype="error", session_id=None, raw=None):
    env = {
        "ok": False,
        "subtype": subtype,
        "is_error": True,
        "engine": engine,
        "model": model,
        "effort": effort,
        "session_id": session_id,
        "result": message,
        "structured_output": None,
        "total_cost_usd": 0.0,
        "usage": {},
        "modelUsage": {},
        "permission_denials": [],
        "error": message,
    }
    if isinstance(raw, dict):
        if raw.get("session_id") and not env["session_id"]:
            env["session_id"] = raw["session_id"]
        if isinstance(raw.get("usage"), dict):
            env["usage"] = raw["usage"]
        if isinstance(raw.get("modelUsage"), dict):
            env["modelUsage"] = raw["modelUsage"]
        if isinstance(raw.get("total_cost_usd"), (int, float)):
            env["total_cost_usd"] = float(raw["total_cost_usd"])
        if isinstance(raw.get("permission_denials"), list):
            env["permission_denials"] = raw["permission_denials"]
    return env


def _run_claude(
    prompt, *, cwd, env, model, effort, tools, allowed_tools, disallowed_tools,
    system_prompt, json_schema, mcp_config, strict_mcp_config, safe_mode,
    max_turns, max_budget_usd, timeout_s, max_output_bytes, session_id,
    no_session_persistence, name, binary=None, runner=_run_process_group,
):
    exe = binary or _resolve_binary("claude")
    argv = [exe, "-p", "--output-format", "json", "--setting-sources", "", "--permission-mode", "dontAsk"]
    if safe_mode:
        argv += ["--safe-mode", "--restricted"]
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]

    tools_mode = (tools or "none").strip()
    if tools_mode == "none":
        argv += ["--tools", ""]
        if not strict_mcp_config:
            argv += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    elif tools_mode == "readonly":
        argv += ["--tools", "Read,Grep,Glob"]
        if not allowed_tools:
            argv += ["--allowedTools", "Read,Grep,Glob"]
        if not disallowed_tools:
            argv += ["--disallowedTools", "Bash,Write,Edit,MultiEdit,NotebookEdit,Agent,Task,WebFetch,WebSearch"]
        if not strict_mcp_config:
            argv += ["--strict-mcp-config"]
    elif tools_mode == "full":
        argv += ["--dangerously-skip-permissions"]
    else:
        argv += ["--tools", tools_mode]

    if allowed_tools:
        argv += ["--allowedTools", *allowed_tools]
    if disallowed_tools:
        argv += ["--disallowedTools", *disallowed_tools]
    if strict_mcp_config:
        argv += ["--strict-mcp-config", "--disable-slash-commands"]
    if mcp_config:
        argv += ["--mcp-config", str(mcp_config)]
    if system_prompt:
        argv += ["--system-prompt", system_prompt]
    if json_schema is not None:
        argv += ["--json-schema", json.dumps(json_schema)]
    if max_turns:
        argv += ["--max-turns", str(int(max_turns))]
    if max_budget_usd:
        argv += ["--max-budget-usd", f"{float(max_budget_usd):.2f}"]
    if no_session_persistence:
        argv += ["--no-session-persistence"]
    elif session_id:
        argv += ["--session-id", session_id]
    if name and not no_session_persistence:
        argv += ["--name", name]

    rc, stdout, stderr, timed_out, capped = runner(argv, prompt, cwd, env, timeout_s, max_output_bytes)
    if timed_out:
        return _error_envelope("claude", model, effort, f"claude timed out after {timeout_s}s", subtype="timeout", session_id=session_id)
    if capped:
        return _error_envelope("claude", model, effort, f"claude exceeded output cap ({max_output_bytes} bytes)", subtype="output_cap", session_id=session_id)

    parsed = None
    try:
        parsed = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        parsed = None

    if rc != 0 or not isinstance(parsed, dict) or parsed.get("is_error") or parsed.get("subtype") not in (None, "success"):
        detail = ""
        if isinstance(parsed, dict):
            detail = str(parsed.get("result") or parsed.get("error") or parsed.get("subtype") or "").strip()
        if not detail:
            detail = (stderr.strip() or stdout.strip())[-1500:] or f"claude exited {rc}"
        return _error_envelope(
            "claude", model, effort, detail,
            subtype=(parsed.get("subtype") if isinstance(parsed, dict) and parsed.get("subtype") else "error"),
            session_id=(parsed.get("session_id") if isinstance(parsed, dict) else session_id),
            raw=parsed,
        )

    structured = parsed.get("structured_output")
    result_text = parsed.get("result")
    if structured is None and json_schema is not None and isinstance(result_text, str) and result_text.strip():
        try:
            structured = _extract_json_object(result_text)
        except ValueError:
            structured = None

    return {
        "ok": True,
        "subtype": parsed.get("subtype") or "success",
        "is_error": False,
        "engine": "claude",
        "model": model,
        "effort": effort,
        "session_id": parsed.get("session_id") or session_id,
        "result": result_text if isinstance(result_text, str) else (json.dumps(structured) if structured is not None else ""),
        "structured_output": structured,
        "total_cost_usd": float(parsed.get("total_cost_usd") or 0.0),
        "usage": parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {},
        "modelUsage": parsed.get("modelUsage") if isinstance(parsed.get("modelUsage"), dict) else {},
        "permission_denials": parsed.get("permission_denials") if isinstance(parsed.get("permission_denials"), list) else [],
        "stop_reason": parsed.get("stop_reason"),
        "num_turns": parsed.get("num_turns"),
    }


def _codex_mcp_flags(mcp_config):
    """Translate a Claude-style mcp.json ({mcpServers: {...}}) into `codex -c` flags."""
    if not mcp_config:
        return []
    cfg = _load_json_arg(mcp_config)
    if not isinstance(cfg, dict):
        return []
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    flags = []
    for srv_name, spec in servers.items():
        if not isinstance(spec, dict) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(srv_name)):
            continue
        if spec.get("command"):
            flags += ["-c", f"mcp_servers.{srv_name}.command={_toml_value(str(spec['command']))}"]
        if isinstance(spec.get("args"), list):
            flags += ["-c", f"mcp_servers.{srv_name}.args={_toml_value([str(a) for a in spec['args']])}"]
        if isinstance(spec.get("env"), dict) and spec["env"]:
            flags += ["-c", f"mcp_servers.{srv_name}.env={_toml_value({str(k): str(v) for k, v in spec['env'].items()})}"]
    return flags


def _run_codex(
    prompt, *, cwd, env, model, effort, tools, allowed_tools, disallowed_tools,
    system_prompt, json_schema, mcp_config, strict_mcp_config, safe_mode,
    max_turns, max_budget_usd, timeout_s, max_output_bytes, session_id,
    no_session_persistence, name, binary=None, runner=_run_process_group,
):
    exe = binary or _resolve_binary("codex")
    tools_mode = (tools or "none").strip()

    with tempfile.TemporaryDirectory(prefix="ccc-run-codex-") as tmpdir:
        tmp = Path(tmpdir)
        out_file = tmp / "last_message.txt"
        schema_file = tmp / "schema.json"

        # Tool-free runs execute inside an empty scratch dir so even read-only
        # sandbox cannot read workspace files.
        exec_cwd = str(tmp) if tools_mode == "none" else str(cwd)
        sandbox = "workspace-write" if tools_mode == "full" else "read-only"

        argv = [
            exe, "exec",
            "-s", sandbox,
            "-C", exec_cwd,
            "--skip-git-repo-check",
            "--json",
            "-o", str(out_file),
        ]
        if tools_mode in ("none", "readonly") or strict_mcp_config:
            argv += ["--ignore-user-config", "--ignore-rules"]
        if no_session_persistence:
            argv += ["--ephemeral"]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["-c", f"model_reasoning_effort={_toml_value(effort)}"]
        if json_schema is not None:
            schema_file.write_text(json.dumps(json_schema), encoding="utf-8")
            argv += ["--output-schema", str(schema_file)]
        if mcp_config:
            argv += _codex_mcp_flags(mcp_config)
        argv.append("-")

        parts = []
        if system_prompt:
            parts.append(system_prompt.strip())
        if tools_mode == "none" and not mcp_config:
            parts.append("IMPORTANT: Do not invoke any shell, file, or network tools. Answer directly from the prompt.")
        elif tools_mode == "none" and mcp_config:
            parts.append("IMPORTANT: Do not invoke shell or file tools. Use only the configured MCP tools.")
        parts.append(prompt)
        full_prompt = "\n\n".join(p for p in parts if p)

        rc, stdout, stderr, timed_out, capped = runner(argv, full_prompt, exec_cwd, env, timeout_s, max_output_bytes)
        if timed_out:
            return _error_envelope("codex", model, effort, f"codex timed out after {timeout_s}s", subtype="timeout")
        if capped:
            return _error_envelope("codex", model, effort, f"codex exceeded output cap ({max_output_bytes} bytes)", subtype="output_cap")

        thread_id = None
        usage = {}
        agent_messages = []
        errors = []
        disallowed_tool_used = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            ev_type = ev.get("type")
            if ev_type == "thread.started" and ev.get("thread_id"):
                thread_id = ev["thread_id"]
            elif ev_type == "turn.completed" and isinstance(ev.get("usage"), dict):
                usage = ev["usage"]
            elif ev_type in ("error", "turn.failed"):
                msg = ev.get("message") or (ev.get("error") or {}).get("message") or json.dumps(ev)
                errors.append(str(msg))
            elif ev_type == "item.completed" and isinstance(ev.get("item"), dict):
                item = ev["item"]
                itype = item.get("type")
                if itype == "agent_message" and isinstance(item.get("text"), str):
                    agent_messages.append(item["text"])
                elif tools_mode == "none" and itype in ("command_execution", "file_change"):
                    disallowed_tool_used = itype

        if disallowed_tool_used:
            return _error_envelope(
                "codex", model, effort,
                f"codex invoked disallowed {disallowed_tool_used} under tools=none",
                subtype="policy_violation", session_id=thread_id,
            )

        if rc != 0 or errors:
            detail = "; ".join(errors) if errors else (stderr.strip() or stdout.strip())[-1500:] or f"codex exited {rc}"
            return _error_envelope("codex", model, effort, detail, subtype="error", session_id=thread_id)

        result_text = ""
        if out_file.is_file():
            result_text = out_file.read_text(encoding="utf-8", errors="replace").strip()
        if not result_text and agent_messages:
            result_text = agent_messages[-1].strip()

        structured = None
        if json_schema is not None:
            try:
                structured = _extract_json_object(result_text)
            except ValueError as exc:
                return _error_envelope(
                    "codex", model, effort,
                    f"codex output did not contain valid JSON: {exc}",
                    subtype="invalid_json", session_id=thread_id,
                )

        model_usage = {}
        if model and usage:
            model_usage[model] = {
                "inputTokens": int(usage.get("input_tokens") or 0),
                "outputTokens": int(usage.get("output_tokens") or 0),
                "cacheReadInputTokens": int(usage.get("cached_input_tokens") or 0),
            }

        return {
            "ok": True,
            "subtype": "success",
            "is_error": False,
            "engine": "codex",
            "model": model,
            "effort": effort,
            "session_id": thread_id,
            "result": result_text,
            "structured_output": structured,
            "total_cost_usd": 0.0,
            "usage": usage,
            "modelUsage": model_usage,
            "permission_denials": [],
            "stop_reason": "end_turn",
        }


def _run_devin(
    prompt, *, cwd, env, model, effort, tools, allowed_tools, disallowed_tools,
    system_prompt, json_schema, mcp_config, strict_mcp_config, safe_mode,
    max_turns, max_budget_usd, timeout_s, max_output_bytes, session_id,
    no_session_persistence, name, binary=None, runner=_run_process_group,
):
    if mcp_config and strict_mcp_config:
        return _error_envelope("devin", model, effort, "devin does not support strict MCP-only isolation", subtype="unsupported")
    exe = binary or _resolve_binary("devin")
    tools_mode = (tools or "none").strip()

    with tempfile.TemporaryDirectory(prefix="ccc-run-devin-") as tmpdir:
        tmp = Path(tmpdir)
        argv = [exe, "-p", "--respect-workspace-trust", "false"]
        if model:
            argv += ["--model", model]
        if tools_mode == "none":
            deny_cfg = tmp / "deny-all.json"
            deny_cfg.write_text(json.dumps({"permissions": {"deny": [
                "read", "edit", "write", "grep", "glob", "exec",
                "webfetch", "websearch", "mcp__*",
            ]}}), encoding="utf-8")
            argv += ["--config", str(deny_cfg)]
        elif tools_mode == "readonly":
            ro_cfg = tmp / "readonly.json"
            ro_cfg.write_text(json.dumps({"permissions": {"deny": [
                "edit", "write", "exec", "webfetch", "websearch", "mcp__*",
            ]}}), encoding="utf-8")
            argv += ["--config", str(ro_cfg)]

        parts = []
        if system_prompt:
            parts.append(system_prompt.strip())
        parts.append(prompt)
        if json_schema is not None:
            parts.append(
                "Respond with a single JSON object matching this JSON Schema. "
                "Output only the JSON, no prose, no code fences:\n" + json.dumps(json_schema)
            )
        combined = "\n\n".join(p for p in parts if p)
        argv += ["--", combined]

        exec_cwd = str(tmp) if tools_mode == "none" else str(cwd)
        rc, stdout, stderr, timed_out, capped = runner(argv, "", exec_cwd, env, timeout_s, max_output_bytes)
        if timed_out:
            return _error_envelope("devin", model, effort, f"devin timed out after {timeout_s}s", subtype="timeout")
        if capped:
            return _error_envelope("devin", model, effort, f"devin exceeded output cap ({max_output_bytes} bytes)", subtype="output_cap")
        if rc != 0:
            detail = (stderr.strip() or stdout.strip())[-1500:] or f"devin exited {rc}"
            return _error_envelope("devin", model, effort, detail, subtype="error")

        cleaned = _ANSI_RE.sub("", stdout).strip()
        structured = None
        if json_schema is not None:
            try:
                structured = _extract_json_object(cleaned)
            except ValueError as exc:
                return _error_envelope("devin", model, effort, f"devin output did not contain valid JSON: {exc}", subtype="invalid_json")

        return {
            "ok": True,
            "subtype": "success",
            "is_error": False,
            "engine": "devin",
            "model": model,
            "effort": effort,
            "session_id": None,
            "result": cleaned,
            "structured_output": structured,
            "total_cost_usd": 0.0,
            "usage": {},
            "modelUsage": {},
            "permission_denials": [],
            "stop_reason": "end_turn",
        }


def run_headless(
    prompt,
    *,
    cwd=None,
    engine=None,
    model=None,
    effort=None,
    tools="none",
    allowed_tools=None,
    disallowed_tools=None,
    system_prompt=None,
    json_schema=None,
    mcp_config=None,
    strict_mcp_config=False,
    safe_mode=False,
    max_turns=None,
    max_budget_usd=None,
    timeout_s=300.0,
    max_output_bytes=4 * 1024 * 1024,
    session_id=None,
    no_session_persistence=False,
    name=None,
    subscription_only=False,
    server_url=None,
    defaults_file=None,
    binary=None,
    base_env=None,
    runner=_run_process_group,
):
    """Run one headless turn using CCC's saved session defaults (unless overridden)."""
    defaults = resolve_session_defaults(server_url=server_url, defaults_file=defaults_file)
    resolved_engine = (engine or defaults["engine"] or "claude").strip().lower()
    # Only inherit the saved model/effort when using the saved engine (or when
    # no explicit engine override was passed); if a caller overrides --engine
    # without --model, let that engine's own model default apply.
    if model:
        resolved_model = str(model).strip()
    elif not engine or resolved_engine == defaults["engine"]:
        resolved_model = defaults["model"]
    else:
        resolved_model = ""
    if effort is not None and str(effort).strip():
        resolved_effort = str(effort).strip()
    elif not engine or resolved_engine == defaults["engine"]:
        resolved_effort = defaults["effort"]
    else:
        resolved_effort = ""

    schema_obj = _load_json_arg(json_schema) if json_schema is not None else None
    env = _build_env(subscription_only, base_env=base_env)
    work_cwd = str(cwd or os.getcwd())

    kwargs = dict(
        cwd=work_cwd,
        env=env,
        model=resolved_model,
        effort=resolved_effort,
        tools=tools,
        allowed_tools=list(allowed_tools or []),
        disallowed_tools=list(disallowed_tools or []),
        system_prompt=system_prompt,
        json_schema=schema_obj,
        mcp_config=mcp_config,
        strict_mcp_config=bool(strict_mcp_config),
        safe_mode=bool(safe_mode),
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        timeout_s=timeout_s,
        max_output_bytes=max_output_bytes,
        session_id=session_id,
        no_session_persistence=bool(no_session_persistence),
        name=name,
        binary=binary,
        runner=runner,
    )

    if resolved_engine == "claude":
        return _run_claude(prompt, **kwargs)
    if resolved_engine == "codex":
        return _run_codex(prompt, **kwargs)
    if resolved_engine == "devin":
        return _run_devin(prompt, **kwargs)
    return _error_envelope(
        resolved_engine,
        resolved_model,
        resolved_effort,
        f"engine {resolved_engine!r} is not supported by headless `ccc run` (supported: claude, codex, devin)",
        subtype="unsupported",
    )
