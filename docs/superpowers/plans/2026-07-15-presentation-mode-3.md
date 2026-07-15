# Presentation Mode 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a conversation-scoped Mode 3 that obtains prose plus a safe LLM-authored slide artifact from the working agent, renders it beside complete live updates, reclaims the bottom progress row, and advances correctly only for readers following the old tail.

**Architecture:** A shared server extractor removes a terminal `ccc-slides` JSON fence from completed Claude/Codex assistant text and exposes validated data as an additive `presentation_artifact` event field. The browser persists Mode 3 by conversation, requests a one-time latest-answer artifact on activation, adds a Mode 3 hint to subsequent CCC sends, and renders schema-owned DOM components with Present slides as a per-answer fallback.

**Tech Stack:** Python 3 stdlib (`json`, `re`, `http.server`), single-file browser JavaScript/CSS, `unittest`/`pytest`, Puppeteer 25.

## Global Constraints

- Keep `server.py` stdlib-only.
- Never render model-authored HTML, CSS, SVG, JavaScript, URLs, or event handlers.
- Accept one to eight slides and only `statement`, `bullets`, `steps`, `comparison`, `metrics`, `quote`, `code`, and `summary` layouts.
- Store artifacts inside native session transcripts; add no deck database.
- Mode 3 is conversation-scoped and costs no tokens before activation.
- Present remains the per-answer fallback for absent or invalid artifacts.
- Preserve live parity, split-pane isolation, Escape, End, arrows, reduced motion, and Off restoration.
- Auto-advance only from the old tail and select the first slide of the newly completed answer.
- Preserve unrelated shared-worktree edits; commit only explicit paths or hunks.

## File map

- `server.py` — validation/extraction, hidden generation prompts, parser integration, inject augmentation.
- `static/index.html` — Mode 3 selector and toolbar progress host.
- `static/app.js` — scoped preference, bootstrap, artifact attachment/rendering, progress, auto-advance.
- `static/app.css` — Mode 3 layouts and toolbar progress; remove the bottom dock.
- `tests/test_presentation_mode3.py` — parser, schema, prompt, and injection tests.
- `tests/test_presentation_mode_static.py` — browser-code contracts.
- `scripts/verify-presentation-live-parity.js` — Chromium Mode 3 and auto-advance matrix.
- `changelog.d/added-presentation-mode-3-2026-07-15.md` — user-visible addition.

---

### Task 1: Validate and extract durable artifacts

**Files:**
- Create: `tests/test_presentation_mode3.py`
- Modify: `server.py:18377-18600`
- Modify: `server.py:25787-25920`

**Interfaces:**
- Produces: `_extract_presentation_artifact(text: str) -> tuple[str, dict | None, str]`.
- Produces: optional assistant-event fields `presentation_artifact` and `presentation_artifact_error`.

- [ ] **Step 1: Write failing extraction tests**

```python
import json
import unittest
import server


class PresentationMode3Tests(unittest.TestCase):
    def valid(self):
        return {
            "version": 1,
            "deck_title": "Refresh behavior",
            "theme": "cyan",
            "slides": [
                {"id": "cause", "layout": "statement", "title": "The key collided", "subtitle": "Resize exposed it."},
                {"id": "fix", "layout": "bullets", "title": "The fix", "items": ["Scope keys by answer", "Follow only the old tail"]},
            ],
        }

    def fenced(self, value):
        return "Human prose.\n\n```ccc-slides\n" + json.dumps(value) + "\n```"

    def test_extracts_terminal_artifact_and_preserves_prose(self):
        prose, artifact, error = server._extract_presentation_artifact(self.fenced(self.valid()))
        self.assertEqual(prose, "Human prose.")
        self.assertEqual(artifact["slides"][1]["id"], "fix")
        self.assertEqual(error, "")

    def test_rejects_unknown_layout_duplicate_ids_and_nine_slides(self):
        unknown = self.valid(); unknown["slides"][0]["layout"] = "html"
        duplicate = self.valid(); duplicate["slides"][1]["id"] = "cause"
        too_many = self.valid(); too_many["slides"] = [
            {"id": "s" + str(i), "layout": "statement", "title": str(i)} for i in range(9)
        ]
        for value in (unknown, duplicate, too_many):
            with self.subTest(value=value):
                prose, artifact, error = server._extract_presentation_artifact(self.fenced(value))
                self.assertEqual(prose, "Human prose.")
                self.assertIsNone(artifact)
                self.assertTrue(error)
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_presentation_mode3.py -q`

