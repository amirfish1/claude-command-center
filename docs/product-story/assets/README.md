# Product-story assets — gallery index

Captures of the real CCC UI running on the seeded, privacy-safe demo fixtures
(`docs/demo/api` — 16 fake sessions, 3 fake repos, fake GitHub issues; all data
invented, no real user content). Captured 2026-07-10 with
`scripts/story-capture/`, then **corrected 2026-07-16 to the LIST view as the
hero** (the kanban board is still a documented feature but is no longer the
primary surface in the hero/overview/attention captures). See the harness README
for how to reproduce or add assets.

Videos are H.264 MP4 (yuv420p, faststart, no audio), 30 fps, 1440x900 unless
noted, each with a matching poster in `video/posters/`. Screenshots are 2x
(retina) PNGs.

## Screenshots (`shots/`)

| ID | File | What it shows | Dimensions | Alt text |
|---|---|---|---|---|
| S-OVR (hero) | `shots/S-OVR.png` | **List-view hero.** Full dashboard: the fleet in list view — engine badges (Claude / Codex / Gemini), status pills, repo chips, and the project tree — beside an open session transcript with composer, branch chip, and health footer. | 2880x1800 (2x) | CCC dashboard in dark mode: a list of coding-agent sessions with engine and status badges, next to an open session transcript with a reply composer. |
| S-F2a | `shots/S-F2a.png` | **List view.** The needs-attention fleet: the question-waiting session ("Migrate blog to Eleventy", flagged NEEDS APPROVAL) centered in the list beside an open transcript with the answer composer. | 2880x1800 (2x) | A session list with one row flagged Needs approval, next to an open session transcript ready to answer. |
| S-F3d | `shots/S-F3d.png` | Flow canvas: repo clusters (widgets-api, blog-engine) with their session nodes arranged and edges drawn, at a readable zoom. View-neutral. | 2880x1800 (2x) | A node-and-edge canvas of repos and sessions arranged as an organized board. |
| S-F4b | `shots/S-F4b.png` | Group chat "Ship the v5.7 release notes": three agent sessions — Planner, Builder, Reviewer — taking turns in one shared thread with reply-after markers. | 2880x1800 (2x) | A group chat thread where three named agent sessions take turns replying in one conversation. |
| S-F5a | `shots/S-F5a.png` | Work queue board (synthetic-HOME server): three queues with health states (STUCK, WAITING, CLEAR) above the ticket list with priorities and ages. | 938x966 | Work queue panel: three named queues with health badges, above a list of open tickets with priority pills and last-activity times. |
| S-F6a | `shots/S-F6a.png` | Continue-on-another-machine handoff dialog ("Continue on another node") over the list view, with a paired-node destination picker and Preflight. | 2880x1800 (2x) | A handoff dialog to move a session to a paired node, with a destination dropdown and a preflight button. |
| S-F5b | `shots/S-F5b.png` | Queue health strip (synthetic-HOME server): WIDGETS flagged STUCK with drain on, BILLING WAITING, CHECKOUT CLEAR. | 894x196 | Compact queue health strip showing one queue flagged stuck, one waiting, one clear. |
| S-F2c | `shots/S-F2c.png` | Usage and rate-limit windows (synthetic-HOME server): Claude weekly 63% and Codex weekly 34% with pace projections and reset times. | 2728x380 | Usage strip showing Claude and Codex weekly limit bars with projected usage at reset. |
| S-F2d | `shots/S-F2d.png` | Model Advisor modal (synthetic-HOME server): live up/downgrade recommendations with scores and one-click Switch now, above the scanned-sessions verdict table. | 2880x1800 (2x) | Model Advisor dialog listing per-session model recommendations with scores, switch buttons, and a verdict table. |
| S-F2e | `shots/S-F2e.png` | Token Throughput Analyzer (synthetic-HOME server): quota used, daily burn, cache savings, and the 3-hour cache-adjusted burn chart with weekly projection. | 2880x1800 (2x) | Throughput dashboard with usage stats tiles and a bar chart of token burn across the week with a projected quota line. |

## Videos (`video/`, posters in `video/posters/`)

