---
name: testing-ccc-dashboard
description: Runtime-test the CCC dashboard on Linux using server discovery, isolated verification instances, Puppeteer screenshots, and hook stdin checks.
---

# Runtime-testing CCC (`claude-command-center`)

The dashboard runtime is stdlib-only; browser verification also needs the repo's
Puppeteer dependency and an available Chrome/Chromium browser.

## Find the running dashboard

Prefer the existing server when testing its current behavior. Use an explicit
URL supplied for the task, otherwise `$CCC_SERVER`, otherwise
`~/.claude/command-center/port.txt`. The file contains a **full base URL**, not
just a port. This matches `ccc --server URL` discovery; do not assume a machine
uses the default port, 8090, or any particular alternative.

```bash
ccc_test_url="${CCC_SERVER:-$(cat ~/.claude/command-center/port.txt)}"
curl --fail --silent --show-error --max-time 10 "$ccc_test_url/api/health"
SNAPSHOT_URL="$ccc_test_url" SNAPSHOT_OUT=/tmp/ccc-dashboard.png node snapshot.js
```

If discovery is missing or the URL does not respond, inspect listeners and their
owning processes (`ss -ltnp`) before selecting a URL or starting another server.
A listener alone does not prove that it is CCC. `snapshot.js` probes the port
file and falls back to the default if it is unavailable; set `SNAPSHOT_URL`
explicitly when the server identity matters.

## Run changed code in a verification instance

Choose an unused local port after inspecting listeners. For example, **only if
18190 is free**:

```bash
PORT=18190 CCC_EPHEMERAL=1 CCC_BIND_HOST=127.0.0.1 python3 server.py \
  >/tmp/ccc-verification.out 2>/tmp/ccc-verification.err
```

- `CCC_EPHEMERAL=1` bypasses the same-repo duplicate guard and skips claiming the
  shared `port.txt`. It also forces loopback binding. The same guard recognizes
  worktrees by their shared git common directory.
- `CCC_ALLOW_DUPLICATE_REPO=1` also bypasses the guard, but does **not** by itself
  protect the shared discovery file. Prefer ephemeral mode for verification.
- Ephemeral mode is not full data isolation: the server can still access the
  user's state. Keep runtime checks read-only unless writes are part of the task.
  Fresh-install or destructive fault injection needs a disposable environment.
- Wait for the chosen URL's `/api/health` with a bounded timeout and inspect
  startup logs on failure. Startup duration varies with local state and load.
- Do not kill a process because its port is occupied. For the managed dashboard,
  use `ccc restart dashboard` (drain-aware); see `docs/agent-rules.md`. For an
  ad-hoc verification process you launched, stop only that process gracefully.

## Browser verification

Use `node snapshot.js` or an ad-hoc Puppeteer script importing
`require-puppeteer.js` and `puppeteer-browser-config.js`. See `snapshot.js` for
URL, output, Chrome, timeout, and localStorage options. Use the repo's Puppeteer
harness, not Playwright or the Codex in-app browser.

- A fresh browser profile has no saved view/layout preferences. Seed them with
  `SNAPSHOT_LOCALSTORAGE` when reproducing a stateful UI. First-run prompts depend
  on saved state; inspect the actual page instead of assuming a fixed sequence.
- CCC polls continuously: prefer navigation with `load`/`domcontentloaded`, then
  wait for the specific row/control or condition. Avoid a navigation wait for
  global network idleness; it can stall behind ongoing polling.
- Puppeteer 25 has no `page.waitForTimeout()` or `Locator.first()`. Use condition
  waits and DOM selectors; for a necessary short delay use a Promise timer.
- Inspect screenshots directly when vision is available. Follow `AGENTS.md` for
  the fallback only when direct image inspection is unavailable.
- Check desktop and phone layouts when the change affects both. Keep checks
  proportionate to the task, and never send replies or execute session options
  merely to inspect a reported row.

## Failure and hook checks

Use a disposable copy/environment for deliberate serialization, permission, or
fresh-install failures. Check the current implementation and focused tests for
the failure path before injecting it; do not rely on historical error strings,
removed logging helpers, or assumed harmless console errors.

Malformed hook input should return promptly without prompting or changing a real
session. A focused stdin check is:

```bash
printf '%s\n' '{bad json' | python3 hooks/stop.py
```

Check exit status and stderr against the current hook's contract. Other hooks
include `notification.py`, `post-tool-use.py`, and `pre-tool-use.py`; inspect the
specific handler before asserting what diagnostic it should emit.
