---
slug: decisions-and-a-clearer-workspace
title: "Find the decisions holding up your agents"
date: 2026-11-02
version: "5.43.0"
problem_family: needs-you
summary: "Review owner questions in one inbox, track sessions through completion, and manage connections and voice setup in one place."
status: published
cta:
  label: "Get CCC v5.43.0"
  href: "https://github.com/amirfish1/claude-command-center/releases/tag/v5.43.0"
tags: [decisions, sessions, settings, mobile]
changelog_refs: ["CHANGELOG#5-43-0"]
media:
  - type: image
    src: ../images/ccc-v5-43-hero.png
    alt: "CCC v5.43 session list beside an open conversation, using synthetic demo sessions."
    caption: "The current session workspace and conversation reader, captured with synthetic sessions."
related: []
---

## Pain

When several agents are running, a request for your input can disappear into a
conversation.

## Why workarounds fail

Cycling through every terminal becomes harder as more sessions run. A quiet
session may be waiting for a decision, rather than finished.

## Limitations

CCC v5.43 brings queue owner questions and explicitly promoted
session questions into a shared Decisions inbox, with visible source coverage
and delivery states. Open Decisions to review the questions and their context.

Answer controls are opt-in. Saving an answer, sending it, and checking its
receipt remain separate actions. Unavailable sources show a coverage gap;
numbering a question alone does not publish it to the inbox.

## What CCC does

The session workspace now separates Active, Needs you, and Finished work,
with collapsible categories and explicit archive actions. An idle turn does
not automatically mean the whole task is finished.

The capture above shows the current session list and conversation reader
running against synthetic fixtures. It contains no private sessions.

## Proof

Connections brings provider accounts and named service credentials into one
directory. Voice setup uses the same native controls, health checks point to
concrete actions, and customization review shows settings across scopes.

On your phone, app navigation now reaches the same apps as desktop. This
release also improves search, read-aloud controls, decision buttons, Codex
startup and handoff, image previews, and cold-start performance.

## How to try

Use your usual CCC install method. Homebrew users can run:

```bash
brew update && brew upgrade ccc
```

The release includes a signed macOS app and Sparkle update. Queued work may
show **needs reconciliation** once after upgrading; use Reconcile in
Settings → Maintenance if prompted.

[Read the full release notes](https://github.com/amirfish1/claude-command-center/releases/tag/v5.43.0).
