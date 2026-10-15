# Remembering decisions

`ccc decisions` and the Ask agent can retrieve user decision quotes captured
by CCC's background decision scan:

```bash
ccc decisions "queue engine"
ccc decisions "queue engine" --repo widget-repo --json
```

In Ask, try “What did we decide about the queue engine?” The read-only
`ccc-state` MCP tool `decisions(topic, repo?, limit?)` is available even when
the optional `claude-index` CLI is absent. Ask can cite a decision's session
using its usual source chips.

The existing `GET /api/memory/decisions?topic=...&repo=...&limit=10` endpoint
keeps its `topic` and `results` response fields. `repo` is an optional exact
repository name. Each result still includes `session_id`, `title`, `repo`,
`date`, and `snippet`, with additional evidence fields:

| Evidence | Meaning |
|---|---|
| `provenance: extracted_user_quote`, `status: candidate` | A user-authored quote selected by the background extractor. Includes a decision ID, extraction confidence, and `source` with transcript path, line, timestamp, and engine. |
| `provenance: session_snippet`, `status: unverified` | A session snippet containing decision language. Returned only when no stored quotes match. It may lack the context needed to establish a decision. |

Quotes are ranked by topic keyword coverage, then recency. A quote records
what was said at that time; it does not establish that the decision remains
current. Check later rulings and their dates when results conflict.

The request reads the stored extraction; it never starts a transcript scan
or model call. New quotes appear after the background scan runs. When a
transcript changes, that scan replaces its previous extracted quotes so
appends do not duplicate them and edits do not leave superseded text.
Failed file reads retain the previous extraction for retry.

Stores created before transcript line tracking still work. Their `source.line`
is `null` until the transcript is parsed again by the background writer.
Neither queries nor citation resolution migrate the store.

Decision quotes and source paths stay in local state. The existing Ask engine
receives tool results when answering a question; this feature adds no provider
or hosted memory service. No note is written or approved by the live agent.
