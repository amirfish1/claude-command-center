const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const toast = app.slice(app.indexOf('  function showOpToast('), app.indexOf('  // Prepare a report locally;', app.indexOf('  function showOpToast('))).replace('function showOpToast(', 'function renderOpToast(');
const editor = app.slice(app.indexOf('  async function followRowReportReceipt('), app.indexOf('  function sessionQuickAction('));

test('real report HTTP receipt closes desktop and phone editor while filing is blocked', async () => {
  const backend = spawn('python3', ['-u', 'tests/fixtures/row_report_server.py'], { cwd: require('node:path').resolve(__dirname, '..'), stdio: ['ignore', 'pipe', 'pipe'] });
  let diagnostics = '';
  backend.stderr.on('data', chunk => { diagnostics += chunk; });
  let browser;
  try {
    const lines = readline.createInterface({ input: backend.stdout });
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture startup timeout: ' + diagnostics)), 20000);
      lines.on('line', line => { try { const v = JSON.parse(line); if (v.port) { clearTimeout(timer); resolve(v.port); } } catch (_) {} });
      backend.on('exit', code => { clearTimeout(timer); reject(new Error('Fixture exited ' + code + ': ' + diagnostics)); });
    });
    const origin = 'http://127.0.0.1:' + port;
    browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
    const pages = [];
    for (const width of [1280, 390]) {
      const page = await browser.newPage(); pages.push(page);
      await page.setViewport({ width, height: 844 });
      await page.goto(origin + '/fixture');
      await page.setContent(`<style>:root{--surface:#161b22;--surface-2:#21262d;--text:#e6edf3;--text-muted:#9da7b3;--bg:#0d1117;--border:#484f58;--accent:#80bfff;--green:#3fb950;--red:#ff7b72} body{background:var(--bg);color:var(--text)} ${css}</style><div class="conv-item" data-session-id="test-${width}" style="position:fixed;right:12px;top:30px"><span>Working: Working…</span><button data-role="report-decision">Report</button></div>`);
      await page.addScriptTag({ content: `
        const _rowDecisionReportSending=new Set(); let _rowDecisionReportEditor=null;
        window.toasts=[]; window.posts=[];
        const nativeFetch=window.fetch.bind(window);
        window.fetch=(url, options)=>{if(options?.method==='POST') posts.push(JSON.parse(options.body));return nativeFetch(url,options)};
        const _ultraFreshRow=sid=>({session_id:sid});
        const rowDecisionReportSnapshot=(row,base)=>({session_id:base.session_id,visible_row:{text:row.textContent},rect:{x:1,y:2,width:3,height:4}});
        const captureRowDecisionScreenshot=async()=>null;
        const debugModeEnabled=()=>false;
        const bugErrorAction=()=>document.createElement('button');
        ${toast}
        const showOpToast=(...args)=>{toasts.push(args);return renderOpToast(...args)};
        ${editor}
      ` });
      await page.click('[data-role="report-decision"]');
      await page.type('#rowReportNote', 'Why is the status explanation missing?');
      const started = Date.now();
      await page.click('.conv-report-editor [type="submit"]');
      await page.waitForFunction(() => !document.querySelector('.conv-report-editor'), { timeout: 2000 });
      console.log(JSON.stringify({viewport:width,receiptLatencyMs:Date.now()-started}));
      assert.ok(Date.now() - started < 2000, 'editor closes without waiting for queue filing');
      assert.match(await page.evaluate(() => toasts.at(-1)[0]), /Report saved/);
      const original = await page.evaluate(() => posts[0]);
      const retry = await page.evaluate(async payload => (await fetch('/api/decisions-fix/report', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).json(), original);
      assert.equal(retry.submission_id, original.submission_id);
      assert.ok(retry.saved && !retry.ref, 'receipt does not claim an unfiled ticket');
      await page.screenshot({ path: '/tmp/row-report-receipt-' + width + '.png' });
    }
    let stats = await (await fetch(origin + '/fixture/stats')).json();
    assert.equal(stats.calls, 1, 'one worker blocked; retry did not start another enqueue');
    assert.equal(stats.tickets, 0);
    await fetch(origin + '/fixture/release');
    for (const page of pages) {
      await page.waitForFunction(() => toasts.some(t => /reported as DECISIONS-FIX-TEST/.test(t[0])), { timeout: 10000 });
      await page.screenshot({ path: '/tmp/row-report-receipt-complete-' + (await page.viewport()).width + '.png' });
    }
    stats = await (await fetch(origin + '/fixture/stats')).json();
    assert.equal(stats.calls, 2);
    assert.equal(stats.tickets, 2, 'exactly one ticket per distinct browser report');
  } finally {
    if (browser) await browser.close();
    backend.kill('SIGTERM');
  }
});
