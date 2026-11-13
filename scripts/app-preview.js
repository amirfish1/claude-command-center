// Renders preview.png for user apps (light theme) so the Apps home can show
// pictures instead of live iframes.
//   node scripts/app-preview.js <baseUrl> <appsDir> <appId> [<appId> ...]
// Writes <appsDir>/<appId>/preview.png from <baseUrl>/view/<appId>#thumb.
const fs = require('fs');
const path = require('path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

async function main() {
  const [base, appsDir, ...ids] = process.argv.slice(2);
  if (!base || !appsDir || !ids.length) {
    console.error('usage: app-preview.js <baseUrl> <appsDir> <appId>...');
    process.exit(2);
  }
  const exe = process.env.SNAPSHOT_CHROME || findChromePath();
  const browser = await puppeteer.launch({
    headless: true,
    ...(exe ? { executablePath: exe } : {}),
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  let failed = 0;
  try {
    for (const id of ids) {
      if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(id)) { failed++; continue; }
      const page = await browser.newPage();
      try {
        await page.setViewport({ width: 960, height: 600, deviceScaleFactor: 1 });
        await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'light' }]);
        // App pages hand off to the dashboard when opened top-level, so show
        // the page the way the dashboard does: inside a full-size iframe.
        await page.goto(`${base}/static/app-kit.js`, { waitUntil: 'load', timeout: 30000 });
        await page.evaluate(async (src) => {
          try { localStorage.setItem('ccc-theme', 'light'); } catch (_) {}
          document.body.innerHTML = '';
          document.body.style.margin = '0';
          const f = document.createElement('iframe');
          f.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;border:0;background:#fff';
          document.body.appendChild(f);
          await new Promise((res) => { f.onload = res; f.src = src; setTimeout(res, 20000); });
        }, `/view/${id}#thumb`);
        await new Promise((r) => setTimeout(r, 1500));
        const out = path.join(appsDir, id, 'preview.png');
        const tmp = out + '.tmp.png';
        await page.screenshot({ path: tmp, type: 'png' });
        fs.renameSync(tmp, out);
        console.log('ok ' + id);
      } catch (err) {
        failed++;
        console.error('fail ' + id + ': ' + err.message);
      } finally {
        await page.close().catch(() => {});
      }
    }
  } finally {
    await browser.close();
  }
  process.exit(failed ? 1 : 0);
}
main().catch((e) => { console.error(e.message); process.exit(1); });
