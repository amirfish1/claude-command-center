# v5.8 Download Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Publish CCC v5.8.0, make its notarized DMG the landing page's single above-the-fold DOWNLOAD CCC CTA, and expose a privacy-safe aggregate count of landing-page download clicks.

**Architecture:** The hero links directly to GitHub's stable ccc.dmg release asset, so analytics is never in the download path. A best-effort browser beacon posts an empty request to the existing Cloudflare Worker, which stores only receive time plus fixed artifact/source constants and returns aggregates through /v1/stats. The Worker and release assets go public before the landing-page commit is pushed.

**Tech Stack:** Static HTML/CSS/JavaScript, Python/pytest, Cloudflare Worker ES modules, Node's built-in test runner, Cloudflare D1, GitHub Releases, Sparkle, Homebrew.

## Global Constraints

- The hero's only CTA text is exactly DOWNLOAD CCC.
- The CTA href is exactly https://github.com/amirfish1/claude-command-center/releases/latest/download/ccc.dmg.
- Tracking never delays, redirects, replaces, or cancels native link navigation.
- A click stores only receive time, fixed artifact ccc.dmg, and fixed source landing-hero.
- Public wording says site download clicks, never completed downloads, installs, or unique users.
- No cookie, identifier, fingerprint, IP, User-Agent, Referer, or request body is persisted.
- Publish the existing ccc-v5.8.0.dmg; do not rebuild or substitute it.
- GitHub Release contains byte-identical ccc-v5.8.0.dmg and ccc.dmg assets.
- Preserve unrelated shared-worktree edits and commit only named files.

---

### Task 1: Add the bounded Worker event

**Files:**
- Create: infra/telemetry-worker/package.json
- Create: infra/telemetry-worker/index.test.mjs
- Create: infra/telemetry-worker/migrations/0001-downloads.sql
- Modify: infra/telemetry-worker/index.js
- Modify: .github/workflows/ci.yml

**Interfaces:**
- Consumes: empty POST /v1/download.
- Produces: D1 values (received_at, artifact, source) and opaque 204.

- [ ] **Step 1: Write failing tests**

Create package.json with type module and test script node --test index.test.mjs. In index.test.mjs, import the Worker. The first test passes private-looking IP, User-Agent, Referer, Cookie, and body values; its fake DB records bind arguments and requires exactly an ISO timestamp, ccc.dmg, and landing-hero. The second test makes DB.prepare throw and still requires an empty 204 response.

- [ ] **Step 2: Verify RED**

Run: npm test --prefix infra/telemetry-worker

Expected: 2 failures because /v1/download returns 404.

- [ ] **Step 3: Add schema and handler**

Create migrations/0001-downloads.sql:

~~~sql
CREATE TABLE IF NOT EXISTS downloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at TEXT NOT NULL,
  artifact TEXT NOT NULL CHECK (artifact = 'ccc.dmg'),
  source TEXT NOT NULL CHECK (source = 'landing-hero')
);
CREATE INDEX IF NOT EXISTS downloads_received_at_idx ON downloads(received_at);
~~~

Add to index.js and route POST /v1/download to handleDownload(env):

~~~javascript
async function handleDownload(env) {
  try {
    await env.DB.prepare(
      "INSERT INTO downloads (received_at, artifact, source) VALUES (?, ?, ?)"
    ).bind(new Date().toISOString(), "ccc.dmg", "landing-hero").run();
  } catch (_) {
    // Counting never exposes storage health or enters the download path.
  }
  return new Response(null, { status: 204 });
}
~~~

The handler deliberately does not receive request.

- [ ] **Step 4: Verify GREEN and add CI**

Run: npm test --prefix infra/telemetry-worker

Expected: 2 tests pass.

Add a telemetry-worker job to .github/workflows/ci.yml using setup-node@v4, Node 22, and npm test --prefix infra/telemetry-worker.

- [ ] **Step 5: Commit**

~~~bash
git add infra/telemetry-worker/package.json \
  infra/telemetry-worker/index.test.mjs \
  infra/telemetry-worker/migrations/0001-downloads.sql
git commit --only infra/telemetry-worker/package.json \
  infra/telemetry-worker/index.test.mjs \
  infra/telemetry-worker/migrations/0001-downloads.sql \
  infra/telemetry-worker/index.js .github/workflows/ci.yml \
  -m "feat(telemetry): count landing download clicks"
~~~

### Task 2: Add aggregate stats and privacy documentation

**Files:**
- Modify: infra/telemetry-worker/index.test.mjs
- Modify: infra/telemetry-worker/index.js
- Modify: docs/stats/index.html
- Modify: docs/telemetry.md
- Modify: docs/telemetry-public.md
- Modify: infra/telemetry-worker/README.md

