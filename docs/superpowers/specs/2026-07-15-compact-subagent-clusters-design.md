# Compact Subagent Clusters

## Goal

Keep Codex and Claude subagent lineage visible without allowing completed
children to dominate the sidebar.

## Interaction

Any top-level session with descendants becomes a subagent cluster. Its normal
session row gains a disclosure chevron and a summary such as `12 agents · 2
running`. Clusters start collapsed and expansion is remembered per parent
session. Polling never opens a cluster automatically.

When expanded, live, queued, waiting, or blocked descendants render as compact
indented session rows. A completed ancestor of an active descendant remains as
a muted bridge row so multi-level lineage is not lost. Fully completed branches
render as clickable chips in a wrapping `Completed` strip, newest first. Both
rows and chips open the original session.

The compact presentation applies to the non-search Active and All session
trees. Search remains flat and relevance ordered. Trash remains flat. Missing
parents and cycles keep their existing top-level fallback behavior.

## Presentation

Compact active rows keep the engine icon, task label, attention/lifecycle state,
and last-activity age. They suppress secondary metadata that is already
available after opening the session. Completed chips show the task label and
completion age; their tooltip includes the full label and session identifier.

The parent summary counts all descendants and separately counts active ones.
Waiting or blocked descendants use `needs attention` instead of `running` in the
summary so collapsed clusters still surface required user action.

## State and Accessibility

Expanded parent session IDs are stored in local storage. The disclosure is a
real button with `aria-expanded`; Enter and Space work through native button
behavior. Chip buttons are keyboard reachable. Invalid or unavailable local
storage falls back to collapsed without hiding the parent.

## Verification

- Static regression tests require the cluster renderer, persisted disclosure,
  active/bridge classification, completed chip strip, and delegated click
  handlers.
- The full smoke suite and JavaScript syntax check must pass.
- Puppeteer must confirm that a real parent starts collapsed, expands without a
  scroll jump, shows active descendants as compact rows, completed descendants
  as chips, and opens a chip's session.

