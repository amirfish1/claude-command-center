# Product-story assets — gallery index

Captures of the real CCC UI (v5.6.0) running on the seeded, privacy-safe demo
fixtures (`docs/demo/api` — 16 fake sessions, 3 fake repos, fake GitHub
issues; all data invented, no real user content). Captured 2026-07-10 with
`scripts/story-capture/` (see its README for how to reproduce or add assets).

Videos are H.264 MP4 (yuv420p, faststart, no audio), 30 fps, 1440x900 unless
noted, each with a matching poster in `video/posters/`. Screenshots are 2x
(retina) PNGs.

## Screenshots (`shots/`)

| ID | File | What it shows | Dimensions | Alt text |
|---|---|---|---|---|
| S-OVR | `shots/S-OVR.png` | Full dashboard overview: kanban board (GH Issues, Needs Attention, Icebox, In Progress columns with seeded cards) beside an open session transcript with composer, branch chip, and health footer. | 2880x1800 (2x) | CCC dashboard in dark mode: a kanban board of coding-agent sessions grouped by stage, next to an open session transcript with a reply composer. |
| S-F5a | `shots/S-F5a.png` | Work queue board (synthetic-HOME server): three queues with health states (STUCK, WAITING, CLEAR) above the ticket list with priorities and ages. | 938x966 | Work queue panel: three named queues with health badges, above a list of open tickets with priority pills and last-activity times. |
| S-F5b | `shots/S-F5b.png` | Queue health strip (synthetic-HOME server): WIDGETS flagged STUCK with drain on, BILLING WAITING, CHECKOUT CLEAR. | 894x196 | Compact queue health strip showing one queue flagged stuck, one waiting, one clear. |
| S-F2c | `shots/S-F2c.png` | Usage and rate-limit windows (synthetic-HOME server): Claude weekly 63% and Codex weekly 34% with pace projections and reset times. | 2728x380 | Usage strip showing Claude and Codex weekly limit bars with projected usage at reset. |
| S-F2d | `shots/S-F2d.png` | Model Advisor modal (synthetic-HOME server): live up/downgrade recommendations with scores and one-click Switch now, above the scanned-sessions verdict table. | 2880x1800 (2x) | Model Advisor dialog listing per-session model recommendations with scores, switch buttons, and a verdict table. |
| S-F2e | `shots/S-F2e.png` | Token Throughput Analyzer (synthetic-HOME server): quota used, daily burn, cache savings, and the 3-hour cache-adjusted burn chart with weekly projection. | 2880x1800 (2x) | Throughput dashboard with usage stats tiles and a bar chart of token burn across the week with a projected quota line. |

## Videos (`video/`, posters in `video/posters/`)

| ID | File | What it shows | Duration / dimensions / size | Alt text |
|---|---|---|---|---|
| V-01 (hero) | `V-01-fleet-scan.mp4` | Scan the fleet: list view with Claude/Codex/Gemini engine icons, live status pills, and the project tree; scene-cut to the same fleet as a kanban board; pan across columns and open a live session — transcript and composer load. | 21.7s · 1440x900 · 885 KB | Cursor sweeps a list of live coding-agent sessions, then the same sessions appear as a kanban board; a card is opened and its transcript loads. |
| V-03 | `V-03-attention.mp4` | Spot the session waiting on you: pan to the Waiting column, hover the NEEDS APPROVAL card with its inline "Send to terminal" answer box, open it. | 11.2s · 1440x900 · 533 KB | A kanban card labeled Needs Approval is hovered and opened; the session transcript pane appears. |
| V-06 | `V-06-kanban-drag.mp4` | Kanban drag: the parked Icebox card is dragged into In Progress (client-side move, persists), then a session card is opened. | 13.7s · 1440x900 · 545 KB | A card is dragged from the Icebox column and dropped into In Progress; a session is then opened. |
| V-07 | `V-07-flow-canvas.mp4` | Flow canvas: expand repo clusters, Organize lays out the session nodes with edges, drag a node to a new spot, zoom in. | 19.5s · 1440x900 · 686 KB | A node-and-edge canvas of repos and sessions is auto-organized; one node is dragged and the view zooms in. |
| V-08 | `V-08-split-pane.mp4` | Split pane: with one transcript open, a second session row is dragged onto the conversation's right edge — two transcripts side by side. | 14.1s · 1440x900 · 500 KB | A session row is dragged onto the right edge of an open conversation, splitting the view into two transcripts. |
| V-09 | `V-09-search.mp4` | Search: typing "stripe" filters 24 rows down to the two matching sessions; the right one opens. | 10.6s · 1440x900 · 303 KB | Typing in the search box filters the session list; a filtered result is clicked and its transcript opens. |
| V-14 | `V-14-issue-to-session.mp4` | Issue to session: hover a GH issue card, click "Start session" — the optimistic spawn UI appears (new "spawning…" card in In Progress and a fresh session pane). Demo-mode note: the POST is stubbed, so no real agent starts; the clip shows the UI's genuine immediate response. | 11.0s · 1440x900 · 282 KB | Clicking Start session on a GitHub issue card creates a new spawning session card and opens an empty session pane. |
| V-15 | `V-15-mobile.mp4` | Mobile walkthrough (390x844): scroll the session list, open a session full-screen, read the transcript, tap Back to the list. | 14.2s · 390x844 · 235 KB | On a phone-sized screen, a session list is scrolled, a session opens full screen, and the Back button returns to the list. |

## Skipped flows (do not fake)

- **V-04 context meter** — the demo fixtures carry no context/token fields, so
  no context meters render; nothing truthful to show.
- **V-05 pinning** — pin state is server-derived; the demo stub accepts the
  POST but the next poll reverts it, so there is no persistent visible outcome.
- **V-11 group chat** — the group-chat fixtures are empty
  (`docs/demo/api/group-chats/active.json` = `{"chats": []}`); no chat to open.
  Unblock by seeding a group-chat fixture.
