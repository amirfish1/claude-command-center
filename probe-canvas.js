// Probe the pipeline canvas: console errors, node/edge counts, positions.
const puppeteer = require('./require-puppeteer.js');
const { findChromePath } = require('./puppeteer-browser-config.js');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'shell',
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800 });
  const logs = [];
  page.on('console', (m) => logs.push(m.type() + ': ' + m.text()));
  page.on('pageerror', (e) => logs.push('PAGEERROR: ' + e.message));
  await page.goto('http://127.0.0.1:8090/canvas.html', { waitUntil: 'networkidle2', timeout: 30000 });
  await new Promise((r) => setTimeout(r, 2500));
  const info = await page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll('.pc-node')).map((n) => ({
      id: n.dataset.id, arch: n.dataset.archetype, health: n.dataset.health,
      left: n.style.left, top: n.style.top,
      text: (n.textContent || '').slice(0, 60),
    }));
    return {
      nodeCount: nodes.length,
      nodes: nodes.slice(0, 40),
      edges: document.querySelectorAll('.pc-edge-group').length,
      worldTransform: document.getElementById('pcWorld').style.transform,
      libraryHidden: document.getElementById('pcLibrary').hidden,
      emptyHidden: document.getElementById('pcEmpty').hidden,
    };
  });
  console.log(JSON.stringify(info, null, 1));
  console.log('--- console ---');
  logs.forEach((l) => console.log(l));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
