# Session Cost Orbit Design

**Date:** 2026-07-12  
**Status:** Approved for implementation planning

## Summary

Redesign the session icons in the left sidebar so they communicate three
independent properties at a glance:

1. Engine identity
2. Unified model cost tier
3. Whether the session is actively working now

The selected visual direction is **Cost Orbit**. Existing engine glyphs and
engine hues remain intact. Cost is expressed by a geometric orbit surrounding
the glyph. Active work is expressed by a separate green status dot.

## Goals

- Make relative cost comparable across Claude and Codex.
- Preserve immediate recognition of the engine.
- Replace process-oriented liveness with an engine-independent definition of
  active work.
- Keep cost legible for idle and completed sessions.
- Work at the existing 13px glyph scale without changing row height or sidebar
  width.
- Remain understandable without relying on color alone.

## Non-goals

- Display exact prices or token rates in the sidebar.
- Assign a tier to models outside the approved Claude and Codex families.
- Redesign lifecycle chips such as Waiting, Stuck, or Needs approval.
- Change the `/api/*` response contract.

## Information Architecture

Each visual channel has exactly one meaning:

| Channel | Meaning |
| --- | --- |
| Inner glyph shape | Engine |
| Inner glyph hue | Engine |
| Surrounding orbit | Unified cost tier |
| Green or hollow status dot | Actively working now |
| Tooltip | Exact engine, model, tier, and activity state |

Cost must not be inferred from activity brightness, and activity must not be
inferred from the presence of an engine process.

## Unified Cost Scale

| Tier | Claude | Codex | Ornament |
| --- | --- | --- | --- |
| Premium | FABLE | SOL | Double gold orbit, restrained warm glow, tiny `$` |
| High | OPUS | — | Complete amber orbit with one satellite point |
| Medium | SONNET | TERRA | Single cool-blue broken orbit |
| Low | HAIKU | LUNA | Faint dotted inner orbit |
| Unknown | Any unrecognized model | Any unrecognized model | No orbit |

Family matching is case-insensitive and works with full versioned identifiers,
including examples such as `claude-opus-4-8` and `gpt-5.6-sol`. Classification
must be scoped by engine to prevent accidental cross-family matches.

FABLE no longer replaces the Claude engine hue with gold. FABLE and SOL retain
their Claude and Codex identities and receive the same premium gold orbit.

## Activity Semantics

The status dot means **actively executing a turn now**.

- Working: solid green dot with a restrained pulse.
- Not working: hollow gray dot.
- Reduced motion: solid, non-pulsing green dot.

Activity classification uses the best canonical engine signal:

- Codex is working when `codex_state === "working"`.
- Claude is working when `state === "working"`.
- An optimistic send or pending spawn is treated as working immediately so the
  interface responds without waiting for the first backend event.
- Waiting, stuck, attached-but-idle, and completed sessions are not working.
- `is_live` alone never qualifies a session as working.

The existing whole-icon live animation is removed. It currently scales an
`.is-live` icon from 100% to 108% and back every 2.8 seconds, but it is subtle,
process-oriented, and not noticed reliably. Motion is reserved for the activity
dot so it has one clear meaning.

## Visual Specification

- Preserve the existing 13px engine glyphs.
- Draw the orbit in the surrounding gutter; do not increase row height.
- Permit the premium orbit to extend slightly beyond the 16px icon column into
  the existing title gap, without overlapping text.
- Keep all orbits stationary.
- Keep premium glow restrained enough that several premium sessions do not
  produce a bright vertical stripe.
- Slightly soften an idle engine glyph, but do not grayscale it enough to erase
  engine identity or weaken its cost ornament.
- On hover or keyboard focus, brighten the glyph and show the semantic tooltip.
- FABLE and SOL must have equal premium emphasis.

The four tier geometries must remain distinguishable in monochrome:

- Low: dotted
- Medium: broken
- High: complete plus satellite
- Premium: double

## Presentation Logic

One shared presentation helper drives every session-icon placement, including
the sidebar row and conversation pane header. It derives:

```text
session
  -> engine identity
  -> cost tier
  -> actively-working state
  -> CSS classes and tooltip
```

The helper replaces the duplicated one-off FABLE checks. Expected class-level
outputs include an engine class, one optional cost-tier class, and one activity
class. Unknown models receive no cost-tier class.

Tooltip format:

```text
Codex · SOL · Premium cost · Working now
Claude · SONNET · Medium cost · Not working
Codex · gpt-5.5 · Cost tier unknown · Not working
```

No new server field or endpoint is required because session rows already expose
the engine, model, canonical state, and Codex state used by the classifier.

## Fallbacks and Error Handling

- Missing model: render the plain engine glyph and report `Cost tier unknown`
  in the tooltip.
- Unrecognized model: same plain-glyph fallback; never guess based on price or
  model size.
- Missing activity state: use the hollow gray dot.
- Missing engine: preserve the existing default-engine behavior and omit the
  cost orbit unless the engine/model pair is recognized safely.

These fallbacks must not throw, hide the session row, or cause layout shift.

## Accessibility and Performance

- Tier differences use geometry as well as color.
- Activity uses fill state as well as color: solid means working, hollow means
  not working.
- Tooltip content is available to hover and keyboard focus.
- `prefers-reduced-motion` disables the activity pulse.
- Do not animate filter, blur, orbit geometry, or the entire glyph.
- Avoid per-frame effects across the full sidebar; only the small status dot may
  animate.

## Verification

Automated classification coverage should include:

- FABLE, OPUS, SONNET, and HAIKU with short and versioned model identifiers.
- SOL, TERRA, and LUNA with short and versioned model identifiers.
- Unknown and missing models.
- Working, waiting, idle, stuck, optimistic-send, pending-spawn, and completed
  sessions.
- Engine-scoped matching that rejects misleading cross-engine names.

UI verification should confirm:

- Sidebar and pane header render identical engine/tier semantics.
- Several premium rows remain visually balanced.
- Idle sessions retain readable cost tiers.
- Long titles do not collide with premium ornaments.
- No row-height or title-position shift occurs.
- Normal and high-density Puppeteer snapshots remain crisp.
- Reduced-motion mode contains no pulsing or whole-icon scaling.

Run `node --check static/app.js`, the repository smoke suite, and the repository's
Puppeteer snapshot harness after implementation.

## Acceptance Criteria

A user scanning the sidebar can answer three independent questions without
opening a session:

1. Which engine is this?
2. What unified cost tier is it using?
3. Is it actively working right now?

The design is successful only if all three remain readable for both active and
dormant sessions at the normal sidebar scale.
