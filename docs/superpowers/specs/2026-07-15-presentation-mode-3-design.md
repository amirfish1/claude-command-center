# Presentation Mode 3 Design

## Goal

Add a conversation-scoped **Mode 3** that asks the working agent to return two
outputs in the same major turn:

1. its normal prose answer; and
2. a structured slide artifact that deliberately rewrites and designs that
   answer as a presentation.

Mode 3 exists to make an honest comparison possible between Present mode's
deterministic pagination of transcript text and an LLM-authored deck with
purposeful hierarchy, editing, and layouts.

## Locked product decisions

- The selector becomes `Off | Present | Mode 3`.
- Mode 3 is enabled per conversation, not globally.
- After it is enabled, future major answers produce prose and a slide artifact
  in the same model response.
- Enabling it mid-conversation generates a Mode 3 artifact for the latest
  completed answer once, then uses same-response artifacts going forward.
- Existing conversations and agents pay no token cost until Mode 3 is enabled.
- Present remains the reliable fallback whenever a Mode 3 artifact is absent,
  invalid, or unsupported.
- Auto-advance occurs only when the reader was following the old slide tail.
  A reader who navigated backward is never pulled forward.
- Slide progress moves into the existing presentation toolbar, removing the
  separate bottom dock and returning that row to the slide/live regions.

## User experience

### Activating Mode 3

The first Mode 3 click stores the preference against the conversation id. CCC
immediately asks the existing working agent to convert its latest completed
answer into a Mode 3 artifact. The request is an internal presentation-control
turn: CCC hides its control prompt and hides any artifact-only assistant prose
from the normal transcript, while the resulting deck remains durable in the
underlying session JSONL.

While the first artifact is being produced, the slide region shows a compact
`Designing AI deck…` state and the complete live-updates region remains usable.
If generation fails, Mode 3 shows Present's semantic slides plus a non-blocking
`AI deck unavailable · Retry` notice.

After activation, every substantive message sent through CCC to that
conversation carries a server-authored, hidden instruction requiring the final
answer to end with a Mode 3 artifact. The instruction applies to that response;
it does not permanently alter agents or repositories. Slash commands, picker
answers, steering fragments, and control-plane wake messages do not require an
artifact unless they produce a substantive final answer.

### Reading Mode 3

Mode 3 uses the existing large side arrows, left/right keyboard navigation,
Escape-to-Off behavior, End affordance, split-pane isolation, live projection,
and reduced-motion behavior. Its slides are a flat chronological deck keyed by
answer and artifact slide id.

The toolbar owns one progress component for both presentation modes:

`Present  [Off | Present | Mode 3]       Answer 8 · 2 of 4   ━━━   27 / 41`

On narrow panes the dots collapse first, followed by the answer-local label;
the mode selector and overall counter remain visible. No presentation dock row
is rendered below the conversation view.

## Same-response artifact contract

The agent appends one fenced artifact after its human-readable prose:

````markdown
Normal answer visible in Off and Present modes.

```ccc-slides
{"version":1,"deck_title":"Why the refresh jumped","theme":"cyan","slides":[...]}
```
````

CCC authors the instruction and schema; user text is never interpolated into
the instruction. The artifact fence is removed from normal prose before
Markdown rendering and returned as a new additive `presentation_artifact`
field on the parsed assistant event. Adding this response field preserves the
public `/api/conversations/*` compatibility contract.

An artifact belongs to the assistant event that contained it. The transcript
is the durable store and cache: reopening a conversation reparses the same
artifact without another model call.

## Declarative slide schema

Mode 3 does not accept raw HTML, CSS, SVG, JavaScript, URLs, or event handlers.
The model chooses from a small presentation grammar and CCC creates every DOM
node with `textContent`.

Top-level fields:

- `version`: exactly `1`;
- `deck_title`: optional, at most 120 characters;
- `theme`: `cyan`, `violet`, `amber`, `green`, or `neutral`;
- `slides`: one to eight slide objects.

Every slide has a stable `id`, a `layout`, a required `title`, and optional
`eyebrow` and `subtitle`. Supported layouts are:

- `statement` — one concise thesis;
- `bullets` — up to six edited points;
- `steps` — up to six ordered label/text pairs;
- `comparison` — two titled columns with up to five points each;
- `metrics` — up to four value/label pairs;
- `quote` — quote plus optional attribution;
- `code` — language, code text, and optional caption;
- `summary` — a closing takeaway plus up to four next actions.

Validation clamps string lengths, item counts, slide count, and total decoded
artifact size. Unknown fields are discarded. An unknown layout, missing title,
duplicate slide id, invalid JSON, or oversized artifact rejects the whole
artifact rather than partially guessing at the model's intent.

