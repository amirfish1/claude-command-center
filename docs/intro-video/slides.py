"""CCC introduction video.

SLIDES is the source of truth for narration spoken in
out/ccc-introduction.mp4. docs/intro-video/transcript.md is the bound
coverage transcript: same narration, plus the scene map of real UI sources.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent
VIDEO = DOCS / "product-story" / "assets" / "video"
SIMPLE = DOCS / "simple-mode" / "assets" / "01-home.png"

CONFIG = {
    "title": "CCC introduction",
    "orientation": "horizontal",
    "out_path": "out/ccc-introduction.mp4",
    "tts": {
        "provider": "elevenlabs",
        "voice_id": "cgSgspJ2msm6clMCkdW9",
        "model": "eleven_turbo_v2_5",
        "speaking_rate": 1.0,
    },
    "avatar": {
        "static": False,
    },
}

SLIDES = [
    {
        "type": "html",
        "html": "slides/00-identity.html",
        "scene": "identity",
        "visual": "brand title card",
        "moment": "identity: local dashboard, attaches, needs you",
        "narration": (
            "CCC is a local dashboard that attaches to coding-agent sessions "
            "on your machine, however they were launched, and shows you which "
            "one needs you. It is a lens, not a runtime. Close the board, and "
            "the sessions keep running."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-01-fleet-scan.mp4"),
        "title": "One board, eight engines",
        "scene": "fleet",
        "visual": "V-01-fleet-scan.mp4",
        "moment": "fleet/list with multiple engines",
        "narration": (
            "One board, eight engines. Every Claude Code, Codex, Cursor, "
            "Antigravity, Kilo Code, Kimi Code, OpenCode, and Devin session "
            "lands here. CCC reads each engine's on-disk state, so even a "
            "session you started by hand in a terminal shows up. Spawn, "
            "monitor, and review them from the same dashboard."
        ),
    },
    {
        "type": "html",
        "html": "slides/02-engines.html",
        "scene": "engines",
        "visual": "engine support matrix card",
        "moment": "eight spawnable plus three read-only, with qualifications",
        "narration": (
            "Spawn from the dashboard works for all eight. Follow-up works "
            "on seven. Kilo Code is fire-and-forget: no resume wiring yet. "
            "Cursor IDE sync is metadata-only by design, so this is one board "
            "for eight engines, not the same support on every engine. Three more engines are "
            "ingested read-only today: GitHub Copilot CLI, VS Code Copilot "
            "Chat, and Grok CLI. They appear on the board with their "
            "transcripts, but you cannot spawn or steer them from the "
            "dashboard yet. Kimi Code has a guided setup flow in Settings, "
            "Engines, that detects the CLI, walks through install and login, "
            "and verifies with a smoke-test spawn."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-03-attention.mp4"),
        "title": "Needs you",
        "scene": "attention",
        "visual": "V-03-attention.mp4",
        "moment": "needs-you / attention moment",
        "narration": (
            "When an agent is waiting on you, the row flags it. Attention "
            "detection picks up a real question, including a plain-prose one, "
            "and on macOS it can fire a desktop notification. You see the "
            "needs-you moment instead of discovering it forty minutes later."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-07-flow-canvas.mp4"),
        "title": "Flow canvas and Project tree",
        "scene": "flow",
        "visual": "V-07-flow-canvas.mp4",
        "moment": "Flow canvas or project tree",
        "narration": (
            "When a flat list is not enough, the Flow canvas lays repos, "
            "sessions, group chats, and objects on an infinite zoomable board "
            "with edges. The sidebar Project tree groups sessions under "
            "nestable Flow objects you name, drag, and reparent, with a live "
            "Current sessions band on top."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-08-split-pane.mp4"),
        "title": "Split conversations",
        "scene": "split",
        "visual": "V-08-split-pane.mp4",
        "moment": "split conversations",
        "narration": (
            "Drag any sidebar session onto the right or bottom edge of an "
            "open conversation to view two transcripts side by side, each "
            "with its own input bar."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-06-kanban-drag.mp4"),
        "title": "Board view, optional",
        "scene": "kanban",
        "visual": "V-06-kanban-drag.mp4",
        "moment": "optional kanban board view",
        "narration": (
            "Board view is optional. Drag-drop columns derived from session "
            "state, with rubber-band multi-select. The list is the primary "
            "surface. The board is an opt-in lens on the same state."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-14-issue-to-session.mp4"),
        "title": "Spawn from the dashboard",
        "scene": "spawn",
        "visual": "V-14-issue-to-session.mp4",
        "moment": "spawn or steer from the dashboard",
        "narration": (
            "Start a session from a GitHub issue with one click. Verify "
            "closes the issue with a commit-SHA comment. That needs the gh "
            "CLI signed in. Toggle worktree mode to launch in a fresh "
            "worktree on a feature branch, with optional init scripts. "
            "Headless spawn keeps an in-browser input bar so you can keep "
            "talking, no terminal needed."
        ),
    },
    {
        "type": "html",
        "html": "slides/08-steer.html",
        "scene": "steer",
        "visual": "feature grid: attach, desktop, titles, permissions, composer",
        "moment": "attach, resume-on-demand, titles, permission prompts",
        "narration": (
            "Terminal Claude processes show up automatically. Jump to "
            "terminal focuses them by TTY. On macOS, Open in Claude Desktop "
            "resumes the current CLI session inside the Desktop app. "
            "Messaging a dormant session is resume-on-demand: CCC auto-spawns "
            "a headless resume to deliver it. Click the sparkle on any card "
            "for AI-assisted titles, regenerated via Claude Haiku. Claude "
            "Code permission prompts surface inline. CCC never interrupts a "
            "possibly-mid-turn session without your Approve. When a session "
            "is large and stale, the cost-aware cold-session composer "
            "replaces Send with ranked cheaper routes: continue fresh on a "
            "lower tier, or search history, instead of a blind expensive "
            "resume."
        ),
    },
    {
        "type": "html",
        "html": "slides/09-cost.html",
        "scene": "cost",
        "visual": "usage and deploy callouts",
        "moment": "usage tracking, auto-fix deploys",
        "narration": (
            "Usage tracking shows your pace against plan limits, per engine, "
            "with cache-adjusted token rankings, before you hit the wall. "
            "Throughput attributes a spend spike to the session or automation "
            "that caused it. Auto-fix deploys is opt-in: it polls Vercel and "
            "spawns a fix-deploy session on new production errors, deduped by "
            "commit. The spawned session still has to investigate. It does "
            "not arrive with the failure logs."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-16-group-chat.mp4"),
        "title": "Sessions that coordinate",
        "scene": "group-chat",
        "visual": "V-16-group-chat.mp4",
        "moment": "group chat or queues/workers",
        "narration": (
            "Group chats keep two sessions on one goal in sync. Post once, "
            "and every participant is pinged. Sessions can also ask a sibling "
            "synchronously over a local API. CCC ships an orchestration skill "
            "so one Claude session can spawn, inject into, and ask sibling "
            "sessions over plain HTTP, plus a twelve-skill pack for concrete "
            "workflows."
        ),
    },
    {
        "type": "html",
        "html": "slides/11-inbox.html",
        "scene": "inbox",
        "visual": "Decision Inbox card (synthetic demo copy)",
        "moment": "Decision Inbox",
        "narration": (
            "Stalled work should not become your problem again. Decision "
            "Inbox scans once an hour for what is stuck: a strategy board, "
            "WatchTower queues, and idle sessions. A cheap analyst leaves "
            "you a three-option card. You click one. CCC spawns or steers "
            "the follow-through. The token governor flags sessions burning "
            "tokens for nothing, with one-click Nudge, Pause, or Kill."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-17-queues.mp4"),
        "title": "WatchTower queues",
        "scene": "queues",
        "visual": "V-17-queues.mp4",
        "moment": "group chat or queues/workers",
        "narration": (
            "File work into named WatchTower queues instead of remembering "
            "what to ask which session. Tickets survive closed sessions. The "
            "queue inbox shows what needs you. More queue tooling: per-queue "
            "AI status briefs, GitHub-backed queues synced from issues, and "
            "one-click create a queue for this session."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-18-queue-workers.mp4"),
        "title": "Workers that learn",
        "scene": "workers",
        "visual": "V-18-queue-workers.mp4",
        "moment": "queues/workers",
        "narration": (
            "Workers drain queues in parallel. Each worker reads that "
            "queue's shared learnings file before it starts and writes back "
            "when it ends, so a queue handling the same kind of ticket keeps "
            "getting faster, not just busier. Plan-to-fleet imports a plan "
            "or mission brief into a WatchTower queue from the dashboard, "
            "previews the tickets, files them on confirm, and can drain them "
            "with a worker."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-09-search.mp4"),
        "title": "Find anything",
        "scene": "search",
        "visual": "V-09-search.mp4",
        "moment": "search",
        "narration": (
            "Full-text search across session history is built in, with zero "
            "setup. It covers Claude Code and Codex today. An optional deeper "
            "semantic mode, local embeddings, is available when you cannot "
            "remember the words you used. Semantic search is not on by default."
        ),
    },
    {
        "type": "video",
        "video": str(VIDEO / "V-15-mobile.mp4"),
        "title": "Work from anywhere",
        "scene": "mobile",
        "visual": "V-15-mobile.mp4",
        "moment": "mobile or Simple Mode",
        "narration": (
            "The whole fleet on your phone: monitor sessions, answer agents, "
            "and steer from a browser on your trusted network. CCC binds to "
            "loopback by default. It is never meant for the open internet."
        ),
    },
    {
        "type": "image",
        "image": str(SIMPLE),
        "title": "Simple Mode",
        "scene": "simple-mode",
        "visual": "docs/simple-mode/assets/01-home.png",
        "moment": "mobile or Simple Mode",
        "narration": (
            "Simple Mode gives your phone a plain-language Home screen: New "
            "conversation, Needs you, and Your conversations, without the "
            "advanced dashboard vocabulary."
        ),
    },
    {
        "type": "html",
        "html": "slides/17-settings.html",
        "scene": "settings",
        "visual": "settings, tour, system status, ACP",
        "moment": "FIRST FLIGHT, Settings modal, System status, ACP adapter",
        "narration": (
            "The FIRST FLIGHT tour is a spotlight walkthrough of the "
            "dashboard on first run, replayable any time from Settings. The "
            "Settings modal opens with instant search, Command or Control "
            "comma, keyboard navigation, and per-section reset. System status "
            "is a health modal over the whole fleet: restart-all, "
            "spawned-process cleanup, and delivery receipts. An optional ACP "
            "adapter exposes CCC over the Agent Client Protocol so editors "
            "and ACP clients can drive Claude Code sessions. It runs as a "
            "separate process. The core server stays stdlib-only."
        ),
    },
    {
        "type": "html",
        "html": "slides/18-cli.html",
        "scene": "cli",
        "visual": "ccc CLI command panel",
        "moment": "ccc CLI",
        "narration": (
            "Once CCC is running, the ccc CLI answers what sessions exist "
            "and what they are doing, with no dashboard needed. ccc sessions "
            "is the live census. ccc models lists every engine's models and "
            "effort ladders. ccc quota shows weekly quota left. ccc doctor "
            "reports per-engine CLI, auth, and key health, dry-run only. "
            "ccc spawn starts a session. ccc send messages a running session. "
            "ccc ask blocks for the reply."
        ),
    },
    {
        "type": "html",
        "html": "slides/19-install.html",
        "scene": "install",
        "visual": "install paths and live demo",
        "moment": "demo and install",
        "narration": (
            "Try the live demo first, the full dashboard with seeded fake "
            "data, no install required, at ccc.amirfish.ai/demo. To install: "
            "one curl line, or brew install ccc, or download the macOS DMG. "
            "Git and Python 3.9 are enough to start the dashboard. WatchTower "
            "comes with it as the queue engine."
        ),
    },
]
