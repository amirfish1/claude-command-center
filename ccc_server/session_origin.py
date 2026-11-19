"""Bounded launch metadata for an opened session, independent of list windows."""
import json
import re
from pathlib import Path

_MAX_BYTES = 262144


def launch_metadata(session_id, markers, transcript_path=None, registry=None):
    sid = str(session_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", sid):
        raise ValueError("invalid session id")
    marker = (markers or {}).get(sid) or {}
    result = {"session_id": sid}
    for key in ("spawned_via", "parent_session_id"):
        value = (registry or {}).get(key)
        if isinstance(value, str) and value:
            result[key] = value
    for source, target in (("spawned_via", "spawned_via"), ("caller", "spawn_caller"),
                           ("kind", "spawned_kind"), ("parent_session_id", "parent_session_id")):
        if isinstance(marker.get(source), str) and marker[source]:
            result[target] = marker[source]
    if not transcript_path:
        return result
    try:
        with Path(transcript_path).open("rb") as stream:
            left = _MAX_BYTES
            for _ in range(64):
                line = stream.readline(left + 1)
                left -= len(line)
                if not line or left < 0:
                    break
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload") or {}
                if not isinstance(payload, dict):
                    payload = {}
                meta = payload if event.get("type") == "session_meta" else event
                if not result.get("session_cwd") and isinstance(meta.get("cwd"), str):
                    result["session_cwd"] = meta["cwd"][:4096]
                if event.get("type") == "session_meta":
                    source = payload.get("source")
                    subagent = source.get("subagent") if isinstance(source, dict) else None
                    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                    parent = payload.get("parent_thread_id") or (spawn.get("parent_thread_id") if isinstance(spawn, dict) else None)
                    if isinstance(parent, str) and parent != sid:
                        result.setdefault("parent_session_id", parent)
                    for key in ("originator", "source"):
                        if isinstance(payload.get(key), str):
                            result["launch_" + key] = payload[key][:80]
                message = payload if payload.get("role") == "user" else event.get("message")
                if event.get("type") != "user" and payload.get("role") != "user":
                    continue
                if not isinstance(message, dict):
                    continue
                content = message.get("content") or []
                if isinstance(content, str):
                    content = [{"text": content}]
                if not isinstance(content, list):
                    continue
                for block in content:
                    text = block.get("text") if isinstance(block, dict) else None
                    if not isinstance(text, str) or not text.strip():
                        continue
                    text = text.strip()
                    if text.startswith(("# AGENTS.md", "<environment_context>", "<INSTRUCTIONS>")):
                        continue
                    result["first_message"] = text[:200]
                    return result
    except OSError:
        pass
    return result
