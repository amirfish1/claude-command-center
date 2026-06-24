# CCC Marketing Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a comprehensive, ~10-page static marketing + positioning website for CCC (Claude Command Center) at the quality of peer dev-tool sites (Omnara, Conductor), with zero build step.

**Architecture:** Pure static HTML/CSS/JS under a new top-level `site/` directory. One shared design system (`assets/css/site.css`), one shared behavior file (`assets/js/site.js`). Nav + footer are hand-duplicated per page (≤10 pages, no-build constraint). The home page embeds the existing `docs/demo/` kanban as the interactive centerpiece. Compare data comes verbatim from `competitor-analysis/`.

**Tech Stack:** HTML5, CSS custom properties (no preprocessor), vanilla ES (no framework, no npm, no bundler). Served by GitHub Pages / any static host. Verified locally with `python3 -m http.server` + the `browse` skill.

## Global Constraints

- **No build step, no bundler, no npm, no framework.** Hand-authored static files only. (Spec §3, repo CLAUDE.md.)
- **New directory `site/`** — do NOT modify the live `docs/index.html` or `docs/demo/`. (Spec §2.)
- **Peer comparisons only:** Omnara, Conductor, Vibe Kanban, opcode, Claude Squad, Crystal/Sculptor. **Never** compare to Cursor, Warp, or Zed. (Spec §1.)
- **Honest cells:** mark CCC ⚠️/❌ where true (macOS-first, no worktrees, no fork, no mobile/remote/cost). Data verbatim from `competitor-analysis/01-unified-matrix.md`. (Spec §6, §7.)
- **OSS hygiene:** no private paths, client names, PII, or real secrets. Public repo. (CLAUDE.md.)
- **Dark theme primary**, single accent, warm-neutral direction. Geist or Inter + a mono. (Spec §8.)
- **Each page is one self-contained `.html` file**; shared look in `site.css`, shared behavior in `site.js`. (Spec §8.)
- **Reuse assets:** link/iframe `../docs/demo/`, reuse `docs/images/` where useful. (Spec §3.)
- **Commit style:** `git commit --only <paths> -m "type(scope): subject"`. Scope `site`. Do NOT push, do NOT add `changelog.d/`. (CLAUDE.md Tier A.)

---

## File Structure

```
site/
  index.html                      # Home
  features/index.html             # Features deep-dive
  compare/index.html              # Compare hub + full matrix
  compare/vibe-kanban/index.html  # vs Vibe Kanban
  compare/conductor/index.html    # vs Conductor
  compare/omnara/index.html       # vs Omnara
  why/index.html                  # Manifesto
  changelog/index.html            # Shipping velocity
  roadmap/index.html              # Now / Next / Considering
  install/index.html              # Install + download
  assets/
    css/site.css                  # Design system: tokens, layout, components
    js/site.js                    # Nav toggle, scroll-reveal, GitHub stars fetch
    img/                          # Site-specific imagery (copied/symlinked from docs/images)
  README.md                       # How to run/deploy the site (no build)
```

**Shared chrome contract (defined in Task 1, reused verbatim everywhere):**
- `<header class="site-nav">…</header>` — logo, links (Features, Compare, Why, Changelog, Roadmap, Docs↗, GitHub stars), Download CTA, mobile toggle.
- `<footer class="site-footer">…</footer>` — 5 columns (Product / Resources / Compare / Project / Connect), copyright.
- Links between pages are **root-relative** (`/features/`, `/compare/`) so the site works at a domain root. A `<base>`-free, relative-from-root scheme. Local testing uses `python3 -m http.server` from inside `site/` so `/` resolves correctly.

---

## Verification approach (replaces unit-test TDD)

This is a static content site; there is no unit-test harness. Each page task uses this verification cycle instead:

1. **Serve:** `cd site && python3 -m http.server 8099` (run once, in background, for the whole plan).
2. **Render check** via the `browse` skill (or `mcp__plugin_chrome-devtools-mcp_chrome-devtools__navigate_page`): open `http://localhost:8099/<route>`, screenshot, and assert the key elements named in the task's checklist are present and laid out (no overflow, nav/footer render, no console errors).
3. **HTML sanity:** `python3 -c "import html.parser,sys; ..."` tag-balance check (provided in Task 1) — no unclosed critical tags.
4. **Link check:** every internal `href` in the page resolves to an existing file under `site/`.

Each task's "verify" steps spell these out concretely.

---

### Task 1: Scaffold + design system + shared chrome

**Files:**
- Create: `site/assets/css/site.css`
- Create: `site/assets/js/site.js`
- Create: `site/index.html` (minimal stub that proves the design system + nav + footer; replaced/expanded in Task 2)
- Create: `site/README.md`
- Create: `site/assets/.gitkeep` for `img/` (or copy a couple images)

