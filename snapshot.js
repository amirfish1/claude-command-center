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
//   SNAPSHOT_URL          default: port.txt-resolved URL, else http://127.0.0.1:8091
//   SNAPSHOT_OUT          default snapshot.png
//   SNAPSHOT_LOCALSTORAGE path to a JSON file of {"key": "value", ...} (strings)
//   SNAPSHOT_CHROME       explicit Chrome/Chromium executable path (overrides auto-detect)
const fs = require('fs');
const os = require('os');
const path = require('path');
const puppeteer = require('puppeteer');

// Chrome for Testing v149 on macOS ARM has a renderer crash during
// Page.captureScreenshot ("Target closed"). Prefer the user's installed
// Chrome Beta or Chrome, which don't have this bug. Falls back to puppeteer's
// bundled Chrome for Testing when neither is present (OPS-4).
function findChromePath() {
  if (process.env.SNAPSHOT_CHROME) return process.env.SNAPSHOT_CHROME;
  const macs = [
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  for (const p of macs) {
    try { fs.accessSync(p, fs.constants.X_OK); return p; } catch (_) {}
  }
  return undefined; // puppeteer default (Chrome for Testing)
}

// Resolve the live dashboard URL from the command-center port file, written by
// the server on startup. Falls back to the conventional local port.
function resolveBaseUrl() {
  const portFile = path.join(os.homedir(), '.claude', 'command-center', 'port.txt');
  try {
    const raw = fs.readFileSync(portFile, 'utf8').trim();
    if (/^https?:\/\//.test(raw)) return raw;          // full URL form
    if (/^\d+$/.test(raw)) return `http://127.0.0.1:${raw}`; // bare port form
  } catch (_) {
    // fall through to default
  }
  return 'http://127.0.0.1:8091';
}

(async () => {
  const url = process.env.SNAPSHOT_URL || resolveBaseUrl();
  const out = process.env.SNAPSHOT_OUT || 'snapshot.png';
  const lsPath = process.env.SNAPSHOT_LOCALSTORAGE || '';

  // --no-sandbox is required on hosts where AppArmor restricts unprivileged
  // user namespaces (Ubuntu 23.10+); safe here since we only load localhost.
  const chromePath = findChromePath();
  if (chromePath) console.log(`[snapshot] using chrome: ${path.basename(chromePath)}`);
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    args: ['--no-sandbox'],
  });
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

  await page.goto(url, { waitUntil: 'networkidle2' });
  await page.screenshot({ path: out });
  await browser.close();
  console.log(`[snapshot] wrote ${out} (${url})`);
})();
