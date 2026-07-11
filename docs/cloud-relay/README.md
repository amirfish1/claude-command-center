# CCC Cloud — user guide

CCC Cloud is an **optional, account-based relay** that lets you check on your
CCC machines from a phone browser and send bounded actions back to them. Your
laptop stays the execution environment: the cloud stores small routing
metadata about what needs your attention, never your code or your work. You can
run CCC forever without it — CCC Cloud is something you turn on, and can turn
off just as easily.

This guide is the user-facing companion to the wire contract in
[`PROTOCOL.md`](./PROTOCOL.md) and the security detail in
[`SECURITY-AND-PRIVACY.md`](./SECURITY-AND-PRIVACY.md).

---

## What CCC Cloud is

When you enable it, your local CCC opens an **outbound-only** connection to a
hosted relay and pushes a minimized snapshot of what is happening on that
machine: which sessions are live, which need attention, queue and worker
health, schedules, and any agent questions waiting for you. From a phone
browser signed in to the same account, you see all of that across every paired
machine, and you can send a few narrow actions back (answer a question, send
input, wake a session). The relay is **not a proxy** into your laptop: it can
carry only the specific messages described here, and your machine validates and
executes every one of them itself.

The local machine does the work. The cloud is a mailbox and a status board.

## What stays local vs. what is stored vs. what transits briefly

CCC Cloud sorts every piece of data into a class, and each class is handled
differently. In plain language:

- **Never leaves your machine (class E).** Source code, diffs, transcripts,
  tool output, environment files, tokens, and raw absolute paths. These are
  never uploaded, under any setting.
- **Stored durably, but small (classes A–C).** Your email and consent records
  (class A); your paired devices as opaque IDs with a *hashed* credential,
  their platform, CCC version, and last-seen time (class B); and operational
  metadata — session status as bounded enums, counts, timestamps, and short
  redacted labels (class C). Retention windows are listed in the data
  classification document; for example the per-device audit log is kept 90
  days, notification records 30 days.
- **Transits briefly, then deleted (class D).** The handful of things that must
  actually pass through the relay: input text you send to a session (deleted on
  delivery or after a 10-minute expiry), a waiting question's text and options
  (superseded on the next sync, or at most 24 hours), and an on-demand session
  preview window (cached at most 5 minutes, then deleted). These are the only
  message contents the relay ever holds, and only for as long as it takes to
  hand them over.

Session titles and queue/schedule labels are treated as sensitive by default:
your local CCC runs a redaction pass over them before anything leaves the
machine, and you can switch to fully opaque labels per device (see *Disabling
and opaque labels* below).

## How pairing works

Pairing links one computer to your account. You need to be signed in to the
cloud app in a browser, and CCC running on the machine you want to pair.

1. In the cloud app, start pairing. It shows a short **single-use pairing
   code** (8 characters, valid for 10 minutes) as text and as a QR code.
2. Open the CCC Cloud settings panel in your **local** CCC dashboard and paste
   the code in. (Pairing can only be started from the machine itself, on a
   localhost connection — a remote page cannot pair a computer for you.)
3. Your local CCC contacts the relay to complete pairing and gets back a
   **masked** version of the account email — something like `a***@***.com`.
4. CCC shows you: **"Pair this computer with `a***@***.com`?"** Credentials are
   saved **only after you confirm**. This is the moment to check that the masked
   email is really yours — if it is not, decline, and nothing is stored.
5. Once confirmed, the machine appears online in the cloud app on its first
   sync.

The pairing code is single-use and rate-limited, so a leaked or shoulder-surfed
code has a narrow window and can pair exactly one machine.

## Using the mobile app

The cloud app is a **PWA** (progressive web app) built for a phone screen. Open
it in your phone's browser, sign in with your email and a single-use 6-digit
code, and — on iOS 16.4+ and Android — install it to your home screen to enable
notifications. There are six views:

- **Attention** — the home feed: waiting questions, stuck workers or queues,
  failures, completed work awaiting follow-up, and offline machines, ranked so
  the thing that needs you most is on top.
- **Sessions** — recent and live sessions across all paired machines, each with
  a safe title, which machine and engine it is on, its status, how recently it
  ran, remaining context, and a freshness stamp.
- **Workers** — queue depth, worker health, and stuck flags.
- **Schedules** — recurring/cron jobs and their recent results.
- **Questions** — agent questions waiting for an answer, with their options.
- **Devices** — your paired machines, with the ability to revoke any of them.

**Freshness and "stale."** Every remote datum carries an explicit *as-of*
timestamp. An item is marked **stale** when its snapshot is older than about 90
seconds, and a device is shown **offline** when it has not synced for about 60
seconds. Staleness is visually unmistakable on purpose — you should never
mistake an old snapshot for the live truth.

**Sending → delivered semantics.** When you send an action (input, a wake, an
answer), the app shows **"sending"** until the target machine has actually
fetched the command, and only shows **"done"** once that machine reports the
result. You always see the real state of a round-trip, not an optimistic guess.

## Notifications

Notifications are **opt-in as a whole**. There is a single master switch that is
**off by default**; until you turn it on, nothing is delivered on any channel —
not even a waiting-question alert. On top of the master switch:

