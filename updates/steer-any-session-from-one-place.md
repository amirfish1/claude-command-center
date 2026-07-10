---
slug: steer-any-session-from-one-place
title: "Steer any session without hunting for its terminal"
date: 2026-07-10
version: "5.6.0"
problem_family: steer
summary: "A dormant session needs one more instruction and reopening its terminal is a chore. Type into any session from the board; dormant ones auto-resume to take it."
status: draft
cta:
  label: "Tour the live demo"
  href: "https://ccc.amirfish.ai/demo"
tags: [steer, inject, dormant, group-chat]
changelog_refs: ["CHANGELOG#5-6-0"]
# media-TODO: awaiting asset S-F4a (composer typing into a dormant session, per
#   the asset ledger); secondary V-10 (video: type into a dormant/headless
#   session from the browser). When S-F4a lands at
#   /product-story/assets/shots/S-F4a.png, uncomment the media block below and
#   flip status: published.
# media:
#   - type: image
#     src: /product-story/assets/shots/S-F4a.png
#     alt: "The CCC per-session composer on seeded demo data, an instruction being typed into a dormant session that will auto-resume to receive it."
#     caption: "One input, any session, no terminal in the loop. Seeded fake data."
related: []
---

## Pain
You know exactly what one of your sessions should do next. Problem is it went
dormant an hour ago, and giving it that one instruction means finding the right
terminal, resuming it, waiting for it to boot, and only then typing. By the time
you get there you have lost the thread on the other four.

## Why workarounds fail
Keeping every session in its own live terminal so it is always ready to type
into does not scale past a handful, and it is the exact tab sprawl you were
trying to escape. Leaving notes for later means the instruction is not in the
session, it is in your head. Neither gets the words to the agent.

## What CCC does
CCC lets you type into any session from the board. If the session is dormant, it
auto-resumes to receive the message, so you steer from one place without opening
a terminal or hunting for the right window. The same board is where you read the
reply. You are directing the fleet by talking to it, not by writing
orchestration code.

## Proof
The capture is the CCC composer sending an instruction into a dormant session on
seeded demo data, with the session listed on the board and the message landing
in its transcript. All data is seeded and fake. What it shows: one input, any
session, no terminal in the loop.

## How to try
Open the live demo to see the board and the per-session composer. Running CCC
already? Pick any session, dormant or live, type into it from the browser, and
it resumes if needed to take the message. For work that needs several agents to
divide and coordinate, open a group chat and let them respond in turn.

## Limitations
Steering is delivery, not a guarantee the agent acts as you intend: it puts your
words in front of the session, the agent still decides what to do. Follow-up
depth varies by engine, and some engines are fire-and-forget with no way to
steer after launch, so check the README matrix. A message sent to a headless
session while the server is restarting can be missed; the board flags a send it
cannot confirm rather than pretending it landed.

## Related
Pairs with group chats, where multiple sessions share one thread and are pinged
to respond in turn, and with attention detection, which tells you which session
is waiting on your next instruction in the first place.
