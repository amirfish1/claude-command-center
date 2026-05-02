**Live block-level streaming** for CCC-spawned headless sessions. The
conv pane now tails the spawn log's stream-json events as they happen
and renders prose blocks + tool calls in a transient "streaming"
bubble at the bottom, instead of waiting for the JSONL transcript's
end-of-turn write. A green pulsing `live` badge next to the Launch
button indicates the spawn-log tail is active. New endpoints:
`GET /api/session/<sid>/spawn-info` (capability check) and
`GET /api/session/<sid>/spawn-stream` (SSE). Externally launched and
pkood sessions are unaffected — they still render from JSONL only.