**Interfaces:**
- Produces (consumed by every later task):
  - CSS classes: `.site-nav`, `.nav-inner`, `.nav-links`, `.nav-cta`, `.nav-toggle`, `.site-footer`, `.footer-cols`, `.wrap` (max-width container), `.section`, `.eyebrow`, `.h1`, `.h2`, `.lede`, `.btn`, `.btn-primary`, `.btn-ghost`, `.card`, `.card-grid`, `.pill`, `.matrix` (compare table), `.yes`/`.no`/`.partial`/`.us` (cell states), `.reveal` (scroll-reveal hook), `.mono`, `.accent`.
  - CSS custom properties on `:root`: `--bg`, `--bg-2`, `--surface`, `--fg`, `--fg-dim`, `--border`, `--accent`, `--accent-2`, `--yes`, `--no`, `--maxw`, type scale (`--step-0`…`--step-6`), `--mono`, `--sans`.
  - JS behaviors: mobile nav toggle (`.nav-toggle` flips `body.nav-open`), `IntersectionObserver` revealing `.reveal` → `.is-visible`, GitHub stars fetch into `[data-gh-stars]` with static fallback.
  - The exact `<header>` and `<footer>` HTML blocks (the "shared chrome"), pasted verbatim into every page.

- [ ] **Step 1: Create `site/assets/css/site.css` — design tokens + base.**

```css
/* CCC site — design system. No build step. Dark, warm-neutral, single accent. */
:root{
  --bg:#0e0c0b; --bg-2:#141110; --surface:#1c1917; --surface-2:#232020;
  --fg:#eae8e6; --fg-dim:#a8a29e; --border:#2b2724;
  --accent:#ee7e00; --accent-2:#ff9d33;
  --yes:#0ac864; --no:#7c7873; --partial:#d8a200;
  --maxw:1120px;
  --sans:"Geist","Inter",system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  --mono:"Geist Mono","SF Mono",ui-monospace,Menlo,monospace;
  --step-0:1rem; --step-1:1.125rem; --step-2:1.375rem; --step-3:1.75rem;
  --step-4:2.5rem; --step-5:3.5rem; --step-6:4.5rem;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--sans);
  font-size:var(--step-0);line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 24px}
.section{padding:96px 0}
.eyebrow{font:600 .8rem/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent)}
.h1{font-size:clamp(2.4rem,6vw,var(--step-6));line-height:1.04;font-weight:600;letter-spacing:-.02em;margin:.3em 0}
.h2{font-size:clamp(1.8rem,4vw,var(--step-4));line-height:1.1;font-weight:600;letter-spacing:-.01em;margin:0 0 .4em}
.lede{font-size:var(--step-2);color:var(--fg-dim);max-width:60ch}
.mono{font-family:var(--mono)}
.accent{color:var(--accent)}
.btn{display:inline-flex;align-items:center;gap:.5em;padding:.7em 1.2em;border-radius:10px;
  font-weight:600;font-size:var(--step-0);border:1px solid transparent;cursor:pointer;transition:.15s}
.btn-primary{background:var(--accent);color:#1a1206}
.btn-primary:hover{background:var(--accent-2)}
.btn-ghost{border-color:var(--border);color:var(--fg)}
.btn-ghost:hover{border-color:var(--fg-dim);background:var(--surface)}
.pill{display:inline-flex;align-items:center;gap:.4em;padding:.35em .8em;border:1px solid var(--border);
  border-radius:999px;font:600 .85rem/1 var(--sans);color:var(--fg-dim);background:var(--bg-2)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:28px}
.card-grid{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.reveal{opacity:0;transform:translateY(16px);transition:.6s ease}
.reveal.is-visible{opacity:1;transform:none}
/* nav */
.site-nav{position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--bg) 86%,transparent);
  backdrop-filter:blur(10px);border-bottom:1px solid var(--border)}
.nav-inner{max-width:var(--maxw);margin:0 auto;padding:14px 24px;display:flex;align-items:center;gap:24px}
.nav-brand{font-weight:700;letter-spacing:-.02em;display:flex;align-items:center;gap:.5em}
.nav-links{display:flex;gap:22px;margin-left:auto;align-items:center}
.nav-links a{color:var(--fg-dim);font-size:.95rem}
.nav-links a:hover{color:var(--fg)}
.nav-cta{margin-left:8px}
.nav-toggle{display:none;margin-left:auto;background:none;border:1px solid var(--border);
  border-radius:8px;color:var(--fg);padding:8px 10px;cursor:pointer}
/* footer */
.site-footer{border-top:1px solid var(--border);background:var(--bg-2);padding:64px 0 40px}
.footer-cols{display:grid;gap:32px;grid-template-columns:1.4fr repeat(4,1fr)}
.footer-cols h4{font:600 .8rem/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--fg-dim);margin:0 0 14px}
.footer-cols a{display:block;color:var(--fg-dim);padding:5px 0;font-size:.92rem}
.footer-cols a:hover{color:var(--fg)}
.footer-base{max-width:var(--maxw);margin:40px auto 0;padding:24px 24px 0;border-top:1px solid var(--border);
  color:var(--fg-dim);font-size:.85rem;display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px}
/* compare matrix */
.matrix{width:100%;border-collapse:collapse;font-size:.92rem}
.matrix th,.matrix td{padding:12px 14px;border-bottom:1px solid var(--border);text-align:center}
.matrix th:first-child,.matrix td:first-child{text-align:left;color:var(--fg)}
.matrix thead th{font:600 .85rem/1.2 var(--sans);color:var(--fg-dim);vertical-align:bottom}
.matrix .us{color:var(--accent)}
.matrix td.yes{color:var(--yes);font-weight:600}
.matrix td.no{color:var(--no)}
.matrix td.partial{color:var(--partial)}
.matrix-scroll{overflow-x:auto;border:1px solid var(--border);border-radius:16px}
@media(max-width:820px){
  .nav-links{display:none}
  .nav-toggle{display:inline-block}
  body.nav-open .nav-links{display:flex;position:absolute;top:100%;left:0;right:0;flex-direction:column;
    background:var(--bg-2);border-bottom:1px solid var(--border);padding:16px 24px;gap:8px}
  body.nav-open .nav-links a{padding:8px 0}
  .footer-cols{grid-template-columns:1fr 1fr}
  .section{padding:64px 0}
}
```

