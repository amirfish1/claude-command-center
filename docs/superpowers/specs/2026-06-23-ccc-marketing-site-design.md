# CCC Marketing Site — Design Spec

Date: **2026-06-23**
Status: **Draft for review**
Author: Claude (brainstormed with Amir)

## 1. Goal

Build a comprehensive, multi-page marketing + docs website for CCC (Claude
Command Center) at the **same comprehensiveness and quality** as best-in-class
dev-tool sites. Benchmarked against 6 sites mapped in research:

- **Peer-scale (the bar we match):** Omnara, Conductor.
- **Quality/pattern reference only (we do NOT compare ourselves to them):**
  Warp, Zed, Cursor, Vibe Kanban.

Explicit positioning decision from the owner: **CCC compares itself only to
peers its own size** (Omnara, Conductor, Vibe Kanban, opcode, Claude Squad,
Crystal/Sculptor). It does **not** position against Cursor or Warp — different
weight class, different category.

## 2. Where it lives

- **New, standalone site** intended for a fresh domain (working name
  `ccc.dev` — owner will decide). It is **separate** from the current
  `ccc.amirfish.ai` landing page (`docs/index.html`), which stays as-is.
- Built into a **new top-level directory: `site/`** so it can be deployed
  independently (own GitHub Pages target / CNAME or a separate host) without
  disturbing the live `docs/` site.
- The current `ccc.amirfish.ai` may later redirect/link to the new site; that
  wiring is out of scope for this spec.

## 3. Constraints (house rules)

- **No build step. No bundler. No npm.** Pure static HTML/CSS/JS, matching the
  repo's single-file-app ethos (`static/index.html` is hand-authored; this site
  follows the same discipline). This is what the peer sites' *output* looks like
  to a browser anyway — we just author it directly.
- Shared CSS + a tiny shared JS file across pages (nav, footer, scroll-reveal,
  theme). No framework.
- **Reuse existing assets:** the working `docs/demo/` kanban (seeded fake data)
  is the hero centerpiece; `docs/images/` screenshots; the release demo video.
- Public-OSS hygiene applies (CLAUDE.md): no private paths, client names, or
  PII. Honest comparison cells — mark CCC ⚠️/✗ where true.

## 4. Site map (pages)

| Route | Page | Purpose |
|---|---|---|
| `/` | **Home** | Hero + live demo + the two big bets + feature highlights + social proof + install |
| `/features/` | **Features** | Deep feature blocks: attach-first, kanban state machine, Flow canvas, group chat, multi-engine, GitHub issue pipeline, auto-fix-deploy |
| `/compare/` | **Compare hub** | "CCC vs the field" honest matrix (peer set only) + links to vs-pages |
| `/compare/vibe-kanban/` | **vs Vibe Kanban** | Dedicated head-to-head (archetype: worktree-per-task orchestrator) |
| `/compare/conductor/` | **vs Conductor** | Dedicated head-to-head (closed, native Mac, well-funded) |
| `/compare/omnara/` | **vs Omnara** | Dedicated head-to-head (omni-device remote, peer-on-par) |
| `/why/` | **Why CCC** | The manifesto: attach-not-own + issues-as-state-machine. The uncopyable paragraph. |
| `/changelog/` | **Changelog** | Shipping-velocity signal. Generated from `changelog.d/` + `CHANGELOG.md`. |
| `/roadmap/` | **Roadmap** | Zed-style transparency: now / next / considering. Sourced from `docs/roadmap.md`. |
| `/install/` | **Install / Download** | curl, Homebrew, DMG, VS Code ext; per-method instructions; demo link |
| `/docs/` | **Docs hub** (phase 2, see §8) | Unify scattered `docs/*.md` into a real docs surface |

**Phase 1 ships pages `/` through `/install/`. Docs hub (`/docs/`) is phase 2**
to avoid blocking launch on a full docs migration.

## 5. Homepage section order (the centerpiece)

Following the Conductor/Omnara pattern (hero = the product, lean copy,
high-signal demo), adapted to CCC:

1. **Announcement / release pill** — "CCC v4.6.0 — see what's new" → changelog.
2. **Top nav** — logo, Features, Compare, Changelog, Docs, GitHub stars badge,
   **Download** CTA. Keyboard-shortcut flourish optional (Conductor/Zed do `D`).
3. **Hero** — H1 (positioning line, see §6), one-sentence subhead, dual CTA
   (Download / Try the live demo). Multi-engine logo strip (Claude Code, Codex,
   Cursor, Antigravity, Kilo Code).
4. **Live demo centerpiece** — embed the existing `docs/demo/` kanban (iframe or
   linked-out "open the live demo"). This is the Conductor move: a real,
   interactive product surface instead of a static screenshot.
5. **The two big bets** — two side-by-side blocks: *Attach, don't own* and
   *GitHub issues are the state machine*. This is the differentiation core.
6. **Feature highlights** — 4–6 cards linking into `/features/`: Flow canvas,
   kanban, group chat, multi-engine, auto-fix-deploy, resume-on-demand.
7. **Social proof** — GitHub stars + star-history chart (already in README),
   "one-person project" honest framing, any real user quotes if available.
