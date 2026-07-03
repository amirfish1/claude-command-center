// Headless screenshot of CCC for agent-side UI verification.
//
// By default this launches an EMPTY browser profile, so localStorage-backed
// state (custom objects, Evergreen Agents section, flowNodeParents /
// flowCustomObjects, view prefs) is absent — a fresh-profile probe shows
// "evergreen section gone" etc. as an artifact, not a real regression (OPS-33).
//
// To reproduce stateful UI, seed localStorage: dump it from your real browser
//   (DevTools console: `copy(JSON.stringify(localStorage))`) into a file, then
//   SNAPSHOT_LOCALSTORAGE=state.json node snapshot.js
//
// Env (all optional):
//   SNAPSHOT_URL          default: port.txt-resolved URL, else http://127.0.0.1:8090
//   SNAPSHOT_OUT          default snapshot.png
//   SNAPSHOT_LOCALSTORAGE path to a JSON file of {"key": "value", ...} (strings)
//   SNAPSHOT_CHROME       explicit Chrome/Chromium executable path (overrides auto-detect)
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const puppeteer = require('puppeteer');

// Chrome 149+ (all channels: Chrome for Testing, stable, Beta) has a headless
// renderer crash during Page.captureScreenshot — the target dies mid-capture
// with "Target closed" and no PNG is written (OPS-74). Verified on this machine:
// CfT 149.0.7827.22, Chrome stable 149 and Chrome Beta 151 all crash, while
// CfT 146 and 148 capture cleanly.
//
// So we prefer the NEWEST cached Chrome-for-Testing build with a major version
// <= LAST_GOOD_MAJOR. Only if none is cached do we fall back to an installed
// Chrome (which is likely 149+ and may crash) and finally to puppeteer's
// default. (This inverts the old OPS-4 logic, which preferred installed Chrome
// because back then it was the bundled CfT v149 that had the bug — now the
// installed browsers have auto-updated into the broken range too.)
const LAST_GOOD_MAJOR = 148;

