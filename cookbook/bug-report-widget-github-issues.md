# In-app bug report widget → GitHub issue

A floating **Report an issue** button for your web app that screenshots the
page client-side, posts everything to your backend, and opens a
fully-contextualized GitHub issue — screenshot embedded, debug context
pre-attached.

This is the production-facing sibling of the
[Annotate → UX-fixes queue](annotate-to-ux-fixes-queue.md) recipe: Annotate is
a localhost dev tool for *you*; this widget is for *your real users and staff*,
running in production behind your app's normal auth.

## What this gives you

A user hits a bug. Instead of a vague email ("the schedule is broken??"), you
get a GitHub issue that already contains:

- the user's description, typed into a small modal,
- a **screenshot of exactly what they were looking at** (captured client-side
  the moment they clicked the button — before the modal covers the page),
- page URL, user agent, viewport size + DPR + orientation, URL params,
- **the last element they tapped** before reporting (selector, label,
  coordinates, how many seconds before capture) — usually the button that
  misbehaved,
- any app-specific context your pages registered (selected filters, open
  modal, active record id),
- who submitted it and from which account/organization.

Because the issue lands in GitHub with reproduction context, it's immediately
actionable — by a human, or by a Claude Code session that watches the issue
queue.

## Why this design

- **Screenshot at click time, not submit time.** The capture happens when the
  user clicks the floating button, *before* the modal opens. What you see in
  the issue is the broken state, not your own report form.
- **Client-side capture (`html2canvas-pro`)** needs no browser permissions, no
  native APIs, works on mobile. Dynamic-import it so it stays out of your
  initial bundle. JPEG at ~0.85 quality keeps payloads reasonable.
- **The backend owns the fan-out.** The browser sends one JSON payload; the
  server decides what happens: upload screenshot to storage, create the GitHub
  issue, optionally email a notification. Adding a destination never touches
  the client.
- **GitHub issue creation is best-effort.** If the GitHub API call fails, the
  user still gets a success response — never block a bug *report* on the bug
  *tracker* being reachable.
- **Screenshot goes to storage, issue gets a URL.** GitHub's issues API does
  not accept file attachments, so upload the image to object storage (Supabase
  Storage, S3, whatever you have) and embed the public URL as
  `![screenshot](...)` in the issue body.

## Architecture

```
browser                          your backend                       destinations
┌─────────────────────┐  POST   ┌──────────────────────┐
│ floating FAB        │ ──────▶ │ /api/v1/bug-report   │ ──▶ object storage (screenshot → public URL)
│ click → html2canvas │  JSON   │ auth, validate,      │ ──▶ GitHub REST: POST /repos/{owner}/{repo}/issues
│ → modal → submit    │         │ enrich, fan out      │ ──▶ (optional) notification email
└─────────────────────┘         └──────────────────────┘
```

## The payload

What the widget sends to your backend:

```json
{
  "type": "problem",
  "description": "Save button does nothing on the schedule page",
  "screenshot": "data:image/jpeg;base64,....",
  "pageUrl": "https://app.example.com/dashboard/schedule?week=2026-06-08",
  "userAgent": "Mozilla/5.0 ...",
  "viewport": { "width": 390, "height": 844, "dpr": 3, "orientation": "portrait" },
  "searchParams": { "week": "2026-06-08" },
  "appContext": { "selectedView": "week", "openModal": null },
  "lastTap": {
    "selector": "button.save-btn",
    "label": "Save",
    "x": 312, "y": 88,
    "msBeforeCapture": 4200
  }
}
```

`type` is `"problem" | "feature"` — same widget doubles as a feature-request
box, and the server maps it to the `bug` / `enhancement` issue label.

## Closing the loop: the in-app "Reported Issues" view

Reports going *out* is half the feature. The other half is the customer seeing
what happened to them — otherwise every report is a message in a bottle and
users stop reporting. The reference implementation adds a **Reported Issues**
page in the app with five tabs:

| Tab | Meaning | Derived from |
|---|---|---|
| **Open** | Reported, not yet picked up | GitHub state `open`, none of the labels below |
| **Needs Attention** | A human must respond (product team replied, or customer replied back) | label `needs-attention` |
| **In progress** | Someone (or some agent) is on it | issue has an assignee, or label `agent-in-progress` |
| **Icebox** | Deliberately parked — hidden from every other tab | label `icebox` (admin-only tab) |
| **Closed** | Done | GitHub state `closed` |

The key design decision: **there is no second database. GitHub is the single
source of truth**, and every tab is *derived* from GitHub state + labels at
read time. The app never stores issue status, so the view can never drift from
reality — close an issue from the GitHub UI, the in-app view agrees on next
load.

How the pieces work:

- **Read API** (`GET /api/v1/issues`): the backend lists the repo's recent
  issues (`state=all&since=<7 days ago>`), filters out pull requests, and
  keeps only issues belonging to this customer — see scoping below. For each
  issue it fetches comments, strips the metadata Context table out of the
  body for display, and returns a clean JSON shape the page renders.
