# Morning View — Design

**Status:** Draft
**Date:** 2026-04-17
**Author:** Amir Fish + Claude (pairing)
**Scope:** Extension of Claude Command Center (CCC). New `/morning` page, supporting ingestion and session plumbing.

## 1. Problem

Amir's todos, goals, and working context are scattered across 6+ surfaces: Notion (goals page + meeting notes), per-repo files (`TODO.md`, `PARKING_LOT.md`, `<project> updates log.md`), GitHub issues, a free-form Google Doc, Apple Notes, and Wispr Flow voice notes. Opening each one is friction; keeping Notion's goals page up to date is manual labor that doesn't happen; and nothing ties individual tactical items back to the goals they serve.

The first place Amir wants to look each morning is CCC. Therefore: **CCC becomes the single morning landing spot** for goals, strategic priorities, today's tactical queue, and an inbox of LLM-extracted candidates from free-form captures. Notion is retired from the goals role (one-time import); other tools stay authoritative for their own state and are read by CCC, not replaced.

## 2. Goals and non-goals

**Goals:**
- One URL to open each morning that shows what matters and what's in flight.
- Goals + strategies authored *in* CCC, not in Notion. 3–5 top-level goals per quarter; each goal has 3–7 strategies.
- Each strategy has a **persistent Claude session** that Amir and Claude co-author — strategy text, progress, deliverables.
- Auto-filled status ribbon per goal from commits + session activity (replaces the Notion date columns).
- Triage funnel for free-form captures (Google Doc, Apple Notes, Wispr): LLM extracts todo-shaped candidates → Amir promotes or dismisses.
- Per-goal **context library**: meeting notes, emails, and related documents attached to a goal, converted to local markdown for fast in-session access.

**Non-goals:**
- Replacing Todoist, GitHub, Notion (for non-goal pages), Apple Notes, Google Docs, or Wispr as capture surfaces. They keep their roles.
- Writing back to source tools (no "mark TODO.md box from the UI"). Done-state lives where the source lives.
- Mobile authoring UX. Amir has Tailscale for remote read-only access; mobile authoring deferred until pain is felt.
- Multi-user / sharing.

## 3. High-level architecture

New code lives inside the existing `dev/claude-command-center/` repo:

```
dev/claude-command-center/
  server.py             # existing; 1 import + route registration added
  morning.py            # NEW — all morning-view logic
  ingesters/            # NEW — one file per source
    repo_files.py       # TODO.md, PARKING_LOT.md, updates log scanners
    github.py           # gh issue list (already partially in server.py)
    notion_meetings.py  # notion-query-meeting-notes adapter
    gmail_threads.py    # Gmail MCP adapter
    freeform.py         # Google Doc / Apple Notes / Wispr → LLM extraction
    ribbon.py           # per-goal status computation
  static/
    morning/            # NEW
      index.html
      morning.js
      morning.css
```

Runtime state lives in `~/.claude/log-viewer/morning/`, matching CCC's existing sidecar convention:

```
~/.claude/log-viewer/morning/
  goals/
    <slug>/
      goal.md                  # YAML frontmatter + intent body (see §5)
      context/                 # ingested inputs (markdown copies)
        meeting-<date>-<slug>.md
        email-<date>-<slug>.md
        doc-<slug>.md
      attachments.jsonl        # provenance log: source, source_id, fetched_at
  inbox/
    <YYYY-MM-DD>.jsonl         # LLM-extracted candidates from free-form sources
  ribbon-cache.json            # computed ribbon entries per goal per day
  tactical-cache.json          # flattened ingested tactical items (TODO.md, GH, etc.)
  config.json                  # watched repos, Notion workspace info, ingestion cadence
```

Three important properties inherited from CCC:
1. **Human-readable, hand-editable.** Every file is markdown or JSON.
2. **Stateless request handling.** Each HTTP request re-scans sidecars. No background workers except an optional ingestion cron.
3. **Transcript-derived, not duplicated.** Anything that can be computed from Claude Code's own `~/.claude/projects/*.jsonl` transcripts (files touched, commits made, session summaries) is computed, not stored.

## 4. UI

Two new views, both under the existing CCC server on port 8090.

