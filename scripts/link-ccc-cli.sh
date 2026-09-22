#!/usr/bin/env bash
# Symlink the repo-root `ccc` CLI client onto PATH — the single
# implementation, shared by every CCC entry point.
#
# Why one file: CCC reaches users through `curl | bash` (scripts/install.sh),
# Homebrew, the DMG, and a plain `git clone` + `./run.sh`. Only install.sh
# used to link `ccc` onto PATH (via a local function called from its own
# main()), so anyone who reached the product through run.sh directly —
# including a maintainer dev checkout that launches server.py/run.sh without
# ever running install.sh — never got the CLI linked and fell back to
# hand-rolled curl against the HTTP API. What every path DOES have in common
# is that it ends up executing run.sh, so run.sh and scripts/install.sh both
# call this script and get the same behavior.
#
# Convenience only — never fatal, and safe to call on every launch (a no-op
# once the link already points at the right target).
#
# Usage:
#   CCC_CLI_SOURCE_DIR=/path/to/checkout ./scripts/link-ccc-cli.sh
#
# Environment:
#   CCC_CLI_SOURCE_DIR   checkout containing the `ccc` script (required) —
#                         $INSTALL_DIR for install.sh, $HERE for run.sh
#                         (run.sh does not relocate the checkout).
#   CCC_CLI_LOG_PREFIX   line prefix ("install: " for install.sh, etc.)

set -euo pipefail

SOURCE_DIR="${CCC_CLI_SOURCE_DIR:?CCC_CLI_SOURCE_DIR must be set}"
LOG_PREFIX="${CCC_CLI_LOG_PREFIX:-}"
BIN_DIR="$HOME/.local/bin"
TARGET="$SOURCE_DIR/ccc"
LINK="$BIN_DIR/ccc"

if [ ! -f "$TARGET" ]; then
  exit 0
fi

chmod +x "$TARGET" 2>/dev/null || true
mkdir -p "$BIN_DIR" 2>/dev/null || true

if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$TARGET" ]; then
  exit 0
fi

if ln -sfn "$TARGET" "$LINK" 2>/dev/null; then
  printf '%sccc CLI linked at %s\n' "$LOG_PREFIX" "$LINK"
  # shellcheck disable=SC2016
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) printf '%snote: %s is not on PATH — add it to use `ccc` from anywhere\n' "$LOG_PREFIX" "$BIN_DIR" ;;
  esac
else
  printf '%scould not link ccc CLI — run it as %s\n' "$LOG_PREFIX" "$TARGET"
fi
