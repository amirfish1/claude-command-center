# Competitors & adjacent tools

A running log of other projects in the "manage / orchestrate / observe Claude
Code sessions" space. Meant for honest positioning, not marketing.

Format: **Name** — one-line pitch. Where it overlaps. Where it doesn't.
Relevant links.

---

## Native / first-party

### Claude Code (desktop app)
**What it is**: Anthropic's own desktop app for Claude Code. Integrated terminal, file editing, HTML/PDF preview, diff viewer, drag-and-drop layout. Runs multiple sessions side-by-side in one window with a sidebar.
**Bundle**: `com.anthropic.claudefordesktop` (single binary that also hosts chat Claude).
**Session store**: uses the same `~/.claude/projects/*.jsonl` files as the CLI, plus a metadata overlay at `~/Library/Application Support/Claude/claude-code-sessions/` for desktop-specific fields (title, MCP config, VM state).

**Overlap with us**: "multiple sessions in one window" + "sidebar listing sessions".

**Where we don't overlap**:
- We're a *work-management* layer (kanban, state machine, PR pipeline, GitHub issue integration), not an IDE.
- We surface sessions across *any* repo from one board; the desktop app is one-workspace-at-a-time.
- We persist cross-session metadata (verified, archived, issue mappings, org tagging) that the desktop app doesn't track.
- We integrate with GitHub issue state (open/closed, labels, close-reason), not just link out.

**Interop status**: confirmed. Sessions launched in the desktop app write to `~/.claude/projects/*.jsonl` like any other, so they show up on our kanban automatically.

**URL scheme** (`claude://`): currently only wired to auth flows (magic-link, SSO, Google OAuth). No session-open route exposed yet. Revisit after future app updates.

---

## Open-source, adjacent

### Cabinet — runcabinet.com
**Announced**: 2026-04-14 by Hila Shmuel ([tweet](https://x.com/HilaShmuel/status/2044144613393383696)).
**What it is**: open-source AI-first knowledge base. Markdown files on disk, Next.js + WYSIWYG editor, embedded HTML apps as iframes, scheduled cron-based agent jobs, git-backed versioning, xterm.js web terminal, FlexSearch full-text.
**Repo**: [github.com/hilash/cabinet](https://github.com/hilash/cabinet) (MIT).
**Agents supported**: "runs on all agents" — model-agnostic. Pre-configured roles (CEO, Editor, SEO, Sales, QA) with scheduled jobs.

**Overlap with us**: zero on primary use case. Cabinet is a knowledge-base + agent-team builder (Notion + multiple AI roles with cron jobs). We're a session dashboard for Claude Code specifically.

**Why we track it**: adjacent in the "AI at work" landscape. A user might use both. Their scheduled-job model is interesting if we ever add "run this session every night".

---

## Related categories (not yet cataloged)

These exist but we haven't profiled them yet. Add as we learn more:

- **Aider** / **Continue** / **Cursor chat**: code-editing AI tools with their own session mental model. Not orchestrators.
- **Coolify / Coolify-AI**, **Zed AI**: IDE-adjacent.
- **LangChain agents UIs**: orchestrate LLM agents, typically not tied to a specific coding tool.
- **Linear**: project management tool we reference architecturally. Not an AI orchestrator, not a competitor.
- **Stitch, v0, Bolt**: AI build tools, not session managers.

---

## How we position ourselves against this list

The core claim: **Claude Command Center is a work-management layer, not an IDE and not a knowledge base.** It sits between Claude Code (however launched) and your issue tracker. Other tools own execution or authoring; we own orchestration and tracking.

One-line pitch: *"The kanban that shows every Claude Code session on your machine and turns GitHub issues into a working pipeline."*