### 4.1 `/morning` — the daily landing page

Top-to-bottom rows:

1. **Nav bar** — tabs for Morning / Kanban / Sessions. Morning is the default landing.
2. **Goals row** — one card per active goal. Each shows: life-area label, goal name, auto-filled ribbon line for today ("3 commits · spec landed · Eran aligned"). Click → goal-detail view (§4.2).
3. **This-week / strategic row** — priority P0/P1 rows from goals' strategies + Notion-legacy strategic rows during migration. Each row shows priority chip, goal chip, text, source, age.
4. **Today / tactical row** — flattened aggregation of TODO.md checkboxes, open GitHub issues, PARKING_LOT entries. Source-tagged, optionally strategy-tagged.
5. **Inbox / triage row** — LLM-extracted candidates from free-form sources; each has promote / dismiss actions.
6. **Footer** — last-refreshed timestamp + "Scan now" button to trigger `/api/ingest/run`.

### 4.2 `/morning/goals/<slug>` — goal detail

Two-column layout:

**Left column (1.4fr):**
- **Header** — life-area chip, goal name, "Back to morning" / "Edit intent" buttons.
- **Intent panel** — rendered markdown from `goal.md` body.
- **Strategies panel** — one row per strategy with:
  - Session-state dot: **alive** (green) / **dormant** (orange) / **never** (gray).
  - Text + status + last-activity stats (commits, files touched in the session).
  - **Launch button** — behavior branches on session state:
    - alive in terminal → `inject_input_via_keystroke` with task-specific framing message.
    - alive headless → stdin inject via `inject_into_spawned`.
    - dormant → `resume_session_headless` with framing message.
    - never → `spawn_session` with the goal brief; save returned `session_id` to `goal.md` frontmatter.
- **Tactical items tagged to this goal** — ingested items with a `strategy: <id>` tag.

**Right column (1fr):**
- **Deliverables** — derived view of the session transcripts linked from this goal's strategies. Filters tool-use events to Write/Edit/Bash-commit, labels by type (FILE / CODE / COMMIT / DRAFT / LIST).
- **Context library** — list of attached meeting notes, emails, docs from `context/`. Click to view; re-sync button per item.
- **Recent sessions** — session summaries with timestamps, sourced from CCC's existing conversation index.

## 5. `goal.md` schema

One markdown file per goal with YAML frontmatter.

```yaml
---
name: BYM growth
life_area: The Initiatives
created: 2026-04-17
status: active           # active | paused | done | dropped
strategies:
  - id: affiliates
    text: Find 3 pilates-studio affiliates (referral structure)
    status: active       # active | done | dropped
    claude_session_id: 01HK...B9C1   # null until first Launch
    tactical_keywords:   # used to auto-tag ingested items
      - affiliate
      - referral
  - id: video-ad
    text: Create 60s demo video walking through booking + package flow
    status: active
    claude_session_id: null
  - id: youtube-ad
    text: YouTube ad buy ($500)
    status: dropped
    dropped_reason: "too early — claude, Apr 13"
---

# Intent

1 paying studio (Joyce, LCPP) → 10 by end of Q2. Growth is the gating
constraint on proving BYM is a real business vs. a one-customer project.

## Success criteria
- 10 active paying studios
- $5k MRR
- 3 referrals from existing customers

## Context
Joyce confirmed 2026-04-17 she'd refer 2 other owners after Q2 stabilizes…
```

Why frontmatter + markdown:
- **Frontmatter = machine-read** (CCC parses it for strategy list + session IDs).
- **Body = human-read and Claude-editable** — inside a session, `intent`-editing is a plain `Edit` tool call, no bespoke API.
- **One file per goal.** Everything about the goal in one place; easy to `cat`, grep, or move. (The `context/` subdirectory for attached artifacts is a sibling, accessed by convention.)

## 6. Session model

Each strategy owns one **persistent Claude session**. The session's ID is stored in frontmatter.

### 6.1 Launch behavior

