// The app host boots the canonical dashboard only when its controls are used.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const puppeteer = require('../require-puppeteer');
const {findChromePath} = require('../puppeteer-browser-config');
const root = path.resolve(__dirname, '..');
const apps = [{id:'sessions',label:'Sessions',nav:'/'},
  {id:'queues',label:'Queues',nav:'/q2.html'}, {id:'shell-test',label:'Test app',nav:'/test-app'}];
const html = execFileSync('python3', ['-c',
  'from pathlib import Path; from ccc_server.app_embed import application_shell_page; print(application_shell_page(Path("static/index.html").read_text(), {"id":"shell-test","label":"Test app","nav":"/test-app"}))'],
  {cwd:root,encoding:'utf8'});

test('desktop app keeps its document and draft through on-demand Settings and navigation', async () => {
  let hydrationAttempt = 0;
  const server = http.createServer((request,response) => {
    const url = new URL(request.url,'http://localhost');
    if (url.pathname === '/') {
      response.setHeader('Content-Type','text/html');
      if (url.searchParams.has('ccc_app')) {response.end(html);return;}
      hydrationAttempt++;
      if (hydrationAttempt === 1) {response.writeHead(500).end('Unavailable');return;}
      const source = fs.readFileSync(path.join(root,'static/index.html'),'utf8');
      response.end(hydrationAttempt === 2 ? source.replace('id="kanbanLayout"','id="changedLayout"') : source);
    } else if (url.pathname === '/test-app') {
      response.setHeader('Content-Type','text/html');
      response.end('<!doctype html><h1>Test app</h1><textarea id="draft"></textarea><script>parent.postMessage({type:"ccc:app-fully-ready"},location.origin)</script>');
    } else if (url.pathname.startsWith('/static/')) {
      const file = path.join(root,url.pathname);
      if (!file.startsWith(root+'/static/') || !fs.existsSync(file)) {response.writeHead(404).end();return;}
      response.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':'text/css');
      response.end(fs.readFileSync(file));
    } else {
      response.setHeader('Content-Type','application/json');
      const result = url.pathname === '/api/apps' ? {apps} : url.pathname === '/api/features' ? {} : [];
      response.end(JSON.stringify(result));
    }
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
    const page = await browser.newPage();
    await page.setViewport({width:1440,height:1000});
    const requests = [], errors = [];
    page.on('request',request => requests.push(new URL(request.url()).pathname));
    page.on('pageerror',error => errors.push(error.message));
    const base = 'http://127.0.0.1:'+server.address().port;
    await page.setRequestInterception(true);
    page.on('request',request => new URL(request.url()).origin === base ? request.continue()
      : request.respond({status:200,contentType:'application/json',body:'[]'}));
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-tour-done','1');
      localStorage.setItem('ccc-engines-first-run-done','1');
      localStorage.setItem('ccc-ui-mode','advanced');
    });
    await page.goto(base+'/?ccc_app=shell-test',{waitUntil:'load'});
    await page.waitForSelector('.ccc-side-nav [data-app-id="shell-test"]',{visible:true});
    assert.equal(requests.includes('/static/app.js'),false,'no hidden dashboard download after app readiness');
    assert.equal(await page.evaluate(() => !!document.getElementById('settingsModal')),false,
      'unused settings markup is absent from the initial app shell');
    assert.equal(await page.evaluate(() => !!document.getElementById('cccDashboardMain')),false,
      'the parent conversation workspace is absent while the app is open');
    await page.evaluate(() => {
      const frame = document.getElementById('cccMobileAppFrame');
      window.keptDocument = frame.contentDocument;
      frame.contentDocument.getElementById('draft').value='Unsent app draft';
      const rect = frame.getBoundingClientRect();
      if (document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2)!==frame) throw Error('app covered');
    });
    if (process.env.CCC_APP_SHELL_SCREENSHOTS) await page.screenshot({path:process.env.CCC_APP_SHELL_SCREENSHOTS+'/shell-app.png'});
    await page.click('.ccc-account');
    await page.waitForSelector('[data-rail-settings]',{visible:true});
    // Both a failed read and an incomplete template leave all panel markers
    // and the original app draft intact, with no controller replay.
    for (let attempt=0; attempt<2; attempt++) {
      const dialog = new Promise(resolve => page.once('dialog',async dialog => {
        assert.match(dialog.message(),/Please try again/); await dialog.dismiss(); resolve();
      }));
      if (attempt === 0) await page.click('[data-rail-settings]');
      else await page.click('.ccc-side-nav [data-app-id="sessions"]');
      await dialog;
      await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'));
      assert.equal(await page.evaluate(() => !document.getElementById('settingsModal')
        && !document.getElementById('kanbanLayout')
        && !document.getElementById('cccDashboardMain')
        && document.querySelectorAll('template[data-ccc-dashboard-panel]').length===3
        && document.getElementById('cccMobileAppFrame').contentDocument===window.keptDocument
        && window.keptDocument.getElementById('draft').value==='Unsent app draft'),true);
      assert.equal(requests.includes('/static/app.js'),false);
    }
    await page.click('.ccc-account');
    await page.waitForSelector('[data-rail-settings]',{visible:true});
    await page.click('[data-rail-settings]');
    await page.waitForFunction(() => {
      const modal = document.getElementById('settingsModal');
      return modal && !modal.hidden;
    },{timeout:20000});
    assert.equal(requests.filter(name => name==='/static/app.js').length,1);
    assert.equal(hydrationAttempt,3);
    assert.equal(await page.evaluate(() => {
      const frame = document.getElementById('cccMobileAppFrame');
      return frame.contentDocument === window.keptDocument
        && frame.contentDocument.getElementById('draft').value==='Unsent app draft'
        && !!document.querySelector('#cccDashboardMain #convInput');
    }),true);
    if (process.env.CCC_APP_SHELL_SCREENSHOTS) await page.screenshot({path:process.env.CCC_APP_SHELL_SCREENSHOTS+'/shell-settings.png'});
    await page.click('#settingsModalClose');
    await page.click('.ccc-side-nav [data-app-id="sessions"]');
    await page.waitForFunction(() => document.getElementById('cccMobileAppFrame').hidden);
    await page.click('.ccc-side-nav [data-app-id="shell-test"]');
    await page.waitForFunction(() => !document.getElementById('cccMobileAppFrame').hidden);
    assert.equal(await page.evaluate(() => document.getElementById('cccMobileAppFrame').contentDocument===window.keptDocument),true);
    await page.setViewport({width:390,height:844});
    await page.waitForFunction(() => document.body.classList.contains('has-mobile-bottom-nav'));
    const geometry = await page.evaluate(() => ({frame:document.getElementById('cccMobileAppFrame').getBoundingClientRect().bottom,
      nav:document.getElementById('mobileBottomNav').getBoundingClientRect().top}));
    assert.ok(geometry.frame<=geometry.nav+1,JSON.stringify(geometry));
    if (process.env.CCC_APP_SHELL_SCREENSHOTS) await page.screenshot({path:process.env.CCC_APP_SHELL_SCREENSHOTS+'/shell-phone.png'});
    assert.deepEqual(errors,[]);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