- [ ] **Step 2: Create `site/assets/js/site.js` — shared behavior.**

```js
// CCC site behavior. No deps.
(function(){
  // mobile nav
  var t=document.querySelector('.nav-toggle');
  if(t)t.addEventListener('click',function(){document.body.classList.toggle('nav-open')});
  // scroll reveal
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('is-visible');io.unobserve(e.target)}})
  },{threshold:.12});
  document.querySelectorAll('.reveal').forEach(function(el){io.observe(el)});
  // github stars (graceful fallback to whatever text is already in the element)
  var el=document.querySelector('[data-gh-stars]');
  if(el){fetch('https://api.github.com/repos/amirfish1/claude-command-center')
    .then(function(r){return r.json()}).then(function(d){
      if(d&&typeof d.stargazers_count==='number'){
        el.textContent=Intl.NumberFormat().format(d.stargazers_count);
      }}).catch(function(){});}
})();
```

- [ ] **Step 3: Define the shared chrome. Create `site/index.html` stub using it.**

Paste this exact `<header>` and `<footer>` into the stub. (Later tasks copy these blocks verbatim, changing only the active-link emphasis if desired.)

```html
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CCC — Claude Command Center</title>
<meta name="description" content="One local dashboard for every Claude Code, Codex, Cursor, and Antigravity session on your Mac. Attach, don't own.">
<link rel="stylesheet" href="/assets/css/site.css">
</head><body>
<header class="site-nav"><div class="nav-inner">
  <a class="nav-brand" href="/">▦ CCC</a>
  <nav class="nav-links">
    <a href="/features/">Features</a>
    <a href="/compare/">Compare</a>
    <a href="/why/">Why CCC</a>
    <a href="/changelog/">Changelog</a>
    <a href="/roadmap/">Roadmap</a>
    <a href="https://github.com/amirfish1/claude-command-center">GitHub ★ <span data-gh-stars>star</span></a>
    <a class="btn btn-primary nav-cta" href="/install/">Download</a>
  </nav>
  <button class="nav-toggle" aria-label="Menu">☰</button>
</div></header>

<main>
  <section class="section"><div class="wrap">
    <p class="eyebrow">Scaffold OK</p>
    <h1 class="h1">Design system live.</h1>
    <p class="lede">Replaced by the real home page in Task 2.</p>
  </div></section>
</main>

<footer class="site-footer"><div class="wrap footer-cols">
  <div><a class="nav-brand" href="/">▦ CCC</a><p style="color:var(--fg-dim);max-width:30ch">
    A local command center for every coding agent on your Mac. Open source.</p></div>
  <div><h4>Product</h4><a href="/features/">Features</a><a href="/why/">Why CCC</a><a href="/install/">Download</a><a href="/demo/">Live demo</a></div>
  <div><h4>Resources</h4><a href="/changelog/">Changelog</a><a href="/roadmap/">Roadmap</a><a href="https://github.com/amirfish1/claude-command-center">GitHub</a></div>
  <div><h4>Compare</h4><a href="/compare/">All tools</a><a href="/compare/vibe-kanban/">vs Vibe Kanban</a><a href="/compare/conductor/">vs Conductor</a><a href="/compare/omnara/">vs Omnara</a></div>
  <div><h4>Connect</h4><a href="https://github.com/amirfish1/claude-command-center/issues">Issues</a><a href="https://github.com/amirfish1/claude-command-center/releases">Releases</a></div>
</div>
<div class="footer-base"><span>© 2026 Claude Command Center · MIT</span><span class="mono">Built with CCC.</span></div>
</footer>
<script src="/assets/js/site.js"></script>
</body></html>
```

Note: the footer links to `/demo/` — in Task 10 we wire the demo (symlink or copy `docs/demo/` into `site/demo/`, or point these links to the live `ccc.amirfish.ai/demo`). Until then `/demo/` 404s locally; that is expected and resolved in Task 10.

- [ ] **Step 4: Create `site/README.md`.**

```markdown
# CCC site

Static marketing/positioning site. No build step.

## Run locally
    cd site && python3 -m http.server 8099
    open http://localhost:8099/

## Deploy
Any static host. Intended for a standalone domain (e.g. ccc.dev), served from
this `site/` directory. Separate from the existing `docs/` GitHub Pages site
(ccc.amirfish.ai). Root-relative links assume the site is served at domain root.
```

- [ ] **Step 5: Verify — serve and render.**

Run (background): `cd /Users/amirfish/Apps/claude-command-center/site && python3 -m http.server 8099`
Then open `http://localhost:8099/` via the `browse` skill, screenshot.
Expected: dark page, sticky nav with "▦ CCC" + links + orange Download button, "Design system live." heading, 5-column footer. No console errors. Resize to 400px wide → nav collapses to ☰, footer to 2 columns.

- [ ] **Step 6: HTML sanity + link check.**