function findGoodChromeForTesting() {
  const cacheDir = path.join(os.homedir(), '.cache', 'puppeteer', 'chrome');
  let entries;
  try { entries = fs.readdirSync(cacheDir); } catch (_) { return undefined; }
  const platform = os.platform();
  // Map a build dir (e.g. "mac_arm-148.0.7778.167") to its chrome binary.
  const binForBuild = (dir) => {
    const base = path.join(cacheDir, dir);
    if (platform === 'darwin') {
      const sub = dir.startsWith('mac_arm') ? 'chrome-mac-arm64' : 'chrome-mac-x64';
      return path.join(base, sub, 'Google Chrome for Testing.app', 'Contents', 'MacOS', 'Google Chrome for Testing');
    }
    if (platform === 'linux') return path.join(base, 'chrome-linux64', 'chrome');
    if (platform === 'win32') return path.join(base, 'chrome-win64', 'chrome.exe');
    return undefined;
  };
  const candidates = entries
    .map((dir) => {
      const m = dir.match(/-(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
      if (!m) return null;
      const [major, minor, build, patch] = m.slice(1, 5).map(Number);
      return { dir, major, key: [major, minor, build, patch] };
    })
    .filter((c) => c && c.major <= LAST_GOOD_MAJOR)
    // newest known-good first
    .sort((a, b) => {
      for (let i = 0; i < 4; i++) if (a.key[i] !== b.key[i]) return b.key[i] - a.key[i];
      return 0;
    });
  for (const c of candidates) {
    const bin = binForBuild(c.dir);
    try { fs.accessSync(bin, fs.constants.X_OK); return bin; } catch (_) {}
  }
  return undefined;
}

function findChromePath() {
  if (process.env.SNAPSHOT_CHROME) return process.env.SNAPSHOT_CHROME;
  const good = findGoodChromeForTesting();
  if (good) return good;
  console.log(`[snapshot] no cached Chrome-for-Testing <= v${LAST_GOOD_MAJOR} found; ` +
    `falling back to installed Chrome, which may hit the v149+ captureScreenshot crash (OPS-74)`);
  const installed = [
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  for (const p of installed) {
    try { fs.accessSync(p, fs.constants.X_OK); return p; } catch (_) {}
  }
  return undefined; // puppeteer default (Chrome for Testing)
}

const DEFAULT_URL = 'http://127.0.0.1:8090';

// port.txt is written whenever any non-ephemeral server starts (including a
// stray one-off launched on a custom PORT without CCC_EPHEMERAL=1) and is
// never cleaned up on exit, so it can point at a port nothing is listening on
// anymore (OPS-69). Probe before trusting it.
function urlResponds(url, timeoutMs = 500) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

// Resolve the live dashboard URL from the command-center port file, written by
// the server on startup. Falls back to the conventional local port if the
// file is missing or the port it names is dead.
async function resolveBaseUrl() {
  const portFile = path.join(os.homedir(), '.claude', 'command-center', 'port.txt');
  let fromFile;
  try {
    const raw = fs.readFileSync(portFile, 'utf8').trim();
    if (/^https?:\/\//.test(raw)) fromFile = raw;          // full URL form
    else if (/^\d+$/.test(raw)) fromFile = `http://127.0.0.1:${raw}`; // bare port form
  } catch (_) {
    // fall through to default
  }
  if (fromFile) {
    if (await urlResponds(fromFile)) return fromFile;
    console.log(`[snapshot] port.txt points at ${fromFile}, which isn't responding; falling back to ${DEFAULT_URL}`);
  }
  return DEFAULT_URL;
}

// One navigate + capture attempt in a fresh browser. Throws on any failure
// (including the intermittent renderer crash) so the caller can retry.
async function captureOnce(url, out, lsPath, chromePath) {
  // --no-sandbox is required on hosts where AppArmor restricts unprivileged
  // user namespaces (Ubuntu 23.10+); safe here since we only load localhost.
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });

    if (lsPath) {
      const entries = JSON.parse(fs.readFileSync(lsPath, 'utf8'));
      // Set before any page script runs, on the right origin, then the app reads
      // the seeded state on first load.
      await page.evaluateOnNewDocument((data) => {
        try {
          for (const [k, v] of Object.entries(data)) {
            localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v));
          }
        } catch (e) { /* localStorage unavailable before navigation — ignore */ }
      }, entries);
      console.log(`[snapshot] seeded ${Object.keys(entries).length} localStorage keys from ${lsPath}`);
    }

    // CCC is a live-polling dashboard (health/attention/wt-workers polls fire
    // every few seconds forever) — 'networkidle2' waits for network to go
    // quiet and never resolves, hanging until Puppeteer's nav timeout (OPS-71).
    //
    // 'load' doesn't work either: the dashboard opens ~6 long-lived polling
    // requests (attention?scope=live, healthcheck, live-activity,
    // group-chats/active, …) that saturate HTTP/1.1's 6-connections-per-host
    // limit, so a deferred <script> like /static/coo-button.js can never
    // acquire a connection slot and its request stays pending forever. The
    // window 'load' event waits on all subresources, so it never fires and
    // goto() hangs the full 30s nav timeout (OPS-74).
    //
    // Wait for 'domcontentloaded' (fires reliably at ~1s, after the HTML is
    // parsed and inline/deferred app scripts have executed enough to render),
    // then give in-flight fetches a bounded window to settle before capturing.
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.waitForNetworkIdle({ idleTime: 750, timeout: 4000 }).catch(() => {});
    await page.screenshot({ path: out });
  } finally {
    await browser.close().catch(() => {});
  }
}

(async () => {
  const url = process.env.SNAPSHOT_URL || await resolveBaseUrl();
  const out = process.env.SNAPSHOT_OUT || 'snapshot.png';
  const lsPath = process.env.SNAPSHOT_LOCALSTORAGE || '';

  const chromePath = findChromePath();
  if (chromePath) console.log(`[snapshot] using chrome: ${path.basename(chromePath)}`);

  // Chrome's headless captureScreenshot intermittently crashes the target
  // ("Protocol error (Page.captureScreenshot): Target closed") — even on the
  // known-good builds, ~1 in 3 attempts on a busy live dashboard (OPS-74). A
  // relaunched browser almost always succeeds, so retry a few times.
  const MAX_ATTEMPTS = 4;
  let lastErr;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      await captureOnce(url, out, lsPath, chromePath);
      console.log(`[snapshot] wrote ${out} (${url})${attempt > 1 ? ` on attempt ${attempt}` : ''}`);
      return;
    } catch (err) {
      lastErr = err;
      const closed = /Target closed|Session closed|Target crashed/i.test(err && err.message || '');
      console.log(`[snapshot] attempt ${attempt}/${MAX_ATTEMPTS} failed: ${err && err.message}`);
      if (!closed && attempt === 1) break; // non-crash error (bad URL etc.) — don't churn
    }
  }
  console.error(`[snapshot] giving up after ${MAX_ATTEMPTS} attempts`);
  throw lastErr;
})();