**Interfaces:**
- Produces: totals.total_downloads and downloads_by_day entries containing only day and download_clicks.

- [ ] **Step 1: Write a failing stats test**

The fake totals query returns total_downloads 7. The fake downloads query returns [{day: "2026-07-15", download_clicks: 4}]. Require those exact aggregates in GET /v1/stats and require payload.downloads to be absent.

- [ ] **Step 2: Verify RED**

Run: npm test --prefix infra/telemetry-worker

Expected: failure because downloads_by_day is absent.

- [ ] **Step 3: Add aggregate-only queries**

Add a total_downloads subquery and:

~~~javascript
const downloadsByDay = (await env.DB.prepare(
  "SELECT substr(received_at, 1, 10) AS day, COUNT(*) AS download_clicks " +
  "FROM downloads GROUP BY day ORDER BY day DESC LIMIT 30"
).all()).results;
~~~

Expose only downloads_by_day: downloadsByDay.

- [ ] **Step 4: Add public stats UI and docs**

Add a Download clicks tab in docs/stats/index.html with Site download clicks, Clicks today, Clicks last 30d, timelineChart, and dayBars. State repeated clicks count and this is neither unique people nor completed installs.

Add Landing-page download clicks to docs/telemetry.md with the endpoint, three persisted values, absent values, best-effort delivery, and interpretation. Update telemetry-public.md and the Worker README with table, migration command, endpoint, and aggregates.

- [ ] **Step 5: Verify and commit**

~~~bash
npm test --prefix infra/telemetry-worker
git diff --check -- infra/telemetry-worker docs/stats/index.html \
  docs/telemetry.md docs/telemetry-public.md
git commit --only infra/telemetry-worker/index.test.mjs \
  infra/telemetry-worker/index.js docs/stats/index.html docs/telemetry.md \
  docs/telemetry-public.md infra/telemetry-worker/README.md \
  -m "feat(stats): publish site download clicks"
~~~

Expected: 3 Worker tests pass and diff check prints nothing.

### Task 3: Push and deploy the counter first

**Files:**
- Deploy: infra/telemetry-worker/migrations/0001-downloads.sql
- Deploy: infra/telemetry-worker/index.js

- [ ] **Step 1: Run full tests and push**

~~~bash
npm test --prefix infra/telemetry-worker
python3 -m pytest -q tests/
git push origin main
~~~

Expected: zero failures; shared uncommitted files remain local.

- [ ] **Step 2: Apply and inspect D1**

~~~bash
cd infra/telemetry-worker
npx wrangler d1 execute ccc-telemetry --remote \
  --file migrations/0001-downloads.sql
npx wrangler d1 execute ccc-telemetry --remote \
  --command "PRAGMA table_info(downloads)"
~~~

Expected columns: id, received_at, artifact, source.

- [ ] **Step 3: Deploy and prove one event**

Run npx wrangler deploy. Query SELECT COUNT(*) AS total_downloads FROM downloads, POST once to /v1/download with private-looking headers/body, and query again. Require status 204 and exactly +1. The four-column schema proves request metadata/body cannot persist.

### Task 4: Publish the exact v5.8.0 distribution

**Files:**
- Publish: ccc-v5.8.0.dmg
- Commit: docs/appcast.xml
- Modify: /Users/amirfish/Apps/homebrew-ccc/Formula/ccc.rb

**Interfaces:**
- Tag target: e8aa29f.
- DMG SHA-256: 59359ff09172ab50214b2585299d4dc0e9161b896eb94d33830f44d8d13ee9c0.
- Sparkle signature: Q/T+QWVJc10KImbBRyGukOAhMAfFZnnQeH7n+O1GyBLoOMTGxR3PlhTZbGpa/RxBnxtuZuCoY4ETBmcfZRVrDQ==.

- [ ] **Step 1: Re-verify the binary**

Run shasum, hdiutil verify, spctl, stapler validate, mount read-only, codesign --verify --deep --strict the actual app, read embedded versions, and rerun sign_update. Require exact interface values.

- [ ] **Step 2: Create tag and release**

