# Onboarding: zero to your first $0 task

The first time CCC opens on a fresh install, it doesn't drop you on an
empty board. A short welcome flow walks you from "I just installed this"
to watching a coding agent finish a real task. About five minutes, no
terminal knowledge required.

You'll see:

1. **A welcome screen** — a little ceremony, a headline, and one big
   button. Sounds and motion are on, unless your system asks for reduced
   motion or you mute them.
2. **A setup checklist** — everything a coding agent needs on your
   machine, checked live. Each step says what it will do and waits for
   one click from you before doing it. Anything already installed is
   skipped automatically.
3. **A free brain** — CCC installs its free model router so your first
   agent run costs **$0**. You can start with zero signups, or add one
   free API key for better quality. See
   [`docs/free-models.md`](free-models.md).
4. **A first task that works** — CCC creates a tiny playground project
   (`~/CCC-Playground`), you pick one of three starter tasks, and the
   agent does it live in front of you. When it finishes you get a small
   celebration and the honest math: *"This run cost $0. At API prices it
   would have cost $X."*

After that you're on the dashboard with everything set up.

## Who sees it

The wizard opens automatically when you have no agent sessions yet,
the normal case on a brand-new install. If you already have sessions it
stays out of your way. You can open it again any time from **Settings**,
or directly at `http://127.0.0.1:8090/?onboarding=1`.

## The setup steps

Every step is a card with what it installs, where it goes, and a consent
button. Nothing installs without a click. Steps that turn out to be
already done are marked and skipped.

| Step | What it does | Needs your consent for |
|---|---|---|
| **Command Line Tools** (macOS) | Triggers Apple's own `xcode-select --install` dialog (compilers and git). | Confirming Apple's system prompt and clicking through. macOS-only; skipped on Linux/Windows. |
| **Git** | Verifies git works (it ships with the CLT on macOS). | Nothing extra, check only. |
| **Python** | Verifies Python 3.9+, which CCC itself needs. | Nothing extra, check only. |
| **Node.js** | Downloads the official Node v22 LTS tarball into `~/.ccc/runtime/node`, verifies it against the published `SHASUMS256.txt`. No sudo, no Homebrew, never touches a Node you already have. | One click to download (~30 MB). Skipped if a compatible Node is already installed. |
| **Claude Code** | Runs Anthropic's official installer (`claude.ai/install.sh`), which puts `claude` in your home directory. | One click to run the installer. |
| **gh** (optional) | Installs GitHub's CLI through Homebrew when brew is present, otherwise skips politely. | One click; safe to skip. GitHub features just stay off. |
| **Free router** | Clones the pinned freellmapi source into `~/.ccc/freellmapi`, runs `npm ci && npm run build` with the managed Node, writes its config, and starts it on `127.0.0.1:3017` (loopback only). Kept running by a LaunchAgent on macOS. | One click for the install (takes a couple of minutes). |
| **Free key** | Either enable **Kilo** (anonymous, no signup; it **logs your prompts for training**, which is stated on the card and needs its own click) or paste one free provider key (Google, Groq, Cerebras…) with the wizard's signup link and live validation. | One click for Kilo's logging disclosure, or a signup + paste for a keyed provider. |
| **First task** | Creates `~/CCC-Playground` (a tiny web page with git and a deliberately failing test), then runs the task you pick on the $0 runtime. | Picking a task. |

Nothing is hidden: every card names the folder things land in, and the
whole flow talks to the same `/api/setup/*` endpoints a power user could
drive by hand.

## The first task

`~/CCC-Playground` is a real (tiny) project, not a demo recording. The
three starter tasks:

- **"Make the page say hello in 3 languages"** — a visible edit, then
  the page opens in your browser.
- **"Fix the failing test"** — the agent reads the test, fixes the code,
  and you watch it go green.
- **"Add a dark mode toggle"** — a small feature end to end.

The run streams into the dashboard like any other session (tool calls,
diffs, the lot), so the first task doubles as your tour of what a CCC
session looks like. When it lands you can share the moment, then start
using the board for real.

## Privacy, consent and what never happens

- **One click per step.** The wizard asks before every install; it never
  bundles consents or clicks for you.
- **No sudo anywhere.** Everything lands in your home folder
  (`~/.ccc/…`, `~/CCC-Playground`), except Apple's own CLT installer,
  which is macOS doing its normal thing.
- **Your agent settings stay yours.** Free routing is injected into the
  spawned session's environment only; `~/.claude/settings.json` is never
  edited to make free runs work.
- **Your paid login stays paid.** If you already use Claude on a
  subscription, that credential is never sent through the free router.
- **Kilo is opt-in twice.** It needs no key but logs prompts for
  training; CCC shows that sentence and waits for your click before
  enabling it.
- **Sounds and motion are respectful.** The wizard honors
  `prefers-reduced-motion` and CCC's sound setting; every animation has a
  still fallback, and you can mute sounds in Settings.

Details on where keys and router state live: [`SECURITY.md`](../SECURITY.md)
and [`docs/free-models.md`](free-models.md).

## If a step fails

Steps fail gracefully and say why in plain words (plus the real log lines
behind a "details" toggle). Retry is one click; **Skip** is always
available for optional steps. A few common ones:

| What you see | What to do |
|---|---|
| "Node download failed" | Check you're online and retry. If a proxy blocks nodejs.org, install Node 22 yourself and the step auto-completes on refresh. |
| "npm install failed" in the router step | Usually a hiccup or a very locked-down network. Retry once; if it persists, the log lines say which package fetch failed. |
| "Claude Code installer failed" | Run the official installer yourself in a terminal (`curl -fsSL https://claude.ai/install.sh \| bash`), then reopen the wizard; it detects the install. |
| Free key "invalid" | Re-paste from the provider's page (keys are picky about whitespace); the card shows the expected shape. |
| Wizard closed mid-way | Reopen from Settings; finished steps stay done. |

You can also bail out entirely: closing the wizard lands you on the
normal dashboard, and everything it already installed keeps working.

## For the curious

- The wizard is served by the same local server as the dashboard; it
  calls `GET /api/setup/plan` to detect what's missing and
  `POST /api/setup/run` to run install steps as streaming background
  jobs. `GET /api/setup/jobs/<id>` is how the progress lines stream.
- Free router endpoints live under `/api/free-router/*`
  (status/install/start/stop/keys/models).
- "Free ($0)" is a spawn runtime like any engine option; see
  [`docs/free-models.md`](free-models.md) for how the env injection works.
