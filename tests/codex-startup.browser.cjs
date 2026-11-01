// Verify startup diagnostics with deterministic timing responses; no paid run.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
(async () => {
  const base = process.env.CCC_TEST_URL;
  if (!base) throw new Error('Set CCC_TEST_URL to a verification dashboard');
  const browser = await puppeteer.launch({executablePath:findChromePath(), headless:true,args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:1200,height:850});
    let marks = {thread_start_done:120,turn_accepted:230,spawn_response_sent:350,codex_first_reasoning:12500};
    await page.setRequestInterception(true);
    page.on('request', req => {
      if (new URL(req.url()).pathname.endsWith('/app.js')) {
        let source = fs.readFileSync(require.resolve('../static/app.js'),'utf8');
        source = source.replace('  function renderPendingCodexStats(card, $view) {','  window.startupTest = {renderPendingCodexStats, renderSpawnStatsPanel, loadCodexLog, getConvView};\n  function renderPendingCodexStats(card, $view) {');
        return req.respond({status:200,contentType:'application/javascript',body:source});
      }
      if (req.url().includes('/api/sessions/spawned/424242/log')) return req.respond({status:200,contentType:'application/json',body:JSON.stringify({ok:true,running:true,pid:424242,text:''})});
      if (req.url().includes('/api/session/spawn-timeline?')) return req.respond({status:200,contentType:'application/json',body:JSON.stringify({ok:true,timeline:{engine:'codex',marks}})});
      return req.continue();
    });
    await page.goto(base,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>!!window.startupTest,{timeout:60000});
    await page.evaluate(()=>{
      const host=document.createElement('div'); host.id='startup-test';
      host.style='position:fixed;inset:20px;z-index:2147483647;background:var(--bg);padding:24px;overflow:auto';
      document.body.appendChild(host);
      window.startupTest.renderPendingCodexStats({spawn_requested_at:Date.now()-8000},host);
    });
    assert.match(await page.$eval('#startup-test',el=>el.textContent),/8\.0s.*elapsed/);
    await page.evaluate(()=>window.startupTest.renderSpawnStatsPanel('fixture',document.querySelector('#startup-test'),{open:true}));
    assert.match(await page.$eval('#startup-test',el=>el.textContent),/Waiting for first visible output/);
    assert.match(await page.$eval('#startup-test',el=>el.textContent),/Codex began streaming reasoning12\.5s/);
    assert.equal(await page.$eval('#startup-test details',el=>el.open),true);
    marks.client_first_visible_output=14200;
    await page.evaluate(()=>window.startupTest.renderSpawnStatsPanel('fixture',document.querySelector('#startup-test')));
    assert.match(await page.$eval('#startup-test summary',el=>el.textContent),/14\.2s.*first visible output/);
    await page.evaluate(()=>window.startupTest.loadCodexLog({source:'codex',spawn_pid:424242,expected_session_id:'fixture'}));
    assert.equal(await page.evaluate(()=>!!window.startupTest.getConvView().querySelector('.spawn-stats[open]')),true);
    await page.screenshot({path:process.env.CCC_TEST_SCREENSHOT||'/tmp/codex-startup-desktop.png'});
    await page.setViewport({width:390,height:844});
    assert.equal(await page.$eval('#startup-test',el=>el.scrollWidth<=el.clientWidth),true);
    await page.screenshot({path:'/tmp/codex-startup-mobile.png'});
    console.log('PASS: pre-response elapsed time, live milestones, waiting and visible-output states, expanded panel, mobile width');
  } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