Run:
```bash
python3 - <<'PY'
import re,glob,os
root="/Users/amirfish/Apps/claude-command-center/site"
for f in glob.glob(root+"/**/*.html",recursive=True):
    s=open(f).read()
    for tag in ("html","head","body","header","footer","main"):
        if s.count("<"+tag)!=s.count("</"+tag+">"):
            print("UNBALANCED",tag,f)
    for href in re.findall(r'href="(/[^"#]*)"',s):
        if href.startswith("/assets")or href in ("/",):
            p=root+href; p=p if os.path.exists(p) else p.rstrip("/")+"/index.html"
            if not os.path.exists(p) and not href.startswith("/assets"):
                print("DEADLINK",href,"in",os.path.relpath(f,root))
print("sanity done")
PY
```
Expected: `sanity done`, no `UNBALANCED`. `DEADLINK /demo/` allowed (resolved Task 10). Internal page links (`/features/` etc.) will report dead until their tasks land — acceptable mid-plan.

- [ ] **Step 7: Commit.**

```bash
git add site/assets/css/site.css site/assets/js/site.js site/index.html site/README.md
git commit --only site/assets/css/site.css site/assets/js/site.js site/index.html site/README.md -m "feat(site): design system, shared chrome, scaffold"
```

---

### Task 2: Home page

**Files:**
- Modify: `site/index.html` (replace the stub `<main>` with the real home sections; keep the Task 1 header/footer/head verbatim).

**Interfaces:**
- Consumes: all classes/chrome from Task 1.
- Produces: nothing other tasks depend on.

**Section order (Spec §5):** release pill → hero (H1 + subhead + dual CTA + engine strip) → live demo centerpiece → two big bets → feature highlights → social proof (stars + star-history) → compare teaser → install → footer.

- [ ] **Step 1: Replace `<main>` with the home content.**

Use this content (fill the `<main>`…`</main>` block). Copy is final, not placeholder:

```html
<main>
<!-- HERO -->
<section class="section" style="padding-top:72px">
 <div class="wrap reveal">
  <a class="pill" href="/changelog/">● CCC v4.6.0 — see what's new →</a>
  <h1 class="h1">Every coding agent on your Mac.<br><span class="accent">One board.</span></h1>
  <p class="lede">CCC attaches to every Claude Code, Codex, Cursor, and Antigravity session already running on your machine — terminal, headless, or dashboard-spawned — and turns them into one live kanban. Start the next while the first still builds.</p>
  <div style="display:flex;gap:12px;margin-top:28px;flex-wrap:wrap">
    <a class="btn btn-primary" href="/install/">Download for macOS</a>
    <a class="btn btn-ghost" href="/demo/">Try the live demo →</a>
  </div>
  <p class="mono" style="color:var(--fg-dim);margin-top:28px;font-size:.85rem">
    Works with Claude Code · Codex · Cursor · Antigravity · Kilo Code</p>
 </div>
</section>

<!-- LIVE DEMO CENTERPIECE -->
<section class="section" style="padding-top:0">
 <div class="wrap reveal">
  <div class="card" style="padding:0;overflow:hidden">
    <div style="display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--border)">
      <span style="width:11px;height:11px;border-radius:50%;background:#ff5f57"></span>
      <span style="width:11px;height:11px;border-radius:50%;background:#febc2e"></span>
      <span style="width:11px;height:11px;border-radius:50%;background:#28c840"></span>
      <span class="mono" style="color:var(--fg-dim);margin-left:8px;font-size:.8rem">CCC — live demo, seeded data</span>
    </div>
    <iframe src="/demo/" title="CCC live demo" loading="lazy"
      style="width:100%;height:620px;border:0;display:block;background:var(--bg-2)"></iframe>
  </div>
  <p style="text-align:center;color:var(--fg-dim);margin-top:14px">
    Real CCC, seeded with fake data. <a class="accent" href="/demo/">Open it full-screen →</a></p>
 </div>
</section>

<!-- TWO BIG BETS -->
<section class="section" style="background:var(--bg-2)">
 <div class="wrap">
  <p class="eyebrow reveal">Two bets nobody else makes</p>
  <div class="card-grid" style="margin-top:24px">
    <div class="card reveal">
      <h2 class="h2">Attach, don't own.</h2>
      <p style="color:var(--fg-dim)">Most tools only see sessions they launched. CCC reads your agent's on-disk state as the source of truth, so a session you started in any terminal, by any tool, shows up automatically. Nothing slips through.</p>
    </div>
    <div class="card reveal">
      <h2 class="h2">Issues are the state machine.</h2>
      <p style="color:var(--fg-dim)">Start from a GitHub issue in one click. CCC auto-labels it in-progress, renders the issue and comments on the card, and closes it with the commit SHA when you verify. A full bidirectional loop, not just a list.</p>
    </div>
  </div>
 </div>
</section>

<!-- FEATURE HIGHLIGHTS -->
<section class="section">
 <div class="wrap">
  <p class="eyebrow reveal">What's on the board</p>
  <h2 class="h2 reveal">Built for shipping many things at once.</h2>
  <div class="card-grid reveal" style="margin-top:28px">
    <div class="card"><h3>Flow canvas</h3><p style="color:var(--fg-dim)">A Miro-style spatial board of repos → objects → sessions. Arrange your work in space, not just a list.</p></div>
    <div class="card"><h3>Signal-driven kanban</h3><p style="color:var(--fg-dim)">Eight columns, auto-classified from each session's real state. Drag to override; overrides decay back to the truth.</p></div>
    <div class="card"><h3>Group chat</h3><p style="color:var(--fg-dim)">N sessions coordinate through a shared file — agents talk to each other while they work.</p></div>
    <div class="card"><h3>Multi-engine</h3><p style="color:var(--fg-dim)">Claude Code, Codex, Cursor, and Antigravity on one board. Spawn across all of them.</p></div>
    <div class="card"><h3>Auto-fix-deploy</h3><p style="color:var(--fg-dim)">Polls your deploys; on a new production error it spawns a fix session, deduped by SHA. Nobody else ships this.</p></div>
    <div class="card"><h3>Resume on demand</h3><p style="color:var(--fg-dim)">Wake a dormant session and keep talking to it from the browser — the input pipe stays open.</p></div>
  </div>
  <p style="margin-top:24px"><a class="btn btn-ghost reveal" href="/features/">All features →</a></p>
 </div>
</section>

<!-- SOCIAL PROOF -->
<section class="section" style="background:var(--bg-2)">
 <div class="wrap reveal" style="text-align:center">
  <p class="eyebrow">Open source · one-person project</p>
  <h2 class="h2">Read the whole thing in an afternoon.</h2>
  <p class="lede" style="margin:0 auto">A stdlib-only Python server and a single HTML app. No Electron, no account, no cloud, no DB. <strong><span data-gh-stars>★</span> stars</strong> and counting.</p>
  <p style="margin-top:24px">
    <img alt="Star history" loading="lazy"
      src="https://api.star-history.com/svg?repos=amirfish1/claude-command-center&type=Date"
      style="max-width:680px;width:100%;border:1px solid var(--border);border-radius:12px"></p>
 </div>
</section>

<!-- COMPARE TEASER -->
<section class="section">
 <div class="wrap reveal" style="text-align:center">
  <p class="eyebrow">Honest comparison</p>
  <h2 class="h2">Where CCC wins — and where it doesn't.</h2>
  <p class="lede" style="margin:0 auto 24px">Every tool in this space makes different bets. We list where CCC loses too.</p>
  <a class="btn btn-ghost" href="/compare/">See the full matrix →</a>
 </div>
</section>

<!-- INSTALL -->
<section class="section" style="background:var(--bg-2)">
 <div class="wrap reveal" style="text-align:center">
  <h2 class="h2">Install in one line.</h2>
  <pre class="mono" style="display:inline-block;text-align:left;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 22px;overflow-x:auto;max-width:100%"><code>curl -fsSL https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh | bash</code></pre>
  <div style="display:flex;gap:12px;justify-content:center;margin-top:22px;flex-wrap:wrap">
    <a class="btn btn-primary" href="/install/">All install options</a>
    <a class="btn btn-ghost" href="https://github.com/amirfish1/claude-command-center/releases/latest">Download DMG</a>
  </div>
 </div>
</section>
</main>
```

