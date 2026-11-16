// Exercise the real lightweight composition, markup, controls and shared state.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const puppeteer = require('../require-puppeteer');
const {findChromePath} = require('../puppeteer-browser-config');
const root = path.resolve(__dirname, '..');
const html = execFileSync('python3', ['-c',
  'from pathlib import Path; from ccc_server.app_embed import conversation_page; print(conversation_page(Path("static/index.html").read_text()))'], {cwd: root, encoding: 'utf8'});

test('embedded entry binds real controls before readiness and keeps drafts live', async () => {
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/') {
      response.setHeader('Content-Type', 'text/html');
      response.end(url.search ? html : '<script>window.receipts=[];addEventListener("message",e=>receipts.push(e.data))</script><iframe src="/?ccc_popout=conversation&embed=app&conv=session-test"></iframe>');
    } else if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, url.pathname);
      if (!file.startsWith(root + '/static/') || !fs.existsSync(file)) { response.writeHead(404).end(); return; }
      response.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : 'text/css');
      response.end(fs.readFileSync(file));
    } else {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify(url.pathname === '/api/conversations/session-test'
        ? {events:[{type:'user',text:'A test request',line:1},{type:'assistant',text:'A test response',line:2}],last_line:2}
        : {}));
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // This fixture never reaches an external service or performs a send.
    await page.setRequestInterception(true);
    const base = 'http://127.0.0.1:' + server.address().port;
    page.on('request', request => new URL(request.url()).origin === base
      ? request.continue() : request.respond({status:200,body:'{}'}));
    await page.goto(base, {waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => window.receipts.some(item => item.type === 'ccc:embed-ready'), {timeout:20000});
    const frame = page.frames().find(item => item.url().includes('embed=app'));
    const initial = await frame.evaluate(() => ({
      events:document.querySelectorAll('#conversationsView .event').length,
      disabled:document.getElementById('convInput').disabled,
      resources:performance.getEntriesByType('resource').map(item => new URL(item.name).pathname),
    }));
    assert.equal(initial.events, 2);
    assert.equal(initial.disabled, false);
    assert.ok(!initial.resources.includes('/static/app.js'));
    await frame.click('#convActionsBtn');
    assert.equal(await frame.$eval('#convActionsBtn', element => element.getAttribute('aria-expanded')), 'true');
    await frame.click('#convActionsBtn');
    await frame.type('#convInput', 'A draft stays with this conversation');
    const draft = await frame.evaluate(() => {
      const c=window.cccConversation;
      c.rememberComposerDraftForPane(c.activePaneId());
      document.getElementById('convInput').value='';
      c.restoreComposerDraftForPane(c.activePaneId(), c.currentConversation);
      return document.getElementById('convInput').value;
    });
    assert.equal(draft, 'A draft stays with this conversation');
    await frame.evaluate(() => document.dispatchEvent(new KeyboardEvent('keydown', {key:'f',ctrlKey:true,bubbles:true})));
    await frame.waitForSelector('#chatFindModal', {visible:true});
    await frame.click('#chatFindClose');
    assert.deepEqual(errors, []);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
