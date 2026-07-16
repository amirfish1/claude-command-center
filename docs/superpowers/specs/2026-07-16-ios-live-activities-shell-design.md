# CCC iOS Live Activities shell — design spec

**Status:** Design spec (not a build). No Swift, no Xcode project shipped.
**Date:** 2026-07-16
**Lane:** W34 (Fable-5 batch, wave 4)
**Decision this spec supports:** *When* cloud usage data says go, build a thin
iOS shell whose only reason to exist is Live Activities + lock-screen widgets.
Until then, the Cloud Relay PWA is the iPhone story.

---

## 0. TL;DR

Fable's standing recommendation is **not now** on a native iOS CCC app. The Cloud
Relay PWA (repo `ccc-cloud`) already gives iPhone users an installable
home-screen app with Web Push; W19 shipped the signup / My-CCC funnel. A full
native rewrite would buy almost nothing a PWA can't do — *except one thing*:

> **Live Activities + lock-screen / Dynamic Island widgets.** A glanceable
> "queue: 3 tickets · 2 workers grinding · 1 needs you" that lives on the lock
> screen and Dynamic Island, updated by push, with zero app open.

That single capability is the entire justification for going native, and it does
not require a native app — it requires a **thin SwiftUI `WKWebView` shell** that
loads the existing PWA, plus a **native ActivityKit / WidgetKit layer** fed by
the same push path the PWA already uses. Roughly 90% of the product stays HTML
served from `ccc-cloud`; ~10% is native glass.

**Recommendation:** hold. Write this spec, keep it warm. Build the MVP (WebView
shell + one Live Activity) **only after** the cloud analytics surface (W19/W25)
shows the go/no-go metric in §5 is crossed.

---

## 1. Thesis — thin shell, not a rewrite

### What we build
A single-screen SwiftUI app:

1. A `WKWebView` that loads `https://<cloud-relay-host>/` (the existing PWA),
   authenticated with the user's My-CCC session cookie/token exactly as Safari
   would be. The web app is the product. The shell adds no UI chrome of its own
   beyond a launch splash and an error/offline fallback.
2. A native **ActivityKit** Live Activity + **WidgetKit** extension. This is the
   *only* net-new product surface. It never renders CCC's app UI — it renders a
   tiny, purpose-built glance (§2) driven by data the relay already computes.
3. A thin JS ↔ native bridge (`WKScriptMessageHandler`) so the web app can
   `startActivity` / `endActivity` and register for push, without the native
   layer needing to understand CCC's domain model.

### Why this over a full native app

| Axis | Full native rewrite | Thin WebView shell + native glass |
|---|---|---|
| **Maintenance tax** | Every CCC feature reimplemented twice (web + Swift), forever. Two codebases drift. | One codebase (`ccc-cloud` HTML/JS). Native layer is ~1 screen + 1 widget, changes rarely. |
| **Iteration speed** | Ship a feature → App Store review (hours-to-days) before iPhone users get it. | Ship to the PWA → live on iPhone the moment the relay redeploys. Only the *glass* needs a release. |
| **App Store review risk** | Full surface under review every version; more rejection area. | Tiny native surface; PWA content updates never touch the binary. |
| **Simplicity moat** | Contradicts it. CCC's edge over Nimbalyst (heavyweight Electron IDE) is that it is *small*. A second native codebase is the exact bloat we refuse. | Preserves it. The native binary is deliberately dumb. |
| **Reversibility** | Sunk cost in Swift domain code if the bet is wrong. | Delete the shell, PWA users lose nothing. See §6. |

This mirrors the existing CCC macOS `.app`: a thin native shell (`scripts/macapp/main.swift`)
that spawns the git-tracked `run.sh` and shows a web dashboard. **The proven CCC
pattern is "native shell, web body."** iOS extends that pattern; it does not
break it.

CCC's product manifest positions autonomous-ops trust (Watchtower) and
*simplicity* as the moat — not feature-count parity. A second full native app is
precisely the maintenance surface the moat exists to avoid. The Live Activity is
the one place native buys something web physically cannot reach (the locked
screen / Dynamic Island), so that is the *only* place we go native.

---

## 2. Live Activity content

### What it shows
A CCC Live Activity answers one question at a glance: **"is my fleet okay, and
does anything need me?"** The state it renders (all already computed server-side
— see §3):