- [ ] **Step 2: Verify — render the home page.**

Open `http://localhost:8099/` via `browse`, full-page screenshot.
Assert present: release pill, H1 "Every coding agent on your Mac. One board.", dual CTA buttons, demo iframe frame (the `docs/demo` may not load until Task 10 wires `/demo/`; the framed card must still render), two-bets cards, six feature cards, star-history image, compare teaser, install code block. Scroll → `.reveal` elements fade in. No console errors except possibly the `/demo/` 404 (expected pre-Task-10).

- [ ] **Step 3: Commit.**

```bash
git commit --only site/index.html -m "feat(site): home page"
```

---

### Task 3: Features page

**Files:**
- Create: `site/features/index.html`

**Interfaces:**
- Consumes: Task 1 chrome + classes. Copy the verbatim `<head>`, `<header>`, `<footer>`, `<script>` from `site/index.html`; change `<title>` to `Features — CCC` and the meta description to a features-specific line.

- [ ] **Step 1: Create the page.** Header/footer copied verbatim from home. `<main>` contains an intro hero then one detailed block per feature. Use these blocks (alternate text/visual; final copy):

Intro:
```html
<section class="section"><div class="wrap reveal">
  <p class="eyebrow">Features</p>
  <h1 class="h1">One board. Every agent. Full write-path.</h1>
  <p class="lede">CCC is the union of two archetypes — it observes every session like a dashboard, and it writes back like an orchestrator: spawn, resume, verify, close.</p>
</div></section>
```

Then a `.section` per feature, alternating background (`var(--bg-2)` on odd ones), each with `.wrap`, an `.eyebrow`, `.h2`, a paragraph, and a bullet list. Author one block for each of these eight features, using the facts from `competitor-analysis/00-master-listing.md` and `01-unified-matrix.md` (do not invent capabilities CCC lacks — CCC has **no** worktrees, mobile, remote, cost-tracking, fork):