- **Multi-tenant scoping with zero schema**: when the bug-report route creates
  the issue, the Context table already embeds the organization/tenant id in
  the body. The read API simply filters `issue.body.includes(orgId)`. A
  sentinel string like `ALL-ORGS` in a body broadcasts that issue to every
  tenant's view — free announcement channel.
- **Actions are thin GitHub mutations**, each its own endpoint:
  - *Resolve* → comment with the resolution note, `PATCH state: closed,
    state_reason: completed`, and **remove the `needs-attention` label** so it
    doesn't reappear as needing attention if reopened.
  - *Reopen* → comment with why, `PATCH state: open, state_reason: reopened`.
  - *Icebox / un-icebox* → add/remove the `icebox` label (create the label on
    first use).
  - *Reply* → post a comment; when the **customer** replies, also add
    `needs-attention` so the product team's queue lights up. The reply box on
    the customer's view is literally labeled "Reply as product team (flags
    Needs Attention)" on the admin side.
- **Permissions split**: customers see Open / Needs Attention / In progress /
  Closed and can reply. Admins additionally see Icebox and get the
  Resolve / Icebox buttons. Same page, gated by role.
- **Screenshot gotcha**: GitHub `user-attachments` URLs in issue bodies
  require a browser session — they 404 for the public and for PAT-authenticated
  fetches. Fetch comments with `Accept: application/vnd.github.full+json` to
  get `body_html`, which contains short-lived *signed* CDN URLs, and rewrite
  the raw-body attachment links to those before sending to the client.
- **Agent integration**: the `agent-in-progress` label is how an automated
  fixer (e.g. a Claude Code session watching the repo, or a CCC-managed
  worker) signals "claimed" — the customer sees the issue move from Open to
  In progress without any human touching it.

## Step-by-step deployment

1. **Create a GitHub token** with `issues: write` on the target repo
   (fine-grained PAT or a GitHub App). Put it in your backend env:
   `GITHUB_TOKEN=ghp-test-XXXX` (never in client code) plus
   `GITHUB_REPO=yourname/yourrepo`.
2. **Pick screenshot storage** and create a bucket/prefix for bug reports
   (e.g. Supabase Storage bucket `bug-reports` with public read).
3. **Implement widget + route** — hand the prompt below to Claude Code in your
   repo.
4. **Smoke-test**: click the FAB, submit a test report, confirm the issue
   appears in GitHub with the screenshot rendering inline.
5. **Add the Reported Issues view** (second half of the prompt below) and
   create the three labels in the repo: `needs-attention`, `agent-in-progress`,
   `icebox` (the icebox endpoint can also auto-create on first use).
6. **Optional**: point a Claude Code session (or a CCC-managed worker) at the
   repo's issues to triage/fix incoming reports — have it add
   `agent-in-progress` when it claims one so customers see it move tabs.

## The prompt

Paste this into Claude Code inside **your app's repo** (fill in the
ALL-CAPS placeholders):

