#!/usr/bin/env bash
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
# Claude Command Center uninstaller.
#
# Usage:
#   ~/.ccc/claude-command-center/scripts/uninstall.sh [--yes] [--purge-data] [--keep-checkout]
#
# Removes:
#   - CCC background services (launchd on macOS, systemd user units on Linux)
#   - WatchTower daemon LaunchAgent (`ai.watchtower.watcher`) and any still-
#     running `watchtower.cli start` process
#   - the `ccc` CLI link in ~/.local/bin when it points at a CCC checkout or a
#     Homebrew Cellar/opt path (including a broken symlink after `brew uninstall`)
#   - CCC hook entries in ~/.claude/settings.json and ~/.codex/hooks.json
#     (commands that reference command-center/hooks/ or the legacy log-viewer path)
#   - WatchTower skill symlinks under ~/.claude/skills and ~/.codex/skills that
#     point into ~/.ccc/watchtower
#   - the CCC-managed WatchTower clone at ~/.ccc/watchtower
#   - the checkout at ~/.ccc/claude-command-center (unless --keep-checkout)
# Keeps by default (your settings, queue, and session metadata):
#   - ~/.claude/command-center   (removed with --purge-data)
#   - ~/.watchtower              (removed with --purge-data)
# Never touches ~/.claude/projects, your Claude Code transcripts, or unrelated
# hooks/skills (e.g. Boost).
#
# Environment: CCC_INSTALL_DIR overrides the checkout path (as in install.sh).
#              WATCHTOWER_INSTALL_DIR overrides the managed WatchTower clone
#              (default ~/.ccc/watchtower, as in install-watchtower.sh).

set -euo pipefail

INSTALL_DIR="${CCC_INSTALL_DIR:-$HOME/.ccc/claude-command-center}"
WATCHTOWER_DIR="${WATCHTOWER_INSTALL_DIR:-$HOME/.ccc/watchtower}"
DATA_DIR="$HOME/.claude/command-center"
WATCHTOWER_STATE_DIR="$HOME/.watchtower"
CLI_LINK="$HOME/.local/bin/ccc"
WT_LABEL="ai.watchtower.watcher"
WT_PLIST="$HOME/Library/LaunchAgents/${WT_LABEL}.plist"
ASSUME_YES=0
PURGE_DATA=0
KEEP_CHECKOUT=0

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --purge-data) PURGE_DATA=1 ;;
    --keep-checkout) KEEP_CHECKOUT=1 ;;
    -h|--help) sed -n 5,28p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "uninstall: unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Resolve which checkout to remove. An explicit CCC_INSTALL_DIR always wins
# (tests and custom prefixes). Otherwise prefer the tree this script lives in
# when it looks like a CCC checkout, else the default ~/.ccc path.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
if [ -n "${CCC_INSTALL_DIR:-}" ]; then
  CHECKOUT="$INSTALL_DIR"
elif [ -n "$HERE" ] && [ -f "$HERE/run.sh" ] && [ -f "$HERE/server.py" ]; then
  CHECKOUT="$HERE"
else
  CHECKOUT="$INSTALL_DIR"
fi

echo "Claude Command Center uninstall"
echo "  services : stop and remove CCC + WatchTower"
echo "  ccc CLI  : remove $CLI_LINK if it points at CCC (checkout or Homebrew)"
echo "  hooks    : strip CCC entries from Claude/Codex hook config"
echo "  skills   : remove WatchTower skill symlinks into $WATCHTOWER_DIR"
echo "  watchtower clone: delete $WATCHTOWER_DIR"
if [ "$KEEP_CHECKOUT" = 1 ]; then
  echo "  checkout : keep $CHECKOUT"
else
  echo "  checkout : delete $CHECKOUT"
fi
if [ "$PURGE_DATA" = 1 ]; then
  echo "  data     : DELETE $DATA_DIR and $WATCHTOWER_STATE_DIR"
else
  echo "  data     : keep $DATA_DIR and $WATCHTOWER_STATE_DIR (use --purge-data to delete)"
fi

if [ "$ASSUME_YES" != 1 ]; then
  if [ ! -t 0 ]; then
    echo "uninstall: not a terminal; re-run with --yes to confirm." >&2
    exit 1
  fi
  printf 'Continue? [y/N] '
  read -r reply
  case "$reply" in y|Y|yes|YES) ;; *) echo "Cancelled."; exit 1 ;; esac
fi

# 1. CCC services. run.sh owns the unit names and paths, so reuse its remover.
if [ -f "$CHECKOUT/run.sh" ]; then
  bash "$CHECKOUT/run.sh" --uninstall-service || true
else
  echo "note: $CHECKOUT/run.sh not found; skipping CCC service removal"
fi

# 2. WatchTower daemon (LaunchAgent on macOS; process kill everywhere).
uninstall_watchtower_daemon() {
  if [ "$(uname -s)" = "Darwin" ]; then
    if command -v launchctl >/dev/null 2>&1; then
      launchctl bootout "gui/$(id -u)/$WT_LABEL" >/dev/null 2>&1 || true
      # Older macOS / manually loaded plists.
      if [ -f "$WT_PLIST" ]; then
        launchctl unload "$WT_PLIST" >/dev/null 2>&1 || true
      fi
    fi
    if [ -f "$WT_PLIST" ]; then
      rm -f "$WT_PLIST"
      echo "✓ Removed $WT_PLIST"
    fi
  fi
  # Prefer an orderly stop when wt is on PATH.
  if command -v wt >/dev/null 2>&1; then
    wt stop >/dev/null 2>&1 || true
  fi
  pkill -f 'watchtower\.cli start' >/dev/null 2>&1 || true
}
uninstall_watchtower_daemon

