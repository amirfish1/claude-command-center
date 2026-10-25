#!/bin/bash
# Opt in to hourly browser checks and deduplicated WatchTower bug tickets.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
QUEUE="${1:-CCC}"
command -v node >/dev/null
command -v wt >/dev/null
mkdir -p "$UNIT_DIR"
python3 - "$HERE" "$UNIT_DIR" "$QUEUE" "$(command -v node)" <<'PY'
import pathlib,sys
repo,units,queue,node=sys.argv[1:]
def quote(value):
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
service='''[Unit]
Description=Audit finished session rows and file missing-control bugs
After=network-online.target

[Service]
Type=oneshot
Environment="PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin"
WorkingDirectory={repo}
ExecStart={node} {script} --file --queue {queue}
TimeoutStartSec=20min
Nice=10
'''.format(repo=quote(repo),node=quote(node),script=quote(repo+'/scripts/audit-session-rows.cjs'),queue=quote(queue))
pathlib.Path(units,'ccc-row-audit.service').write_text(service)
pathlib.Path(units,'ccc-row-audit.timer').write_text(pathlib.Path(repo,'systemd/ccc-row-audit.timer').read_text())
PY
systemctl --user daemon-reload
systemctl --user enable --now ccc-row-audit.timer
systemctl --user start --no-block ccc-row-audit.service
printf 'Hourly audit enabled; tickets: %s. First run started.\n' "$QUEUE"
printf 'Status: systemctl --user status ccc-row-audit.service ccc-row-audit.timer\n'
