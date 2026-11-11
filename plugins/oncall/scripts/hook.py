#!/usr/bin/env python3
"""Fast, fail-open adapter to the installed CCC hooks, with no model calls."""
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

MISSING = 'Install CCC with the instructions at https://github.com/amirfish1/claude-command-center#installation, then run ccc oncall setup.'


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ''
    executable = shutil.which('ccc')
    if not executable:
        if event == 'SessionStart':
            print(json.dumps({'systemMessage': MISSING}))
        return
    data = json.loads(sys.stdin.read(1024 * 1024))
    sid = data.get('session_id', '')
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', sid):
        return
    root = Path(executable).resolve().parent
    if event == 'SessionStart':
        subprocess.Popen([executable, 'oncall', 'agent-service'], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return
    if event == 'Notification':
        kind = data.get('notification_type') or data.get('type')
        if kind == 'idle_prompt':
            event = 'Stop'
        elif kind != 'permission_prompt':
            return
        data['type'] = kind
    name = {'Stop': 'stop', 'PostToolUse': 'post-tool-use', 'PermissionRequest': 'permission-request',
            'Notification': 'notification'}.get(event)
    if not name:
        return
    spec = importlib.util.spec_from_file_location('ccc_agent_hook', root / 'hooks' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Disable desktop notifications and auto-title requests: phone alerts are
    # raised by the existing session_signals observer, not by this adapter.
    module.notify_session = lambda *a, **k: None
    module.request_auto_title = lambda *a, **k: None
    if event == 'PermissionRequest':
        # Recording the prompt is fast. Never park a hook waiting for a phone.
        module.record_ask(sid, data.get('tool_name', ''), data.get('tool_input', {}),
                          data.get('transcript_path', ''))
        module.raise_badge(sid, data.get('tool_name', ''), data.get('tool_input', {}))
    else:
        sys.stdin = io.StringIO(json.dumps(data))
        module.main()
        if event == 'Stop':
            marker = Path(module.LIVE_STATE_DIR) / (sid + '.json')
            state = json.loads(marker.read_text())
            state['oncall_notify_idle'] = True
            temporary = marker.with_suffix('.plugin.tmp')
            temporary.write_text(json.dumps(state))
            temporary.replace(marker)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
