# Periodic triggers (pollers)

Reference for every timer-driven refresh in the dashboard (`static/app.js`).
Each is wrapped by the poller kill-switch harness (`_gated` / `_pollerSkip`) and
shown live in the footer transparency strip. Surfaces are consolidated into the
**three** real UI regions the app renders into.

## Surfaces

1. **Top bar** — the header row of status pills/badges (all `.topbar-btn`).
2. **Sidebar** — the conversation/session list panel: rows, repo picker, GitHub
   Issues section, archive loading bar, and the bottom-left footer pill.
3. **Open conversation pane** — the transcript/reading view on the right,
   including the inline live-activity indicator at the bottom of the transcript.

## Triggers

| Trigger (label) | Interval | Surface | What it updates | Pauses when |
|---|---|---|---|---|
| `liveToolStrip` (tools) | 1s | Open conversation pane | Inline live tool-activity indicator at the bottom of the open transcript | hidden · typing · off |
| `gcReader` (gc-read) | 3s | Open conversation pane | Group-chat transcript stream (open chat only) | chat closed · typing · off |
| `codexLog` (codex) | varies | Open conversation pane | Codex session log (open codex convo only) | wrong convo · off |
| `liveStatus` (status) | 5s | Sidebar | Conversation-row status dots + state labels | hidden · typing · off |
| `issues` | 10s | Sidebar | GitHub Issues section | hidden · typing · off |
| `sessionsList` (sessions) | 10s | Sidebar | Session list refetch (~3MB) | hidden · typing · off · not on sessions tab |
| `gcActive` (gc-live) | 15s | Sidebar | Bottom-left active-group-chat footer pill (`#gcActiveBtn`) | hidden · typing · off |
| `archiveProgress` (archive) | 250ms | Sidebar | Archive-load progress checklist in `#convList` (transient, self-clears) | off |
| `peer` | varies | Sidebar | Repo/peer picker dropdown (`$sbRepoPicker`) | picker closed · off |
| `vercelDeploy` (vercel) | 15s | Top bar | Vercel deploy badge (`#deployPill`) | hidden · typing · off |
| `localhost` | 15s | Top bar | Localhost dev-server pill (`#localhostPill`) | hidden · typing · off |
| `hiStatus` (hist-ix) | 4s / 60s | Top bar | History/search-index status pill (`#historyStatusPill`); 4s while indexing, else 60s | typing · off |
| `worktreesBadge` (worktrees) | 60s | Top bar | Worktrees button badge (`#kptWorktreesBtn`) | hidden · typing · off |

## Gating notes

- **hidden** — paused via `_PAUSE_WHEN_HIDDEN` when the window isn't frontmost;
  kicked once on refocus (`visibilitychange`).
- **typing** — a global `setInterval` wrapper mutes *all* timer callbacks while a
  text field is focused (`muteTickersWhileTyping`, debug scaffolding).
- **off** — the `Cmd/Ctrl+Shift+0` kill-switch (persists in
  `localStorage: ccc-pollers-off`) or per-trigger `window.__pollersOff[name]`.
