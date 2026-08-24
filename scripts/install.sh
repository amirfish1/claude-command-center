#!/usr/bin/env bash
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
# Claude Command Center one-command installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | CCC_FROM=hn bash
#   curl -fsSL .../install.sh | bash               # channel defaults to unknown
#   ./install.sh --from=readme                     # direct invocation after git clone
#
# Behaviour:
#   - Supports macOS and Linux. Windows users can use scripts/install.ps1
#     natively, or run this script inside WSL2 for the Linux service path.
#   - Clones to ~/.ccc/claude-command-center if absent, git pulls if present.
#   - Verifies git and python3 are on PATH.
#   - Persists an attribution channel to ~/.claude/command-center/install-source.
#   - Launches ./run.sh in the foreground and opens http://localhost:8090
#     once the port answers.

set -euo pipefail

REPO_URL="${CCC_REPO_URL:-https://github.com/amirfish1/claude-command-center}"
INSTALL_DIR="${CCC_INSTALL_DIR:-$HOME/.ccc/claude-command-center}"
PORT="${PORT:-8090}"
DASHBOARD_URL="http://localhost:${PORT}"
SOURCE_FILE="$HOME/.claude/command-center/install-source"
INSTALL_STAGING=""
PYTHON3="${CCC_PYTHON:-python3}"

VALID_CHANNELS="readme landing-hero hn ph devto yt gh-trending dmg unknown"

err() {
  printf 'install: %s\n' "$*" >&2
}

cleanup_install_staging() {
  if [ -n "$INSTALL_STAGING" ] && [ -e "$INSTALL_STAGING" ]; then
    rm -rf "$INSTALL_STAGING"
  fi
}

trap cleanup_install_staging EXIT HUP INT TERM

is_app_install() {
  [ "${CCC_INSTALL_MODE:-}" = "app" ]
}

# ---------------------------------------------------------------------------
# Attribution channel
# ---------------------------------------------------------------------------
# Resolution order (highest precedence first):
#   1. --from=<channel> CLI flag (for direct ./install.sh invocation)
#   2. CCC_FROM env var (for `curl ... | CCC_FROM=hn bash` pipe invocation)
#   3. default 'unknown'
#
# We can't recover the URL from $0 under `curl ... | bash` because bash sets
# $0 to "bash" or "-", not the source URL. Hence the env-var hand-off.
parse_channel() {
  local raw=""
  if [ -n "${CCC_FROM:-}" ]; then
    raw="$CCC_FROM"
  fi
  for arg in "$@"; do
    case "$arg" in
      --from=*) raw="${arg#--from=}" ;;
    esac
  done
  if [ -z "$raw" ]; then
    printf 'unknown'
    return
  fi
  for valid in $VALID_CHANNELS; do
    if [ "$raw" = "$valid" ]; then
      printf '%s' "$valid"
      return
    fi
  done
  printf 'unknown'
}

persist_channel() {
  local channel="$1"
  local dir
  dir="$(dirname "$SOURCE_FILE")"
  mkdir -p "$dir"
  printf '%s\n' "$channel" > "$SOURCE_FILE"
}

# ---------------------------------------------------------------------------
# Platform gate
# ---------------------------------------------------------------------------
require_supported_platform() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || printf 'unknown')"
  case "$uname_s" in
    Darwin|Linux) return 0 ;;
    *)
      err "CCC install supports macOS or Linux. On Windows, use scripts/install.ps1 in PowerShell, or run this script inside WSL2 for the Linux service path; unsupported OS: ${uname_s}"
      exit 2
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Prereq checks
# ---------------------------------------------------------------------------
require_python3() {
  if ! command -v "$PYTHON3" >/dev/null 2>&1; then
    err "python3 not found on PATH. Install Python 3, then re-run this installer."
    exit 1
  fi
  if ! "$PYTHON3" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    got="$("$PYTHON3" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)"
    err "python3 ${got} found, but CCC requires Python 3.9+. Install a newer python3, then re-run this installer."
    exit 1
  fi
}

