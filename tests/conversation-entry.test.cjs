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
const bundle = execFileSync('python3', ['-c',
  'from pathlib import Path; from ccc_server.conversation_assets import ConversationScript; import sys; sys.stdout.buffer.write(ConversationScript(Path("static")).read_bytes())'], {cwd:root,maxBuffer:8*1024*1024});

test('embedded entry binds real controls before readiness and keeps drafts live', async () => {
  const injections = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/api/inject-input') {
      let body = '';
      request.on('data', chunk => {body += chunk;});
      request.on('end', () => {
        const payload = JSON.parse(body); injections.push(payload);
        response.setHeader('Content-Type','application/json');
        if (payload.text === 'Rejected draft') {
          response.writeHead(400).end(JSON.stringify({ok:false,error:'Fixture rejection'}));
        } else response.end(JSON.stringify({ok:true,queued:true,delivery_id:'fixture-'+injections.length}));
      });
    } else if (url.pathname === '/') {
      response.setHeader('Content-Type', 'text/html');
      response.end(url.search ? html : '<script>window.receipts=[];addEventListener("message",e=>receipts.push(e.data))</script><iframe src="/?ccc_popout=conversation&embed=app&conv=session-test&source=codex"></iframe>');
    } else if (url.pathname === '/static/conversation-app.js') {
      response.setHeader('Content-Type', 'application/javascript');
      response.end(bundle);
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
    await page.evaluateOnNewDocument(() => {
      // Exercise browser dictation without requesting a real microphone.
      window.SpeechRecognition = class {
        constructor() { window.dictationFixture = this; }
        start() { this.onstart?.(); }
        stop() { this.onend?.(); }
        abort() { this.onend?.(); }
      };
    });
    await page.goto(base, {waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => window.receipts.some(item => item.type === 'ccc:embed-ready'), {timeout:20000});
    const frame = page.frames().find(item => item.url().includes('embed=app'));
    const initial = await frame.evaluate(() => ({
      events:document.querySelectorAll('#conversationsView .event').length,
      disabled:document.getElementById('convInput').disabled,
      resources:performance.getEntriesByType('resource').map(item => new URL(item.name).pathname),
      dashboardOverlay:!!document.getElementById('cccLoadingOverlay'),
    }));
    assert.equal(initial.events, 2);
    assert.equal(initial.disabled, false);
    assert.equal(initial.dashboardOverlay, false);
    assert.ok(initial.resources.includes('/static/conversation-app.js'));
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
    // Real button bindings use the shared composer and preserve rejection drafts.
    for (const [text, selector, mode, forceQueue] of [
      ['Normal send','#convSendBtn','send',false],
      ['Queued send','#convSendQueueBtn','send',true],
      ['Steered send','#convSteerBtn','steer',false],
      ['Rejected draft','#convSendBtn','send',false],
    ]) {
      await frame.evaluate(text => {
        const input=document.getElementById('convInput');
        input.value=text; input.dispatchEvent(new Event('input',{bubbles:true}));
      },text);
      const completed = page.waitForResponse(response => new URL(response.url()).pathname==='/api/inject-input');
      // A busy-only button can be hidden in this idle fixture; invoke the
      // actual element listener without changing the canonical state policy.
      await frame.$eval(selector, button => button.click());
      await completed;
      await frame.waitForFunction(text => {
        const input=document.getElementById('convInput');
        return input.value===(text==='Rejected draft'?text:'') && !document.getElementById('convSendBtn').disabled;
      },{},text);
      const payload=injections.at(-1);
      assert.equal(payload.text,text);
      assert.equal(payload.session_id,'session-test');
      assert.equal(payload.mode,mode);
      assert.equal(!!payload.force_queue,forceQueue);
      assert.equal(payload.composer_form,'ccc-composer-v1');
      assert.ok(payload.idempotency_key);
    }
    assert.equal(new Set(injections.map(item=>item.idempotency_key)).size,4);
    await frame.evaluate(() => {
      const input=document.getElementById('convInput');
      input.value='Existing draft'; input.selectionStart=input.selectionEnd=input.value.length;
    });
    await frame.$eval('#convMicBtn',button=>button.click());
    assert.equal(await frame.$eval('#convMicBtn',button=>button.getAttribute('aria-pressed')),'true');
    await frame.evaluate(() => {
      const result=[{transcript:'dictated words'}]; result.isFinal=true;
      window.dictationFixture.onresult({results:[result]});
    });
    assert.equal(await frame.$eval('#convInput',input=>input.value),'Existing draft dictated words');
    await frame.$eval('#convMicBtn',button=>button.click());
    assert.equal(await frame.$eval('#convMicBtn',button=>button.getAttribute('aria-pressed')),'false');
    assert.deepEqual(errors, []);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