# 3. Any CCC server still running in the foreground from this checkout.
pkill -f "$CHECKOUT/server.py" 2>/dev/null || true
pkill -f "$CHECKOUT/ccc_worker.py" 2>/dev/null || true

# 4. ccc CLI link: checkout install, Homebrew Cellar/opt, or a broken link to either.
remove_ccc_cli_link() {
  if [ ! -L "$CLI_LINK" ] && [ ! -e "$CLI_LINK" ]; then
    return 0
  fi
  if [ ! -L "$CLI_LINK" ]; then
    echo "note: $CLI_LINK is not a symlink; left in place"
    return 0
  fi
  local target
  target="$(readlink "$CLI_LINK" 2>/dev/null || true)"
  case "$target" in
    "$CHECKOUT"/*|"$INSTALL_DIR"/*|*/Cellar/ccc/*|*/opt/ccc|*/opt/ccc/*)
      rm -f "$CLI_LINK"
      echo "✓ Removed $CLI_LINK (was → $target)"
      ;;
    *)
      echo "note: $CLI_LINK points at $target; left in place"
      ;;
  esac
}
remove_ccc_cli_link

# 5. Strip CCC hook commands from agent config (keeps unrelated hooks).
strip_ccc_hooks_from_agent_config() {
  local py
  py="$(command -v python3 || true)"
  if [ -z "$py" ]; then
    echo "note: python3 not found; skipping CCC hook strip from agent config"
    return 0
  fi
  "$py" - <<'PY'
import json
import os
from pathlib import Path

MARKERS = ("command-center/hooks/", "log-viewer/hooks/")

def is_ccc_command(cmd):
    return isinstance(cmd, str) and any(m in cmd for m in MARKERS)

def strip_hooks(config):
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept_entries = []
        event_changed = False
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                kept_entries.append(entry)
                continue
            kept = [
                h for h in entry["hooks"]
                if not (isinstance(h, dict) and is_ccc_command(h.get("command")))
            ]
            if len(kept) == len(entry["hooks"]):
                kept_entries.append(entry)
                continue
            event_changed = True
            if kept:
                entry = dict(entry)
                entry["hooks"] = kept
                kept_entries.append(entry)
        if event_changed:
            changed = True
            if kept_entries:
                hooks[event] = kept_entries
            else:
                del hooks[event]
    if changed and not hooks:
        del config["hooks"]
    return changed

home = Path(os.path.expanduser("~"))
paths = [
    home / ".claude" / "settings.json",
    home / ".codex" / "hooks.json",
]
for path in paths:
    if not path.is_file():
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"note: could not parse {path}: {exc}")
        continue
    if not isinstance(data, dict):
        continue
    if not strip_hooks(data):
        continue
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"✓ Stripped CCC hooks from {path}")
PY
}
strip_ccc_hooks_from_agent_config

# 6. WatchTower skill symlinks that point into the managed clone.
remove_watchtower_skill_links() {
  local skills_dir link target removed
  removed=0
  for skills_dir in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
    [ -d "$skills_dir" ] || continue
    # Avoid `find -print0` + read loops that break under `set -e` when empty.
    for link in "$skills_dir"/*; do
      [ -e "$link" ] || [ -L "$link" ] || continue
      [ -L "$link" ] || continue
      target="$(readlink "$link" 2>/dev/null || true)"
      case "$target" in
        "$WATCHTOWER_DIR"/*|"$HOME/.ccc/watchtower"/*)
          rm -f "$link"
          removed=$((removed + 1))
          ;;
      esac
    done
  done
  if [ "$removed" -gt 0 ]; then
    echo "✓ Removed $removed WatchTower skill symlink(s)"
  fi
}
remove_watchtower_skill_links

# 7. CCC-managed WatchTower clone.
if [ -d "$WATCHTOWER_DIR" ]; then
  if [ -f "$WATCHTOWER_DIR/pyproject.toml" ] && [ -f "$WATCHTOWER_DIR/watchtower/cli.py" ]; then
    rm -rf "$WATCHTOWER_DIR"
    echo "✓ Removed $WATCHTOWER_DIR"
  else
    echo "note: $WATCHTOWER_DIR does not look like a WatchTower checkout; left in place"
  fi
fi

# 8. CCC checkout. Refuse anything that does not look like a CCC checkout.
if [ "$KEEP_CHECKOUT" != 1 ] && [ -d "$CHECKOUT" ]; then
  if [ -f "$CHECKOUT/server.py" ] && [ -f "$CHECKOUT/run.sh" ]; then
    rm -rf "$CHECKOUT"
    echo "✓ Removed $CHECKOUT"
  else
    echo "note: $CHECKOUT does not look like a CCC checkout; left in place"
  fi
fi

# Drop ~/.ccc when it is empty after removing managed clones.
rmdir "$HOME/.ccc" 2>/dev/null || true

# 9. User data, only on request.
if [ "$PURGE_DATA" = 1 ]; then
  if [ -d "$DATA_DIR" ]; then
    rm -rf "$DATA_DIR"
    echo "✓ Removed $DATA_DIR"
  fi
  if [ -d "$WATCHTOWER_STATE_DIR" ]; then
    rm -rf "$WATCHTOWER_STATE_DIR"
    echo "✓ Removed $WATCHTOWER_STATE_DIR"
  fi
fi

echo "✓ Claude Command Center uninstalled."
echo "  If you installed the macOS app, drag Claude Command Center.app to the Trash."
echo "  If you installed via Homebrew, also run: brew uninstall ccc && brew untap amirfish1/ccc"
