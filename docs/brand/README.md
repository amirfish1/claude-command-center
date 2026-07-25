# CCC brand

The visual identity for **CCC**, the coding-agent command center.

This kit standardizes one look across the site, the app, and outbound video.
It retires the coral/terracotta that read as an Anthropic affiliation signal and
commits to the slate-blue already used inside the running app, with amber as the
one accent that means "this is the session that needs you."

Everything here is dark-first: assets are tuned for near-black backgrounds and
stay legible, or degrade gracefully, on white.

---

## Files

| File | What it is |
|---|---|
| `tokens.css` | Color, type, radii as CSS custom properties. The source of truth. |
| `ccc-mark.svg` | Primary mark: a board of agent sessions, one flagged amber. 16px to 512px. |
| `ccc-wordmark.svg` | The `CCC` wordmark: three geometric arcs, the middle one amber. |
| `ccc-wordmark-descriptor.svg` | Wordmark locked up with "the coding-agent command center". |
| `ccc-mark-mono-light.svg` | One-color mark for dark backgrounds. Transparent. |
| `ccc-mark-mono-dark.svg` | One-color mark for light backgrounds. Transparent. |
| `favicon.svg` | Simplified mark for 16px tabs. Solid tiles, no inner detail. |
| `avatar.svg` | Square social avatar. Reads inside a 48px circle crop. |
| `headers/linkedin-banner.svg` | 1584 x 396. |
| `headers/x-header.svg` | 1500 x 500. |
| `headers/youtube-banner.svg` | 2048 x 1152, safe area 1235 x 338 marked in-file. |
| `templates/thumbnail-template.svg` | 1280 x 720 YouTube thumbnail system, with zone guides. |
| `templates/thumbnail-example-attention.svg` | Filled example: "It's been waiting an hour." + Attention detection. |
| `templates/thumbnail-example-board.svg` | Filled example: "Nine tabs. Which one needs you?" + One board, every session. |
| `templates/title-card.svg` | 1920 x 1080 video title card. |

---

## The mark

A dark board holds a two-by-two grid of session tiles. Three sit calm in
slate-blue. The top-right tile is amber and carries a small dot: the one that
needs you. It is the product thesis compressed to a glyph, and it survives the
shrink to a favicon because it reduces to "a grid, one square is different."

The wordmark spells `CCC` as three arcs. The middle arc is amber, echoing the
mark. In monochrome the highlight cannot be a hue, so the calm tiles become
outlines and the one that needs you stays filled.

### Clear space and minimum sizes

