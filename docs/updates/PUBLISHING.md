# Publishing the Updates hub — runbook

The Updates hub at <https://ccc.amirfish.ai/updates/> is generated from
`updates/*.md` (the source) into this directory (`docs/updates/`, the output
GitHub Pages serves). This file is the operator runbook. For the authoring
workflow and the content schema, see `updates/README.md` and
`updates/_template.md`.

Everything here is generated. **Do not hand-edit** `index.html`, `feed.xml`,
`updates.json`, `styles.css`, or any `<slug>.html` in this directory. Edit the
source in `updates/` and rebuild.

## Ship an update

1. **Author** — copy the template, fill it, keep `status: draft` while writing.

   ```bash
   cp updates/_template.md updates/<slug>.md
   ```

   Claims must trace to `docs/product-story/pain-feature-proof.md` (status
   **Built**), never the never-claim list. Tone and copy rules:
   `docs/product-story/message-architecture.md` sections 10-11. No em-dashes.
   Every image in `media:` must be a real capture that already exists.

2. **Build** (stdlib only, no `pip install`):

   ```bash
   python3 scripts/updates_build.py
   ```

   It regenerates every file under `docs/updates/`, validates each page with
   `html.parser` and the feed with `xml.dom.minidom` before writing, and prunes
   pages whose source was removed or unpublished. `--check` makes a validation
   failure a hard non-zero exit (for CI). Drafts are skipped, so a half-written
   update never ships.

3. **Preview locally:**

   ```bash
   cd docs && python3 -m http.server 8099
   # open http://127.0.0.1:8099/updates/
   ```

   Check: the card and page render, the filter chips and search work, the Atom
   feed loads at `/updates/feed.xml`, and every image resolves.

4. **Publish** — flip `status: draft` → `status: published`, rebuild, and commit
   the source and the generated output together:

   ```bash
   git commit --only updates/<slug>.md docs/updates \
     -m "docs(updates): <slug>"
   ```

5. **Push** is done by Amir or the integrator. GitHub Pages serves `docs/` on the
   custom domain within about a minute of the push. `docs/.nojekyll` stays in
   place; the hub is plain static files served verbatim.

## Notes on the design

- **No build dependency.** The generator is stdlib-only. If a future update body
  needs richer markdown (tables, blockquotes, footnotes), see the header of
  `scripts/updates_build.py` for the one-line switch to the `markdown` package
  (build-time only, never shipped in `server.py`).
- **Brand tokens.** If `docs/brand/tokens.css` exists it is linked after the
  hub's own `styles.css` and supplies the color tokens; otherwise the hub falls
  back to its inline dark tokens. The pages are pinned to `data-theme="dark"` to
  match the dark-only product site.
- **The feed is the integration contract.** Email and analytics both sit on top
  of `/updates/feed.xml`. Swapping either never touches the hub.

---

## Email subscription — Buttondown (NOT ACTIVE YET)

The signup form on the index and every page footer is a **progressive
placeholder**: the email input and Subscribe button are **disabled**, with a
visible "Email updates launching soon" note, a working RSS/Atom link, and a
GitHub Releases watch link as the interim path. Nothing is wired to a live
account yet.

**Activation checklist (each step is an approval item — do not self-serve):**

- [ ] **APPROVAL: create the Buttondown account.** Account creation is the
      go/no-go decision. Buttondown was chosen for its privacy-matched defaults
      (no tracking pixels unless enabled, double opt-in on by default) and its
      Atom-feed-native auto-send. Free tier is 100 subscribers.
- [ ] Pick the Buttondown username. Replace `USERNAME-TODO-PENDING-APPROVAL` in
      `subscribe_block()` inside `scripts/updates_build.py` with the real
      username, then rebuild. The form `action` becomes
      `https://buttondown.com/api/emails/embed-subscribe/<username>`.
- [ ] Remove the `disabled` attributes and the `onsubmit="return false;"` guard
      on the form, and delete the "Email updates launching soon" note (or change
      it to a live confirmation line). Rebuild and preview.
- [ ] Confirm double opt-in is on (Buttondown default) and unsubscribe/List-
      Unsubscribe compliance is handled provider-side.
- [ ] For true RSS-to-email auto-send, add Buttondown's "RSS-to-email" add-on
      (a paid add-on) and point it at `https://ccc.amirfish.ai/updates/feed.xml`.
      Until then, send each update manually by pasting it into a Buttondown
      email (about two minutes per release).
- [ ] Keep the privacy line accurate: email only for updates, no tracking
      pixels, unsubscribe any time.

Fallback provider if the priority flips to "maximize the free ceiling": **Kit**
(ex-ConvertKit), 10,000-subscriber free plan. See the research doc for the full
comparison.

---

## Attribution — GoatCounter (NOT ACTIVE YET)

No analytics script ships today. The hub carries **no tracking beacon** and no
cookies, consistent with `docs/telemetry-public.md`.

**Activation checklist (each step is an approval item — do not self-serve):**

- [ ] **APPROVAL: create the GoatCounter site.** Account/site creation is the
      go/no-go decision. GoatCounter was chosen for a ~1 KB cookieless script,
      no consent banner required, free for a small OSS site, with a JSON export
      API for the growth dashboard.
- [ ] Add the GoatCounter script tag to the page template head in
      `scripts/updates_build.py` (a single `<script data-goatcounter=...>` line),
      then rebuild. Keep it the only third-party script on the page.
- [ ] Add a one-line "analytics are cookieless (GoatCounter)" note near the
      footer so the hub does not undercut CCC's own privacy story.
- [ ] Apply strict UTM discipline on every outbound hub/feed/email link
      (`utm_source`, `utm_medium`, `utm_campaign=<version-or-slug>`) so email →
      site clicks are attributable.

**Zero-script fallback** if any beacon is unwanted: rely on GitHub repo Traffic
insights plus the email provider's own click stats. Coarse, but no script at all.