1. **Attach-first session discovery** — reads `~/.claude/projects/*.jsonl` + session registry + 2 hooks; any terminal/headless/spawned session appears automatically. Contrast: 14 of 20 surveyed tools only see what they launched.
2. **Signal-driven kanban** — 8 columns, rule-based auto-classify, drag-to-override that decays back to truth.
3. **GitHub issue pipeline** — start-from-issue → auto-label in-progress → render issue+comments on card → verify → close with commit SHA → archive = close as not-planned. The full loop; only Vibe Kanban & Kanban Code touch this and neither closes-with-reason.
4. **Flow canvas** — spatial repos→objects→sessions board (cite `docs/flow-workspace.md` concepts: nodes, parents, organize, edges). Unique among peers.
5. **Group chat** — N sessions coordinate via a shared file.
6. **Multi-engine** — Claude Code, Codex, Cursor, Antigravity; spawn across all; note transcript/UX parity varies (honest, per README engine matrix).
7. **Auto-fix-deploy** — poll deploys, spawn `/fix-deploy` on new prod error, dedupe by SHA. Unique (0/20 competitors).
8. **Local & hackable** — stdlib-only server + single HTML, no DB/Electron/account, plain-JSON hand-editable sidecars, localhost-by-default.

End with a CTA band linking `/install/` and `/compare/`.

- [ ] **Step 2: Verify.** Open `http://localhost:8099/features/`, screenshot. Assert all 8 feature blocks render with headings; nav/footer present; alternating backgrounds visible; no console errors. Run the Task 1 Step 6 sanity script again — `/features/` no longer a deadlink from home.

- [ ] **Step 3: Commit.** `git commit --only site/features/index.html -m "feat(site): features page"`

---

### Task 4: Compare hub + full matrix

**Files:**
- Create: `site/compare/index.html`

**Interfaces:**
- Consumes: Task 1 chrome + `.matrix`/`.matrix-scroll`/`.yes`/`.no`/`.partial`/`.us`.
- Produces: the canonical peer matrix that the three vs-pages link back to.

- [ ] **Step 1: Create the page.** Header/footer verbatim. `<main>`:

Intro hero: eyebrow "Compare", H1 "How CCC differs from the other tools.", lede noting peer-only scope and honest losses.

Then a `.matrix-scroll > table.matrix`. Columns: **feature**, **CCC** (`class="us"`), Vibe Kanban, opcode, Claude Squad, Crystal/Sculptor, Conductor, Omnara. Rows + cell states taken **verbatim** from `competitor-analysis/01-unified-matrix.md` for the peer columns, plus Conductor/Omnara filled from the research agents' findings (Conductor: closed-source native Mac, Claude+Codex+Cursor, worktrees ✅, no attach-to-unspawned, no kanban, no GH-issue loop; Omnara: omni-device remote, Claude+Codex, no attach-to-unspawned-by-others kanban, no worktree-per-task focus, mobile ✅, voice ✅, free hosted/closed). Rows to include (each cell yes/partial/no with the right class):

| Row | CCC | Vibe Kanban | opcode | Claude Squad | Crystal/Sculptor | Conductor | Omnara |
|---|---|---|---|---|---|---|---|
| Sees sessions it didn't spawn | yes | partial | yes | no | no | no | no |
| Kanban board w/ columns | yes | yes | no | no | no | no | no |
| GitHub issue lifecycle (close-with-reason) | yes | partial | no | partial | no | no | no |
| Flow / spatial canvas | yes | no | no | no | no | no | no |
| Group chat (N sessions) | yes | no | no | no | no | no | no |
| Multi-engine (3+) | yes | partial | no | partial | partial | partial | partial |
| Auto-fix-deploy | yes | no | no | no | no | no | no |
| Local, survives close | yes | partial | yes | yes | yes | yes | partial |
| Readable source (afternoon) | yes (stdlib) | partial | partial | partial | no (archived) | no (closed) | no (closed) |
| Native signed app | partial (DMG+Sparkle) | no | yes | no | yes | yes | yes |
| Git worktrees | no | yes | no | yes | yes | yes | no |
| Mobile / remote | no | yes | no | no | no | no | yes |
| Cost / token tracking | no | no | yes | no | partial | no | no |
| Open source | yes | yes | yes | yes | yes (archived) | no | no |

Map yes→`class="yes"`>✓, partial→`class="partial"`>partial (or the parenthetical), no→`class="no"`>no. CCC column cells use `class="us yes"` etc. — the `.us` color plus the state.

Below the table: three "vs" cards linking `/compare/vibe-kanban/`, `/compare/conductor/`, `/compare/omnara/` with a one-line hook each. Closing CTA to `/install/`.

- [ ] **Step 2: Verify.** Open `http://localhost:8099/compare/`, screenshot. Assert: table renders with 8 tool columns, CCC column tinted accent, ✓/no/partial colored correctly, at least one CCC `no` cell visible (worktrees/mobile/cost) proving honesty, table horizontally scrolls on narrow width, three vs-cards present. Run sanity script.

- [ ] **Step 3: Commit.** `git commit --only site/compare/index.html -m "feat(site): compare hub + matrix"`

---

### Task 5: Three vs-pages

**Files:**
- Create: `site/compare/vibe-kanban/index.html`
- Create: `site/compare/conductor/index.html`
- Create: `site/compare/omnara/index.html`

**Interfaces:**
- Consumes: Task 1 chrome + `.matrix`. Each page = same template, different data.

