# Saved launch routing preview

Settings → Session defaults and Queue engines share a read-only context selector
for a standard new local session, an unpinned worker, or any active queue.
“Inspect launch” on an auto-drain queue selects its context. The cards show
engine, model and effort with source explanations, stored queue pins, fallback
candidate order, and whether headroom dispatch can change the result.

This is a snapshot of saved launch arguments, not a launch reservation. It does
not check authentication, provider availability, quota, cooldown or scheduling.
Headroom decisions and failure-driven replacements remain unresolved until
runtime. One-off session overrides, free routing, remote placement and managed
model profiles are separate contexts; worker fallback does not route an ordinary
new session. Running sessions and workers are unaffected.

## Integration contract

`GET /api/settings/queue-engines` retains its existing fields and adds
`launch_preview: {observed_at, plans}`. Each plan has a stable `id` (`session`,
`worker`, or `queue:<name>`), label, `basis`, fields, fallback information, notes
and warnings. Each engine/model/effort field includes `value`, `source`,
`explanation` and `certainty`. An absent override or `auto`/`adaptive` is explicitly
left to the engine; it is not replaced with an invented model or effort.

`basis: consumer` means resolution used the existing session helpers or installed
WatchTower readers and launch predicates. WatchTower must read the same defaults
and queue files. When it is absent, incompatible, or points elsewhere, worker
plans are marked `configuration_only`; the preview does not guess availability
fallbacks. Model aliases, ignored pins, model-policy substitutions, Fable worker
safeguards and incompatible effort removal follow those readers. Codex model
validation failures appear as launch-rejection warnings. The endpoint does not
invoke quota/headroom selection or launch subprocesses.

The request never seeds or migrates defaults and never edits queue configuration.
If defaults or queue data change during resolution it returns HTTP 409 and the
browser replaces its prior result with a retry message. Catalog and model-policy
changes are not covered by that configuration consistency check. Refresh reads a
new snapshot; successful defaults saves dispatch `ccc:spawn-defaults-changed` to
refresh visible cards. A removed queue stays explicitly unavailable until the
user selects a different context.

`ccc_server.launch_preview.snapshot(...)` is the pure projection API. Callers
supply session defaults, raw stored worker defaults, queues and optional consumer
facts; it performs no I/O. `window.cccLaunchPreview` exposes `mount`, `refresh`,
`accept` and `selectQueue` for the existing Settings surfaces. It writes no browser
preferences or configuration.

## Verification

`python3 -m pytest -q tests/test_launch_preview.py` covers pure resolution and
contracts against installed WatchTower consumers using temporary files. Native
consumer cases skip when that optional package is unavailable.

`node --test tests/settings-launch-preview.test.cjs` exercises the actual GET
handler with disposable configuration and the real cards, styles and queue-table
handler at desktop and phone widths, including keyboard inspection, failure
recovery and stale-context handling. This suite skips when WatchTower is absent;
it never sends a mutation request or launches a worker.
