#!/usr/bin/env python3
"""SessionStart hook — records who launched this session.

A session started by a script (`node grade-findings.ts` -> `claude -p`) leaves
no trace of its caller in the transcript, and the process tree that could say
who it was is gone by the time anyone looks. At startup the tree still exists,
so this writes one small marker, ~/.claude/command-center/spawn-markers/<sid>.json,
with:

  caller              human label: the launching script/app ("grade-findings.ts",
                      "Terminal", "Cursor"), never a bare "Terminal" guess
  parent_session_id   the launching Claude session, when the launcher was one

The parent id comes from CLAUDE_CODE_SESSION_ID in the claude process's own
environment (inherited from the Bash tool that ran the script). CCC_PARENT_SESSION_ID
and CCC_CALLER_NAME override it for scripts that want to say so explicitly.

The marker is written once (first start wins; resume/compact don't rewrite it)
and carries no lane/spawned_via, so it never moves a session between lanes.
Hooks must exit fast and never prompt: everything here is best effort.
"""

import json
import os
import re
import subprocess
import sys

MARKERS_DIR = os.path.expanduser("~/.claude/command-center/spawn-markers")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{7,127}$")
ENV_SID_RE = re.compile(r"(?:^|\s)CLAUDE_CODE_SESSION_ID=([A-Za-z0-9][A-Za-z0-9_.-]{7,127})(?=\s|$)")
ENV_PARENT_RE = re.compile(r"(?:^|\s)CCC_PARENT_SESSION_ID=([A-Za-z0-9][A-Za-z0-9_.:-]{7,166})(?=\s|$)")
ENV_CALLER_RE = re.compile(r"(?:^|\s)CCC_CALLER_NAME=(\S{1,80})(?=\s|$)")

# Wrappers that say nothing about who the caller is.
SKIP_EXES = {
    "sh", "bash", "zsh", "fish", "dash", "ksh", "login", "env", "sudo", "su",
    "npx", "npm", "pnpm", "yarn", "bunx", "script", "tmux", "screen", "xargs",
    "timeout", "nohup", "caffeinate", "time",
}
INTERPRETERS = {"node", "bun", "deno", "tsx", "ts-node", "python", "python3", "ruby", "perl"}
SCRIPT_EXT = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".py", ".sh", ".rb")
APP_RE = re.compile(r"/([^/]+)\.app/")


def _ps_table():
    out = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,command="],
        capture_output=True, text=True, timeout=3,
    ).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def _exe(cmd):
    first = cmd.split(None, 1)[0] if cmd.strip() else ""
    return os.path.basename(first)


def _is_claude(cmd):
    first = cmd.split(None, 1)[0] if cmd.strip() else ""
    return os.path.basename(first) == "claude" or "/claude/versions/" in first


def _label(cmd):
    """Short human label for one ancestor command line, or '' to skip it."""
    exe = _exe(cmd)
    if exe in SKIP_EXES or exe.startswith("-"):
        return ""
    if exe.lower() in INTERPRETERS or exe.lower().startswith("python"):
        # Checked before the .app match: Homebrew/framework Python runs from
        # inside a Python.app bundle, which would otherwise label as "Python".
        for tok in cmd.split()[1:]:
            if tok.lower().endswith(SCRIPT_EXT):
                return os.path.basename(tok)
        return exe[:60]
    app = APP_RE.search(cmd.split(None, 1)[0])
    if app:
        return app.group(1)
    return exe[:60]


def _claude_env(pid):
    try:
        return subprocess.run(
            ["/bin/ps", "-Eww", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return ""


def _read_marker(marker_path):
    try:
        with open(marker_path) as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(marker_path, data):
    os.makedirs(MARKERS_DIR, exist_ok=True)
    tmp = marker_path + ".%d.tmp" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(data, f, sort_keys=True)
    os.replace(tmp, marker_path)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    sid = str(payload.get("session_id") or "").strip()
    if not SESSION_ID_RE.match(sid):
        return
    marker_path = os.path.join(MARKERS_DIR, sid + ".json")
    # A $0 spawn carries CCC_SESSION_RUNTIME in its env (set per-child by
    # CCC's free-runtime overlay). It rides the marker so the dashboard can
    # show the runtime, and prints as context so the session itself knows.
    runtime = str(os.environ.get("CCC_SESSION_RUNTIME") or "").strip().lower()
    existing = _read_marker(marker_path)

    caller = ""
    parent = ""
    # A runtime-only marker (a $0 spawn stamped before this hook ran) still
    # needs caller/parent detection; only skip when attribution already landed.
    if not (existing.get("caller") or existing.get("parent_session_id")):
        table = _ps_table()
        pid = os.getppid()
        claude_pid = None
        for _ in range(8):
            entry = table.get(pid)
            if not entry:
                break
            if _is_claude(entry[1]):
                claude_pid = pid
                break
            pid = entry[0]
        if claude_pid is not None:
            env = _claude_env(claude_pid)
            m = ENV_PARENT_RE.search(env) or ENV_SID_RE.search(env)
            if m and m.group(1) != sid:
                parent = m.group(1)
            m = ENV_CALLER_RE.search(env)
            if m:
                caller = m.group(1)
            if not caller:
                pid = table[claude_pid][0]
                for _ in range(10):
                    entry = table.get(pid)
                    if not entry or pid <= 1:
                        break
                    caller = _label(entry[1])
                    if caller:
                        break
                    pid = entry[0]

    # Merge, never clobber: CCC may have stamped runtime before the hook ran,
    # and an earlier start may already own caller/parent_session_id.
    data = dict(existing)
    if caller:
        data["caller"] = caller[:80]
    if parent:
        data["parent_session_id"] = parent
    if runtime:
        data["runtime"] = runtime
    if data and data != existing:
        _write_marker(marker_path, data)

    if runtime == "free":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    "This session runs on a free model ($0) through the CCC "
                    "free router. It does not use the user's paid plan."
                ),
            }
        }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
