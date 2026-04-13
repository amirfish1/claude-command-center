#!/usr/bin/env python3
"""Temporary test hook — dumps stdin + env vars to a file."""
import json, os, sys, time

dump_dir = os.path.expanduser("~/.claude/log-viewer")
os.makedirs(dump_dir, exist_ok=True)
dump_file = os.path.join(dump_dir, "hook-test-dump.json")

# Capture stdin
try:
    stdin_data = sys.stdin.read()
    stdin_json = json.loads(stdin_data) if stdin_data.strip() else None
except:
    stdin_json = None
    stdin_data = ""

# Capture relevant env vars
env_vars = {k: v for k, v in os.environ.items()
            if any(x in k.upper() for x in ("CLAUDE", "SESSION", "TOOL", "HOOK", "PROJECT"))}

dump = {
    "timestamp": time.time(),
    "stdin_raw": stdin_data[:2000],
    "stdin_json": stdin_json,
    "env_vars": env_vars,
}

with open(dump_file, "w") as f:
    json.dump(dump, f, indent=2, default=str)
