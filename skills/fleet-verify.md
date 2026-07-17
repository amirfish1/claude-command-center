---
name: fleet-verify
description: Use after a UI or user-visible change and before you claim it works — it spawns ONE CCC verification lane that drives a real browser (gstack browse, or CCC's puppeteer snapshot as fallback) against the running app, checks the specific thing you changed, and reports a visual/behavioral verdict with a screenshot. Independent of your context; catches what a diff read and a unit test cannot.
allowed-tools: Bash
---

`/code-review` reads the diff. `pair-verify` runs a bug at two refs. Neither
opens the app and *looks*. Fleet-verify spawns one CCC lane that drives a
headless browser against the running dashboard (or your app), verifies the exact
change you describe, captures a screenshot as evidence, and reports a verdict.
It pairs CCC's spawn/report_to mechanics (`ccc-orchestration`) with gstack
`browse` (headless Chromium QA).

## Cost

**1 spawned session.** A real billed session on the kanban — state the count
before spawning. Skip it for pure backend/logic changes with no rendered surface
(there is nothing to look at); use a test or `pair-verify` instead.

## Preconditions — gather these BEFORE spawning

The lane can only check what you can point it at. Have all three:

- **(a) A running, reachable app + URL.** For CCC itself that is
  `http://127.0.0.1:8090`. For another app, its dev-server URL. If nothing is
  running, start it first (or say so in the prompt so the lane starts it).
- **(b) The exact thing to verify** — "the skills chip shows a count and the
  status rail lists each subagent", not "check the UI". Name the selector,
  text, or state you expect.
- **(c) The repo path.**

## Setup

```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_URL="http://127.0.0.1:8090"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_DOWN=1
```

- Run every CCC curl with the **network sandbox disabled** (loopback is blocked
  in the Bash sandbox and fails spuriously even when CCC is up).
- **URL-encode `repo_path`** in query strings.

## Spawning the verifier

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Independent visual verification. Drive a headless browser and confirm a specific change. 1) Use gstack browse (the `browse` skill): `$B goto <url>`, then `$B text`, `$B js \"<expr>\"`, `$B is visible @<ref>` to check state, and `$B screenshot /tmp/fleet-verify-<slug>.png` for evidence. If the gstack `browse` binary is unavailable, fall back to CCC's `node snapshot.js` (writes snapshot.png) or the chrome-devtools MCP. 2) Verify EXACTLY this: <the specific thing — selector/text/state you expect>. 3) Report VERDICT (VERIFIED / NOT-VISIBLE / WRONG-STATE / APP-DOWN), the commands you ran, and the screenshot path as evidence. Be literal: report what you actually see, not what the change intended.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

- Omit `"model"` to use the server spawn default; if you pass `"model"`, pass
  `"engine"` too (model ids are validated per engine).
- CCC appends the return-address footer (`report_to`), so the verdict injects
  back to you when the lane finishes.

## Waiting for the report

After spawning, **end your turn.** Tell the user a lane is verifying and give its
session id. The verdict arrives by injection — do **not** poll or sleep-loop. If
it never arrives, check `GET /api/sessions/spawned` for whether the lane is alive.

## Interpreting the verdict

- **VERIFIED** — the change renders/behaves as described, with a screenshot to
  prove it. Ship it.
- **NOT-VISIBLE** — the lane could not find what you described. Either the change
  did not take, or you pointed it at the wrong place. Investigate before claiming.
- **WRONG-STATE** — it rendered, but not as you expected. Read the evidence.
- **APP-DOWN** — nothing was running to verify. Start the app and re-run.

## Dry run

If the arguments contain `dry-run`, print the exact payload — session count (1),
the filled-in verifier prompt with URL and the specific check, and the target
repo — and POST nothing.

## Honest fallbacks

- **CCC down (`CCC_DOWN=1`):** run gstack `browse` (or `node snapshot.js`)
  yourself against the running app, and in your claim say verification was
  **self-run** — you know what the change was meant to do, so you are a weaker
  skeptic than a fresh lane. Never pretend the spawn ran.
- **No browser tooling at all:** say so plainly and fall back to a functional
  check (curl the endpoint, assert the JSON/DOM string). A string assertion is
  not the same as *seeing* it render — flag the gap.
