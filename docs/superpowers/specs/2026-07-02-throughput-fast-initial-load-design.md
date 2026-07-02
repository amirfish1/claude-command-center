# Throughput Fast Initial Load Design

## Goal

The throughput page must render its initial dashboard UI, including metric cards
and the graph area, in under 200 ms. Expensive transcript scans may continue
after that first render and update the page incrementally when fresh data is
ready.

## Current Behavior

`static/throughput.html` boots by fetching
`/api/throughput?session_id=all_7_days`. That endpoint scans recent
conversations, resolves transcript paths, loads or parses per-file turn data,
deduplicates turns, summarizes buckets, and serializes the aggregate payload.
On a representative local run, the default 7-day aggregate took about 30 seconds
for roughly 22,000 turns. That path cannot satisfy a 200 ms initial render.

## Design

Add a cache-only aggregate snapshot path for the default throughput view. The
fast path must never trigger conversation discovery or transcript parsing. It
should return the freshest persisted snapshot for `all_7_days` when one exists,
or a small valid empty dashboard payload when it does not. The response includes
metadata that tells the frontend whether the payload is fresh, stale, or empty.

The existing full `/api/throughput?session_id=all_7_days` endpoint remains the
authoritative refresh path. When it computes an aggregate successfully, the
server persists that aggregate snapshot to disk so a later page load, including
after daemon restart, has immediate initial data.

The frontend should render the fast snapshot first, then start the full
aggregate refresh in the background. If the full response arrives with newer
data, the dashboard updates in place. The initial graph must be visible even
when the fast snapshot is empty; in that case it shows the existing no-data chart
state rather than a blocking loader.

## API Shape

Add `GET /api/throughput/initial?session_id=all_7_days`.

Successful response shape matches the existing throughput payload enough for
`renderDashboard()` to consume:

```json
{
  "ok": true,
  "session_id": "all_7_days",
  "scope": {
    "aggregate": true,
    "range": "Last 7 Days",
    "cutoff_epoch": 1782398471.0,
    "total_turns": 0
  },
  "summary": {
    "total_turns": 0,
    "hourly": [],
    "daily": [],
    "per_model": []
  },
  "turns": [],
  "snapshot": {
    "state": "empty",
    "cached": false,
    "stale": true,
    "generated_at": null
  }
}
```

When a persisted snapshot exists, `snapshot.state` is `"cached"`, `cached` is
`true`, and `generated_at` is the persisted timestamp.

## Data Flow

1. Page boot calls `/api/throughput/initial?session_id=all_7_days`.
2. The frontend renders the returned payload immediately.
3. The frontend starts the full `/api/throughput?session_id=all_7_days` request
   without showing the blocking initial loader.
4. The server computes the full aggregate as it does today.
5. The server writes the successful aggregate payload to a small JSON snapshot
   file in the existing throughput cache directory.
6. The frontend replaces the initial snapshot view with the fresh full payload.

## Error Handling

If the initial endpoint cannot read the persisted snapshot, it returns a valid
empty payload with `snapshot.state = "empty"` and HTTP 200. A corrupt snapshot
must not break page load. The full refresh path keeps existing error behavior.

If the full refresh fails after the initial render, the dashboard keeps showing
the initial snapshot and changes status text to indicate the refresh failed.

## Testing

Add server-level tests proving that the initial endpoint helper does not call
`find_all_conversations()` or parse transcripts when no aggregate cache exists.
Add tests proving a full aggregate compute persists a snapshot and the initial
helper can read it back after clearing in-memory caches.

Add a static UI test proving the throughput boot code fetches
`/api/throughput/initial` before the expensive aggregate endpoint and renders the
initial payload before starting the background refresh.

Manual verification should use the repo's Puppeteer harness and a browser timing
probe against `http://127.0.0.1:8090/throughput.html`. Playwright is not a CCC
dependency for this repo.