- **Clear space:** keep empty space around the mark equal to one grid tile
  (25% of the mark's width) on every side. Do not crowd it with text or rules.
- **Mark minimum:** 16px (use `favicon.svg` at or below 32px; use `ccc-mark.svg`
  above 32px, where the inner content lines and attention dot start to read).
- **Wordmark minimum:** 88px wide. Below that the arc gaps close up.
- **Descriptor lockup minimum:** 240px wide, or the descriptor text goes muddy.

### Don'ts

- Do not recolor the mark. The calm tiles are slate-blue `#6e8caf`; the one that
  needs you is amber `#f5a623`. No other palette.
- Do not reintroduce coral or terracotta anywhere. That signal is retired.
- Do not add gradients, glows, bevels, or drop shadows to the mark itself.
- Do not rotate, shear, or add perspective.
- Do not place the color wordmark on white for body-size use. On light
  backgrounds use `ccc-mark-mono-dark.svg` plus ink-colored text, since amber on
  white is a graphics-only contrast (about 2:1), not a text contrast.
- Do not stretch. Scale uniformly.
- Do not put the mark on a busy photo. It needs a flat field.

---

## Color

Dark-first tokens. Contrast is stated against the background each token is used
on, per WCAG 2.1: AA is 4.5:1 for normal text, 3:1 for large text and graphics.
Full ramp and light-mode overrides live in `tokens.css`.

| Token | Hex (dark) | Role | Contrast check |
|---|---|---|---|
| `--ccc-bg` | `#0d1117` | Base background | n/a |
| `--ccc-surface` | `#161b22` | Cards, panels | n/a |
| `--ccc-surface-2` | `#21262d` | Raised, hover | n/a |
| `--ccc-border` | `#30363d` | Hairlines | n/a |
| `--ccc-text` | `#c9d1d9` | Body text | 11.4:1 on bg. AA+ |
| `--ccc-text-muted` | `#8b949e` | Secondary text | 5.3:1 on bg. AA |
| `--ccc-text-strong` | `#e6ebf2` | Headings | 15.0:1 on bg. AA+ |
| `--ccc-blue-300` | `#9ab3ce` | Light accent text on dark | 8.7:1 on bg. AA+ |
| `--ccc-blue-500` | `#6e8caf` | **Primary.** The in-app accent | 5.4:1 on bg. AA |
| `--ccc-blue-700` | `#46607f` | Primary text on light | 6.3:1 on white. AA |
| `--ccc-accent` | `#f5a623` | **Amber.** Highlight, "needs you" | 9.3:1 on bg. AA+ (dark only) |
| `--ccc-success` | `#3fb950` | Positive state | 6.9:1 on bg. AA |
| `--ccc-warn` | `#d29922` | Caution state | 7.0:1 on bg. AA |
| `--ccc-danger` | `#f85149` | Error, blocked | 5.2:1 on bg. AA |

The slate ramp is built around `#6e8caf`, the accent the app already ships.
Amber is a highlight color. It clears AA as text on the dark background, but on
white it is graphics-only: for amber text on light, use `--ccc-accent-600`
(`#d9910f`) or darker.

Light mode is a full override block in `tokens.css`, driven by
`prefers-color-scheme` and by an explicit `[data-theme]` attribute so a manual
toggle wins in both directions.

---

## Type

- **UI and wordmark contexts:** Inter, with a system fallback stack. The running
  app already loads Inter, so this matches.
- **Code and terminal flavor:** a monospace stack, `ui-monospace, 'SF Mono',
  'JetBrains Mono', Menlo, Consolas, monospace`. Use it for URLs, install lines,
  and anything that should read as a terminal.

Scale is a 1.25 modular scale on a 16px base. Tokens `--ccc-text-xs` through
`--ccc-text-5xl` are in `tokens.css`. Display and wordmark weight is 800
(`--ccc-weight-black`); body is 400.

The SVG headers and cards reference the Inter stack by name. If a viewer lacks
Inter, they fall back to the system sans and stay on-brand. For print or for
embedding where the exact glyphs matter, convert text to outlines (see Export).

---

## Screenshots

Every product visual is a real capture. This is a brand rule, not a preference:
the message architecture forbids presenting a mockup as product, and forbids
invented numbers.

- **Real UI only.** Capture the actual app with seeded demo data. Never fake a
  screen and never fabricate metrics. The thumbnail examples ship with a
  clearly-labeled placeholder frame ("drop a real capture here"), not a
  convincing fake, on purpose.
- **Dark theme.** Capture in the app's dark theme so it sits on the brand base.
- **Consistent treatment.** Rounded corners at 16px (`--ccc-radius-xl`), a 1px
  `--ccc-border` frame, one soft shadow (`--ccc-shadow-screenshot`), and equal
  inset on all sides. Same treatment on every capture in a set.
- **Crop tight.** Show the one surface the frame is about. No stray browser
  chrome, no desktop, no notification banners.

---

## Motion and cursor (demo videos)

The demo is the proof asset, so let it breathe.

- **Deliberate cursor.** Move it slowly and in straight, intentional paths. No
  hunting, no jitter, no fast flicks. The cursor is a narrator.
- **Pause on the moment of pain.** Before the fix, hold on the problem: the
  session that has been waiting, the context meter in the red, the queue that
  stalled. Let the viewer feel it for a beat before CCC answers it.
- **Two to three seconds per beat.** One idea per beat. Land it, hold it, then
  move. Do not rush cuts.
- **Show, then name.** Let the UI do the work; put the feature name on screen
  only after the action reads.
- **Amber marks the point.** When you highlight, highlight in amber, the same
  color that means "needs you" everywhere else.

---

## Export

The SVGs are the masters. Rasterize when a platform needs PNG.

**Exact dimensions (recommended for headers, thumbnails, cards).** Use
`rsvg-convert` from librsvg. Install once:

```sh
brew install librsvg
```

```sh
# Marks at multiple sizes
rsvg-convert -w 512 -h 512 ccc-mark.svg    -o ccc-mark-512.png
rsvg-convert -w 180 -h 180 favicon.svg     -o apple-touch-icon.png
rsvg-convert -w 32  -h 32  favicon.svg     -o favicon-32.png
rsvg-convert -w 16  -h 16  favicon.svg     -o favicon-16.png

# Headers and cards (exact platform sizes)
rsvg-convert -w 1584 -h 396  headers/linkedin-banner.svg -o linkedin-banner.png
rsvg-convert -w 1500 -h 500  headers/x-header.svg        -o x-header.png
rsvg-convert -w 2048 -h 1152 headers/youtube-banner.svg  -o youtube-banner.png
rsvg-convert -w 1280 -h 720  templates/thumbnail-example-attention.svg -o thumb.png
rsvg-convert -w 1920 -h 1080 templates/title-card.svg    -o title-card.png
```

**No-install preview (macOS QuickLook).** `qlmanage` needs nothing extra but
pads the output to a square of the longest side and appends `.png` to the name.
Good for a quick look, not for exact-size deliverables:

```sh
qlmanage -t -s 512 -o . ccc-mark.svg     # writes ./ccc-mark.svg.png (512 square)
```

**Resizing or reformatting an existing PNG.** `sips` (built into macOS) resizes
and converts raster files. Use it on a PNG you already produced, not on the SVG:

```sh
sips -z 180 180 ccc-mark-512.png --out apple-touch-icon-180.png
```

**Convert wordmark text to outlines** (for print or font-free embedding): open
the SVG in Inkscape, select all, Path > Object to Path, save. The mark and
wordmark arcs are already pure geometry and need no conversion; only the
descriptor and header text depend on a font.

---

## Naming

The name is **CCC**, standalone. This is the whole name.

- New lockups, filenames, and metadata: **`CCC`**. No "Claude" in the name.
- Need a descriptor: **"the coding-agent command center"** (lower case, no
  engine list, so it never goes stale).
- The tagline may reference Claude as an engine: **"Start the next while Claude
  builds the first."** That is the one place Claude appears, and it appears as
  the engine doing the building, not as part of the product name.
- The old full form **"Claude Command Center"** stays only in historical and
  legal contexts that already exist: the git repo slug `claude-command-center`,
  the LICENSE copyright entity, and shipped release notes. Do not introduce it
  into new surfaces.

Reason: the brand audit found seven distinct name forms in active use and three
conflicting accent colors across the three surfaces a user touches. This kit
collapses the names to `CCC` plus one descriptor, and the colors to one slate
ramp plus amber.

---

## Adoption checklist

This kit adds new files under `docs/brand/` only. It does **not** modify any
existing file. Applying it to the product is a follow-up, proposed here in the
order that ships the most consistency for the least risk:

1. **Retire the coral icon.** Replace `static/icon.svg` (terracotta three-node
   mark) and the inline coral favicon in `static/index.html` with `favicon.svg`
   and a mark derived from `ccc-mark.svg`. This removes the last coral surface.
2. **Unify the accent.** Confirm `static/app.css` stays on `#6e8caf`; point the
   landing page accent at the same slate rather than the standalone amber, and
   keep amber as the "needs you" highlight only.
3. **Fix the metadata names.** Update `og:title`, the landing footer entity, the
   in-app `<title>`, the macOS window title, and the `CFBundle*` display names
   to `CCC` plus "the coding-agent command center". Drop the hard-coded engine
   lists (they already disagree file to file).
4. **Swap social art.** Regenerate `docs/images/social-preview.png` and the
   channel banners from the headers here.
5. **Ship the favicon set.** Export the 16/32/180 PNGs and wire them in
   `docs/index.html` and `static/index.html`.

Each of these touches existing files and belongs in its own reviewed change, not
in the brand-kit drop.
