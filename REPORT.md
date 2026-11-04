# W2i: app marketplace, wave 2

## Delivered locally

Built on L6 and rebased cleanly onto `origin/main`. No push, merge, deployment,
managed-service restart, production-data changes or subagents.

- Three installable public listings: Effects kit, Session summary and Queue
  summary. Queue summary adapts the existing read-only queue projection and
  session bridge example. No private or personal app is included.
- Local ZIP installs with validation before extraction, compressed/expanded
  size limits, traversal/duplicate-path/symlink/special-file rejection and a
  streaming byte cap. Folder and HTTPS Git installation remain supported.
- Strict manifests reject duplicate keys, unsupported fields, malformed icons,
  excessive size/depth and unknown permissions.
- Chromium tests exercise direct API fetch denial, forged postMessage identity
  and permissions, real bridge reads, consent, all three listings, opaque
  origins, direct HTML/SVG visits and desktop/mobile layouts.
- Installed listings retain their Installed state and Open link when reopened;
  removing an app updates the catalog. Expired previews are pruned in bounded
  batches. Git clones ignore inherited configuration so package attributes
  cannot invoke user-configured checkout filters.
- Contributor guide, PR-based submission description, JSON schemas and an
  offline stdlib-only validator share runtime catalog rules.

## Commits

- `ca2835f4` — queue example, ZIP imports and hostile-package tests.
- `fa308619` — contributor guide, schemas and catalog validator.
- `d60681a8` — installed UI state, preview cleanup, isolated Git configuration.

- `868b7be4` — fail closed on corrupt grants and mismatched catalog identities;
  clean CLI errors for unsupported ZIP compression.

Rebased onto fetched `origin/main` at `709c8860`.

## Verification

```sh
python3 -m pytest tests/test_app_marketplace.py
python3 scripts/validate-apps-index.py
node --check static/app-marketplace.js
python3 -m py_compile ccc_server/app_marketplace.py
git diff --check
```

Last completed focused run: **109 passed**, including real Chromium against a
disposable loopback HTTP server with temporary marketplace state. Desktop and
mobile screenshots inspected directly. No managed server was used. Malformed-input and corrupt-grant coverage also passed.

The offline validator accepts all three listings and package manifests. It
never clones a contributor repository. The smoke suite was not run, per repo
instructions. A dedicated marketplace CI workflow now installs Puppeteer and requires the
browser check instead of silently skipping it; schemas are also validated there.
This workflow has not been run on GitHub (the branch is not pushed).

No external Git repository was cloned; a real local Git config
probe verifies inherited filters and injected config are disabled.

## Remaining limits / owner decisions

No owner decision is needed to review this branch. Publisher signatures,
updates, write permissions and network permissions remain out of scope. Git
clone download/disk consumption before checkout validation is not quota-limited
(the existing 60-second timeout remains). Granted data may be disclosed by an
app; the sandbox is not a CPU/memory or exfiltration-proof boundary.

The L6 report recorded a pre-existing archive-signature performance-test failure;
this lane does not change that subsystem or relax any performance bound.

## Restart requirements after integration

- CCC dashboard server: **needs restart** for backend changes.
- CCC worker / control-plane worker: **no restart needed**.
- WatchTower: **no restart needed**.

No restart was performed. All changes remain local on the lane branch.
