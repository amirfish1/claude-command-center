# Attention Feed Archive Cache Design

## Problem

The cross-repository attention endpoint calls `find_all_conversations()` for
every request even though the dashboard already maintains a coalesced archive
snapshot containing the same row fields. On a large local corpus, one attention
request reparses thousands of transcript tails and sustains high CPU long after
the dashboard opens.

## Design

`compute_attention_feed()` will obtain its input rows from
`_archive_all_rows_cached()` with the minimal archive options: no PR-state,
effective-state, worktree-dirty, or PR hydration. This is the same lightweight
cache key used by the base archive view, so simultaneous archive and attention
requests share the existing snapshot, build lock, and stale-while-revalidate
refresh.

The classifier, recency rules, bounded turn enrichment, sorting, and JSON
response remain unchanged. A cold process may still perform one archive build;
subsequent callers reuse that build rather than starting an independent scan.

The existing uncommitted change that routes
`/api/conversations/all?stale_ok=1` through `_archive_serve_rows()` is compatible
with this design and must be preserved. Both paths converge on the same serve
cache instead of maintaining separate caches.

## Error Handling

Attention currently treats archive discovery failures as an empty feed. That
behavior remains unchanged: an exception from `_archive_all_rows_cached()` is
caught and classification proceeds with no rows.

## Tests

Add a regression test that supplies controlled rows through
`_archive_all_rows_cached()`, makes a direct `find_all_conversations()` call fail
the test, and verifies the existing attention output. Update attention tests
that currently replace `find_all_conversations()` so they inject rows at the new
cache boundary.

Run the focused attention and performance suites, then the repository smoke
suite. The change is successful when the tests pass and a direct attention
request no longer starts an independent all-corpus scan.

## Scope

This change does not alter the public API, add a new cache, or change frontend
polling. Frontend request suppression and WatchTower negative caching remain
separate follow-up optimizations because they are not required to remove the
dominant CPU path.
