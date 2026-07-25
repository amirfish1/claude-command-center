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
//   SNAPSHOT_TIMEOUT_MS    dashboard/capture deadline after browser launch (default 60000)
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const puppeteer = require('./require-puppeteer.js');
const { findChromePath } = require('./puppeteer-browser-config.js');

const DEFAULT_URL = 'http://127.0.0.1:8090';
// The dashboard can take longer than 20 seconds to settle while local workers
// are active. Keep the default long enough for a real primary, while callers
// that need a stricter failure budget can still override it.
const DEFAULT_TIMEOUT_MS = 60_000;

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

(async () => {
  const url = process.env.SNAPSHOT_URL || await resolveBaseUrl();
  const out = process.env.SNAPSHOT_OUT || 'snapshot.png';
  const lsPath = process.env.SNAPSHOT_LOCALSTORAGE || '';
  const timeoutMs = Number(process.env.SNAPSHOT_TIMEOUT_MS) || DEFAULT_TIMEOUT_MS;

  // --no-sandbox is required on hosts where AppArmor restricts unprivileged
  // user namespaces (Ubuntu 23.10+); safe here since we only load localhost.
  const chromePath = findChromePath();
  if (chromePath) console.log(`[snapshot] using chrome: ${path.basename(chromePath)}`);
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    args: ['--no-sandbox'],
  });
  let deadline;
  try {
    const timedOut = new Promise((_, reject) => {
      deadline = setTimeout(() => reject(new Error(`snapshot exceeded ${timeoutMs}ms`)), timeoutMs);
    });
    await Promise.race([timedOut, (async () => {
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
  // quiet and never resolves, hanging until Puppeteer's nav timeout. This
  // is worse with a seeded, data-heavy profile: more in-flight requests at
  // once, so the connection count rarely dips to the networkidle2
  // threshold before the next poll tick refills it (OPS-71). Wait for
  // 'load' (DOM + initial script execution) instead, then give in-flight
  // fetches a bounded window to settle before capturing.
      await page.goto(url, { waitUntil: 'load', timeout: timeoutMs });
      await page.waitForNetworkIdle({ idleTime: 750, timeout: 4000 }).catch(() => {});
      await page.screenshot({ path: out });
      console.log(`[snapshot] wrote ${out} (${url})`);
    })()]);
  } finally {
    clearTimeout(deadline);
    if (browser) await browser.close();
  }
})();