warn_if_no_claude_cli() {
  # Don't hard-exit if `claude` isn't installed: CCC also drives Codex,
  # Gemini, and Antigravity sessions, and the dashboard itself is useful
  # without any engine on PATH (the user gets a clear in-UI hint to
  # install). Hard-exiting here used to silently drop DMG users who
  # downloaded out of curiosity without a Claude Code install — install.sh
  # would print to a Terminal they already closed and the .app's only
  # signal was a "didn't start in 60s" fatal.
  if ! command -v claude >/dev/null 2>&1; then
    err "claude CLI not on PATH — install from https://docs.claude.com/en/docs/claude-code if you want Claude Code sessions. CCC will still start; Codex / Gemini / Antigravity sessions don't need it."
  fi
}

require_git() {
  if ! command -v git >/dev/null 2>&1; then
    err "git not found on PATH. Install git, then re-run this installer."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Fetch / update repo
# ---------------------------------------------------------------------------
sync_repo() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    printf 'install: updating existing checkout at %s\n' "$INSTALL_DIR"
    if git -C "$INSTALL_DIR" pull --ff-only; then
      return
    fi
    # History no longer fast-forwards (e.g. an upstream rewrite) or the
    # checkout is otherwise broken. Don't leave the user stuck on a crashed
    # installer — reclone fresh and replace it.
    err "existing checkout at ${INSTALL_DIR} could not fast-forward; recloning fresh"
    clone_into_install_dir replace
    return
  fi

  if [ -e "$INSTALL_DIR" ]; then
    err "install destination exists but is not a Git checkout: ${INSTALL_DIR}. Move it aside or choose CCC_INSTALL_DIR, then retry. No files were changed."
    return 1
  fi

  clone_into_install_dir fresh
}

# Clone into a staging dir next to INSTALL_DIR, then atomically publish it.
#   mode=fresh:   INSTALL_DIR must not exist yet. If a concurrent installer
#                 published it while we were cloning, leave that untouched
#                 rather than overwrite it.
#   mode=replace: INSTALL_DIR is expected to already exist (a broken or
#                 diverged checkout) and gets replaced.
clone_into_install_dir() {
  local mode="$1" parent staging
  parent="$(dirname "$INSTALL_DIR")"
  staging="${INSTALL_DIR}.installing.$$"
  mkdir -p "$parent"
  INSTALL_STAGING="$staging"

  printf 'install: cloning %s to %s\n' "$REPO_URL" "$staging"
  if ! git clone "$REPO_URL" "$staging"; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "clone failed; no partial installation was published"
    return 1
  fi

  if [ "$mode" = "fresh" ] && [ -e "$INSTALL_DIR" ]; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "another installer published ${INSTALL_DIR}; leaving it untouched"
    return 1
  fi

  if [ "$mode" = "replace" ]; then
    rm -rf "$INSTALL_DIR"
  fi

  if ! mv "$staging" "$INSTALL_DIR"; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "could not publish completed checkout at ${INSTALL_DIR}"
    return 1
  fi
  INSTALL_STAGING=""
}

# ---------------------------------------------------------------------------
# Launch + open browser
# ---------------------------------------------------------------------------
open_when_ready() {
  if is_app_install; then
    return 0
  fi

  # Background watcher: poll the port, then `open` the URL.
  # Bounded by ~60 seconds so we never wedge if the server fails to start.
  (
    for _ in $(seq 1 60); do
      if (echo > "/dev/tcp/127.0.0.1/${PORT}") >/dev/null 2>&1; then
        if command -v open >/dev/null 2>&1; then
          open "$DASHBOARD_URL" >/dev/null 2>&1 || true
        elif command -v xdg-open >/dev/null 2>&1; then
          xdg-open "$DASHBOARD_URL" >/dev/null 2>&1 || true
        fi
        exit 0
      fi
      sleep 1
    done
  ) &
}

