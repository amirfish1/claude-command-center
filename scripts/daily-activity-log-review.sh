#!/bin/bash
# Scheduled daily health-check of CCC's activity.log. Runs a headless Claude
# session that looks for anomalous patterns (retry storms, error loops,
# double-firing operations), cross-checks them against open/recent WatchTower
# CCC tickets so it doesn't refile known issues, and only files a ticket for
# genuinely new findings backed by evidence from the log.
set -euo pipefail

CLAUDE_BIN="/Users/amirfish/.local/bin/claude"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROMPT=$(cat <<'EOF'
You are doing a scheduled daily health-check of Claude Command Center's
activity log at ~/.claude/command-center/logs/activity.log.

1. Look at roughly the last 24 hours of entries (or the last ~5000 lines,
   whichever is smaller).
2. Compute frequency by category+verb. Flag:
   - Any verb/category firing in a tight loop with no backoff (many events
     within seconds of each other, sustained over minutes).
   - Any TIMEOUT/ERROR/FAIL/BLOCK/WEDGED/DEAD-style verb appearing more than
     a handful of times.
   - Anything that looks like one logical operation double-firing (e.g.
     duplicate consecutive log lines for what should be a single action).
3. For each suspicious pattern, use `ps aux`, `wt status -q CCC --json`, and
   `wt ls -q CCC` to check whether it's already covered by an open or
   very-recently-closed ticket, and whether the underlying condition is
   still live right now.
4. Only file a new ticket (`wt add -q CCC --type bug ...`) for patterns that
   are (a) not already covered by an open or very-recently-closed ticket
   with the same root cause, and (b) genuinely anomalous -- not routine
   reaper/prewarm/health-beat/self-health activity.
5. If nothing anomalous is found, do not file anything. Exit quietly.
6. Read-only investigation plus `wt add` only. Never edit, write, or commit
   any file, never push, never restart services.

Be specific and skeptical: cite exact log lines, counts, and time windows in
any ticket you file. Do not speculate without evidence from the log, ps, or
wt.
EOF
)

"$CLAUDE_BIN" -p "$PROMPT" \
  --permission-mode bypassPermissions \
  --disallowedTools "Edit,Write,NotebookEdit,WebFetch,WebSearch"