8. **Compare teaser** — condensed matrix snapshot → `/compare/`.
9. **Install** — curl one-liner + Homebrew + DMG, demo link.
10. **Footer** — Product / Resources / Compare / Legal / Connect columns.

## 6. Positioning & copy

- **Owner voice:** confident peer-of-Omnara/Conductor, not "humble indie."
  Omnara's lesson: a small tool's site can read like a category leader.
- **Hero H1 candidates** (pick during build):
  - "Every coding agent on your Mac. One board."
  - "Start the next while Claude builds the first." (existing README line)
- **The uncopyable paragraph** (from `99-oss-assessment.md`, lightly adapted)
  anchors `/why/`:
  > CCC is a kanban that treats your coding agent's on-disk state as truth. It
  > shows every session you have — terminal, headless, or dashboard-spawned —
  > and turns GitHub issues into a pipeline. Unlike worktree-per-task
  > orchestrators, it doesn't care how you launched the agent. Unlike
  > observability dashboards, it writes back: spawn, resume, verify, close.
- **Honesty as a feature:** the compare matrix lists where CCC *loses*
  (Windows/Linux is macOS-first; no branch/fork; etc.). This mirrors the
  existing `#compare` section on the current site and Conductor's honest cells.

## 7. Compare content (data already in hand)

Source of truth: `competitor-analysis/01-unified-matrix.md` +
`competitor-analysis/00-master-listing.md` + per-tool profiles in
`competitor-analysis/projects/`. Peer set for the public matrix:

- Vibe Kanban, opcode, Claude Squad, Crystal/Sculptor, Conductor, Omnara.
- Axes: attach-to-unspawned-sessions, multi-engine, kanban state machine,
  GitHub issue lifecycle, Flow canvas, group chat, local-survives-close,
  readable source, native app, Windows/Linux, price/OSS.
- Honest cells: CCC marked ⚠️/✗ where true (macOS-first, no fork, etc.).

Three dedicated vs-pages (Vibe Kanban, Conductor, Omnara) each follow one
template: positioning contrast → 2-column matrix → "choose CCC if / choose them
if" → CTA.

## 8. Architecture (files)

```
site/
  index.html                # Home
  features/index.html
  compare/index.html        # hub + matrix
  compare/vibe-kanban/index.html
  compare/conductor/index.html
  compare/omnara/index.html
  why/index.html
  changelog/index.html
  roadmap/index.html
  install/index.html
  assets/
    css/site.css            # shared design system (tokens, layout, components)
    js/site.js              # nav, mobile menu, scroll-reveal, theme, stars fetch
    img/                    # site-specific imagery (reuse docs/images where possible)
  _partials/                # OPTIONAL: HTML snippets if we script a tiny include step
```

- **Shared chrome** (nav + footer) is duplicated per page (acceptable at ~10
  pages, no-build constraint). If duplication becomes painful, a tiny
  `build.sh` that concatenates `_partials/` is allowed — but **not** a JS
  framework. Default: hand-duplicate, keep it simple.
- **Design system in `site.css`:** CSS custom properties for color, type scale,
  spacing. Dark theme primary (Omnara/Conductor/Warp all default dark), with the
  warm-neutral accent direction (Conductor's stone palette reads premium and
  un-SaaS). Single accent color. Geist or Inter + a mono for terminal bits.
- **Each unit is independently understandable:** one page = one file; shared
  look = `site.css`; shared behavior = `site.js`. A reader can open any page and
  understand it without reading the others.

## 9. Data sources (no manual duplication where avoidable)

- Changelog page: render from `CHANGELOG.md` + `changelog.d/` (a small JS fetch
  + render, or a one-time generation script — TBD in plan; prefer static
  pre-render so the page works with JS off).
- Roadmap page: from `docs/roadmap.md`.
- GitHub stars badge: client-side fetch of the GitHub API (cached, graceful
  fallback to a static number).
- Demo: link to / embed existing `docs/demo/`.

## 10. Non-goals (YAGNI)

- No pricing page (CCC is free/OSS; an install page covers it).
- No accounts, no backend, no forms, no analytics beyond what the repo already
  uses (telemetry is a separate, existing concern — not added here).
- No comparison to Cursor/Warp/Zed (explicit owner decision).
- No blog in phase 1 (changelog covers "we ship"). Blog is a later option.
- No i18n.

## 11. Success criteria

- ~10 pages, each visually polished and consistent via one shared design system.
- Home page has a working, interactive demo surface (the real `docs/demo/`).
- Compare hub + 3 vs-pages with accurate, honest data from the research folder.
- Zero build step; opens by double-clicking any `.html` or via `python3 -m
  http.server`.
- Reads like a peer of Omnara/Conductor, not a hobby README.
- Passes OSS hygiene: no private data, honest cells.

## 12. Open questions for owner

1. Final domain (`ccc.dev`?) — affects canonical URLs / CNAME, but not the
   build. Can be set last.
2. Dark vs warm-light theme as primary. Recommendation: **dark** (peer norm).
3. Include `/docs/` migration in this pass or defer to phase 2.
   Recommendation: **defer** (don't block launch).
