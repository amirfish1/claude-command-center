#!/bin/bash
# Install (or reinstall) Claude Command Center systemd units on the hermes VM.
# Run as root (units run as User=hermes regardless of who installs them).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"

# 1. Stop and disable any legacy user units for user hermes so they don't collide on port 8091
if command -v systemctl >/dev/null 2>&1; then
  if id -u hermes >/dev/null 2>&1; then
    sudo -u hermes XDG_RUNTIME_DIR=/run/user/1000 systemctl --user stop ccc.service ccc-worker.service >/dev/null 2>&1 || true
    sudo -u hermes XDG_RUNTIME_DIR=/run/user/1000 systemctl --user disable ccc.service ccc-worker.service >/dev/null 2>&1 || true
  fi
fi

# 2. Copy systemd unit files into /etc/systemd/system/
cp "$HERE/systemd/ccc-worker.service" "/etc/systemd/system/ccc-worker.service"
cp "$HERE/systemd/ccc.service" "/etc/systemd/system/ccc.service"
echo "installed ccc-worker.service and ccc.service to /etc/systemd/system/"

# 3. Reload systemd daemon
systemctl daemon-reload

# 4. Enable and start units
systemctl enable --now ccc-worker.service
systemctl enable --now ccc.service

echo
echo "✓ CCC systemd services installed and started on port 8091"
echo "  Status : systemctl status ccc.service"
echo "  Worker : systemctl status ccc-worker.service"
echo "  Logs   : journalctl -u ccc.service -f"
