// Review-fix verification: 1280x800 + 1920x1080, runtime first view,
// fit view, design mode (library scroll/fade), edge routing.
const puppeteer = require('./require-puppeteer.js');
const { findChromePath } = require('./puppeteer-browser-config.js');
const fs = require('fs');
const os = require('os');
const LAYOUT = os.homedir() + '/.claude/command-center/canvas-layout.json';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    headless: 'shell',
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));

  for (const vp of [{ w: 1280, h: 800, tag: '1280' }, { w: 1920, h: 1080, tag: '1920' }]) {
    // Force the true first-run path: no saved layout before load.
    try { fs.unlinkSync(LAYOUT); } catch (_) {}
    await page.setViewport({ width: vp.w, height: vp.h });
    await page.goto('http://127.0.0.1:8090/canvas.html', { waitUntil: 'networkidle2', timeout: 30000 });
    await sleep(2400);
    const info = await page.evaluate(() => ({
      zoom: document.getElementById('pcZoomLevel').textContent,
      nodes: document.querySelectorAll('.pc-node').length,
      edges: document.querySelectorAll('.pc-edge-group').length,
    }));
    console.log(vp.tag, 'first view:', JSON.stringify(info));
    await page.screenshot({ path: 'out/canvas-polish/review-fixes/' + vp.tag + '-first.png' });

    // Fit view (wall mode on demand).
    await page.click('#pcZoomFit');
    await sleep(500);
    await page.screenshot({ path: 'out/canvas-polish/review-fixes/' + vp.tag + '-fit.png' });

    // Design mode: library panel + fade cue.
    await page.click('#pcModeDesign');
    await sleep(500);
    const lib = await page.evaluate(() => {
      const sc = document.getElementById('pcLibraryScroll');
      const panel = document.getElementById('pcLibrary');
      const items = Array.from(panel.querySelectorAll('.pc-lib-item, .pc-template'));
      const last = items[items.length - 1];
      const pr = panel.getBoundingClientRect();
      const lr = last.getBoundingClientRect();
      return {
        hasMore: panel.classList.contains('has-more'),
        scrollable: sc.scrollHeight > sc.clientHeight,
        lastItemVisibleBeforeScroll: lr.bottom <= pr.bottom + 1,
        panelBottom: Math.round(pr.bottom), stageH: document.getElementById('pcStage').clientHeight,
      };
    });
    console.log(vp.tag, 'library:', JSON.stringify(lib));
    await page.screenshot({ path: 'out/canvas-polish/review-fixes/' + vp.tag + '-design.png' });

    // Scroll library to bottom: fade must clear, last item fully visible.
    await page.evaluate(() => {
      const sc = document.getElementById('pcLibraryScroll');
      sc.scrollTop = sc.scrollHeight;
    });
    await sleep(300);
    const lib2 = await page.evaluate(() => {
      const panel = document.getElementById('pcLibrary');
      const items = Array.from(panel.querySelectorAll('.pc-template'));
      const last = items[items.length - 1].getBoundingClientRect();
      const pr = panel.getBoundingClientRect();
      return { hasMore: panel.classList.contains('has-more'),
               lastVisible: last.bottom <= pr.bottom + 1 };
    });
    console.log(vp.tag, 'library after scroll:', JSON.stringify(lib2));
    await page.screenshot({ path: 'out/canvas-polish/review-fixes/' + vp.tag + '-design-scrolled.png' });
    await page.click('#pcModeRuntime');
  }
  console.log('pageerrors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