Expected: FAIL because `_extract_presentation_artifact` is undefined.

- [ ] **Step 3: Implement strict stdlib validation**

Add a terminal-fence regex and an allowlist validator. Build a fresh dict rather than returning decoded input. Enforce: version `1`; themes `cyan|violet|amber|green|neutral`; 1–8 slides; ids matching `[A-Za-z0-9_-]{1,64}` and unique; titles ≤120; subtitles/statements/quotes/captions ≤320; code ≤4,000; ≤6 bullet/step items; ≤5 comparison items per side; ≤4 metrics/actions; encoded artifact ≤24 KiB. Reject strings containing active HTML tags (`script`, `style`, `svg`, `iframe`, `object`, `html`). Discard unknown keys.

The extractor must return `(clean_prose, validated_artifact, "")` on success, `(clean_prose, None, error_code)` for an invalid terminal fence, and `(original_text.strip(), None, "")` when no fence exists.

- [ ] **Step 4: Integrate completed Claude and Codex text paths**

Call the helper only for completed assistant text. Keep prose as a normal text block and attach the validated artifact to the event. Return artifact-only bootstrap assistant events even when clean prose is empty. Do not parse streaming partial JSON.

- [ ] **Step 5: Run GREEN and parser regressions**

Run: `python3 -m pytest tests/test_presentation_mode3.py tests/test_codex_tail_incremental.py tests/test_classify.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git commit --only server.py tests/test_presentation_mode3.py -m "feat(ui): parse mode 3 slide artifacts"`

---

### Task 2: Add the hidden same-response contract

**Files:**
- Modify: `tests/test_presentation_mode3.py`
- Modify: `server.py:41804-42120`
- Modify: `server.py:59692-59830`
- Modify: `static/app.js:7142-7360`

**Interfaces:**
- Produces: `_mode3_prompt(text: str, *, bootstrap: bool = False) -> str`.
- Produces: `_strip_mode3_instruction(text: str) -> str`.
- Consumes request booleans `presentation_mode3` and `presentation_bootstrap`.

- [ ] **Step 1: Write failing prompt tests**

```python
def test_mode3_prompt_adds_server_owned_contract_and_hides_it_from_display(self):
    augmented = server._mode3_prompt("Explain the failure")
    self.assertTrue(augmented.startswith("Explain the failure\n\n<ccc-mode3-instruction"))
    self.assertIn("```ccc-slides", augmented)
    self.assertEqual(server._strip_mode3_instruction(augmented), "Explain the failure")

def test_bootstrap_requests_latest_answer_only(self):
    prompt = server._mode3_prompt("", bootstrap=True)
    self.assertIn("latest completed substantive answer", prompt)
    self.assertIn("Return only the ccc-slides fence", prompt)
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_presentation_mode3.py -q`

Expected: FAIL because the prompt helpers are undefined.

- [ ] **Step 3: Implement constant prompt construction**

Use a terminal `<ccc-mode3-instruction version="1">…</ccc-mode3-instruction>` trailer. It must contain the exact schema/limits, concise-copy instruction, no-invention rule, and terminal-fence requirement. User text must never be formatted into the instruction body. Add a regex stripper for user-visible transcript/title extraction, but do not strip before agent delivery.

- [ ] **Step 4: Augment inject input before routing or queueing**

For `presentation_bootstrap: true`, ignore body text and use the constant latest-answer prompt. For `presentation_mode3: true`, mode `send`, and non-slash text, append the response trailer. Do not augment picker answers or steering. Existing requests without the fields remain byte-for-byte unchanged.

- [ ] **Step 5: Send the hint from the composer**

```javascript
const payload = { session_id: sid, text, mode: injectMode };
if (injectMode === 'send' && presentationModeForConversation(sid) === '3') {
  payload.presentation_mode3 = true;
}
```

- [ ] **Step 6: Run GREEN**

Run: `python3 -m pytest tests/test_presentation_mode3.py tests/test_smoke.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

