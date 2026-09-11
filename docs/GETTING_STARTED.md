# Getting Started with CCC

## What is this?

CCC (Claude Command Center) is a dashboard for running and watching multiple AI coding sessions at once — a fleet view for your terminal-based agents.

For the full feature tour, screenshots, and comparison tables, see the [README](../README.md).

## Install

Pick whichever fits how you work:

- **curl (quickest)** — clones into `~/.ccc/claude-command-center` and runs it:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | CCC_FROM=readme bash
  ```
  Re-run the same line any time to update (it does a `git pull` under the hood).

- **Homebrew** — if you're already a brew person:
  ```bash
  brew install ccc
  ```
  Update later with `brew upgrade ccc`.

- **macOS DMG** — for a normal double-click app experience: download the DMG from the [releases page](../README.md), drag `CCC.app` to Applications, launch it. It auto-updates itself after that (Sparkle).

All three install paths end up running the same local server — none of them phone home or need an account.

## Launch — what you'll see

However you installed it, CCC opens a browser tab pointed at `http://127.0.0.1:8090` (localhost only, nothing exposed to the network by default). The first thing you'll see is your **fleet** — every active coding session across your repos, each as a card showing what it's working on and whether it's waiting on you.

If you don't have any sessions running yet, the view will look empty — that's normal. Start a session (see the quick action below) and it'll show up.

## The three main views

- **Fleet** — the default view. A live list of every session across every repo, sorted by what needs your attention first. This is where you triage: reply, approve, or just glance at progress.
- **Flow** — a canvas. Drag sessions into named groups ("Flow objects") to build a visual map of the day's work — useful once you've got more than a handful of sessions running and plain lists stop making sense.
- **Search** — full-text search across session history, so you can find "that thing Claude said about the auth bug last week" without scrolling through transcripts by hand.

Switch between them from the sidebar/tabs in the top of the dashboard.

## Quick action: spawn a session

The fastest way to see CCC do something: pick a repo card and hit **New session** (or the `+` on a repo tile), type what you want done, and watch it show up live in Fleet.

Once you've got a few sessions running, switch to **Flow** and hit **Organize** — it'll auto-arrange them into a tidy layout you can then drag around and group by hand.

## Where to go next

- Full feature walkthrough, screenshots, and CLI/API reference → [README.md](../README.md)
- Something broken or confusing? Open an issue on the repo.