**Template (each page):** header/footer verbatim → hero (`eyebrow "CCC vs <Tool>"`, H1, one-paragraph positioning contrast) → a focused 2-column `.matrix` (feature | CCC | Tool, ~8 rows pulled from the hub matrix's relevant columns) → two `.card`s side by side: **"Choose CCC if…"** / **"Choose <Tool> if…"** (3 honest bullets each) → CTA to `/install/` and back-link to `/compare/`.

- [ ] **Step 1: vs Vibe Kanban.** Positioning: Vibe Kanban is the 25k-star worktree-per-task orchestrator — you launch agents *through* it; it's strong on PR flow and worktrees. CCC attaches to sessions you started anywhere and closes GitHub issues with a reason. "Choose CCC if" = you live in the terminal and want the board to follow you / issue-pipeline / single-file hackable. "Choose Vibe Kanban if" = you want worktree-per-task, a big community, mobile. Use honest cells (CCC `no` on worktrees/mobile).

- [ ] **Step 2: vs Conductor.** Positioning: Conductor is a polished closed-source native Mac app, well-funded, worktree-isolated parallel agents with diff/PR review. CCC is open, stdlib-simple, attach-first, and adds the GitHub issue loop + Flow canvas + group chat. "Choose CCC if" = open source / attach-don't-own / issue pipeline. "Choose Conductor if" = you want a first-party-grade native app with worktree review and don't need to read the source.

- [ ] **Step 3: vs Omnara.** Positioning: Omnara is the omni-device remote (desktop+web+mobile+watch+voice, free hosted, YC). CCC is local-first, no account, single board on your Mac, deeper work-management integration (issues, Flow, group chat, auto-fix-deploy). "Choose CCC if" = local-only/no-account/issue-pipeline/hackable. "Choose Omnara if" = you want to drive agents from your phone/voice away from the desk. Honest: CCC `no` on mobile/remote/voice.

- [ ] **Step 4: Verify all three.** Open each route via `browse`, screenshot. Assert: hero, 2-col matrix, the two choose-if cards, working back-link to `/compare/`. Run sanity script — the three vs-pages no longer deadlink from hub/footer.

- [ ] **Step 5: Commit.** `git commit --only site/compare/vibe-kanban/index.html site/compare/conductor/index.html site/compare/omnara/index.html -m "feat(site): vs-pages (vibe-kanban, conductor, omnara)"`

---

### Task 6: Why CCC (manifesto)

**Files:**
- Create: `site/why/index.html`

- [ ] **Step 1: Create the page.** Header/footer verbatim. `<main>`: hero "Why CCC" → the uncopyable paragraph (Spec §6, verbatim) set large → two long-form sections expanding each bet ("Attach, don't own" with the on-disk-state-as-truth argument; "Issues are the state machine" with the full lifecycle) → a short "What CCC is not" honesty section (not worktree-per-task, not a mobile remote, macOS-first) → CTA. Pull substance from `competitor-analysis/99-oss-assessment.md` §(a). No invented claims.

- [ ] **Step 2: Verify.** Open `http://localhost:8099/why/`, screenshot. Assert the manifesto paragraph renders prominently, two bet-sections present, honesty section present. Run sanity script.

- [ ] **Step 3: Commit.** `git commit --only site/why/index.html -m "feat(site): why-ccc manifesto"`

---

### Task 7: Changelog page

**Files:**
- Create: `site/changelog/index.html`

**Decision:** Static pre-render (so the page works with JS off and at deploy time). The author reads `CHANGELOG.md` + the README "Recent" list and hand-writes the entries into the page at build-authoring time. (No runtime fetch of repo files — keeps it dependency-free and works offline.)

- [ ] **Step 1: Gather source.** Run:
```bash
sed -n '1,80p' /Users/amirfish/Apps/claude-command-center/CHANGELOG.md
ls /Users/amirfish/Apps/claude-command-center/changelog.d/
```
Use the most recent ~12 entries.

- [ ] **Step 2: Create the page.** Header/footer verbatim. `<main>`: hero "Changelog — we ship." → a vertical list; each entry = a `.card` with a `.pill` version + date and bullet(s). Lead with v4.6.0 (perf pass), v4.0.0 (Antigravity), and the dated README "Recent" items. Footer link to GitHub releases for the full history.

- [ ] **Step 3: Verify.** Open `http://localhost:8099/changelog/`, screenshot. Assert ≥8 dated entries render newest-first, version pills visible. Run sanity script.

- [ ] **Step 4: Commit.** `git commit --only site/changelog/index.html -m "feat(site): changelog page"`

---

### Task 8: Roadmap page

**Files:**
- Create: `site/roadmap/index.html`

- [ ] **Step 1: Gather source.** Run `cat /Users/amirfish/Apps/claude-command-center/docs/roadmap.md`. Map items into three buckets.

- [ ] **Step 2: Create the page.** Header/footer verbatim. `<main>`: hero "Roadmap" → three `.card`-column sections: **Now**, **Next**, **Considering** (Zed-style). Each bullet one line. Add an honest "from the competitive analysis, things we deliberately skip" note (worktrees, mobile, cost) linking `/why/`. If `docs/roadmap.md` is thin, supplement from `competitor-analysis/01-unified-matrix.md` "Features CCC lacks" table — framed as candidates, not promises.

- [ ] **Step 3: Verify.** Open `http://localhost:8099/roadmap/`, screenshot. Assert three buckets render. Run sanity script.

- [ ] **Step 4: Commit.** `git commit --only site/roadmap/index.html -m "feat(site): roadmap page"`

---

### Task 9: Install / Download page

**Files:**
- Create: `site/install/index.html`

- [ ] **Step 1: Create the page.** Header/footer verbatim. Pull exact commands from `README.md` (already read this session): curl one-liner (`CCC_FROM=readme bash`), Homebrew (`brew tap amirfish1/ccc && brew install ccc && ccc`), DMG (releases/latest, drag CCC.app), VS Code extension v0.1.0, "From source" git clone. `<main>`: hero "Install CCC" → a `.card` per method with a copy-ready `<pre><code>` block → a "Try before you install" card linking `/demo/` → requirements note (macOS first; Win/Linux partial). Each code block in `.mono` styling from Task 1.

- [ ] **Step 2: Verify.** Open `http://localhost:8099/install/`, screenshot. Assert all four install methods render with code blocks, demo link present. Run sanity script — `/install/` (the nav/hero Download target) now resolves everywhere.

- [ ] **Step 3: Commit.** `git commit --only site/install/index.html -m "feat(site): install page"`

---

### Task 10: Demo wiring, polish, meta/OG, final QA

**Files:**
- Create: `site/demo/` (copy of `docs/demo/`) OR repoint `/demo/` links to the live demo URL.
- Modify: every `site/**/*.html` `<head>` to add Open Graph + Twitter meta + favicon.
- Create: `site/assets/img/og.png` (or reuse `docs/images/`).

- [ ] **Step 1: Wire the demo.** Choose ONE and apply consistently:
  - (a) Copy the demo into the site: `cp -R /Users/amirfish/Apps/claude-command-center/docs/demo /Users/amirfish/Apps/claude-command-center/site/demo` — self-contained, works at any domain. Verify the copied demo's internal asset paths resolve when served from `site/` (it currently lives at `docs/demo/` with `static/` + `api/` siblings; check relative paths). If the demo uses root-absolute paths that break, prefer (b).
  - (b) Repoint all `/demo/` hrefs and the home iframe `src` to `https://ccc.amirfish.ai/demo/` (the already-live demo). Simpler, always works, but couples the new site to the old domain.
  Record the choice in `site/README.md`.

- [ ] **Step 2: Add meta/OG/favicon to every page `<head>`.** For each `site/**/*.html`, insert after the description meta:
```html
<meta property="og:title" content="CCC — Claude Command Center">
<meta property="og:description" content="One local board for every coding agent on your Mac. Attach, don't own.">
<meta property="og:type" content="website">
<meta property="og:image" content="/assets/img/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/assets/img/favicon.svg">
```
Create `site/assets/img/favicon.svg` (a simple ▦ glyph on accent bg) and `og.png` (reuse `docs/images/kanban.png` if present: `cp docs/images/kanban.png site/assets/img/og.png`).

- [ ] **Step 3: Cross-link + active-state pass.** Verify every nav link, footer link, and in-page CTA across all 10 pages resolves (run the Task 1 Step 6 sanity script — expect zero DEADLINK now, including `/demo/`). Fix any stragglers.

- [ ] **Step 4: Responsive QA.** Via `browse`, load `/`, `/features/`, `/compare/` at widths 1280, 768, 390. Screenshot each. Assert: nav collapses to ☰ under 820px and the menu opens on tap; matrix scrolls horizontally on mobile; no element overflows the viewport; text legible. Fix CSS in `site.css` if anything breaks.

- [ ] **Step 5: Full-site link + sanity sweep.** Re-run the sanity script over all of `site/`. Expected: `sanity done`, zero `UNBALANCED`, zero `DEADLINK`.

- [ ] **Step 6: Commit.**
```bash
git add site/
git commit --only $(git -C /Users/amirfish/Apps/claude-command-center diff --cached --name-only | tr '\n' ' ') -m "feat(site): demo wiring, meta/OG, responsive polish, final QA"
```
(If the demo was copied, this includes `site/demo/`. If repointed, it does not.)

---

## Self-Review (completed)

**Spec coverage:**
- §2 location `site/` → Task 1. §3 no-build/reuse → all tasks, Task 10 demo. §4 sitemap (10 pages) → Tasks 2–9. §5 home order → Task 2. §6 positioning/copy/honesty → Tasks 2,4,5,6. §7 compare data → Tasks 4,5. §8 architecture/design system → Task 1. §9 data sources (changelog/roadmap/stars/demo) → Tasks 1(stars),7,8,10. §10 non-goals respected (no pricing/blog/Cursor-Warp). §11 success criteria → Task 10 QA. §12 open questions: dark theme (decided, Task 1), `/docs/` deferred to phase 2 (out of this plan, noted), domain (set at deploy, no code impact).
- **Gap found & resolved:** Spec §4 lists `/docs/` as phase 2 — intentionally excluded from this plan; called out here so it isn't mistaken for a miss.

**Placeholder scan:** No "TBD/handle appropriately" left. Changelog/roadmap pull real data via the gather steps; compare data is tabulated verbatim. Copy is written, not stubbed.

**Type/name consistency:** Class names (`.matrix`, `.us`, `.yes/.no/.partial`, `.reveal`, `.card`, `.wrap`, `.btn-primary/.btn-ghost`, `.pill`, `.eyebrow`, `.h1/.h2`, `.lede`, `.mono`) defined in Task 1 and used identically in Tasks 2–10. `[data-gh-stars]` defined in Task 1 JS, used in Task 2. `/demo/` deadlink explicitly tracked from Task 1 → resolved Task 10. Routes match the footer/nav defined in Task 1.
