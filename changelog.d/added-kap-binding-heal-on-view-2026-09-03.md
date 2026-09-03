Opening a Kimi conversation in CCC now heals a stale `acp:<sid>` runtime
binding back to `local` in the background (threaded, throttled per session,
skipped while a turn is running). Previously the rebind only happened when a
prompt was driven over kap, so a session you only ever *viewed* stayed
degraded for the TUI and `kimi web` — no Bash/Read/Write/Edit until something
drove it.
