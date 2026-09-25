#!/usr/bin/env bash
# Install the perf-budget pre-push gate for this clone. Idempotent.
#
# Git hooks are not versioned, so a fresh clone has no pre-push hook until
# this runs, and the gate (scripts/pre-push.sh) silently never fires. It also
# never fires when core.hooksPath points somewhere else (a global hooks dir),
# because git then ignores .git/hooks entirely. This script installs the shim
# where git will actually look, or tells you why it can't.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HOOKS_PATH="$(git config --get core.hooksPath || true)"
if [ -n "$HOOKS_PATH" ]; then
  HOOKS_DIR="${HOOKS_PATH/#\~/$HOME}"
  echo "core.hooksPath is set ($HOOKS_PATH): git ignores .git/hooks here."
  if [ -e "$HOOKS_DIR/pre-push" ]; then
    echo "  $HOOKS_DIR/pre-push exists; make sure it runs scripts/pre-push.sh"
    echo "  when present in the pushed repo. Not overwriting it."
  else
    echo "  Add a delegating pre-push to that directory, e.g.:"
    echo ""
    echo "    #!/bin/sh"
    echo "    top=\$(git rev-parse --show-toplevel 2>/dev/null) || exit 0"
    echo "    [ -x \"\$top/scripts/pre-push.sh\" ] || exit 0"
    echo "    exec \"\$top/scripts/pre-push.sh\" \"\$@\""
  fi
  exit 1
fi

HOOK="$(git rev-parse --git-common-dir)/hooks/pre-push"
mkdir -p "$(dirname "$HOOK")"
if [ -e "$HOOK" ] && ! grep -q "scripts/pre-push.sh" "$HOOK"; then
  echo "$HOOK exists and does not call scripts/pre-push.sh. Not overwriting."
  exit 1
fi
cat > "$HOOK" <<'SHIM'
#!/bin/sh
# Installed by scripts/install-git-hooks.sh: run the committed perf gate.
top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -x "$top/scripts/pre-push.sh" ] || exit 0
exec "$top/scripts/pre-push.sh" "$@"
SHIM
chmod +x "$HOOK"
echo "installed $HOOK -> scripts/pre-push.sh"