Run: `git commit --only server.py static/app.js tests/test_presentation_mode3.py -m "feat(ui): request same-response mode 3 decks"`

---

### Task 3: Add conversation-scoped activation

**Files:**
- Modify: `static/index.html:765-780`
- Modify: `static/app.js:38781-39820`
- Modify: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Produces: `presentationModeForConversation(conversationId) -> 'off' | '2' | '3'`.
- Produces: `persistPresentationModeForConversation(conversationId, mode)`.
- Produces: `requestMode3Bootstrap(paneId, conversationId) -> Promise<void>`.

- [ ] **Step 1: Write failing selector/state tests**

```python
def test_mode3_selector_and_conversation_state(self):
    html = INDEX_HTML.read_text()
    app = APP_JS.read_text()
    self.assertEqual(html.count("data-presentation-mode="), 3)
    self.assertIn('data-presentation-mode="3"', html)
    self.assertIn("ccc-conv-presentation-mode-by-conversation", app)
    self.assertIn("function presentationModeForConversation", app)
    self.assertIn("function persistPresentationModeForConversation", app)
    self.assertIn("presentation_bootstrap: true", app)
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_presentation_mode_static.py -q`

Expected: FAIL.

- [ ] **Step 3: Add Mode 3 and the versioned state map**

Add a third selector button. Store `{version:1,modes:{[conversationId]:'3'}}` under `ccc-conv-presentation-mode-by-conversation`. Accept only `off`, `2`, `3`; migrate legacy `1` to `2`. A pane reads its conversation entry before the existing default.

- [ ] **Step 4: Bootstrap once per latest answer**

On `off/2 -> 3`, if the latest completed assistant root has no artifact and its message key is not already pending, POST `/api/inject-input` with `{session_id, text:'', mode:'send', presentation_bootstrap:true}`. Show `Designing AI deck…`; clear on artifact arrival; expose Retry on failure. Never echo the internal prompt as a human message.

- [ ] **Step 5: Run GREEN**

Run: `python3 -m pytest tests/test_presentation_mode_static.py tests/test_presentation_mode3.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git commit --only static/index.html static/app.js tests/test_presentation_mode_static.py -m "feat(ui): add conversation-scoped mode 3"`

---

### Task 4: Render safe authored layouts with Present fallback

**Files:**
- Modify: `static/app.js:39038-39240`
- Modify: `static/app.js:39804-40790`
- Modify: `static/app.css:19029-19410`
- Modify: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Produces: `buildMode3Slide(turn, artifactSlide, index, count) -> HTMLElement`.
- Produces: `mode3SlidesForTurn(turn) -> HTMLElement[] | null`.
- Consumes event fields from Task 1.

- [ ] **Step 1: Write failing renderer contracts**

```python
def test_mode3_renderer_is_declarative_and_falls_back(self):
    source = APP_JS.read_text()
    render = _javascript_function_source("buildMode3Slide")
    for layout in ("statement", "bullets", "steps", "comparison", "metrics", "quote", "code", "summary"):
        self.assertIn("'" + layout + "'", render)
    self.assertIn("textContent", render)
    self.assertNotIn("innerHTML", render)
    deck = _javascript_function_source("buildPresentationDeck")
    self.assertIn("mode3SlidesForTurn", deck)
    self.assertIn("paginatePresentationItemsMeasured", deck)
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_presentation_mode_static.py -q`

Expected: FAIL.

- [ ] **Step 3: Attach artifacts to canonical roots**