```text
Add an in-app "Report an issue" feature to this project, in two parts:
(A) a floating button that screenshots the page, posts to our backend, and
opens a GitHub issue; (B) a "Reported Issues" page where the customer sees
the status of their reports, derived live from GitHub.

# Part A — capture and file

## Client widget

1. A small draggable floating action button (FAB), rendered on authenticated
   pages. Persist its dragged position in localStorage. Distinguish drag from
   click with a ~5px movement threshold.
2. On click — BEFORE opening any modal — capture:
   - a screenshot of document.body using html2canvas-pro (dynamic import so it
     stays out of the initial bundle), scale = window.devicePixelRatio,
     exported as JPEG dataURL at 0.85 quality, ignoring the FAB element itself;
   - a context snapshot: page URL, viewport {width, height, dpr, orientation},
     URL search params, and the last tapped element (install a lightweight
     global click/tap tracker that records selector, role, label, text, x/y,
     element id, and timestamp — exclude taps on the FAB itself).
   If the screenshot fails, still open the modal — a report without a
   screenshot beats no report.
3. The modal: a type toggle (Problem / Feature request), a required description
   textarea, and a preview thumbnail of the captured screenshot. On submit,
   POST everything as JSON to /api/v1/bug-report. Show a success toast with
   the resulting issue URL if one comes back.
4. Optionally expose a global hook (e.g. window.__openBugReport = openFn) so
   other UI (menus, error boundaries) can trigger the same flow.

## Server route — POST /api/v1/bug-report

1. Require this app's normal auth; reject anonymous calls. Validate that
   description is non-empty and type is "problem" | "feature".
2. Decode the screenshot dataURL to a Buffer and upload it to STORAGE_CHOICE
   (bucket "bug-reports", filename from timestamp + submitter), getting back a
   public URL. Upload failure must not fail the request — degrade to a
   "(screenshot upload failed)" note.
3. Create a GitHub issue via the REST API:
     POST https://api.github.com/repos/${GITHUB_REPO}/issues
     headers: Authorization: Bearer ${GITHUB_TOKEN},
              Accept: application/vnd.github+json
     body: {
       "title": "[APP_NAME Problem] <first 80 chars of description>",
       "labels": ["bug"]  // or ["enhancement"] for feature requests
     }
   Issue body (markdown): the description, then a "### Context" table
   (submitted by, page URL, viewport, URL params, last tap, timestamp), an
   optional "### App context" json block, and "### Screenshot" with
   ![screenshot](<public URL>).
   GitHub failure is logged but non-blocking — the user still gets success.
4. GITHUB_REPO and GITHUB_TOKEN come from server env vars only; never expose
   them to the client. Respond with { sent: true, issueUrl }.
5. Optional: also send a notification email to BUG_REPORT_NOTIFY_EMAIL with
   the same content and the screenshot attached.
6. IMPORTANT for Part B: include this app's tenant/organization id as a row in
   the issue body's Context table — the Reported Issues view filters on it.

# Part B — the customer-facing "Reported Issues" view

GitHub is the single source of truth. Do NOT add issue-status tables to our
database; every status below is derived from GitHub state + labels at read
time.

## Read API — GET /api/v1/issues

1. Require auth. Fetch the repo's issues from GitHub
   (state=all, since=<7 days ago>, per_page=100), skip pull requests, and keep
   only issues whose body contains this user's tenant/organization id OR the
   literal sentinel "ALL-ORGS" (broadcast announcements).
2. For issues with comments, fetch them with
   Accept: application/vnd.github.full+json. GitHub user-attachments URLs in
   bodies are NOT publicly fetchable; build a map from the signed
   private-user-images.githubusercontent.com URLs found in body_html and
   rewrite the raw-body attachment links to them.
3. Return clean JSON per issue: number, title (strip the "[APP_NAME ...]"
   prefix), state, stateReason, url, createdAt/updatedAt/closedAt, labels,
   assignees, description (body with the "### Context" section and images
   stripped), and comments.

## The page — "Reported Issues"

1. Five tabs with counts, classified from the read API:
   - Open:            state open, none of the conditions below
   - Needs Attention: state open + label "needs-attention"
   - In progress:     state open, no needs-attention, and (has assignee OR
                      label "agent-in-progress")
   - Icebox:          state open + label "icebox" — admin-only tab; iceboxed
                      issues are hidden from every other tab
   - Closed:          state closed
2. Each issue card: #number, status pill, title, description excerpt, comment
   thread, and action buttons appropriate to the tab and role.
3. A reply box ("Type your response…") on open-state tabs.

## Action endpoints (thin GitHub mutations, all auth-gated)

- POST /api/v1/issues/[number]/resolve — post a resolution comment, PATCH the
  issue {state: "closed", state_reason: "completed"}, and DELETE the
  "needs-attention" label.
- POST /api/v1/issues/[number]/reopen — post a comment with the reason, PATCH
  {state: "open", state_reason: "reopened"}.
- POST /api/v1/issues/[number]/icebox — add the "icebox" label (create the
  label in the repo on first use); DELETE removes it (un-icebox).
- POST /api/v1/issues/[number]/comment — post the reply as a comment,
  prefixed with who wrote it; when the reply comes from the customer side,
  also add the "needs-attention" label so it surfaces in the team's queue.

## Roles

- Customers/staff: see Open, Needs Attention, In progress, Closed; can reply.
- Admins: additionally see Icebox and get Resolve / Icebox / Reopen actions.

## Constraints
- No new client-visible secrets. The browser only ever talks to our own route.
- Match this codebase's existing conventions for components, routes, and auth
  helpers.
- Keep html2canvas-pro out of the main bundle (dynamic import on first click).

Verify end-to-end: run the app, submit a test report, confirm a GitHub issue
exists with the screenshot rendering inline and correct labels, then confirm
it appears under Open in the Reported Issues page, moves to Needs Attention
after a customer reply, and lands in Closed after Resolve.
```

## Hard-won details (from the reference implementation)

These came out of running this widget on a production site; steal them:

- **`msBeforeCapture` on the last tap is gold.** "User tapped *Save* 4.2s
  before reporting" usually *is* the repro.
- **Snapshot context before the modal opens**, then prefer the snapshot over
  live values at submit time — otherwise the URL/viewport describe your modal,
  not the bug.
- **Per-page app context hook**: let pages register a small
  `appContext` object (selected filters, open record). The widget includes
  whatever is registered at capture time. Cheap to add, massively shortens
  triage.
- **One email per recipient, not one email with N recipients** — a single bad
  address shouldn't block the others. Dedupe first.
- **Cap the GitHub issue title** (~80 chars from the description) and put the
  full text in the body; GitHub truncates ugly.
- **Different submitter kinds can fan out differently.** The reference routes
  staff reports to GitHub issues, but end-customer reports into the app's own
  inbox/escalation surface — customers shouldn't show up in your issue
  tracker raw. Start with everything → GitHub; split later if you have two
  audiences.
