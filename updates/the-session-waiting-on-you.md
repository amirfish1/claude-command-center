---
slug: the-session-waiting-on-you
title: "The session that's been waiting on you for an hour"
date: 2026-07-10
version: "5.6.0"
problem_family: needs-you
summary: "Five sessions running, one asked a question 40 minutes ago and nothing told you. CCC reads each transcript and surfaces the ones waiting on you."
status: draft
cta:
  label: "Tour the live demo"
  href: "https://ccc.amirfish.ai/demo"
tags: [needs-you, attention, ground-truth]
changelog_refs: ["CHANGELOG#5-6-0"]
# media-TODO: awaiting asset S-F2a (needs-attention lane with a question-waiting
#   session, per the asset ledger); secondary V-03 (video: spot the session,
#   open it, answer). When S-F2a lands at /product-story/assets/shots/S-F2a.png,
#   uncomment the media block below and flip status: published.
# media:
#   - type: image
#     src: /product-story/assets/shots/S-F2a.png
#     alt: "The CCC needs-attention lane on seeded demo data, one session flagged because its last turn ends on a plain-prose question."
#     caption: "The blocked session, pulled out of the pile. Seeded fake data."
related: []
---

## Pain
You know that moment: five sessions running, and one of them asked "want me to
proceed?" forty minutes ago. It has been idle ever since, blocked on a one-word
answer, while the other four kept scrolling and held your eye. Nothing surfaced
it. You found it by accident, long after it went quiet.

## Why workarounds fail
The instinct is more discipline: keep cycling every terminal, re-read each one,
never let a tab go stale. That holds at two sessions. At five it breaks, because
the session that needs you looks exactly like the four that do not until you
have read each transcript to the end. Watching harder does not scale.

## What CCC does
CCC reads the actual transcript of every session and flags the ones whose latest
turn ends on a question, including plain-prose asks with no question mark like
"paused for review" or "pick one." The flagged sessions collect in a
needs-attention lane you can scan in seconds. It judges from ground truth, the
transcript itself, not from an agent reporting that it is done. On macOS you
also get a desktop notification the moment a session starts waiting; on Linux
the lane still surfaces it, with no desktop popup.

## Proof
The capture is the needs-attention lane on seeded demo data, with a session
flagged because its last turn ends on a plain-prose question rather than a formal
prompt. The other running sessions sit quietly in their columns. All rows are
fake seeded data. The point the shot makes: the one waiting on you is pulled out
of the pile, not left for you to find.

## How to try
Open the live demo, no install: look at the needs-attention lane and find the
row marked as waiting. Running CCC already? Any session that ends its turn on a
question surfaces there automatically, across every repo, with nothing to
configure.

## Limitations
Detection is a strong signal, not a guarantee. A request phrased with no question
mark and no question words can slip past, and a rhetorical question can get
flagged by mistake. Desktop notifications are macOS-only. And it tells you a
session is waiting; it does not answer for you.

## Related
Pairs with the per-session context meter, which catches the other silent
failure, a session about to run out of context left, and with group chats when
the answer is really a decision several sessions need to share.