| Session state | Detection | Action | CCC function |
|---|---|---|---|
| Alive in terminal | TTY registered in CCC session list | Inject framing message via System Events keystroke | `inject_input_via_keystroke` (server.py:1114) |
| Alive headless | `pid` in `_spawned_sessions`, process running | Write to stdin | `inject_into_spawned` (server.py:2512) |
| Dormant | Transcript exists, no live process | Spawn `claude --resume <sid>` and inject | `resume_session_headless` (server.py:2523) |
| Never started | No `claude_session_id` in frontmatter | Spawn fresh with initial goal brief, capture new `session_id`, write to frontmatter | `spawn_session` (server.py:2430) |

### 6.2 Framing message

When injecting into an existing strategy session for a specific task, the morning view sends:

```
Still working on the overall goal "<goal name>". Focusing right now on:
<task text>

Context links: <paths to relevant TODO.md rows, GH issues, context files>
```

### 6.3 Tactical items and launch

Ingested tactical items (TODO.md rows, GH issues, PARKING_LOT entries) can be:
- **Tagged to a strategy** (via matching `tactical_keywords` in frontmatter, or manual override). Launch injects into that strategy's session.
- **Untagged.** Launch spawns a fresh session, same as CCC does today for GH issues.

Auto-tagging heuristic: substring match on `tactical_keywords` against ingested item title + body. Ambiguous items remain untagged; Amir can manually tag via the UI.

## 7. Ingestion

One worker per source type. All workers are pure functions — take source state, produce cache entries. Invoked from `/api/ingest/run`; default cron every 10 minutes; `/morning` page has a manual "Scan now" button.

### 7.1 Structured sources (cheap)

| Source | Worker | Output |
|---|---|---|
| Per-repo `TODO.md` | `repo_files.py` | Cache entries keyed by `(repo, line_hash)`. Checkbox state parsed. |
| Per-repo `PARKING_LOT.md` | `repo_files.py` | Cache entries keyed by `(repo, section_title)`. |
| Per-repo `<project> updates log.md` | `repo_files.py` | Feeds ribbon computation, not tactical cache. |
| GitHub issues | `github.py` (reuse existing CCC code) | Cache entries keyed by `(repo, number)`. Filter: open issues. |
| Notion meeting notes | `notion_meetings.py` | Via `mcp__claude_ai_Notion__notion-query-meeting-notes`. Triggered on demand for attach-to-goal. |
| Gmail threads | `gmail_threads.py` | Via `mcp__claude_ai_Gmail__search_threads` / `get_thread`. Triggered on demand. |

### 7.2 Free-form sources (LLM-extracted)

| Source | Access path | Extraction |
|---|---|---|
| Google Doc "private notes" | Google Docs API (OAuth — to be wired) | Daily `claude -p` pass extracts todo-shaped items → `inbox/<date>.jsonl`. |
| Apple Notes "bedtime ideas" | AppleScript export → markdown | Same LLM pass. |
| Wispr Flow voice notes | TBD (need to investigate local storage or API) | Same LLM pass. |

Each inbox candidate has: source, source reference (URL / note ID / transcript timestamp), extracted text, suggested goal (optional, best-effort LLM classification), `promoted_to` (null / goal slug), `dismissed_at`.

### 7.3 Ribbon computation

`ribbon.py` produces `ribbon-cache.json` per goal per day. Sources:
1. Git log: commits in watched repos, filtered to repos associated with a goal's strategies.
2. BYM updates log (and equivalents in other repos): rows within the date range, by `Area / Page` column.
3. Session transcripts: tool-use counts, duration, summary.
4. Manual entries in `progress.jsonl` (if any).

Output per day per goal: one human-readable sentence like "5 commits · 3 issues closed · demo mode shipped." Rendered in the goal card on the morning page.

## 8. Context library (per-goal attachments)

**Mode 1 (ship first): manual / in-session attachment.**

Inside a Claude session for, e.g., `real-estate`, Amir says *"grab the Apr 14 kickoff meeting notes"*. Claude:
1. Calls `notion-query-meeting-notes` with appropriate filter.
2. Converts result to markdown.
3. Writes to `~/.claude/log-viewer/morning/goals/real-estate/context/meeting-2026-04-14-kickoff.md`.
4. Appends to `attachments.jsonl`:
   ```json
   {"source": "notion_meeting", "source_id": "<page_id>", "path": "context/meeting-2026-04-14-kickoff.md", "fetched_at": "2026-04-17T12:00:00Z"}
   ```