- **Active sessions** — count of live agent sessions right now.
- **Queue depth** — open tickets across watched queues (Watchtower / ux-fixes).
- **Workers grinding** — sessions actively working (writing / tool-running) vs idle.
- **Needs attention** — the highest-priority session waiting on the human
  (question pending, approval needed, or blocked). This is the money field — it
  is what turns a glance into an action.

Priority when space is tight: **needs-attention > workers grinding > queue depth
> active count.** If one session needs you, that dominates the compact view.

### Layouts

**Lock Screen / Banner (expanded, full width):**
```
┌──────────────────────────────────────────────┐
│  CCC  ·  fleet                        14:32   │
│                                                │
│   ● 4 sessions      ⚙ 2 grinding               │
│   ▸ queue: 3 open                              │
│                                                │
│   ⚠ needs you: "W34-ios-spec" — question       │
│      "Ship the shell MVP now?"                 │
└──────────────────────────────────────────────┘
```

**Dynamic Island — compact (the default resting state):**
```
 (⚙2)  … leading shows grind count, trailing shows attention badge …  (⚠1)
```
- Leading: `⚙ 2` (workers grinding).
- Trailing: `⚠ 1` (sessions needing you) — hidden when zero, so a healthy fleet
  shows only the grind count.

**Dynamic Island — minimal (when another app owns the Island):**
```
 ⚠   ← single glyph: amber ⚠ if anything needs you, else ⚙ grind dot, else ● idle
```
One glyph, color-coded: **amber = needs you**, **green = grinding**,
**grey = idle**. The color *is* the message.

**Dynamic Island — expanded (long-press):**
```
┌─────────────────────────────────────────────┐
│ leading        CCC fleet          trailing   │
│  ● 4                                  14:32   │
│                                               │
│  center:  ⚙ 2 grinding · ▸ 3 queued           │
│                                               │
│  bottom:  ⚠ W34-ios-spec needs you            │
│           [ Open ]        [ Answer ]          │
└─────────────────────────────────────────────┘
```
`Open` deep-links the WebView shell to that session; `Answer` deep-links
straight to its input (v2 — see §5). Deep links are `ccc://session/<id>` handled
by the shell, which navigates the WebView to the PWA's session route.

### States
- **Healthy / idle:** grey dot, "4 sessions · all idle". No amber, no noise.
- **Working:** green, grind count animates on change.
- **Needs you:** amber, session name + one-line reason. This is the state the
  whole feature exists to surface on a locked phone.
- **Drained / done:** the Watchtower `wait`-style terminal — "queue drained · 0
  grinding" — then the activity auto-ends after a short lull (§3 staleness).

---

## 3. Data path

The Live Activity must reflect fleet state **without the app open**. iOS gives
exactly one supported mechanism for that: **APNs push to update a running Live
Activity**, addressed by an ActivityKit **push token**. So the data path is:

```
 CCC daemon(s)            ccc-cloud relay              Apple APNs        iPhone
 (this repo, local)       (existing PWA backend)                        (shell + activity)
 ───────────────         ──────────────────────      ────────────      ───────────────
 /api/sessions/          relay already ingests        APNs push         ActivityKit
   live-activity   ──►    per-user fleet state   ──►   (liveactivity ──► renders new
 /api/attention          + notifications feed          content-state)    ContentState
 /api/ux-fixes/health          │                          ▲
 (already computed)            │  on meaningful delta      │
                               └── build ContentState ─────┘
                                   push to each device's
                                   ActivityKit push token
```

### Grounding on what already exists
This repo *already* computes the exact snapshot the activity needs, cheaply and
coalesced:

- **`/api/sessions/live-activity`** (`server.py`) — the coalesced, `(mtime,size)`-
  gated snapshot of live sessions with per-session activity fields
  (`is_live`, `pending_tool`, `question_waiting`, `needs_approval_message`, …).
  This is the WIP-chip source the dashboard sidebar already polls; it is the
  natural source for "active sessions / workers grinding / needs attention."
- **`/api/attention`** — the needs-you list.
- **`/api/ux-fixes/health`** — per-queue drain state → queue depth + "drained".

The **Cloud Relay (`ccc-cloud`)** already relays this per-user fleet state to the
PWA (that is what makes the PWA useful on a phone at all) and already runs a Web
Push path for notifications (My-CCC funnel, W19). So the server *inputs* exist.

### What is genuinely new (all in `ccc-cloud`, none in this repo)
1. **APNs credentials + a Live Activity push channel.** Web Push (VAPID) and
   ActivityKit push are *different* APNs surfaces. The relay must hold an APNs
   auth key and send `apns-push-type: liveactivity` payloads with a
   `content-state` matching the activity's `ContentState` Codable shape.
