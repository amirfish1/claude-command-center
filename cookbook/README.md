# CCC Cookbook

Copy-paste recipes for wiring **your own app** into Claude Command Center.

Each recipe explains the pattern, why it's useful, the step-by-step setup, and
includes a ready-to-paste prompt you can hand to Claude Code (or any coding
agent) so it implements the integration in your project for you.

| Recipe | What you get |
|---|---|
| [Annotate → UX-fixes queue](annotate-to-ux-fixes-queue.md) | Click any element in your running app, type a note, and it becomes a numbered work item in CCC's durable queue — drained by a Claude session that implements the fix. |
| [In-app bug report widget → GitHub issue](bug-report-widget-github-issues.md) | A floating "Report an issue" button that screenshots the page, posts to your backend, and opens a fully-contextualized GitHub issue — plus a customer-facing "Reported Issues" view (Open / Needs Attention / In progress / Icebox / Closed) derived live from GitHub. |
| [Discord community → WatchTower queue + CCC notify](discord-community-relay.md) | Auto-provision a 4-channel Discord server (`#start-here`, `#general-fleet`, `#ship-log`, `#bugs-and-ideas`) and bridge community bug reports and questions directly into WatchTower (`wt`) and CCC notifications. |

## Conventions

- CCC is assumed to be running locally at `http://127.0.0.1:8090`
  (install: see the [main README](../README.md#quickstart)).
- Code snippets are Next.js / TypeScript because that's what the reference
  implementation uses, but every recipe is framework-agnostic — the contract
  is plain HTTP + JSON.
- Anything secret is an env var with an obvious fake placeholder. Never commit
  real tokens.
