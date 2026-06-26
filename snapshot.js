const puppeteer = require('puppeteer');
const fs = require('fs');
const os = require('os');
const path = require('path');

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
  // --no-sandbox is required on hosts where AppArmor restricts unprivileged
  // user namespaces (Ubuntu 23.10+); safe here since we only load localhost.
  const browser = await puppeteer.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800 });
  await page.goto(resolveBaseUrl(), { waitUntil: 'networkidle2' });
  await page.screenshot({ path: 'snapshot.png' });
  await browser.close();
})();