2. **Push-token registry.** The shell's JS bridge posts the ActivityKit push
   token (per active activity) up to the relay; the relay stores
   `{user, device, activity_push_token, activity_id}` and targets pushes at it.
   This is a small table next to the existing Web Push subscription store.
3. **A delta gate + budget.** Push on *meaningful* change only (attention
   appears/clears, grind count crosses a threshold, queue drains), not every
   2s tick. Coalesce like `_LIVE_ACTIVITY_SNAPSHOT_TTL` does locally. APNs
   throttles Live Activity pushes; over-pushing gets the app rate-limited. Target
   budget: **≤ 1 push / 15s per device under load, and on state-change edges**,
   with a heartbeat push at least every ~30–60 min to keep `staleDate` fresh.
4. **`staleDate` / dismissal policy.** Each pushed `ContentState` carries a
   `staleDate` (~a few minutes out). If pushes stop (daemon offline, phone
   offline), the activity greys to "stale — last seen 14:32" rather than lying.
   On terminal ("drained · 0 grinding") the relay sends a final push with
   `dismissal-date` so iOS auto-clears it.

### Fallback when push is unavailable
No APNs push (dev, or push disabled) → the activity still starts from the JS
bridge with a first `ContentState`, and refreshes opportunistically whenever the
shell is foregrounded (the WebView is already long-polling the relay). Degraded
but honest: the lock-screen number can be stale, and the `staleDate` says so.

**Net:** zero new endpoints in *this* repo. The daemon already emits the state.
All new bits live in `ccc-cloud`: APNs Live Activity channel, push-token table,
delta gate. This keeps the CCC daemon stdlib-only and unchanged.

---

## 4. Widgets

WidgetKit widgets are the **at-rest** companion to the **live** activity: the
activity exists while something is happening; the widget is always on the home /
lock screen even when nothing is live.

| Widget | Family | Shows | Refresh |
|---|---|---|---|
| **Fleet glance** | `.systemSmall` | active count · grind count · attention badge | Timeline, ~15 min; push-refreshed via `WidgetCenter.reloadTimelines` when the relay pushes. |
| **Queue depth** | `.systemMedium` | per-queue open counts + a drained/green state | Same. |
| **Lock-screen inline** | `.accessoryInline` | `CCC · 3 open · ⚠1` one-liner above the clock | Timeline, coarse. |
| **Lock-screen circular** | `.accessoryCircular` | ring = grind/idle ratio, center = attention count | Timeline, coarse. |

### Refresh budget (the hard constraint)
WidgetKit gives an app a **limited daily timeline-reload budget** (Apple does not
publish an exact number; treat it as scarce — order of dozens/day, more when the
widget is prominent). So:

- Widgets **do not** poll. They render the last `ContentState` the relay pushed
  (shared via an App Group container the shell writes to).
- The relay's Live Activity push *also* nudges `reloadTimelines` — the widget
  piggybacks on the activity's push budget instead of spending its own.
- Absent pushes, widgets fall back to a coarse timeline (~15 min) and show a
  `staleDate`-style "as of HH:MM". Never spin, never lie about freshness.

This is the same performance discipline as the daemon (CLAUDE.md § Performance
gates): the phone is just another client that must not do O(fleet) work per tick.

---

## 5. Scope + phasing

### MVP — "one number on the lock screen"
- SwiftUI `WKWebView` shell loading the PWA, My-CCC auth passthrough, deep-link
  scheme `ccc://`, offline/error fallback.
- **One** Live Activity: the fleet glance (§2), lock-screen + Dynamic Island
  compact/minimal/expanded.
- JS↔native bridge: `startActivity`, `endActivity`, `registerPushToken`.
- `ccc-cloud`: APNs Live Activity channel, push-token table, delta gate,
  `staleDate` policy.
- **Explicitly not** in MVP: home-screen widgets, `Answer`-from-activity, rich
  per-session deep links beyond "open session N."

### v2 — widgets + deep actions
- The four widgets in §4.
- `Answer` action on the expanded activity → deep link straight to a session's
  input in the PWA.
- Multi-fleet / multi-account switch in the shell.

### Effort estimate (rough, one engineer)
- MVP shell + one Live Activity: **~1.5–2.5 weeks** native, assuming the PWA and
  relay state already exist (they do). The bulk is APNs Live Activity plumbing
  and the `ContentState`/push-budget tuning, not UI.
