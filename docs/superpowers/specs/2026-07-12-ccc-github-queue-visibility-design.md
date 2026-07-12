# CCC GitHub Queue Visibility Design

## Problem

CCC's queue panel lists tickets through `/api/ux-fixes/list`. When the
WatchTower Python package is unavailable, CCC uses its local file-backed queue
reader. That reader can see configured GitHub queue names, but it cannot fetch
their issues. Consequently, `wt ls -q CCC-GH` returns GitHub issues while CCC
shows an empty `CCC-GH` queue.

## Design

Keep the existing local reader for file-backed queues. For every configured
queue whose backend is `github`, CCC invokes the existing WatchTower CLI as an
argument-vector subprocess:

```text
wt ls -q <queue> --status active --limit 0 --json
```

CCC validates the returned JSON as a list, excludes any item whose status is
`closed`, and merges the remaining items with the local queue items. Ticket
references are the merge key so an item cannot appear twice.

The merged list becomes the shared source for `/api/ux-fixes/list` and queue
health counts. The frontend needs no new rendering behavior; GitHub items use
the fields and existing non-runnable action already supported by the queue UI.

## Performance and failure handling

GitHub-backed results use a short in-process TTL cache. Concurrent or repeated
dashboard requests within the TTL do not invoke the CLI again.

CCC never uses a shell to construct the command. Queue names come from trusted
WatchTower configuration and are passed as individual subprocess arguments.
If `wt` is missing, times out, exits unsuccessfully, or returns invalid JSON,
CCC still returns local queue items. A failed GitHub refresh must not break the
entire queue panel.

## Acceptance criteria

- All non-closed issues returned by `wt ls -q CCC-GH` appear in CCC's queue
  panel when the `CCC-GH` scope or all-queues scope is selected.
- Closed GitHub issues do not appear.
- Local file-backed queue behavior remains unchanged.
- CLI failure leaves local queues usable.
- Queue health counts use the same merged inventory shown by the list API.

## Tests

Unit tests inject subprocess results and verify merging, deduplication,
closed-item exclusion, caching, and graceful failure. The existing smoke suite
then verifies the server still imports and the queue API remains intact.