~~~bash
git tag -a v5.8.0 e8aa29f -m "v5.8.0"
git push origin v5.8.0
gh release create v5.8.0 ccc-v5.8.0.dmg --title v5.8.0 \
  --notes-file <(awk '/^## \[5\.8\.0\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md)
gh api --method POST -H 'Content-Type: application/octet-stream' \
  "https://uploads.github.com/repos/amirfish1/claude-command-center/releases/$(gh api repos/amirfish1/claude-command-center/releases/tags/v5.8.0 --jq .id)/assets?name=ccc.dmg" \
  --input ccc-v5.8.0.dmg
~~~

Expected assets: ccc-v5.8.0.dmg and ccc.dmg.

- [ ] **Step 3: Verify bytes and publish appcast**

Download both public assets to /tmp and require the exact DMG hash for each. Commit only docs/appcast.xml as chore(release): publish v5.8.0 appcast and push. Require the live first item to be v5.8.0, length 3490799, with the interface signature.

- [ ] **Step 4: Publish Homebrew**

Fetch the v5.8.0 source archive and compute SHA-256. Update only Formula/ccc.rb via apply_patch, run available brew audit/test checks, commit ccc 5.8.0, and push the tap.

### Task 5: Implement the one above-the-fold CTA test-first

**Files:**
- Modify: tests/test_landing_hero_static.py
- Modify: docs/index.html

**Interfaces:**
- Consumes: live stable asset and Worker endpoint.
- Produces: #downloadCta, direct anchor, non-blocking listener.

- [ ] **Step 1: Add failing tests**

Extract the hero and require exactly one .btn anchor, id downloadCta, exact stable href, and visible text DOWNLOAD CCC. Require quickInstall and Tour the live demo absent from the hero while the curl installer stays below fold. Extract the handler and require sendBeacon plus keepalive fetch while forbidding preventDefault and window.location.

- [ ] **Step 2: Verify RED**

Run: python3 -m pytest -q tests/test_landing_hero_static.py

Expected: three new tests fail.

- [ ] **Step 3: Implement exact hero and listener**

Use:

~~~html
<div class="cta-row fade-target">
  <a class="btn btn-primary download-cta" id="downloadCta" href="https://github.com/amirfish1/claude-command-center/releases/latest/download/ccc.dmg">DOWNLOAD CCC</a>
</div>
<p class="download-meta fade-target">
  Apple-notarized for macOS 11+ &middot; v5.8.0 &middot;
  <a href="#install">Homebrew, Linux, and Windows</a> &middot;
  <a href="https://github.com/amirfish1/claude-command-center/blob/main/docs/telemetry.md#landing-page-download-clicks">anonymous click count</a>
</p>
~~~

Update visible v5.6 labels and What's New to v5.8.0, remove the unused copy handler, and add:

~~~javascript
const DOWNLOAD_EVENT_URL = "https://telemetry.claude-command-center.workers.dev/v1/download";
const downloadCta = document.getElementById("downloadCta");
downloadCta?.addEventListener("click", () => {
  try {
    if (navigator.sendBeacon && navigator.sendBeacon(DOWNLOAD_EVENT_URL)) return;
  } catch (_) {
    // Direct GitHub navigation remains authoritative.
  }
  fetch(DOWNLOAD_EVENT_URL, {
    method: "POST", mode: "no-cors", keepalive: true,
    referrerPolicy: "no-referrer",
  }).catch(() => {});
});
~~~

- [ ] **Step 4: Verify GREEN and commit**

~~~bash
python3 -m pytest -q tests/test_landing_hero_static.py
git diff --check -- docs/index.html tests/test_landing_hero_static.py
git commit --only docs/index.html tests/test_landing_hero_static.py \
  -m "feat(site): make DMG the primary download"
~~~

Expected: all landing tests pass.

### Task 6: Push and verify the live experience

- [ ] **Step 1: Complete local verification and push**

~~~bash
npm test --prefix infra/telemetry-worker
python3 -m pytest -q tests/
git diff --check
git push origin main
~~~

Expected: zero failures and uncommitted shared files remain local.

- [ ] **Step 2: Require remote workflows**

Require CI, install-smoke, telemetry-worker, and Pages success for the exact pushed SHA.

- [ ] **Step 3: Verify desktop/mobile and tracking resilience**

Use the repository Puppeteer harness at desktop and mobile widths. Prove #downloadCta is above fold, is the only hero CTA, has exact text/href, and Install remains below fold. Record D1 count, click once, and require +1. Block the Worker request on a second run and prove the stable DMG request still occurs.

- [ ] **Step 4: Verify a clean public DMG installation**

Download the stable asset, mount it, launch under a new temporary HOME, and prove /api/version is 5.8.0, dashboard loads, atomic clone publication holds, termination reaps the server, and forced clone failure still exposes actionable Retry recovery.

- [ ] **Step 5: Audit every approved requirement**

Re-read the spec and attach authoritative evidence to each release, CTA, privacy, counter, and install requirement. Only then mark the goal complete. Give the user the release URL, stable friend-facing download URL, and every failure encountered.