- v2 widgets + deep actions: **~1–2 weeks**.
- Ongoing tax: low by design — the native binary changes only when the *glass*
  changes, which is rare. Product features ship through the PWA.

### App Store considerations
- **Dev-tool category risk.** Apple has historically been wary of apps that are
  "a website in a wrapper" (Guideline 4.2 — minimum functionality). The Live
  Activity + widgets are the defensible answer: the app provides **native
  capabilities a website cannot** (lock-screen presence, Dynamic Island, push-
  updated glances). Lead the review notes with that, screenshot the Live
  Activity, and it clears 4.2.
- **Remote code / WebView.** Loading a first-party PWA you control is fine.
  Don't inject arbitrary third-party JS; keep the bridge minimal and audited.
- **Background execution.** We do *not* need background fetch — updates arrive
  by APNs push. That sidesteps the biggest review/battery objection.
- **Account / privacy.** My-CCC sign-in must have a delete-account path and a
  privacy label covering the fleet-state relay. Same posture as the PWA.

### Go / no-go signal
Build the MVP **only when the cloud analytics surface (W19 My-CCC funnel /
W25 cloud analytics) shows both:**

1. **Mobile PWA usage is real** — e.g. **≥ 15–20% of My-CCC weekly-active
   sessions originate from an iPhone-class mobile viewport** (or a raw floor of
   **≥ ~50 weekly mobile PWA users**), sustained over ~4 weeks. Pick the exact
   numbers off the W25 dashboard once it has a baseline; the *shape* is "a real,
   growing mobile cohort," not a handful.
2. **Notification demand is proven** — **≥ 40% of those mobile users have opted
   into Web Push** (they *want* to be pinged about fleet state). High opt-in is
   the direct proxy for "these people would pin a Live Activity."

Both crossing = the lock-screen glance has a real audience → build MVP.
Either missing = hold; the PWA is enough. This is falsifiable and lives on a
dashboard we already own, so the trigger is objective, not vibes.

---

## 6. Non-goals & reversibility

### Explicit non-goals
- **Not** a native reimplementation of the CCC dashboard, Flow, or any web view.
  The PWA is the product; the shell only hosts it.
- **Not** offline agent control. The shell needs the relay online, same as the PWA.
- **Not** background agent execution on the phone. iOS is a *viewport + glance*,
  never a worker host.
- **Not** iPad-optimized or Mac Catalyst in scope (the Mac already has its `.app`).
- **Not** Android (a separate lane if ever; Live Activities are an iOS concept).
- **No** new endpoints or dependencies in *this* repo. `server.py` stays
  stdlib-only; all mobile-specific server work is in `ccc-cloud`.
- **No** push spam. The delta gate (§3) and refresh budget (§4) are load-bearing
  non-goals: a chatty Live Activity gets the app APNs-throttled and the user to
  disable it.

### Reversibility argument
The whole design is built to be cheap to abandon:

- The product lives in the PWA. Killing the iOS shell removes a lock-screen
  glance and nothing else — every PWA user keeps their full experience in Safari
  / home-screen web app.
- The only sunk cost is the thin shell + one widget extension + the relay's APNs
  channel. No CCC domain logic is trapped in Swift, because none was written in
  Swift.
- The go/no-go metric is measured *before* the build, on a dashboard we already
  run. If usage never crosses the threshold, we never pay the build cost at all —
  this spec is the artifact, and holding is the default.

That is the point of writing the spec now and building later: **maximum optionality
for near-zero cost.** Start-up nation move — scope the missile, don't launch it
until the radar lights up.

---

## Appendix — bindings to existing CCC surfaces

| Spec need | Already exists (this repo) | New (in `ccc-cloud`) |
|---|---|---|
| Active / grinding / attention state | `/api/sessions/live-activity`, `/api/attention` | relay per-user fan-out (mostly exists) |
| Queue depth / drained | `/api/ux-fixes/health` | — |
| Notification transport | Web Push (VAPID), W19 | **APNs Live Activity push channel** |
| Push addressing | Web Push subscription store | **ActivityKit push-token table** |
| Coalescing / budget discipline | `_LIVE_ACTIVITY_SNAPSHOT_TTL`, `(mtime,size)` cache | **delta gate + APNs rate budget** |
| Native shell pattern | macOS `scripts/macapp/main.swift` (web body, native shell) | iOS SwiftUI `WKWebView` shell |
