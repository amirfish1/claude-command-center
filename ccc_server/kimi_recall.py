"""Export Kimi Code conversations as Total Recall knowledge documents.

This module deliberately uses Total Recall's supported document-ingestion path.
It never synthesizes Claude transcripts and never writes Total Recall's database.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


_STATE_FILE = ".kimi-recall-state.json"
_LAUNCHD_LABEL = "com.github.claude-command-center.kimi-recall-bridge"
_TOTAL_RECALL_CONNECT_URL = "http://127.0.0.1:24824/api/brain/connect"


@dataclass
class SyncResult:
    exported: list[str]
    skipped: list[str]
    errors: list[str]


@dataclass
class ConnectionResult:
    ok: bool
    error: str = ""
    name: str = ""


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text") or "").strip()
            for item in value
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    return ""


def _read_jsonl(path):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                if not line.endswith("\n"):
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _read_state(session_dir):
    try:
        with (Path(session_dir) / "state.json").open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _session_messages(wire_path):
    model = ""
    messages = []
    for event in _read_jsonl(wire_path):
        event_type = event.get("type")
        if event_type == "config.update":
            model = str(event.get("modelAlias") or model).strip()
            continue
        if event_type == "context.append_message":
            message = event.get("message") or {}
            origin = message.get("origin") or {}
            if message.get("role") != "user" or origin.get("kind") not in (None, "user"):
                continue
            text = _text(message.get("content"))
            if text:
                messages.append(("User", text))
            continue
        if event_type != "context.append_loop_event":
            continue
        loop = event.get("event") or {}
        if loop.get("type") != "content.part":
            continue
        part = loop.get("part") or {}
        if part.get("type") != "text":
            continue
        text = _text(part.get("text"))
        if text:
            messages.append(("Assistant", text))
    return model, messages


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    binary = isinstance(text, bytes)
    with tempfile.NamedTemporaryFile(
        "wb" if binary else "w",
        encoding=None if binary else "utf-8",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(text)
        temp_name = handle.name
    os.replace(temp_name, path)


def _read_export_state(output_dir):
    try:
        with (Path(output_dir) / _STATE_FILE).open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return {}
    sessions = value.get("sessions") if isinstance(value, dict) else None
    return sessions if isinstance(sessions, dict) else {}


def _write_export_state(output_dir, sessions):
    _atomic_write(
        Path(output_dir) / _STATE_FILE,
        json.dumps({"version": 1, "sessions": sessions}, indent=2, sort_keys=True) + "\n",
    )


def _file_signature(path):
    stat = Path(path).stat()
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def _brief_filename(session_id):
    """Return a stable filename that cannot escape the configured output root."""
    safe = str(session_id or "").replace("/", "_").replace("\\", "_").replace("..", "_")
    safe = "".join(char if char.isalnum() or char in "_-" else "_" for char in safe).strip("_")
    return f"{safe or 'kimi-session'}.md"


def launchd_plist(script_path, output_dir, kimi_home, interval_seconds=300):
    """Return the opt-in macOS job definition for periodic bridge sync."""
    return {
        "Label": _LAUNCHD_LABEL,
        "ProgramArguments": [
            sys.executable,
            str(script_path),
            "sync",
            "--output-dir",
            str(output_dir),
            "--kimi-home",
            str(kimi_home),
        ],
        "StartInterval": int(interval_seconds),
        "RunAtLoad": True,
        "ProcessType": "Background",
        "Nice": 19,
    }


def install_launchd(script_path, output_dir, kimi_home, destination=None, interval_seconds=300):
    """Write the opt-in bridge LaunchAgent."""
    if destination is None:
        destination = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
    destination = Path(destination).expanduser()
    _atomic_write(
        destination,
        plistlib.dumps(launchd_plist(script_path, output_dir, kimi_home, interval_seconds)),
    )
    return destination


def load_launchd(destination, user_id=None):
    """Load an already-written bridge LaunchAgent into the current GUI domain."""
    user_id = os.getuid() if user_id is None else int(user_id)
    domain = f"gui/{user_id}"
    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{_LAUNCHD_LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return subprocess.run(
        ["launchctl", "bootstrap", domain, str(Path(destination))],
        capture_output=True,
        text=True,
        check=False,
    )


def connect_total_recall(output_dir, endpoint=None):
    """Connect the folder through Total Recall's dashboard-native API."""
    path = Path(output_dir).expanduser()
    endpoint = endpoint or os.environ.get("CCC_TOTAL_RECALL_CONNECT_URL") or _TOTAL_RECALL_CONNECT_URL
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"path": str(path)}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-TR-Request": "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return ConnectionResult(ok=False, error=str(exc))
    if not isinstance(body, dict) or not body.get("ok"):
        return ConnectionResult(ok=False, error=str((body or {}).get("error") or "Total Recall rejected the folder"))
    return ConnectionResult(ok=True, name=str(body.get("name") or ""))


