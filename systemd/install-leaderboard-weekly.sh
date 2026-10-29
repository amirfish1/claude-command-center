#!/bin/bash
# Install the weekly leaderboard refresh as a systemd *user* timer.
# Needs a free router on this machine (see docs/free-models.md); without one
# each run records status "no_router" in ~/.ccc/leaderboard/last-run.json.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
for unit in ccc-leaderboard-weekly.service ccc-leaderboard-weekly.timer; do
  ln -sfn "$HERE/systemd/$unit" "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now ccc-leaderboard-weekly.timer

echo "✓ ccc-leaderboard-weekly.timer enabled"
echo "  Next run : systemctl --user list-timers ccc-leaderboard-weekly.timer"
echo "  Run now  : systemctl --user start ccc-leaderboard-weekly.service"
echo "  Logs     : journalctl --user -u ccc-leaderboard-weekly.service"
echo "  Health   : python3 $HERE/scripts/leaderboard-weekly.py health"
