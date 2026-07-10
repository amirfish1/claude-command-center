---
slug: one-board-every-session
title: "Every coding-agent session on your machine, on one board"
date: 2026-07-10
version: "5.6.0"
problem_family: see-everything
summary: "Nine terminal tabs and no idea which session is which. CCC reads each engine's on-disk state and puts the whole fleet on one local board."
og_image: /images/social-preview.png
status: published
cta:
  label: "Tour the live demo"
  href: "https://ccc.amirfish.ai/demo"
tags: [see-everything, board, attach, fleet]
changelog_refs: ["CHANGELOG#5-6-0"]
media:
  - type: image
    src: /product-story/assets/shots/S-OVR.png
    alt: "The CCC board on seeded demo data: columns for GitHub issues, a needs-attention lane, an icebox, and eight in-progress sessions, with a live session transcript open in a right-hand pane and a header reading 'for Claude, Codex, Cursor, and Anti-Gravity.'"
    caption: "The whole operation on one local board. All data is seeded and fake."
related: []
---

## Pain
You know the mid-afternoon moment: nine terminal tabs open, several coding
agents running, and no quick way to tell which tab is which session. One
finished an hour ago. One is waiting on you. You cannot see the fleet, so you
cycle through tabs hoping to land on the one that matters.

## Why workarounds fail
More tabs, more tmux panes, more discipline: each buys you a little, then stops.
A terminal shows you one session at a time, and the tools that do hand you a
dashboard usually own the sessions they launched. Resume one by hand, or start
one in a plain terminal, and their board goes blind to it.

## What CCC does
CCC attaches instead of owning. It reads each engine's on-disk state as the
source of truth, so every session shows up on one local board no matter how you
launched it, hand-started terminals included. Claude Code is first-class; Codex,
Cursor, Antigravity, and Kilo Code spawn and appear too, each with a documented
gap, so it is one board for several engines rather than identical support across
all of them.

## Proof
The capture is the CCC board running on seeded demo data. Columns carry GitHub
issues, a needs-attention lane, an icebox, and eight in-progress sessions, with
a live transcript open on the right and a header that reads "for Claude, Codex,
Cursor, and Anti-Gravity." Every row is fake seeded data, nothing real, but it
is the actual dashboard chrome: the whole operation on one screen.

## How to try
Open the live demo, no install required, and scan the board: issues waiting,
sessions in progress, the one flagged for your attention. Running CCC already?
Every coding-agent session on the machine appears automatically, including ones
you started from a plain terminal. Nothing to wire up.

## Limitations
The board is a lens over state the engines already write, not a runtime: it does
not run your agents and does not replace your terminal. Multi-engine support is
real but uneven, Claude Code is deepest and some engines are fire-and-forget, so
read the README matrix before you lean on a specific one. Desktop notifications
and terminal focus are macOS-only.

## Related
Pairs with attention detection, which tells you which of these sessions is
waiting on you, and with the kanban and project-tree views when you want to
organize the fleet instead of just seeing it.
