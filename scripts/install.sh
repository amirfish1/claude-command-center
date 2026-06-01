#!/usr/bin/env bash
# Claude Command Center one-command installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | CCC_FROM=hn bash
#   curl -fsSL .../install.sh | bash               # channel defaults to unknown
#   ./install.sh --from=readme                     # direct invocation after git clone
#
# Behaviour:
#   - macOS only. Linux exits fast with a one-line pointer to the Docker issue.
#   - Clones to ~/.ccc/claude-command-center if absent, git pulls if present.
#   - Verifies python3 and the `claude` CLI are on PATH.
#   - Persists an attribution channel to ~/.claude/command-center/install-source.
#   - Launches ./run.sh in the foreground and opens http://localhost:8090
#     once the port answers.

set -euo pipefail

REPO_URL="https://github.com/amirfish1/claude-command-center"
INSTALL_DIR="$HOME/.ccc/claude-command-center"
PORT="${PORT:-8090}"
DASHBOARD_URL="http://localhost:${PORT}"
SOURCE_FILE="$HOME/.claude/command-center/install-source"

VALID_CHANNELS="readme landing-hero hn ph devto yt gh-trending dmg unknown"

err() {
  printf 'install: %s\n' "$*" >&2
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
require_macos() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || printf 'unknown')"
  if [ "$uname_s" != "Darwin" ]; then
    err "CCC is macOS-only; see the Docker issue ${REPO_URL}/issues/54 once it ships"
    exit 2
  fi
}

# ---------------------------------------------------------------------------
# Prereq checks
# ---------------------------------------------------------------------------
require_python3() {
  if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found on PATH. Install Xcode CLT: xcode-select --install"
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
    err "git not found on PATH. Install Xcode CLT: xcode-select --install"
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
  else
    printf 'install: cloning %s to %s\n' "$REPO_URL" "$INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
}

# ---------------------------------------------------------------------------
# Launch + open browser
# ---------------------------------------------------------------------------
open_when_ready() {
  # Background watcher: poll the port, then `open` the URL.
  # Bounded by ~60 seconds so we never wedge if the server fails to start.
  (
    for _ in $(seq 1 60); do
      if (echo > "/dev/tcp/127.0.0.1/${PORT}") >/dev/null 2>&1; then
        open "$DASHBOARD_URL" >/dev/null 2>&1 || true
        exit 0
      fi
      sleep 1
    done
  ) &
}

ask_install_service() {
  if [ ! -t 1 ] || [ ! -c /dev/tty ]; then
    return 1
  fi

  local choice
  printf 'install: Would you like to install CCC as a system service so it runs automatically in the background? [y/N] '
  if read -r choice < /dev/tty; then
    case "$choice" in
      [yY][eE][sS]|[yY])
        return 0
        ;;
    esac
  fi
  return 1
}

launch_server() {
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
# main
# ---------------------------------------------------------------------------
main() {
  require_macos
  require_git
  require_python3
  warn_if_no_claude_cli

  local channel
  channel="$(parse_channel "$@")"
  persist_channel "$channel"
  printf 'install: attribution channel = %s\n' "$channel"

  sync_repo
  launch_server
}

# Only auto-run when executed, not when sourced (tests source us for
# direct `parse_channel` calls).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