| ID | File | What it shows | Duration / dimensions / size | Alt text |
|---|---|---|---|---|
| V-01 (hero) | `V-01-fleet-scan.mp4` | **List-primary.** Scan the fleet in list view: Claude/Codex/Gemini engine icons, live status pills, repo chips, and the project tree; the cursor sweeps and scrolls the rows, then opens a live session — transcript and composer load. (No kanban scene — list is the hero.) | 14.0s · 1440x900 | Cursor sweeps a list of live coding-agent sessions across engines, then opens one and its transcript loads. |
| V-03 | `V-03-attention.mp4` | **List view.** Spot the session waiting on you: sweep the fleet, scroll to the NEEDS APPROVAL / question-waiting row ("Migrate blog to Eleventy"), then open a session to answer — transcript + composer appear. | 11.4s · 1440x900 | A list row flagged Needs approval is brought into view, then a session is opened with its answer composer. |
| V-06 | `V-06-kanban-drag.mp4` | Kanban drag (explicit kanban-states feature demo, pain row 12): the parked Icebox card is dragged into In Progress (client-side move, persists), then a session card is opened. Gallery-only; not the hero. | 13.7s · 1440x900 · 545 KB | A card is dragged from the Icebox column and dropped into In Progress; a session is then opened. |
| V-07 | `V-07-flow-canvas.mp4` | Flow canvas: expand repo clusters, Organize lays out the session nodes with edges, drag a node to a new spot, zoom in. | 19.5s · 1440x900 · 686 KB | A node-and-edge canvas of repos and sessions is auto-organized; one node is dragged and the view zooms in. |
| V-08 | `V-08-split-pane.mp4` | Split pane: with one transcript open, a second session row is dragged onto the conversation's right edge — two transcripts side by side. | 14.1s · 1440x900 · 500 KB | A session row is dragged onto the right edge of an open conversation, splitting the view into two transcripts. |
| V-09 | `V-09-search.mp4` | Search: typing "stripe" filters 24 rows down to the two matching sessions; the right one opens. | 10.6s · 1440x900 · 303 KB | Typing in the search box filters the session list; a filtered result is clicked and its transcript opens. |
| V-14 | `V-14-issue-to-session.mp4` | Issue to session: hover a GH issue card, click "Start session" — the optimistic spawn UI appears (new "spawning…" card in In Progress and a fresh session pane). Demo-mode note: the POST is stubbed, so no real agent starts; the clip shows the UI's genuine immediate response. | 11.0s · 1440x900 · 282 KB | Clicking Start session on a GitHub issue card creates a new spawning session card and opens an empty session pane. |
| V-15 | `V-15-mobile.mp4` | Mobile walkthrough (390x844): scroll the session list, open a session full-screen, read the transcript, tap Back to the list. | 14.2s · 390x844 · 235 KB | On a phone-sized screen, a session list is scrolled, a session opens full screen, and the Back button returns to the list. |
| V-16 | `V-16-project-tree.mp4` | **List view (new).** Organize work that outgrew a flat list: scroll to the project tree, collapse and re-expand a repo cluster, then pan to the other repo clusters (widgets-api, blog-engine, marketing-site). | 12.6s · 1440x900 | The sidebar project tree groups sessions under their repos; a cluster is collapsed, expanded, and the tree is panned. |
| V-17 | `V-17-by-objects.mp4` | **List view (new).** One click on "By objects" reshapes the flat current-sessions list into repo/object clusters. | 13.6s · 1440x900 | A flat session list is regrouped into labeled repo clusters by a single toggle. |

## Skipped flows (do not fake)

- **V-04 context meter** — the demo fixtures carry no context/token fields, so
  no context meters render; nothing truthful to show.
- **V-05 pinning** — pin state is server-derived; the demo stub accepts the
  POST but the next poll reverts it, so there is no persistent visible outcome.
- **V-11 group chat** — **now unblocked** (2026-07-16): the demo group-chat
  fixtures are seeded (`group-chats/active.json` + `group-chat/read.json`, fake
  names only), so the group-chat thread renders. Captured as the still `S-F4b`;
  a cursor-led V-11 clip can be recorded from `flows/group-chat-shot.js` if a
  video is wanted.