Same pattern for Gmail threads and Google Docs.

**Mode 2 (later): auto-suggest.**

A nightly LLM pass scans recent meetings / emails / docs against goal names + strategy text, surfaces matches in the morning inbox as "looks related to *real-estate* — attach?" Amir clicks attach or dismiss.

## 9. Migration from Notion

One-time script invoked manually:

1. Fetch the TODO page via `notion-fetch`.
2. For each unique (Top category, Sub category) pair, create a goal directory.
3. Map rows into the goal's `strategies:` list based on sub-category; preserve priority text.
4. Write `goal.md` with frontmatter + seed intent (human fills in after).
5. Preserve the date-column status notes as `progress.jsonl` entries, marked `source: notion-import`.
6. Print a summary of what was created; Amir hand-edits intents and prunes.

Notion is **not** re-read after this. If Amir occasionally updates a Notion page (e.g., an ad-hoc page linked from TODO), he notifies CCC manually via a `/api/ingest/notion-page?url=...` endpoint that imports it as a one-off context attachment.

## 10. Deliverables (derived, not stored)

For each strategy with a `claude_session_id`, `morning.py` reads the session transcript at `~/.claude/projects/<project>/<sid>.jsonl` and produces a filtered view:

| Tool event | Deliverable type | Label |
|---|---|---|
| `Write` to a path outside `/tmp` | FILE | path |
| `Edit` to a tracked file | FILE | path (first occurrence within session) |
| `Bash` containing `git commit` | COMMIT | SHA + message |
| `Write` to path matching `**/drafts/**` or `**/*.md` | DRAFT | path |

No separate registry file. This view rebuilds on every `/morning/goals/<slug>` load — cheap because transcripts are already indexed by CCC.

## 11. Error handling

- **Missing / malformed `goal.md`:** skip goal, surface a warning banner on `/morning` linking to the file.
- **Notion / Gmail / Google Docs API failures:** log, show "source unavailable" on affected sections, do not block page render.
- **`claude_session_id` references dead session:** treat as `never started` for UX purposes; offer "re-spawn" button.
- **Ingestion worker crash:** failure per source is isolated; other workers run; error surfaced in last-refreshed status.

## 12. Testing

CCC currently has zero tests. Minimum for this feature:
- `morning.py` core parsing: `goal.md` round-trip (frontmatter + body), tactical keyword matching, ribbon aggregation.
- Ingestion workers: each has a deterministic fixture-based test against canned source data.
- Session-state classifier: unit tests for the four-way branch (alive-terminal / alive-headless / dormant / never).

Happy to start the broader CCC test suite here, but keep scope tight — no integration test for the Notion / Gmail MCPs in this round.

## 13. Open questions / future work

- **Wispr Flow access path** — needs investigation (local SQLite? API? export?). Parked until the first two free-form sources are wired.
- **Google Docs OAuth** — defer until the Notion / Apple Notes ingestion proves out.
- **Auto-suggest context attachment (§8 Mode 2)** — defer.
- **Authoring UX for new goals** — v1 is "edit `goal.md` directly, Claude helps via session." A web form is nice-to-have later.
- **Mobile authoring** — deferred per §2 non-goals.
- **Cross-project goals** — some goals span repos (e.g., "Amirfish.AI" covers multiple products). Strategy file paths currently aren't scoped to a repo; fine for v1 because ingestion aggregates all watched repos.

## 14. Out of scope for v1

- Writing back "done" state to source tools.
- Calendar integration (Amir uses Google Calendar via MCP; could surface next meeting, but not in this scope).
- Notifications / push alerts.
- Multi-user, permissions, sharing.
- Any migration of historical TODO.md / PARKING_LOT.md content — those files stay authoritative in-repo.

## 15. Rollout

1. Land `morning.py` skeleton + `/morning` route with hardcoded sample data. Validate UI shape.
2. Wire ingestion workers one at a time, Notion-meetings last (requires MCP availability per session).
3. Run one-time Notion import (§9). Hand-edit the generated `goal.md` files.
4. Use for a week in parallel with existing Notion TODO page.
5. Retire Notion TODO page once comfortable.
