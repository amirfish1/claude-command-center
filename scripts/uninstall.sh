#!/usr/bin/env bash
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
# Claude Command Center uninstaller.
#
# Usage:
#   ~/.ccc/claude-command-center/scripts/uninstall.sh [--yes] [--purge-data] [--keep-checkout]
#
# Removes:
#   - the background services (launchd on macOS, systemd user units on Linux)
#   - the `ccc` CLI link in ~/.local/bin (only if it points into CCC)
#   - the checkout at ~/.ccc/claude-command-center (unless --keep-checkout)
# Keeps by default (your settings, queue, and session metadata):
#   - ~/.claude/command-center   (removed with --purge-data)
# Never touches ~/.claude/projects, your Claude Code transcripts.
#
# Environment: CCC_INSTALL_DIR overrides the checkout path (as in install.sh).

set -euo pipefail

INSTALL_DIR="${CCC_INSTALL_DIR:-$HOME/.ccc/claude-command-center}"
DATA_DIR="$HOME/.claude/command-center"
CLI_LINK="$HOME/.local/bin/ccc"
ASSUME_YES=0
PURGE_DATA=0
KEEP_CHECKOUT=0

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --purge-data) PURGE_DATA=1 ;;
    --keep-checkout) KEEP_CHECKOUT=1 ;;
    -h|--help) sed -n 5,16p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "uninstall: unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Resolve the checkout this script belongs to, falling back to INSTALL_DIR.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
CHECKOUT="$INSTALL_DIR"
if [ -n "$HERE" ] && [ -f "$HERE/run.sh" ]; then
  CHECKOUT="$HERE"
fi

echo "Claude Command Center uninstall"
echo "  services : stop and remove"
echo "  ccc CLI  : remove $CLI_LINK if it points at CCC"
if [ "$KEEP_CHECKOUT" = 1 ]; then
  echo "  checkout : keep $CHECKOUT"
else
  echo "  checkout : delete $CHECKOUT"
fi
if [ "$PURGE_DATA" = 1 ]; then
  echo "  data     : DELETE $DATA_DIR (settings, queue, session metadata)"
else
  echo "  data     : keep $DATA_DIR (use --purge-data to delete)"
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

# 1. Services. run.sh owns the unit names and paths, so reuse its remover.
if [ -f "$CHECKOUT/run.sh" ]; then
  bash "$CHECKOUT/run.sh" --uninstall-service || true
else
  echo "note: $CHECKOUT/run.sh not found; skipping service removal"
fi

# 2. Any server still running in the foreground from this checkout.
pkill -f "$CHECKOUT/server.py" 2>/dev/null || true
pkill -f "$CHECKOUT/ccc_worker.py" 2>/dev/null || true

# 3. ccc CLI link, only when it points into a CCC checkout.
if [ -L "$CLI_LINK" ]; then
  target="$(readlink "$CLI_LINK")"
  case "$target" in
    "$CHECKOUT"/*|"$INSTALL_DIR"/*) rm -f "$CLI_LINK"; echo "✓ Removed $CLI_LINK" ;;
    *) echo "note: $CLI_LINK points at $target; left in place" ;;
  esac
fi

# 4. Checkout. Refuse anything that does not look like a CCC checkout.
if [ "$KEEP_CHECKOUT" != 1 ] && [ -d "$CHECKOUT" ]; then
  if [ -f "$CHECKOUT/server.py" ] && [ -f "$CHECKOUT/run.sh" ]; then
    rm -r "$CHECKOUT"
    rmdir "$(dirname "$CHECKOUT")" 2>/dev/null || true
    echo "✓ Removed $CHECKOUT"
  else
    echo "note: $CHECKOUT does not look like a CCC checkout; left in place"
  fi
fi

# 5. User data, only on request.
if [ "$PURGE_DATA" = 1 ] && [ -d "$DATA_DIR" ]; then
  rm -r "$DATA_DIR"
  echo "✓ Removed $DATA_DIR"
fi

echo "✓ Claude Command Center uninstalled."
echo "  If you installed the macOS app, drag Claude Command Center.app to the Trash."
