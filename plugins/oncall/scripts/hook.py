#!/usr/bin/env python3
"""Portable bootstrap: find the current CCC checkout through PATH each time."""
import json
from pathlib import Path
import shutil
import sys

MISSING = 'Install CCC using https://github.com/amirfish1/claude-command-center#installation, then run ccc oncall setup.'


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ''
    executable = shutil.which('ccc')
    if not executable:
        if event == 'SessionStart':
            print(json.dumps({'systemMessage': MISSING}))
        return
    data = json.loads(sys.stdin.read(1024 * 1024))
    sid = data.get('session_id', '')
    if (not isinstance(sid, str) or not 1 <= len(sid) <= 100 or
            any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in sid)):
        return
    sys.path.insert(0, str(Path(executable).resolve().parent))
    from ccc_server.oncall.agent_hook import run
    run(event, data, executable)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt):
        pass  # missing/down/old CCC, bad payload or interrupted wait: normal CLI flow
