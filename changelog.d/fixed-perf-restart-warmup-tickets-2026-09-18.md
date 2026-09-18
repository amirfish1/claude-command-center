Perf telemetry: `archive_load`/`conv_open` samples recorded in the first 5
minutes after a server restart are now flagged `warmup` and excluded from
breach-pattern ticket filing — post-boot first paints ride startup
contention and were self-filing spurious "[perf] slow archive load" tickets.
Also, a transiently broken `watchtower` checkout (e.g. mid-merge conflict
markers raising SyntaxError) no longer kills the `--archive-refresh-worker`
subprocess at import; the optional `watchtower.workers`/`watchtower.config`
imports degrade instead.
