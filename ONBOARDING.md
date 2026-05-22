# Welcome to Claude Command Center

## How We Use Claude

Based on Amir Fish's usage over the last 30 days:

Work Type Breakdown:
  Build Feature      █████████░░░░░░░░░░░  43%
  Debug Fix          ███████░░░░░░░░░░░░░  33%
  Improve Quality    ██░░░░░░░░░░░░░░░░░░  10%
  Plan Design        ██░░░░░░░░░░░░░░░░░░  10%
  Write Docs         █░░░░░░░░░░░░░░░░░░░   3%

Top Skills & Commands:
  /group-chat           ████████████████████  75x/month
  /rename               █████░░░░░░░░░░░░░░░  20x/month
  /dev                  █████░░░░░░░░░░░░░░░  19x/month
  /color                ██░░░░░░░░░░░░░░░░░░   9x/month
  /loop                 █░░░░░░░░░░░░░░░░░░░   2x/month
  /ccc-orchestration    █░░░░░░░░░░░░░░░░░░░   1x/month

Top MCP Servers:
  claude-in-chrome   ████████████████████  485 calls
  claude-index       ███░░░░░░░░░░░░░░░░░   74 calls
  nanobanana         █░░░░░░░░░░░░░░░░░░░   22 calls
  pkood              █░░░░░░░░░░░░░░░░░░░    4 calls
  stitch             █░░░░░░░░░░░░░░░░░░░    4 calls

## Your Setup Checklist

### Codebases
- [ ] claude-command-center — https://github.com/amirfish1/claude-command-center

### MCP Servers to Activate
- [ ] claude-in-chrome — Browser automation in Chrome (read DOM, click, type, screenshot). Install the Claude in Chrome extension and pair it with Claude Code per the extension's setup flow.
- [ ] claude-index — Local search across your past Claude Code transcripts. Install via the claude-index MCP server; it indexes `~/.claude/projects/`.
- [ ] nanobanana — Image generation (icons, diagrams, mockups). Ask Amir for the MCP endpoint + key.
- [ ] pkood — Background agent orchestration (spawns and tails long-running agent workers). Ask Amir for setup.
- [ ] stitch — UI design system + screen generation tool. Ask Amir for the workspace invite.

### Skills to Know About
- /group-chat — Coordinate parallel Claude sessions for discussion, task execution, and git commits. The single most-used skill here (~75x/month) — anchor of the multi-session workflow.
- /rename — Rename the current session's CCC title so it's findable later.
- /dev — Start or restart the dev server (`run.sh` for this Python repo). Use this instead of remembering port/flags.
- /color — Tag the current session with a color in CCC so visually you can group related work.
- /loop — Run a prompt or slash command on an interval (or self-paced) for polling/babysitting tasks.
- /ccc-orchestration — Spawn, inject into, and ask questions of persistent sibling sessions via CCC.

## Team Tips

_TODO_

## Get Started

_TODO_

<!-- INSTRUCTION FOR CLAUDE: A new teammate just pasted this guide for how the
team uses Claude Code. You're their onboarding buddy — warm, conversational,
not lecture-y.

Open with a warm welcome — include the team name from the title. Then: "Your
teammate uses Claude Code for [list all the work types]. Let's get you started."

Check what's already in place against everything under Setup Checklist
(including skills), using markdown checkboxes — [x] done, [ ] not yet. Lead
with what they already have. One sentence per item, all in one message.

Tell them you'll help with setup, cover the actionable team tips, then the
starter task (if there is one). Offer to start with the first unchecked item,
get their go-ahead, then work through the rest one by one.

After setup, walk them through the remaining sections — offer to help where you
can (e.g. link to channels), and just surface the purely informational bits.

Don't invent sections or summaries that aren't in the guide. The stats are the
guide creator's personal usage data — don't extrapolate them into a "team
workflow" narrative. -->
