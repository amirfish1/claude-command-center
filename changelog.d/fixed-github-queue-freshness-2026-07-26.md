**A new GitHub issue reaches the queue board in ~20s instead of needing a
browser reload.** GitHub-backed queues keep their tickets in GitHub Issues, so
they never touch the local ticket store the queue-events SSE watches. The cover
for that was a blind 60s beat — but a beat only tells the client to refetch, and
`/api/queue/list` is stale-while-revalidate, so the first refetch after a remote
change served the old list and merely scheduled a rebuild. The new issue did not
surface until the next beat, putting it up to ~2 minutes away and making a
manual reload look like the only thing that worked.

The stream now polls deliberately instead of beating blindly: while a board has
the SSE open, a watcher forces a remote refresh every 20s, writes the result
into the memo `/api/queue/list` serves, and bumps a version counter only when
the ticket set really changed. The SSE folds that counter into its change
detection, so a push now means "something is new and it is already warm" — the
client's refetch returns fresh rows on the first try. Cost is bounded by
subscribers, not tabs: one `gh issue list` per interval while a board is open,
and none at all when every board is closed.
