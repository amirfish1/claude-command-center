#!/usr/bin/env bash
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
  if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found on PATH. Install Python 3, then re-run this installer."
    exit 1
  fi
  # server.py uses 3.10+ union-type syntax (e.g. `float | None`) without a
  # `from __future__ import annotations` guard, so it fails to import on
  # 3.9. Presence alone isn't enough — check the version so a stale
  # system python3 (still common on e.g. Debian bullseye) fails here with
  # a clear message instead of a cryptic TypeError from server.py.
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    got="$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)"
    err "python3 ${got} found, but CCC requires Python 3.10+. Install a newer python3, then re-run this installer."
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
    git -C "$INSTALL_DIR" pull --ff-only
    return
  fi

  if [ -e "$INSTALL_DIR" ]; then
    err "install destination exists but is not a Git checkout: ${INSTALL_DIR}. Move it aside or choose CCC_INSTALL_DIR, then retry. No files were changed."
    return 1
  fi

  local parent staging
  parent="$(dirname "$INSTALL_DIR")"
  staging="${INSTALL_DIR}.installing.$$"
  mkdir -p "$parent"
  INSTALL_STAGING="$staging"

  printf 'install: cloning %s to %s\n' "$REPO_URL" "$INSTALL_DIR"
  if ! git clone "$REPO_URL" "$staging"; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "clone failed; no partial installation was published"
    return 1
  fi

  # A concurrent installer may have published while this clone was running.
  # Never turn its checkout into a parent directory or overwrite it.
  if [ -e "$INSTALL_DIR" ]; then
    cleanup_install_staging
    INSTALL_STAGING=""
    err "another installer published ${INSTALL_DIR}; leaving it untouched"
    return 1
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
install_watchtower() {
  # Probe for a local WatchTower checkout. Precedence:
  #   1. $WATCHTOWER_DIR env var (explicit override for CI / non-standard paths)
  #   2. ~/Apps/watchtower  (default dev location)
  #   3. ~/dev/watchtower   (alternate dev location)
  # If none found, warn and continue — CCC still works via its own ux_fixes_queue.
  local wt_dir=""
  if [ -n "${WATCHTOWER_DIR:-}" ] && [ -d "$WATCHTOWER_DIR" ]; then
    wt_dir="$WATCHTOWER_DIR"
  elif [ -d "$HOME/Apps/watchtower" ]; then
    wt_dir="$HOME/Apps/watchtower"
  elif [ -d "$HOME/dev/watchtower" ]; then
    wt_dir="$HOME/dev/watchtower"
  fi

  if [ -z "$wt_dir" ]; then
    printf 'install: WatchTower not found (checked ~/Apps/watchtower, ~/dev/watchtower, WATCHTOWER_DIR env var)\n'
    printf 'install: CCC will use its built-in queue engine. Set WATCHTOWER_DIR or clone WT to enable delegation.\n'
    return 0
  fi

  printf 'install: installing WatchTower from %s\n' "$wt_dir"
  if python3 -m pip install -e "$wt_dir" --quiet; then
    printf 'install: WatchTower installed — watchtower.queue is now available.\n'
  else
    printf 'install: WARNING: pip install of WatchTower failed. CCC will fall back to its built-in queue engine.\n'
  fi
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
