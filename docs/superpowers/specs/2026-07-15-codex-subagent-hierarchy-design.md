# Codex Subagent Hierarchy and Task Labels

## Problem

Codex-spawned subagents appear as loose top-level rows in the All view and
often use opaque generated nicknames such as `Erdos`, `McClintock`, or
`Dalton`.

The backend already reads Codex's `thread_spawn_edges` table and returns the
correct `parent_session_id`. The Active view already consumes that field and
nests children. The All view shown in the reported screenshot still renders a
flat chronological list, so the relationship is lost there.

Codex subagent rows can also have empty `title` and `first_user_message`
columns because the delegated task payload is encrypted. Their clear-text
`agent_path`, such as `/root/ccc_588_review`, still identifies the task, but
CCC does not currently select or use it.

## Chosen Design

### Backend task labels

Add `agent_path` to the Codex thread query and derive a display label from its
final path segment. If the column is unavailable, extract the same value from
the JSON `source.subagent.thread_spawn.agent_path` metadata.

Humanize stable task identifiers conservatively:

- `ccc_588_review` becomes `CCC-588 review`.
- `trash_fix_review` becomes `Trash fix review`.
- Empty, root-only, or unusable paths produce no task label.

Codex row naming precedence becomes:

1. User rename.
2. Codex title.
3. First user message.
4. Derived subagent task label.
5. Generated `agent_nickname` as a last-resort compatibility fallback.

Expose the derived label additively on the row for inspection and reuse. Apply
the same fallback when resolving group-chat participant names.

### All-view hierarchy

Build a parent/child tree from the rows already present in the selected All
lane. Preserve the existing root ordering, then emit each descendant directly
after its parent with an increasing depth. Orphans, filtered-out parents, and
cycles remain visible as top-level rows.

A subagent without an explicit lane override inherits its visible parent's
lane recursively. This keeps WatchTower reviewers under the WatchTower parent
in Workers and ordinary coding subagents under their parent in Coding. An
explicit user lane override wins and intentionally separates that child.

Use one generic tree helper for both Active and All views so their hierarchy
rules cannot drift. Child-row indentation applies in both containers, without
changing search results, which remain flat and relevance ordered.

In project-grouped All mode, tree nesting is computed within each project
group. A parent filtered into another project leaves the child visible at the
top level of its own group.

## Alternatives Considered

### UI-only indentation

Nest only rows whose parent already happens to be in the same lane, leaving
Codex names and cross-lane children unchanged. This is small but does not fix
the reported WatchTower reviewer case or the opaque names.

### Mutate Codex's thread titles

Write task labels back into Codex's SQLite store. This could improve other
Codex surfaces, but it mutates an external application's state and duplicates
Codex ownership. CCC only needs a display overlay, so this is unnecessary and
riskier.

### Backend lineage bundles

Return pre-nested session objects from the API. This would make the response
shape more complex and duplicate view-specific filtering and ordering in the
server. The existing flat API plus `parent_session_id` is already the stable
contract and should remain so.

## Failure Handling

- Missing or malformed `source` JSON is ignored.
- Missing `agent_path` falls through to the generated nickname.
- Missing parents do not hide children.
- Self-parent links and cycles do not recurse indefinitely.
- User renames and explicit lane overrides always remain authoritative.

## Verification

- Backend unit tests cover direct `agent_path`, JSON-source fallback,
  humanization, precedence, and nickname fallback.
- Static UI tests require All-view lane inheritance, tree rendering, and child
  indentation while preserving flat search behavior.
- The full smoke suite must pass.
- A Puppeteer snapshot of the running dashboard must show representative
  Codex children immediately below and indented under their parent with useful
  task labels.
