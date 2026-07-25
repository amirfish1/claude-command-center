# `updates/` — the Updates hub content source

This directory is the **source** for the CCC Updates hub published at
<https://ccc.amirfish.ai/updates/>. One markdown file here (YAML front matter +
a short pain-to-proof body) becomes one searchable, subscribable page, one card
on the index, and one entry in the Atom feed.

Source lives here; generated HTML lands in `docs/updates/` (which GitHub Pages
serves). Raw `.md` in this directory is never served.

## What an update is (and is not)

An update is a **narrative, user-facing story**: a pain we removed, the proof,
and how to try it. It is not a changelog dump. Most `changelog.d/` lines never
become an update. You editorially pick the 1-3 headline changes per release and
write a real story for each. The exhaustive record stays in `CHANGELOG.md`; an
update just links to it via the `version:` field.

Every claim must trace to `docs/product-story/pain-feature-proof.md` with status
**Built**. Never claim anything on that file's never-claim list. Follow the
tone and copy rules in `docs/product-story/message-architecture.md` (sections
10-11). No em-dashes in public copy.

## Authoring workflow (the under-30-minutes path)

1. **Copy the template.**

   ```bash
   cp updates/_template.md updates/<slug>.md
   ```

   Pick a stable, kebab-case `<slug>` — it becomes the permanent URL.

2. **Fill the front matter and the body.** The template documents every field
   inline. Required front matter: `slug`, `title`, `date`, `version`,
   `problem_family`, `summary`, `status`, `cta`. Body sections (all optional,
   rendered in this order): `## Pain`, `## Why workarounds fail`,
   `## What CCC does`, `## Proof`, `## How to try`, `## Limitations`,
   `## Related`.

   - `problem_family` is exactly one of: `see-everything`, `needs-you`,
     `organize`, `steer`, `unattended`, `anywhere`.
   - Every image in `media:` must be a **real capture that already exists** in
     the repo (for example `/images/demo.png`). Never reference a screenshot you
     have not captured.
   - Leave `status: draft` while you write. The generator skips drafts, so a
     half-written update never ships.

3. **Build.**

   ```bash
   python3 scripts/updates_build.py
   ```

   Stdlib-only, no `pip install` needed. It regenerates everything under
   `docs/updates/` (index, per-update pages, `feed.xml`, `updates.json`,
   `styles.css`). Re-running on an unchanged tree is a no-op diff.

4. **Preview locally.**

   ```bash
   cd docs && python3 -m http.server 8099
   # open http://127.0.0.1:8099/updates/
   ```

5. **Publish** by flipping `status: draft` → `status: published`, rebuild, and
   commit the source **and** the generated output together:

   ```bash
   git commit --only updates/<slug>.md docs/updates -m "docs(updates): <slug>"
   ```

   Pushing is done by Amir / the integrator. See `docs/updates/PUBLISHING.md`
   for the full runbook, including the (not-yet-active) email and analytics
   activation checklists.

## The markdown subset the body supports

The generator ships a small stdlib renderer (no dependency). It supports:
paragraphs, `##` headings (section markers), `**bold**`, `` `inline code` ``,
`[links](url)`, `![images](src)`, fenced code blocks (```), and `-`/`*`/`1.`
lists. That is deliberately all an update body needs. If you ever need richer
markdown, see the note at the top of `scripts/updates_build.py`.
