---
slug: a-queue-that-drains-overnight
title: "Drop tickets in a queue, wake up to closed ones"
date: 2026-07-10
version: "5.6.0"
problem_family: unattended
summary: "You want to hand agents a backlog and step away, but nothing keeps them working or notices when one stalls. CCC gives you a work queue that drains itself."
status: draft
cta:
  label: "Tour the live demo"
  href: "https://ccc.amirfish.ai/demo"
tags: [unattended, queue, watcher, ground-truth]
changelog_refs: ["CHANGELOG#5-6-0"]
# media-TODO: awaiting asset S-F5a (queue board with tickets in claim/fix/verify/
#   close states, per the asset ledger); secondary S-F5b (health strip with a
#   stuck-queue flag) and V-12 (video: ticket claimed, fixed, verified). When
#   S-F5a lands at /product-story/assets/shots/S-F5a.png, uncomment the media
#   block below and flip status: published.
# media:
#   - type: image
#     src: /product-story/assets/shots/S-F5a.png
#     alt: "The CCC work queue on a local server with synthetic tickets in their lifecycle states, claimed through verified, and the health strip flagging one queue as stuck."
#     caption: "Work moving without a human in the dispatch loop. Synthetic tickets."
related: []
---

## Pain
You have a stack of small, well-scoped tasks and a fleet of agents that could
clear them overnight. But handing them out one at a time and babysitting each to
completion means you are the scheduler, and you have to be awake to do it. Step
away and the work stops with you.

## Why workarounds fail
A to-do list in a doc does not dispatch itself. Kicking off five sessions by hand
and hoping they finish means you learn in the morning that two stalled at hour
one and nothing noticed. More discipline just moves the bottleneck back onto you,
which is the thing you were trying to remove.

## What CCC does
CCC gives you a work queue with a real lifecycle: tickets are claimed, fixed,
verified, and closed by bound agent workers. A queue-health watcher judges
progress from ground truth, the actual ticket and worker state, and flags a
queue that has gone stuck, nudging workers automatically instead of waiting for
you to check. You drop the tickets in and step away.

## Proof
The capture is the work queue on a local server with synthetic tickets sitting in
their lifecycle states, claimed through verified, and the health strip showing
one queue flagged as stuck. All tickets are synthetic, nothing real. What it
shows: work moving without a human in the dispatch loop, and a stall surfaced
rather than left silent.

## How to try
Open the live demo to see the queue and its states. Running CCC already? Add
tickets to the queue, bind agent workers to drain it, and leave it: the watcher
keeps them moving and flags anything that stalls. Verification closes each ticket
against ground truth, not an agent's say-so.

## Limitations
Unattended does not mean unsupervised judgment: the queue drains and polices
itself, but it works the tickets you wrote, at the scope you set. A badly framed
ticket produces a badly done task, faster. The watcher nudges stuck workers; it
does not rewrite a task that was wrong to begin with.

## Related
Pairs with the GitHub issue board, where one click turns an issue into a working
session that verify closes with a commit-SHA comment, and with annotate mode,
which turns a click on your running app into a queue ticket.
