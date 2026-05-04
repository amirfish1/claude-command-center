**Search past conversations from CCC.** A new 🔎 History button in the
top toolbar (shortcut: `/`) opens a right-side drawer that runs BM25
keyword search across every Claude Code session that has been indexed
by the separate `claude-index` tool. The drawer reads
`~/.claude-index/index.db` opened with `mode=ro` so CCC can never
mutate the index that claude-index owns.

The drawer shows BM25-ranked results on the left with `<mark>`
highlighted snippets; clicking a row opens the full message — with
metadata (session, cwd, branch, model, source-file) — in the
click-through pane on the right. Filters: time window
(All / Today / 7d / 30d) and a "this repo only" toggle pre-filled
from the current CCC workspace.

Bare multi-word queries are auto-OR-rewritten so a single missing
word can't zero out the result set; explicit FTS5 operators
(`"quoted"`, `OR`, `NEAR`, `prefix*`) pass through unchanged. When
the index hasn't been built yet, the search returns a friendly
empty state pointing at `claude-index`.

New endpoints: `GET /api/search-history?q=&since=&cwd=&limit=`,
`GET /api/history-message?uuid=`. No new runtime dependencies —
read-only `sqlite3` is stdlib. The Ollama / hybrid-vector search
path is intentionally **not** part of this change; CCC stays a
keyword-only consumer of the index.