When rendering a completed assistant event, assign `assistantRoot._presentationArtifact = ev.presentation_artifact || null` and put only the compact error code in `dataset.presentationArtifactError`. `presentationTurns` copies the artifact and stable message key.

- [ ] **Step 4: Implement schema-owned components**

Use `document.createElement` and `textContent` exclusively. Render all eight layouts through shared heading/list/column/metric helpers. Give every authored slide `presentationKey = turn.key + ':mode3:' + artifactSlide.id`, `answerKey = turn.key`, and `artifactSlideId = artifactSlide.id`.

- [ ] **Step 5: Fall back per answer**

In mode `3`, use authored slides when valid. Otherwise run the exact measured Present paginator for that answer and mark the slides `is-mode3-fallback` with `Transcript slides · AI deck unavailable`. One bad artifact must not replace other authored answers.

- [ ] **Step 6: Add bounded responsive CSS**

Add layout/theme classes with grid/flex only, `min-width:0`, internal code overflow, readable contrast, and one-column collapse below 760px. Add no remote assets, generated style attributes, or artifact-controlled CSS variables.

- [ ] **Step 7: Run GREEN**

Run: `node --check static/app.js && python3 -m pytest tests/test_presentation_mode_static.py tests/test_presentation_mode3.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

Run: `git commit --only static/app.js static/app.css tests/test_presentation_mode_static.py -m "feat(ui): render declarative mode 3 decks"`

---

### Task 5: Move progress into the toolbar

**Files:**
- Modify: `static/index.html:765-785`
- Modify: `static/app.js:39524-39695`
- Modify: `static/app.css:19029-19398`
- Modify: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Produces: one `[data-role="presentation-progress"]` host per pane toolbar.
- Removes: `.conv-presentation-dock` and the `present-dock` grid row.

- [ ] **Step 1: Write failing toolbar tests**

```python
def test_progress_lives_in_toolbar_without_dock(self):
    html = INDEX_HTML.read_text(); app = APP_JS.read_text(); css = APP_CSS.read_text()
    self.assertIn('data-role="presentation-progress"', html)
    self.assertNotIn("function ensurePresentationDock", app)
    self.assertNotIn(".conv-presentation-dock", css)
    self.assertNotIn('"present-dock', css)
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_presentation_mode_static.py -q`

Expected: FAIL.

- [ ] **Step 3: Move markup and renderer target**

Place `.conv-presentation-progress` after the segmented control. `renderPresentationCursor` updates the pane's toolbar host. Hide it in Off. Delete dock creation/cleanup/clone code, dock CSS, and `present-dock` grid areas; reduce the pane grid by one row.

- [ ] **Step 4: Verify desktop and narrow layouts**

Run: `python3 -m pytest tests/test_presentation_mode_static.py -q`

Run: `SNAPSHOT_URL=http://127.0.0.1:8090 SNAPSHOT_OUT=/tmp/mode3-toolbar.png node snapshot.js`

Expected: tests PASS; toolbar stays one row at desktop width; dots collapse before the selector/counter on narrow panes.

- [ ] **Step 5: Commit**

Run: `git commit --only static/index.html static/app.js static/app.css tests/test_presentation_mode_static.py -m "fix(layout): move slide progress into toolbar"`

---

### Task 6: Make auto-advance answer-aware

**Files:**
- Modify: `static/app.js:39420-39720`
- Modify: `tests/test_presentation_mode_static.py`
- Modify: `scripts/verify-presentation-live-parity.js`

**Interfaces:**
- Produces: `presentationRefreshIndex(deck, previousDeck, previousIndex, opts) -> number`.
- Consumes: globally unique `presentationKey` and `answerKey`.

- [ ] **Step 1: Write failing pure/browser cases**

