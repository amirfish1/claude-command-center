---
# ── Machine fields (front matter) ───────────────────────────────────────────
# slug: URL segment for /updates/<slug>/. Stable, kebab-case. Also the Atom <id>.
slug: my-update-slug
# title: page <h1>, <title>, feed <title>. Lead with the outcome, not "CCC now has X".
title: "The outcome, stated as a sentence"
# date: published date, YYYY-MM-DD. Feed <published>/<updated> and the sort key.
date: 2026-07-10
# version: the release this update belongs to (X.Y.Z). Shown as a pill.
version: "5.6.0"
# problem_family: taxonomy by PAIN, not by release. Exactly one of:
#   see-everything | needs-you | organize | steer | unattended | anywhere
problem_family: organize
# summary: <=160 chars. Used for og:description, feed <summary>, and the card blurb.
summary: "One sentence a stranger can grok: the pain removed and the win, no jargon."
# og_image: per-page social card. Omit to fall back to the brand default
#   (/updates/assets/og-default.png). A real 1200x630 PNG that already exists in
#   the repo is fine (e.g. /images/demo.png). Never point at an image that does
#   not exist yet.
og_image: /updates/assets/og-default.png
# status: draft | published. The generator SKIPS draft. Ship by flipping to published.
status: draft
# cta: the one primary action. label + href.
cta:
  label: "Update CCC"
  href: "https://ccc.amirfish.ai#install"
# ── Optional fields ─────────────────────────────────────────────────────────
# tags: secondary facets for search.
tags: [organize, sessions]
# changelog_refs: changelog.d slugs or a CHANGELOG version anchor this draws from.
#   Explicit linkage, never a full-text dump.
changelog_refs: ["CHANGELOG#5-4-0"]
# media: proof assets. Every image MUST be a real capture that exists in the repo.
#   type is image | video | gif. Reference by site-absolute path (/images/...).
media:
  - type: image
    src: /images/demo.png
    alt: "Describe exactly what the capture shows"
    caption: "A short, true caption."
# related: slugs of related update units, rendered as Related links.
related: []
---

## Pain
Name the painful moment in second person. "You know that moment when..." Never
open with "CCC now has X". One or two short sentences.

## Why workarounds fail
Why the obvious workaround (more tabs, more tmux, more discipline) stops scaling.

## What CCC does
What CCC does about it, in one or two sentences. Let the proof asset carry the weight.

## Proof
Prose around the media above. Describe the real capture. Never claim a screenshot
you did not capture. Qualify any Partial capability inline, not in a footnote.

## How to try
The concrete steps or command to reproduce the win. A fenced code block is fine:

```bash
# example
open Flow, group your sessions
```

## Limitations
Honest scope. What it does NOT do yet. Admit the limit in the same breath as the win.

## Related
Prose linking related features. Complements the `related:` front matter above.