- **Per-kind toggles.** Waiting-question and approval-needed alerts are the two
  "you must act" kinds and are on by default *once the master switch is on*;
  session-completed, session-failed, worker-stuck, schedule-failed,
  device-offline, and digest are individually opt-in.
- **Quiet hours.** A daily window during which notifications are suppressed.
- **Dedupe and rate limiting.** The same alert will not repeat within a short
  window, and delivery is capped per hour so you are never flooded.
- **Privacy-safe previews.** Lock-screen and push previews use fixed, generic
  copy — for example *"An agent is waiting for your answer."* No titles, prompt
  text, repo names, or machine names ever appear in a preview. Tapping the
  notification opens the cloud app to the right view (which still requires your
  signed-in session).

Delivery is Web Push for the installed PWA, with email as a fallback for the
"you must act" kinds when no push subscription is available.

## Revoking a device

You can revoke a paired machine from **either side**:

- From the cloud app's **Devices** view (useful if the machine is lost or you
  no longer trust it).
- From the machine itself, by unpairing in its local CCC settings.

Revoking **immediately** blocks any new relay actions for that device and
forces it to re-pair before it can be used again. A revoked machine's next
attempt to sync or poll fails closed with a clear error; the local client stops
its loop and marks itself as needing re-pairing. There is no degraded or
partial mode — revocation is immediate and terminal. Local unpair also wipes
the device's stored credentials regardless of whether it could reach the relay.

## Disabling CCC Cloud (and opaque labels)

Two independent ways to turn it off, plus a per-device minimization control:

- **UI toggle.** Turn CCC Cloud off in your local CCC settings. This stops the
  outbound connection and nothing else — it does not change your network
  binding, allowed origins, Tailscale config, same-origin checks, or telemetry
  consent.
- **Hard switch.** Set the environment variable `CCC_CLOUD_DISABLED=1`. This
  kills the relay loop unconditionally, regardless of any saved config. It is a
  parallel, equally hard switch to the telemetry kill switch, and **independent
  of it** — disabling the cloud does not affect telemetry, and disabling
  telemetry does not affect the cloud.
- **Opaque labels.** If you want CCC Cloud on but a given machine's titles and
  repo/queue labels kept private, turn off "share titles" for that device.
  Titles and repo labels are then replaced with opaque labels like *"Session on
  `<machine>`"* before anything is sent. (With sharing on, titles still pass
  through the local redaction pass first.)

## Local / Tailscale vs. CCC Cloud

CCC Cloud does not replace the existing local and Tailscale access paths. Both
are **fully supported**; choose either, both, or neither.

| | Local / Tailscale | CCC Cloud Relay |
|---|---|---|
| Account required | No | Yes (email, no password) |
| Network path | Your private network | Outbound-only to hosted relay |
| Reachable from | Your network / VPN | Anywhere with a browser |
| Cloud dependency | None | Hosted relay + your account |
| Notifications | No | Yes (opt-in) |
| Retention analytics | No | Yes, under separate consent |

Enabling CCC Cloud never changes your bind host, never adds allowed origins,
never touches Tailscale, never exposes your local dashboard, and never alters
anonymous-telemetry consent. Disabling it just stops the outbound connection.

## Export and account deletion

- **Export.** One click produces a JSON copy of your account-linked cloud data.
- **Deletion.** Deleting your account removes your account-linked cloud data —
  sessions, devices, push subscriptions, and product events — and **invalidates
  every paired device**. An online device's next call fails closed exactly like
  a revocation; an **offline** device discovers the deletion on its next
  connection attempt and self-disables. Transient (class D) message content is
  already gone by its short TTL. If you want a copy first, run the export before
  deleting. Deletion is the terminal action — there is nothing left to route to
  afterward.

## Current limitations (v1)

Stated plainly so there are no surprises:

- **No end-to-end encryption in v1.** Relay payloads are encrypted in transit
  (TLS) and minimized, but the relay process can technically read the class C/D
  fields it routes. We do **not** claim end-to-end encryption in protocol v1;
  the hosted trust boundary is documented honestly in
  [`SECURITY-AND-PRIVACY.md`](./SECURITY-AND-PRIVACY.md). A device-verifiable
  signed envelope is planned for v2.
- **No `session.stop`.** Stopping a session remotely is deferred pending
  security review and is not in v1. The v1 write actions are limited to bounded
  session input, answering a question, and waking a session.
- **Browser polling latency.** The mobile app refreshes on a short adaptive
  poll (a few seconds), so remote views are near-real-time, not instantaneous.
  Device-facing command delivery is faster (typically sub-second).
- **Single region / single instance.** The relay runs as a single small service
  in one region; it is sized for the current scale, not multi-region high
  availability.

## Security reporting

Found a security issue in CCC or CCC Cloud? Follow the process in the repo's
[`SECURITY.md`](../../SECURITY.md): non-sensitive issues can go to a public
GitHub issue, but anything that could enable code execution, credential theft,
or a boundary escape should be reported privately to the maintainer (contact in
`LICENSE`) rather than filed publicly.