Require old-tail plus a new answer to return the new answer's first index; historical selection to retain its semantic index; resize-only refresh to retain the same artifact slide id. Update `tail-auto-advance` to assert first new-answer slide rather than `deck.length - 1`; retain `historical-cursor` and `refresh-stable`.

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_presentation_mode_static.py -q`

Run: `CCC_PRESENTATION_PARITY_URL=http://127.0.0.1:8090 node scripts/verify-presentation-live-parity.js`

Expected: tail destination FAILS because current behavior selects the deck's last slide.

- [ ] **Step 3: Implement one refresh-index policy**

Record old tail state before rebuilding. If `followTail`, the reader was at the old tail, and the last answer key changed, return the first index with the new answer key. Otherwise preserve the selected answer-scoped semantic key. Resize/live-only refreshes never advance. End selects the final slide and re-arms follow-tail.

- [ ] **Step 4: Run the browser verifier twice**

Run: `for i in 1 2; do CCC_PRESENTATION_PARITY_URL=http://127.0.0.1:8090 node scripts/verify-presentation-live-parity.js || exit 1; done`

Expected: both runs PASS tail-first-slide, history, resize, and no-jitter checks.

- [ ] **Step 5: Commit**

Run: `git commit --only static/app.js tests/test_presentation_mode_static.py scripts/verify-presentation-live-parity.js -m "fix(ui): advance to new answer starts"`

---

### Task 7: Complete the browser matrix and real-engine gate

**Files:**
- Modify: `scripts/verify-presentation-live-parity.js`
- Create: `changelog.d/added-presentation-mode-3-2026-07-15.md`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: end-to-end evidence for Mode 3 and unchanged Present behavior.

- [ ] **Step 1: Extend Puppeteer coverage**

Add labels for activation, bootstrap, eight layouts, fallback, reload, split pane, toolbar progress, tail-first-slide, and historical stability. Seed validated artifacts on synthetic completed events and retain the complete canonical/live parity matrix.

- [ ] **Step 2: Run automated verification**

Run: `node --check static/app.js`

Run: `python3 -m pytest tests/test_presentation_mode3.py tests/test_presentation_mode_static.py -q`

Run: `CCC_PRESENTATION_PARITY_URL=http://127.0.0.1:8090 node scripts/verify-presentation-live-parity.js`

Run: `python3 -m pytest -q`

Run: `git diff --check`

Expected: focused and browser checks PASS. Record full-suite failures verbatim and rerun each exact failure before classification.

- [ ] **Step 3: Verify real Claude and Codex transcripts**

Use disposable conversations, enable Mode 3, and send: `Explain in three concise points why answer-scoped slide keys prevent cursor jumps.` For each engine verify the user event contains no visible instruction, the assistant event contains prose plus `presentation_artifact`, reload reconstructs the same deck without another call, and auto-advance selects the first new-answer slide only from the old tail.

- [ ] **Step 4: Add the changelog**

```markdown
- Added conversation-scoped Mode 3: working agents can return safe, designed slide artifacts alongside normal answers, with Present fallback, toolbar progress, complete live updates, and answer-aware auto-advance.
```

- [ ] **Step 5: Audit completion against the design**

Re-read every locked decision, schema rule, failure behavior, and verification requirement in `docs/superpowers/specs/2026-07-15-presentation-mode-3-design.md`. Cite its test/runtime evidence. Missing real-engine evidence means the goal remains active.

- [ ] **Step 6: Commit**

Run: `git commit --only scripts/verify-presentation-live-parity.js changelog.d/added-presentation-mode-3-2026-07-15.md -m "test(ui): verify presentation mode 3"`

---

## Plan self-review

- Spec coverage: activation, same-response generation, bootstrap, schema, Claude/Codex parsing, durable cache, eight layouts, fallback, toolbar progress, live parity, auto-advance, split panes, reload, reduced motion, and real-engine verification each map to a task.
- Placeholder scan: no deferred implementation placeholders remain; exact schema limits and interface names are defined above.
- Type consistency: server events use `presentation_artifact`; DOM roots use `_presentationArtifact`; slides use `presentationKey`, `answerKey`, and `artifactSlideId`; later tasks consume those exact names.