The generation instruction asks for three to seven slides by default, concise
copy, no repetition of the prose, and a mix of layouts only when the source
supports them. It explicitly forbids inventing facts, numbers, decisions, or
completion claims.

## Server and parser architecture

### Conversation-scoped state

The browser stores a versioned map of Mode 3-enabled conversation ids in
localStorage. The selected pane derives its mode from that conversation entry,
so switching panes or reopening a conversation restores the correct selector.
Off and Present do not modify other conversations.

The normal `/api/inject-input` request includes a boolean Mode 3 hint for the
open conversation. The server appends its own constant artifact instruction
after validating the session and routing mode. Queued messages retain the
instruction because augmentation happens before queue persistence.

### Parsing

One stdlib-only helper extracts and validates a terminal `ccc-slides` fence
from assistant text. Both Claude and Codex parsers call the same helper before
constructing their text blocks. Clean prose remains a normal text block;
validated deck data becomes `presentation_artifact`; invalid artifacts leave
the prose intact but omit the fence and carry a compact parse-status field for
the Mode 3 retry notice.

Artifact-only bootstrap responses still produce an assistant event because
the presentation field is meaningful even when clean prose is empty. Streaming
text is never parsed as an artifact. Extraction occurs only from a completed
assistant message, so half-written JSON cannot replace the visible deck.

## Rendering architecture

Present continues to build slides from sanitized transcript DOM. Mode 3 first
looks for a validated `presentation_artifact` on each completed assistant
event. When found, it renders schema-owned slide components. When missing, it
uses that answer's existing Present slides and marks them as a fallback; a
single bad answer never breaks the rest of the chronological deck.

Generated nodes carry globally unique semantic keys composed from conversation
id, assistant message id, and artifact slide id. Resize refreshes and new live
updates therefore cannot resolve a slide to another answer's similarly named
item.

The canonical conversation DOM remains mounted and hidden exactly as it is in
Present. The generic live projection remains shared by both modes and mirrors
every canonical mutation and control action.

## Auto-advance contract

Before any deck rebuild, CCC records the previous deck length, selected answer
key, selected semantic key, and whether the reader was on the old final slide.

- If the reader was on the old tail and a new answer artifact arrives, select
  the **first slide of that new answer**, not its last slide.
- If the reader was on the old tail and the same answer receives a replacement
  artifact, preserve the matching slide id when possible, otherwise select the
  first replacement slide.
- If the reader was not on the old tail, preserve its answer-scoped semantic
  key and never advance.
- Live status, elapsed-time, tool, or resize-only mutations never advance the
  deck.
- End explicitly selects the final slide and resumes tail-following for the
  next completed answer.

This contract applies identically to Present and Mode 3.

## Failure and safety behavior

- Raw artifact text is never passed to `innerHTML`.
- Invalid or unsupported artifacts fall back per answer to Present slides.
- A failed bootstrap does not disable Mode 3 or block the transcript; Retry
  resends only the internal conversion request.
- Duplicate completed events are deduplicated by stable message/artifact keys.
- Mode 3 state and artifacts contain no API keys or provider credentials.
- Turning Off disconnects presentation observers and restores the exact prior
  transcript scroll position.

## Verification requirements

### Parser and schema tests

- valid artifacts extract for Claude and Codex while prose remains unchanged;
- artifact-only bootstrap responses survive parsing;
- malformed JSON, invalid layouts, duplicate ids, excess slides/items, and
  oversized strings reject safely;
- raw HTML/script content remains inert text;
- conversations without Mode 3 are byte-for-byte unchanged.

### Browser tests

- selector and conversation-scoped persistence across panes and reload;
- activation bootstrap, loading, success, retry, and per-answer fallback;
- every supported layout renders without overflow at standard and narrow pane
  sizes;
- progress occupies the toolbar and no bottom dock row remains;
- left/right arrows, keyboard arrows, Escape, End, split panes, and Off restore;
- live projection parity remains complete in Mode 3;
- old-tail to new-answer advances to the new answer's first slide;
- historical readers remain fixed through new artifacts, resize, and live
  mutations;
- repeated refreshes do not replay entrance animations or jitter.

### Completion gate

Mode 3 is complete only when a real Claude session and a real Codex session each
produce a same-response artifact through CCC, survive reload from their native
transcripts, render the expected deck, and pass the complete presentation
parity verifier without weakening Present mode.

## Scope boundaries

Version 1 intentionally excludes arbitrary model-authored HTML/CSS, images,
charts computed from untrusted expressions, exporting to PowerPoint, manual
slide editing, and provider API calls outside the existing working session.
Those can be evaluated after the declarative Mode 3 comparison proves a
material benefit over Present.
