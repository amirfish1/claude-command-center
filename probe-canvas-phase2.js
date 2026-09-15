// Phase-2 verification: component library (search, categories), template
// cards with previews, template apply + shimmer mid-animation, edge-draw
// validity, at 1280x800 and 1920x1080.
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
    try { fs.unlinkSync(LAYOUT); } catch (_) {}
    await page.setViewport({ width: vp.w, height: vp.h });
    await page.goto('http://127.0.0.1:8090/canvas.html', { waitUntil: 'networkidle2', timeout: 30000 });
    await sleep(2200);

    // Design mode: library with all categories.
    await page.click('#pcModeDesign');
    await sleep(500);
    const lib = await page.evaluate(() => {
      const cats = Array.from(document.querySelectorAll('.pc-lib-cat')).map((c) => c.textContent);
      const items = document.querySelectorAll('.pc-lib-item').length;
      const ghost = !document.getElementById('pcGhostHint').hidden;
      return { categories: cats, items, ghostHint: ghost };
    });
    console.log(vp.tag, 'library:', JSON.stringify(lib));
    await page.screenshot({ path: 'out/canvas-polish/phase2/' + vp.tag + '-library.png' });

    // Search: type "post" — fuzzy filter.
    await page.type('#pcLibSearch', 'post');
    await sleep(400);
    const search = await page.evaluate(() => ({
      items: document.querySelectorAll('.pc-lib-item').length,
      none: !!document.querySelector('.pc-lib-none'),
    }));
    console.log(vp.tag, 'search "post":', JSON.stringify(search));
    await page.screenshot({ path: 'out/canvas-polish/phase2/' + vp.tag + '-search.png' });
    await page.evaluate(() => {
      const s = document.getElementById('pcLibSearch');
      s.value = '';
      s.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await sleep(300);

    // Template card hover (first card).
    await page.hover('.pc-template');
    await sleep(300);
    await page.screenshot({ path: 'out/canvas-polish/phase2/' + vp.tag + '-template-hover.png',
      clip: { x: 60, y: 330, width: 300, height: 460 } });

    // Apply the self-healing-ops template (has the intentional loop).
    await page.evaluate(() => {
      const cards = Array.from(document.querySelectorAll('.pc-template'));
      const t = cards.find((c) => c.textContent.includes('Feature factory')) || cards[0];
      t.click();
    });
    // Shimmer mid-animation: capture quickly after apply.
    await sleep(650);
    await page.screenshot({ path: 'out/canvas-polish/phase2/' + vp.tag + '-shimmer.png' });
    await sleep(2600);
    const state = await page.evaluate(() => ({
      designed: document.querySelectorAll('.pc-node.is-designed').length,
      userEdges: document.querySelectorAll('.pc-edge-group.is-user').length,
      sparks: document.querySelectorAll('.pc-edge-spark').length,
      ghostHint: !document.getElementById('pcGhostHint').hidden,
    }));
    console.log(vp.tag, 'after template:', JSON.stringify(state));
    await page.screenshot({ path: 'out/canvas-polish/phase2/' + vp.tag + '-template-applied.png' });

    // Inspector on a designed node: pattern + edge contract sections.
    await page.evaluate(() => {
      const n = document.querySelector('.pc-node.is-designed');
      n.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, clientX: 700, clientY: 400 }));
      document.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0, clientX: 700, clientY: 400 }));
    });
    await sleep(500);
    const insp = await page.evaluate(() => {
      const body = document.getElementById('pcInspectorBody').textContent;
      return { pattern: body.includes('Pattern'),
               anchor: !!document.querySelector('.pc-insp-anchor') };
    });
    console.log(vp.tag, 'inspector:', JSON.stringify(insp));
    await page.screenshot({ path: 'out/canvas-polish/phase2/' + vp.tag + '-inspector.png' });
    await page.click('#pcModeRuntime');
  }
  console.log('pageerrors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
