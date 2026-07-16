# Product-story finish report — 2026-07-16 (list-view correction)

Lane **W6-goal1-listview** of the Fable-5 batch. Finishes the Product Story
visual proof and corrects it to the new direction: **the LIST view is the hero,
not the kanban board.** Kanban stays a documented feature (F3, pain row 12) but
is no longer the primary surface in hero/overview/attention captures.

All captures ran against the seeded, privacy-safe demo fixtures
(`docs/demo/api/*`, 16 fake sessions, 3 fake repos) via the existing harness
(`scripts/story-capture/`). No real user data in any pixel.

---

## 1. Asset audit (pre-existing assets, classified)

Classification key: **list-safe** (already list view / view-neutral, kept as-is)
· **Kanban-hero** (showed kanban as the primary surface → re-captured in list)
· **Kanban-feature** (an explicit kanban-only feature demo, gallery-only, not
site-referenced → kept and labelled) · **missing** (site-referenced but no file
on disk → created).

### Screenshots

| Asset | Site-ref? | Before | Class | Action |
|---|---|---|---|---|
| S-OVR | yes (hero + overview) | kanban board beside transcript | **Kanban-hero** | **Re-captured LIST** (`overview-list.js`) — fleet list + engine badges + project tree + open transcript |
| S-F2a | yes | *missing* | missing | **Created LIST** (`attention-list-shot.js`) — needs-approval row centered + live transcript |
| S-F2c | no | usage/rate-limit windows (synthetic HOME) | list-safe | kept |
| S-F2d | no | model advisor (synthetic HOME) | list-safe | kept |
| S-F2e | no | throughput analyzer (synthetic HOME) | list-safe | kept |
| S-F3d | yes | *missing* | missing | **Created** (`shots-flow.js`, fixed op-toast cleanup) — Flow canvas, view-neutral |
| S-F4b | yes | *missing* (V-11 blocker: empty fixture) | missing | **Created** (`group-chat-shot.js`) — group chat thread, 3 agents; seeded fixtures |
| S-F5a | yes | work queue (synthetic HOME) | list-safe | kept |
| S-F5b | no | queue health strip (synthetic HOME) | list-safe | kept |
| S-F6a | yes | *missing* | missing | **Created** (`handoff-shot.js`) — continue-on-another-machine dialog; seeded peers |
| M-01 | yes | *missing* (mobile dir empty) | missing | **Created LIST** (`mobile-list-shot.js`) — 390×844 fleet list |

### Videos

| Asset | Site-ref? | Before | Class | Action |
|---|---|---|---|---|
| V-01 (hero) | yes | list scan → **kanban scene cut** → open card | **Kanban-hero** | **Re-captured LIST-primary** (`fleet-scan-list.js`) — single-scene list scan, no kanban; poster shows list + open transcript |
| V-03 | no | board view, Waiting column | **Kanban-hero** | **Re-captured LIST** (`attention-list.js`) — list scan to needs-approval row, open to answer |
| V-06 | no | kanban drag between columns | **Kanban-feature** | kept — legitimate kanban-states feature demo (pain row 12); gallery-only, not site-referenced, not the hero |
| V-07 | yes | Flow canvas | list-safe (Flow) | kept |
| V-08 | no | split pane (list view) | list-safe | kept |
| V-09 | no | search (list view) | list-safe | kept |
| V-14 | no | issue board → spawn | board (issue board) | kept — the GitHub issue board is a distinct surface (pain row 26); gallery-only, not site-referenced |
| V-15 | no | mobile walkthrough | list-safe | kept |
| **V-16** | no | — | **new (list)** | **Created** (`project-tree.js`) — project-tree grouping under repos (pain row 13) |
| **V-17** | no | — | **new (list)** | **Created** (`by-objects.js`) — one-click "By objects" regroup (pain row 13) |

**Video count: 10** (8 prior + V-16 + V-17). Target ≥10 met.

---

## 2. What changed

**New/replaced assets (all committed, none pushed):**

- Shots: `S-OVR.png` (re-cut list), `S-F2a.png`, `S-F3d.png`, `S-F4b.png`,
  `S-F6a.png` (new), `mobile/M-01.png` (new).
- Videos: `V-01-fleet-scan.mp4` (re-cut list), `V-03-attention.mp4` (re-cut
  list), `V-16-project-tree.mp4`, `V-17-by-objects.mp4` (new) — each with a
  poster in `video/posters/`.
- New capture flows under `scripts/story-capture/flows/`: `overview-list.js`,
  `attention-list-shot.js`, `attention-list.js`, `mobile-list-shot.js`,
  `group-chat-shot.js`, `handoff-shot.js`, `fleet-scan-list.js`,
  `project-tree.js`, `by-objects.js`; `shots-flow.js` op-toast cleanup fixed.
- New synthetic demo fixtures (fake names only): `group-chats/active.json`
  (was empty — the V-11/S-F4b blocker), `group-chat/read.json`,
  `federation/peers.json`.
- New verifier: `scripts/story-capture/verify-page.js`.

**Naming kept stable** — every `docs/index.html` asset reference resolves to the
same filename; no index.html edits were needed.

---

## 3. Verification evidence (Phase 6, first run)

Ran `scripts/story-capture/verify-page.js` against `docs/index.html` served
headless (Chrome via puppeteer, repo root on `:8877`). Result:

```
imgCount: 8   videoSourceCount: 3   posterCount: 3
fallbackImgs: []        (no <img> fell back to images/demo.png)
brokenImgs: []          (every <img> naturalWidth > 0, complete)
brokenVideos: []        (every <source>/poster HEAD 200)
brokenAnchors: []       (every in-page #anchor resolves)
failedRequests: []      badResponses: []
VERIFY_RESULT: PASS
```

Video/poster HTTP checks: `V-01-fleet-scan.mp4` 200, `V-07-flow-canvas.mp4`
200, poster `V-01.png` 200, poster `V-07.png` 200.

Screenshots captured: `/tmp/w6-verify/page-desktop.png` (1440, full page),
`/tmp/w6-verify/page-mobile.png` (390×844, full page). Both render the full
page with every product-story asset in place; zero missing-asset fallbacks.

Site-referenced assets confirmed present and loading: S-OVR, S-F2a, S-F3d,
S-F4b, S-F5a, S-F6a, M-01 (shots); V-01, V-07 (videos) + posters.

---

## 4. Follow-ups (out of scope, flagged not actioned)

- **Hero fallback still legacy.** The `<video>` hero poster is now the list-view
  `V-01.png`, but its static `.frame-fallback` (`images/ccc-live-session-workspace.png`,
  shown until the video plays / with reduced-motion) is a pre-existing image
  outside `product-story/assets/`. If it reads as kanban-hero, swap it to a
  list-view still (`S-OVR.png` or the V-01 poster) — a one-line asset-ref change
  in `index.html`. Left untouched to stay within the product-story scope.
- **V-06 / V-14** remain kanban/issue-board feature demos in the gallery. They
  are not on the live page and not the hero; kept as truthful feature proof. If
  marketing wants zero kanban anywhere, they can be dropped from the gallery
  README.
- Per-session context meters (S-F2b / V-04) still cannot be shown — the demo
  fixtures carry no context/token fields. Unchanged.
