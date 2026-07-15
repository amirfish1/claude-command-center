# v5.8 Download Launch Design

## Goal

Publish CCC v5.8.0 and make the notarized macOS DMG the landing page's one
above-the-fold call to action, labeled exactly `DOWNLOAD CCC`, while counting
anonymous download clicks from the site without making analytics a dependency
of the download.

## Release outcome

The public v5.8.0 release must include both assets:

- `ccc-v5.8.0.dmg`, used by the Sparkle appcast;
- `ccc.dmg`, used by the version-stable landing-page URL.

The `v5.8.0` tag points to the existing v5.8.0 release commit. The GitHub
release, Sparkle feed, and Homebrew formula all publish the same version. The
notarized DMG already produced for this release remains the authoritative
binary; publication must not rebuild or silently substitute another artifact.

## Landing-page hierarchy

The hero contains exactly one CTA button. Its visible text is exactly
`DOWNLOAD CCC`, and its `href` is the direct, version-stable GitHub asset URL:

`https://github.com/amirfish1/claude-command-center/releases/latest/download/ccc.dmg`

The current Tour, Install, and GitHub hero buttons are removed. The quick curl
installer is removed from the hero and remains available in the below-the-fold
Install section. Ordinary navigation links may remain, but none competes with
the hero button visually.

Short helper text below the button states that the download is the
Apple-notarized macOS app for macOS 11 or later and identifies v5.8.0. It also
links to the existing Install section for Homebrew, Linux, and Windows paths.
The page's visible version badges and What's New strip are updated to v5.8.0.

## Download counting

The CTA always keeps its direct GitHub URL. Its click handler starts a
best-effort background `POST` to the existing public telemetry Worker and never
calls `preventDefault`, waits for the response, changes the target, or catches
the user in a redirect. A failed, blocked, or slow telemetry request therefore
cannot delay or prevent the DMG download. The request uses unload-safe browser
delivery (`sendBeacon`, with a keepalive `fetch` fallback).

The Worker adds `POST /v1/download`. The request carries no user data; the
handler writes only:

- the server-generated UTC receive time;
- the fixed artifact name `ccc.dmg`;
- the fixed source name `landing-hero`.

The handler does not read or persist IP address, User-Agent, Referer, cookies,
or request body. It returns `204` whether the insert succeeds or fails, so the
endpoint reveals no storage state and is never part of the download path.

The D1 schema gains a `downloads` table for those three bounded fields. The
public `/v1/stats` aggregate adds `total_downloads` and a 30-day
`downloads_by_day` series. The existing public stats page labels the metric
honestly as **site download clicks**, not completed installations or unique
people. No identity or deduplication is attempted.

## Privacy and failure behavior

The landing page discloses the anonymous click count beside the CTA and links
to the public telemetry contract. The contract and Worker deployment record
are updated in the same release slice.

Download behavior remains functional with JavaScript disabled, telemetry
blocked, the Worker offline, or D1 unavailable. Tracking is intentionally
best-effort. The counter can be inflated by repeated clicks or automation, so
all user-facing wording says `download clicks` and never claims unique users or
successful installs.

## Verification

Automated tests must prove:

- the hero has exactly one CTA and its exact label and stable DMG URL;
- the quick installer and competing hero buttons are absent above the fold;
- clicking the CTA does not cancel or replace native link navigation;
- the Worker accepts the download event, persists only the three approved
  values, and returns `204` even when D1 fails;
- the stats response exposes only aggregate download totals and daily counts.

Release verification must prove:

- the exact local DMG hash, notarization, staple, code signature, embedded
  version, and Sparkle signature;
- the public tag and GitHub release assets;
- the stable and versioned URLs return the published DMG;
- the public appcast advertises v5.8.0 with the matching size and signature;
- the Homebrew formula points to v5.8.0 with the correct source archive hash;
- the live landing page renders the single CTA above the fold;
- a controlled live click increments the Worker counter without changing the
  direct download destination;
- a clean macOS launch from the public DMG reaches CCC v5.8.0.
