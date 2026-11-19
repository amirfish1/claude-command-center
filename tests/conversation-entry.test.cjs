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
const bundles = JSON.parse(execFileSync('python3', ['-c',
  'from pathlib import Path; from ccc_server.conversation_assets import BUNDLES, ConversationScript; import json; print(json.dumps({name:ConversationScript(Path("static"),name).read_bytes().decode() for name in BUNDLES}))'], {cwd:root,maxBuffer:8*1024*1024}));

test('embedded entry binds real controls before readiness and keeps drafts live', async () => {
  const injections = [];
  let entrySource = 'codex';
  let failConversationRead = false;
  let detailsRequests = 0;
  let failDetailsRead = false;
  let issueRequests = 0;
  let failIssuesRead = false;
  const issueActions = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/static/conversation-issues.js') {
      issueRequests++;
      if (failIssuesRead) {response.writeHead(503).end();return;}
    }
    if (url.pathname === '/static/conversation-details.js') {
      detailsRequests++;
      if (failDetailsRead) {response.writeHead(503).end();return;}
    }
    if (url.pathname === '/api/issues/42/details') {
      response.setHeader('Content-Type','application/json');
      response.end(JSON.stringify({ok:true,issue:{title:'Shared issue controls',body:'Canonical issue body',state:'OPEN',comments:[]}}));
    } else if (/^\/api\/issues\/42\/(close|reply|mark-in-progress)$/.test(url.pathname)
        || url.pathname === '/api/sessions/spawn') {
      let body = '';
      request.on('data',chunk=>{body+=chunk;});
      request.on('end',()=>{
        issueActions.push({url:url.pathname,body:JSON.parse(body)});
        response.setHeader('Content-Type','application/json');
        response.end(JSON.stringify({ok:true,spawn_id:'fixture-spawn',pid:123,session_id:'fixture-new-session'}));
      });
    } else if (url.pathname === '/api/ux-fixes/item') {
      response.setHeader('Content-Type','application/json');
      response.end(JSON.stringify({ok:true,item:{ref:'TEST-1',id:'fixture-ticket',title:'A complete ticket',status:'open',description:'Full fixture ticket details'}}));
    } else if (url.pathname === '/api/conversations/list') {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({conversations:[{id:'fixture-archive',display_name:'Archive fixture'}]}));
    } else if (url.pathname === '/api/inject-input') {
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
      response.end(url.search ? html : '<script>window.receipts=[];addEventListener("message",e=>receipts.push(e.data))</script><iframe src="/?ccc_popout=conversation&embed=app&conv=session-test&source=' + entrySource + '"></iframe>');
    } else if (bundles[url.pathname.slice('/static/'.length)]) {
      response.setHeader('Content-Type', url.pathname.endsWith('.css') ? 'text/css' : 'application/javascript');
      response.end(bundles[url.pathname.slice('/static/'.length)]);
    } else if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, url.pathname);
      if (!file.startsWith(root + '/static/') || !fs.existsSync(file)) { response.writeHead(404).end(); return; }
      response.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : 'text/css');
      response.end(fs.readFileSync(file));
    } else {
      if (failConversationRead && url.pathname.startsWith('/api/conversations/session-test')) {
        response.writeHead(500,{'Content-Type':'application/json'});
        response.end(JSON.stringify({error:'Fixture transcript unavailable'})); return;
      }
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify(url.pathname === '/api/conversations/session-test'
        ? {events:[{type:'user',text:'A test request',line:1},{type:'assistant',text:'A test response',blocks:[{kind:'text',text:'A test response'}],line:2,tokens_in:50000,tokens_cached:0}],last_line:2}
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
    // An uncached Claude turn must not need dashboard-only global setup.
    entrySource = 'interactive';
    const coldContext = await browser.createBrowserContext();
    try {
      const cold = await coldContext.newPage();
      await cold.setRequestInterception(true);
      cold.on('request', request => new URL(request.url()).origin === base
        ? request.continue() : request.respond({status:200,body:'{}'}));
      await cold.goto(base, {waitUntil:'domcontentloaded'});
      await cold.waitForFunction(() => window.receipts.some(item => item.type === 'ccc:embed-ready'), {timeout:20000});
      const agent = cold.frames().find(item => item.url().includes('embed=app'));
      assert.equal(await agent.evaluate(() => window._ccCacheMissLog[0].tokens_uncached), 50000);
      assert.equal(await agent.$eval('#convInput', input => input.disabled), false);
      assert.match(await agent.$eval('#conversationsView', view => view.textContent), /A test response/);
      assert.equal(detailsRequests, 0, 'archive code does not load before readiness');
      const archive = await agent.evaluate(() => Promise.all([
        cccConversation.loadArchiveAll({window:'1d'}),
        cccConversation.loadArchiveAll({window:'1d'}),
      ]));
      assert.deepEqual(archive.map(rows => rows.map(row => row.id)), [['fixture-archive'], ['fixture-archive']]);
      assert.equal(detailsRequests, 1, 'concurrent archive reads share the details download');
      assert.equal(await agent.$eval('#convInput', input => input.disabled), false);
    } finally { await coldContext.close(); entrySource = 'codex'; detailsRequests = 0; }
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
    assert.ok(initial.resources.includes('/static/conversation-extensions.js'));
    assert.ok(initial.resources.includes('/static/conversation-deferred.js'));
    assert.equal(detailsRequests,0,'details are not an initial dependency');
    assert.equal(issueRequests,0,'issue controls are not an initial dependency');
    assert.ok(!initial.resources.includes('/static/browser-voice.js'));
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
    await frame.$eval('#convInput',input=>{input.value='Keep this live draft';input.dispatchEvent(new Event('input',{bubbles:true}));});
    // This is the actual delegated transcript ticket control. Two clicks share
    // one module fetch and the modal uses the same current conversation state.
    failDetailsRead = true;
    await frame.evaluate(()=>{
      const ticket=document.createElement('button');
      ticket.className='conv-pane-ticket';ticket.dataset.workerTicket='TEST-1';
      document.getElementById('conversationsView').appendChild(ticket);
      ticket.click();
    });
    await frame.waitForFunction(()=>document.body.textContent.includes('Could not load conversation details. Try again.'));
    assert.equal(await frame.evaluate(()=>!!document.getElementById('uxqItemModal')),false);
    failDetailsRead = false;
    await frame.$eval('.conv-pane-ticket[data-worker-ticket="TEST-1"]',ticket=>{ticket.click();ticket.click();});
    await frame.waitForFunction(()=>document.getElementById('uxqItemModal')?.textContent.includes('A complete ticket'));
    assert.equal(detailsRequests,2);
    assert.equal(await frame.$eval('#convInput',input=>input.value),'Keep this live draft');
    assert.equal(await frame.evaluate(()=>cccConversation.currentSession.id),'session-test');
    await frame.evaluate(()=>cccConversation.loadConversationList());
    assert.equal(await frame.$eval('#convInput',input=>input.value),'Keep this live draft');
    assert.equal(await frame.evaluate(()=>cccConversation.conversationsLoaded),true);
    // The issue picker and composer retain their canonical actions, including
    // retry after a failed module fetch. All writes go only to this fixture.
    await frame.evaluate(()=>{
      cccConversation.conversationsData.push({id:'backlog-issue-42',source:'backlog',issue_number:42,
        display_name:'#42: Shared issue controls',repo_path:'/tmp/fixture-repo'});
    });
    failIssuesRead = true;
    await frame.evaluate(()=>cccConversation.selectConversation('backlog-issue-42'));
    await frame.waitForFunction(()=>document.body.textContent.includes('Could not load issue controls. Try again.'));
    failIssuesRead = false;
    await frame.evaluate(()=>cccConversation.selectConversation('backlog-issue-42'));
    await frame.waitForFunction(()=>document.getElementById('conversationsView').textContent.includes('Canonical issue body'));
    assert.equal(issueRequests,2);
    for (const [action,text,endpoint] of [
      ['not_planned','A reason','/api/issues/42/close'],
      ['needs_attention','A question','/api/issues/42/reply'],
      ['spawn','Implementation instructions','/api/sessions/spawn'],
    ]) {
      await frame.evaluate((action,text)=>{
        document.getElementById('convInputIssueAction').value=action;
        const input=document.getElementById('convInput');
        input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));
      },action,text);
      const completed=page.waitForResponse(response=>new URL(response.url()).pathname===endpoint);
      await frame.$eval('#convSendBtn',button=>button.click());
      await completed;
      await frame.waitForFunction(()=>document.getElementById('convInput').value===''&&!document.getElementById('convSendBtn').disabled);
      assert.equal(issueActions.find(item=>item.url===endpoint).body.repo_path,'/tmp/fixture-repo');
    }
    assert.equal(issueActions.find(item=>item.url.endsWith('/close')).body.reason,'not planned');
    assert.equal(issueActions.find(item=>item.url.endsWith('/reply')).body.action,'needs_attention');
    assert.match(issueActions.find(item=>item.url==='/api/sessions/spawn').body.prompt,/Implementation instructions/);
    // Clipped results expand both in visible context and after opening history.
    for (const [lines, folded] of [[2, false], [40, false], [40, true]]) {
      await frame.evaluate(({lines, folded}) => {
        const c = cccConversation;
        const view = c.getConvViewForPane(c.activePaneId());
        document.body.classList.remove('conv-compact');
        view.closest('.conv-pane').classList.remove('is-codex-session', 'is-kimi-session', 'is-webui-session');
        const group = document.createElement('div');
        group.id = 'fixture-output-group';
        group.className = 'tool-call-group';
        group.innerHTML = '<div class="tool-call-group-header">Tool output</div>'
          + '<div class="tool-call-group-body"><div class="event tool-call"'
          + ' data-tool-use-id="output-fixture" data-tool-name="Bash"></div></div>'
          + (folded ? '' : '<span class="acp-needs-approval">Tool context</span>');
        view.appendChild(group);
        c._currentToolGroup = group;
        c._currentToolCount = 1;
        c.renderConversationEvents([{type:'tool_result', tool_use_id:'output-fixture',
          text:Array.from({length:lines},(_,i)=>'Fixture error line '+i).join('\n'),
          is_error:true, line:1000+lines}], c.activePaneId(), {initialLoad:true});
        if (folded) {
          if (!group.classList.contains('activity-run-source')) throw new Error('History did not fold');
          group.previousElementSibling.querySelector('.activity-run-toggle').click();
        }
      }, {lines, folded});
      await frame.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const result = await frame.evaluate(() => {
        const group = document.getElementById('fixture-output-group');
        const output = group.querySelector('.tool-result-output');
        const toggle = group.querySelector('.tool-result-expand-toggle');
        if (!output) throw new Error('Fixture output did not render');
        const before = output.clientHeight;
        toggle?.click();
        const after = output.clientHeight;
        group.remove();
        cccConversation._currentToolGroup = null;
        cccConversation._currentToolCount = 0;
        return {toggle:!!toggle, before, after};
      });
      assert.equal(result.toggle, lines === 40, `${lines}-line output, folded=${folded}`);
      if (lines === 40) assert.ok(result.after > result.before, 'Show full result expands clipped output');
    }
    await frame.evaluate(async()=>{
      const c=cccConversation;
      c.conversationsData.push({id:'backlog-todo',source:'backlog',display_name:'A todo',first_message:'Todo body'},
        {id:'pr-fixture',source:'github_pr',display_name:'A pull request',pr_state:'MERGED'});
      c.currentConversation='backlog-todo';await c.fetchConversationEvents();
    });
    assert.match(await frame.$eval('#conversationsView',view=>view.textContent),/Todo body/);
    await frame.evaluate(async()=>{cccConversation.currentConversation='pr-fixture';await cccConversation.fetchConversationEvents();});
    assert.match(await frame.$eval('#conversationsView',view=>view.textContent),/A pull request/);
    assert.deepEqual(errors, []);
    // A new browser has no saved tail: a failed read must stay an error,
    // never send the parent the usable-conversation readiness message.
    failConversationRead = true;
    const context = await browser.createBrowserContext();
    try {
      const failed = await context.newPage();
      await failed.setRequestInterception(true);
      failed.on('request',request => new URL(request.url()).origin===base
        ? request.continue() : request.respond({status:200,body:'{}'}));
      await failed.goto(base,{waitUntil:'domcontentloaded'});
      const agent = failed.frames().find(item=>item.url().includes('embed=app'));
      await agent.waitForFunction(()=>document.getElementById('conversationsView').textContent.includes('Failed to load conversation'));
      // Cover both the paint callback and the hidden-frame timer fallback.
      await agent.evaluate(()=>new Promise(resolve=>setTimeout(resolve,300)));
      assert.equal(await failed.evaluate(()=>window.receipts.some(item=>item.type==='ccc:embed-ready')),false);
    } finally { await context.close(); }
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
