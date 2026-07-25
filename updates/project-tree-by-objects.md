---
slug: project-tree-by-objects
title: "Group your agent sessions the way you think about the work"
date: 2026-06-25
version: "5.4.0"
problem_family: organize
summary: "A flat list of sessions stops meaning anything past a dozen. The project tree groups them under nestable objects, so strategy stays above the churn."
og_image: /images/social-preview.png
status: published
cta:
  label: "Update CCC"
  href: "https://ccc.amirfish.ai#install"
tags: [organize, project-tree, objects, sessions]
changelog_refs: ["CHANGELOG#5-4-0"]
media:
  - type: image
    src: /images/demo.png
    alt: "The CCC dashboard: the left sidebar lists sessions grouped in progress, with by project, by time, and by objects controls at the top."
    caption: "The board, with the by project / by time / by objects grouping controls above the session list."
related: []
---

## Pain
You started with three sessions and a flat list was fine. Now there are thirty.
Two of them are the strategy sessions that decide everything, and they have sunk
to the bottom under a pile of one-off execution runs you will archive by tonight.
The list is technically complete and completely unreadable.

## Why workarounds fail
You can rename sessions, or keep the important ones open in their own terminals,
or just remember which is which. None of that survives a busy afternoon. A flat
list has exactly one axis, time, and the work in your head has several: this
project, that feature, the throwaway spikes off to the side.

## What CCC does
The project tree groups your sessions under named objects you define, and those
objects nest. Pin a strategy object at the top, fan its execution sessions out
underneath, and file the throwaway work somewhere else entirely. The board now
matches the shape of the work instead of flattening it into one column.

## Proof
The capture above is the CCC board. The left sidebar carries the session list
with the by project, by time, and by objects controls sitting right above it, so
one click regroups the whole fleet by the axis you care about at that moment.
The sessions are real rows in the seeded demo, each with its live status.

## How to try
Update to 5.4.0 or later, then in the sidebar switch the grouping control to
**by objects**. Use **+ object** to create a group, drag sessions into it (hold
Cmd or Shift to move several at once), and nest objects inside objects for
sub-projects. **Expand all** and **Collapse all** fold the whole tree when you
just want the headlines.

## Limitations
By objects is a grouping over your session list, not automatic filing. You decide
what the objects are and what goes in them. CCC will not guess a taxonomy for
you. If you want the fleet laid out spatially instead of as a tree, the Flow
canvas is the same sessions arranged freely on a board.

## Related
Pairs with the kanban board when you want state (Working, Review, Verified)
instead of hierarchy, and with the Flow canvas when you want to arrange the whole
operation like a whiteboard.
