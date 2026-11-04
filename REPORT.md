# L6: sandboxed app marketplace MVP

## Delivered

Implementation commit: `69bc0c73` — `feat(apps): add sandboxed marketplace with permission consent`.
This report is recorded in a separate documentation commit on the same branch.

- Threat model and protocol in `docs/app-marketplace.md`; security and existing
  Applications documentation updated.
- Opaque-origin app iframe with server CSP sandbox headers on every package
  asset, including direct HTML/SVG visits and legacy asset URLs.
- Fixed read permissions (`sessions.read`, `queues.read`), host source/origin
  checks, independent server grants, generation-based revocation, and no
  arbitrary API forwarding. `spawn` and unknown permissions are refused.
- Shared staged-byte validation and consent for `ccc app install/list/remove`
  and Applications → Browse apps. Local folders and shallow HTTPS Git clones
  are supported. Existing IDs, symlinks, special files and path escapes are
  rejected. Built-ins and trusted local app behavior are retained.
- Configurable HTTPS catalog with two bundled examples: the existing Effects
  kit playground and a Session summary bridge client.
- Changelog fragment and focused Python, JavaScript and real-browser coverage.

Work is committed locally on `astra/L6`. Nothing was pushed, merged or deployed.
No managed service was restarted. No subagents were used.

## Verification

Run:

```sh
python3 -m pytest tests/test_app_marketplace.py
```

Result: **39 passed**, including Puppeteer against a disposable loopback HTTP
server using the real CCC handler. Checked successful consent and reads,
ungranted bridge calls, `Origin: null` rejection, opaque parent/storage access,
blocked direct API fetches, direct HTML/SVG sandboxing, working packaged assets,
and desktop/mobile rendering. Screenshots were inspected directly.

Additional checks:

- Python compilation, JavaScript syntax checks, CLI help and `git diff --check`
  passed.
- `tests/test_perf_budget.py`: **152 passed, 1 failed**. The failure is
  `test_archive_signature_delta_engine_churn_is_engine_scoped_refresh`, whose
  Codex signature delta returned `None`. It also fails when executed against
  the original baseline `HEAD` server source, before this implementation.
  Archive/signature code was not changed and no performance bound was relaxed.
- `tests/test_smoke.py` was **not run**.
- A live external Git repository clone was not exercised; the installer uses
  bounded-time, noninteractive HTTPS shallow cloning with package hooks disabled.

For manual verification after integrating the implementation:

```sh
ccc app install ./examples/apps/session-summary
ccc app list
ccc app remove session-summary
```

Alternatively, use Applications → Browse apps → Effects kit → Install. Consent
must show no permissions, and the app must open in its sandboxed host.

## Remaining limits

- Read-only permissions only; no spawn, writes, arbitrary networking or endpoint
  forwarding. No publisher signatures, automatic updates or dependency builds.
- Git sources must use HTTPS and contain the manifest at repository root.
- Clone timeout is bounded, but download/disk consumption before package
  validation has no quota. Abandoned staging directories need manual cleanup.
- Approved data may be disclosed by an app; this is not an exfiltration-proof
  container, CPU/memory quota, or boundary against the same OS user.
- The baseline archive-signature performance-test failure remains unresolved.

## Owner decisions

None required to review or use this MVP. Future write/spawn permissions,
publisher verification and resource quotas require separate design review.

## Restart requirements

- CCC dashboard server: **needs restart after integration** for backend changes.
- CCC worker / control-plane worker: **no restart needed**.
- WatchTower: **no restart needed**.

No restart was performed, as requested.
