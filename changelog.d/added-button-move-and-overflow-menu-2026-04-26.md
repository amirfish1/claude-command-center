**Sidebar header reorganized + new ⋯ overflow menu in the conv-pane
toolbar.** The four conversation-list controls (Board / Archive / Sort /
Refresh) move from under the search box up into the sidebar's
"Claude Command Center" header row, packed into a `.sidebar-header-actions`
group with new `.sh-btn` styling. The empty space to the right of the
title was wasted before; this puts the always-needed controls a level
higher so the search-box row is just the search box. Adds a `⋯` overflow
button at the right edge of the conv-pane toolbar that opens a per-session
actions menu — currently surfaces "Move to repo…" (re-buckets the session
JSONL into a different repo's `~/.claude/projects/<slug>/` dir via a new
`POST /api/sessions/<sid>/move` endpoint, allow-listed against
`load_known_repos()`), and is designed to grow other per-session actions
later. The move endpoint uses `_encode_project_slug` so target dirs
match what current Claude Code writes (handles `+`, `.`, `_`, spaces —
the same regression `8216fae` fixed).