ask_install_service() {
  # Default to YES on interactive terminals: most users want CCC to keep
  # running after they close this Terminal window, and the alternative
  # (foreground server tied to Terminal) is a frequent "where did CCC go"
  # source for DMG users. Non-interactive runs (CI, headless curl|bash
  # without a TTY) stay in foreground — auto-installing services without
  # the user watching would be surprising.
  if [ ! -t 1 ] || [ ! -c /dev/tty ]; then
    return 1
  fi

  local choice
  printf 'install: Install CCC as a background service so it keeps running after this Terminal closes? [Y/n] '
  if read -r choice < /dev/tty; then
    case "$choice" in
      [nN][oO]|[nN])
        return 1
        ;;
    esac
  fi
  return 0
}

launch_server() {
  if is_app_install; then
    printf 'install: launching CCC for the native app on port %s\n' "$PORT"
    cd "$INSTALL_DIR"
    exec ./run.sh
  fi

  if ask_install_service; then
    printf 'install: installing launchd service...\n'
    open_when_ready
    cd "$INSTALL_DIR"
    ./run.sh --install-service
    printf 'install: CCC successfully installed as a background service!\n'
    exit 0
  else
    printf 'install: launching ./run.sh on port %s\n' "$PORT"
    printf 'install: (Tip: to run CCC in the background and persist after reboot, run: ./run.sh --install-service)\n'
    open_when_ready
    cd "$INSTALL_DIR"
    exec ./run.sh
  fi
}

# ---------------------------------------------------------------------------
# WT-26: install WatchTower alongside CCC so watchtower.queue is importable
# ---------------------------------------------------------------------------
# The chain (dev checkout -> managed clone -> tarball -> PyPI last) lives in
# scripts/install-watchtower.sh, which run.sh calls too — one definition, so a
# curl install and a Homebrew install cannot drift apart.
#
# Resolved at call time, not at load time: this script is routinely run as
# `curl ... | bash` with no checkout on disk at all, and only after sync_repo
# has run does $INSTALL_DIR/scripts/ exist.
install_watchtower() {
  local here script=""
  here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
  local candidate
  for candidate in "$here/install-watchtower.sh" \
                   "$INSTALL_DIR/scripts/install-watchtower.sh"; do
    if [ -f "$candidate" ]; then
      script="$candidate"
      break
    fi
  done
  if [ -z "$script" ]; then
    printf 'install: WARNING: scripts/install-watchtower.sh not found — skipping WatchTower.\n'
    printf 'install:   CCC will use its built-in queue engine (no worker dispatch).\n'
    return 0
  fi
  # CCC_WATCHTOWER_FORCE: an explicit install is a user asking for this now,
  # so it must not be silently skipped by the once-a-day rate limits.
  # CCC_VERSION: records what we just installed, so the very next routine
  # `run.sh` launch (no force) does not redundantly re-force on the same
  # version — see wt_ccc_version_changed in install-watchtower.sh.
  CCC_PYTHON="$PYTHON3" \
  CCC_WATCHTOWER_LOG_PREFIX="install: " \
  CCC_WATCHTOWER_FORCE=1 \
  CCC_VERSION="$(grep -m1 '^__version__ = ' "$INSTALL_DIR/server.py" 2>/dev/null | sed -E 's/^__version__ = "(.*)"$/\1/')" \
    bash "$script" || true
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  require_supported_platform
  require_git
  require_python3
  warn_if_no_claude_cli

  local channel
  channel="$(parse_channel "$@")"
  persist_channel "$channel"
  printf 'install: attribution channel = %s\n' "$channel"

  sync_repo
  install_watchtower  # WT-26: bundle WT as CCC's queue engine
  launch_server
}

# Only auto-run when executed, not when sourced (tests source us for
# direct `parse_channel` calls).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