def connect_kimi_knowledge(output_dir, kimi_home=None, endpoint=None):
    """Sync first, then connect only a complete knowledge export."""
    result = sync_kimi_knowledge(output_dir, kimi_home=kimi_home)
    if result.errors:
        return result, None
    return result, connect_total_recall(output_dir, endpoint=endpoint)


def _render_brief(session_id, session_dir, work_dir, state, model, messages):
    title = str(state.get("title") or session_id).strip()
    lines = [
        f"# Kimi Code session: {title}",
        "",
        f"- Engine: Kimi Code",
        f"- Session ID: {session_id}",
        f"- Project: {work_dir or '(unknown)'}",
        f"- Model: {model or '(unknown)'}",
        f"- Started: {state.get('createdAt') or '(unknown)'}",
        f"- Updated: {state.get('updatedAt') or '(unknown)'}",
        f"- Source: {Path(session_dir) / 'agents' / 'main' / 'wire.jsonl'}",
        "",
        "## Conversation",
        "",
    ]
    for role, text in messages:
        lines.extend((f"### {role}", "", text, ""))
    if not messages:
        lines.extend(("No visible user or assistant text has been recorded yet.", ""))
    return "\n".join(lines)


def sync_kimi_knowledge(output_dir, kimi_home=None):
    """Export each discovered Kimi session as a Markdown knowledge document."""
    home = Path(kimi_home).expanduser() if kimi_home else Path.home() / ".kimi-code"
    output_dir = Path(output_dir).expanduser()
    exported = []
    skipped = []
    errors = []
    state = _read_export_state(output_dir)
    for index in _read_jsonl(home / "session_index.jsonl"):
        session_id = str(index.get("sessionId") or "").strip()
        session_dir = str(index.get("sessionDir") or "").strip()
        if not session_id or not session_dir:
            continue
        wire_path = Path(session_dir) / "agents" / "main" / "wire.jsonl"
        if not wire_path.is_file():
            continue
        try:
            signature = {
                "wire": _file_signature(wire_path),
                "state": _file_signature(Path(session_dir) / "state.json"),
                "work_dir": str(index.get("workDir") or "").strip(),
                "session_dir": str(Path(session_dir)),
            }
            target = output_dir / _brief_filename(session_id)
            if state.get(session_id) == signature and target.is_file():
                skipped.append(session_id)
                continue
            model, messages = _session_messages(wire_path)
            brief = _render_brief(
                session_id,
                session_dir,
                str(index.get("workDir") or "").strip(),
                _read_state(session_dir),
                model,
                messages,
            )
            _atomic_write(target, brief)
            state[session_id] = signature
            exported.append(session_id)
        except OSError as exc:
            errors.append(f"{session_id}: {exc}")
    _write_export_state(output_dir, state)
    return SyncResult(exported=sorted(exported), skipped=sorted(skipped), errors=errors)
