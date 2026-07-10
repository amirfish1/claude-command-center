---
slug: check-the-fleet-from-your-phone
title: "Check the fleet from the couch"
date: 2026-07-10
version: "5.6.0"
problem_family: anywhere
summary: "You stepped away and the fleet kept running, but your phone shows nothing. CCC's mobile UI lets you monitor and steer sessions from a phone on your network."
status: draft
cta:
  label: "Tour the live demo"
  href: "https://ccc.amirfish.ai/demo"
tags: [anywhere, mobile, phone]
changelog_refs: ["CHANGELOG#5-6-0"]
# media-TODO: awaiting assets M-01 (session list on a phone-width viewport) and
#   M-02 (an open conversation on a phone-width viewport), per the asset ledger;
#   secondary V-15 (phone-width walkthrough video). When M-01/M-02 land at
#   /product-story/assets/mobile/M-01.png and M-02.png, uncomment the media
#   block below and flip status: published.
# media:
#   - type: image
#     src: /product-story/assets/mobile/M-01.png
#     alt: "The CCC session list on a phone-width viewport, seeded demo data, rows sized and spaced for a small screen."
#     caption: "The board, legible on a phone. Seeded fake data."
#   - type: image
#     src: /product-story/assets/mobile/M-02.png
#     alt: "An open CCC conversation on a phone-width viewport, seeded demo data, with the composer reachable at the bottom."
#     caption: "Read where an agent got to, then steer it, from your phone."
related: []
---

## Pain
You closed the laptop and walked away, but the agents did not stop. Somewhere in
that fleet a session just finished, and another is waiting on a one-line answer,
and all you have in your pocket is a phone that shows you none of it. The work is
still moving; your view of it went dark.

## Why workarounds fail
SSHing into your machine from a phone to poke at terminals is miserable, and it
does not give you the board, just raw sessions again. Leaving the laptop open at
your desk so you can remote in assumes you are near a real screen. Neither lets
you glance at the fleet the way you glance at a message.

## What CCC does
CCC serves the same board to a phone on your network, laid out for a small
screen: tap through the session list, open a conversation, read where an agent
got to, and steer it with the same composer you use at your desk. The fleet is
on your machine; your view of it is wherever you are.

## Proof
The captures are the CCC session list and an open conversation on a phone-width
viewport, running on seeded demo data. All rows are fake seeded data. What they
show: the board is legible and operable on a phone, not a desktop layout crammed
onto one.

## How to try
Open the live demo on your phone to feel the mobile layout. Running CCC already?
On the same network, open the board's address in your phone browser, tap a
session, and read or steer it from there. CCC binds to 127.0.0.1 by default, so
reaching it from your phone means the explicit, warned opt-in to bind wider:
read SECURITY.md first.

## Limitations
Mobile is for monitoring and quick steering, not heavy work: reading, answering,
nudging, all fine; deep multi-pane review still wants a desktop. Reach is over
your own network, not a hosted cloud, and only once you opt into binding beyond
localhost, so the machine running the fleet has to be reachable from your phone.

## Related
Pairs with continue-on-another-machine, which hands a whole session, its repo
state and transcript included, to a paired machine so you can pick up where the
desktop left off, and with attention detection, so the session that needs you
finds you wherever you are.
