---
name: throughput
description: Use when the user asks to open/show the CCC throughput (token) dashboard, or asks how many tokens they've used — overall/so far, in the last 7 days, or for a specific session (e.g. "how many tokens is session ffbbeef8", "open throughput dashboard", "what's my token burn"). Answers token/cost questions from CCC's throughput API and links the page.
---

# Throughput

CCC's **throughput dashboard** (`static/throughput.html`, served at
`/throughput.html`) charts token usage, TPM/TPS, cost, and cache-hit ratio —
for a 7-day aggregate or per session. There is **no dedicated CLI**; the numbers
come from the dashboard's HTTP API (`/api/throughput`).

This skill wraps both behind one helper: `throughput.sh` (next to this file).
It resolves the running dashboard's port, and — because the API needs a
*full* session id — resolves an 8-char prefix like `ffbbeef8` to the full uuid
first.

Run it from the skill directory, e.g.:

```bash
.claude/skills/throughput/throughput.sh <subcommand>
```

## Requests → what to do

**"open throughput dashboard" / "show me throughput" / "the token page"**
Run `throughput.sh url` and give the user the `throughput.html` link (it's
clickable in the terminal). Only actually launch a browser if they say "open it"
*and* a browser opener is available — `xdg-open <url>` (Linux) or `open <url>`
(macOS); otherwise just hand over the URL. Don't assume a GUI.

**"how many tokens have I used / so far / this week" / "what's my burn"**
Run `throughput.sh total`. This is the **Last-7-days aggregate** across all
Claude sessions (CCC only retains a 7-day throughput window — say "last 7 days",
not "all time"). Relay the total, the input/output split, cache-hit %, and cost.

**"how many tokens is session ffbbeef8" / "tokens for <id>"**
Run `throughput.sh session <id-or-prefix>`. A short prefix is fine — the helper
resolves it. If it prints "No session matches", the id isn't in the current
conversation list (wrong prefix, or older than the 7-day window) — tell the user
that rather than guessing.

## Engines

Default engine is `claude`. For Codex/other engines pass it as the last arg:
`throughput.sh total codex`, `throughput.sh session <id> codex`. If the user
doesn't name an engine, use `claude`.

## Notes

- **Base URL / port:** the helper auto-detects the live `server.py` port (this
  isn't always 8090). Override with `CCC_BASE_URL=http://host:port` if CCC runs
  elsewhere (e.g. over Tailscale).
- **Dashboard must be running.** If every probe fails, the CCC dashboard isn't
  up — say so; don't fabricate numbers.
- Numbers are read-only. This skill never writes or restarts anything, so the
  restart matrix is **N/N/N**.
