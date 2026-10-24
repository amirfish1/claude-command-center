const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync('static/app.js', 'utf8');
const block = app.slice(app.indexOf('  // Ultra-fresh:'), app.indexOf('  // The server derives readiness'));
const rowActions = app.slice(app.indexOf('  // Row actions are model-authored replies'), app.indexOf('  // CCC-1218:'));

// Use the actual shipped controller and CSS in Chromium. SSE frames are
// delivered explicitly so no timing/list-poll coincidence can pass this test.
test('ultra-fresh patches only top-four message slots, rotates on scroll/filter, and rejects stale frames', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });
    await page.setContent(`<style>${fs.readFileSync('static/app.css', 'utf8')}
      #convList {height:420px;width:380px;overflow:auto;container:sidebar / inline-size}
      .conv-item {min-height:100px}
      </style><div class="sidebar"><div id="convList" class="log-list compact-rows workers-dense"></div></div>`);
    await page.addScriptTag({ content: `
      let conversationsData = Array.from({length:8}, (_, i) => ({id:'s'+i, session_id:'s'+i,
        source:'claude', state:'working', is_live:true, last_assistant_text:'Original message '+i}));
      let archiveData = [];
      let sending = false, preSend = null;
      const sessionIsOptimisticallySending = () => sending;
      const preSendPreviewFor = () => preSend;
      const _applyLiveOverlayToRow = c => c;
      const sessionReadyToClose = c => c.session_state?.completion?.valid && c.session_state?.completion?.ready_to_close;
      window.sources = [];
      window.EventSource = class {
        constructor(url) {this.url = url; this.closed = false; sources.push(this);}
        close() {this.closed = true;}
      };
      const rows = () => conversationsData.map(c => '<div class="conv-item active" data-id="'+c.id+'" data-session-id="'+c.id+'">'
        + '<div class="wa-row"><div class="wa-body"><div class="wa-line1"><span class="wa-title">Session '+c.id+'</span></div>'
        + '<div class="wa-line2"><span class="wa-preview"><span class="wa-explanation-label">Working:</span> Original message</span></div></div></div>'
        + '<div class="conv-title-row"><div class="conv-main-row has-inline-step"><span class="conv-title">Session '+c.id+'</span>'
        + '<div class="conv-outcome is-next-step is-inline is-working"><span class="conv-outcome-step" data-label="Working">Original message</span></div>'
        + '<span class="conv-row-break"></span><span class="conv-meta-col"><code>'+c.id+'</code></span><span class="conv-row-end"></span></div></div><button data-role="archive">Archive</button></div>').join('');
      document.querySelector('#convList').innerHTML = rows();
      ${app.slice(app.indexOf('  function readyToCloseArchiveHtml('), app.indexOf('  function _trackedRowStatus('))}
      ${rowActions}
      ${block}
      window.uf = { sync:_ultraFreshSync, accept:_ultraFreshAccept, feed:_ultraFreshFeed,
        spawn:_ultraFreshSpawnFeed, current:_ultraFreshCurrent, state:_ultraFresh,
        rows:() => conversationsData, setSending: (v, text) => { sending=v; preSend=text; } };
    ` });
    await page.waitForFunction(() => window.uf?.state.ids === 's0,s1,s2,s3');
    const slots = await page.evaluate(() => [...document.querySelectorAll('.conv-item')].slice(0,4).map(row => {
      const title = row.querySelector('.conv-title').getBoundingClientRect();
      const outcome = row.querySelector('.conv-outcome').getBoundingClientRect();
      const step = row.querySelector('.conv-outcome-step').getBoundingClientRect();
      return {titleBottom:title.bottom, outcomeTop:outcome.top, width:step.width, height:outcome.height};
    }));
    for (const slot of slots) {
      assert.ok(slot.width > 50, 'actual compact+dense explanation must remain visible');
      assert.ok(slot.outcomeTop >= slot.titleBottom, 'message must sit directly below title');
      assert.ok(slot.height > 0 && slot.height < 100, 'message cannot collapse into a tall empty grid cell');
    }
    const before = await page.evaluate(() => {
      window.originalRow = document.querySelector('[data-id="s0"]');
      const r = originalRow.getBoundingClientRect();
      const t = originalRow.querySelector('.conv-title').getBoundingClientRect();
      return { x: t.x, width:t.width, h:r.height, scroll:document.querySelector('#convList').scrollTop };
    });
    // CCC-143: the stream opens only after the visible set settles.
    assert.equal(await page.evaluate(() => sources.length), 0);
    await page.waitForFunction(() => sources.length === 1, { timeout: 3000 });
    await page.evaluate(() => {
      sources.at(-1).onmessage({ data:JSON.stringify({updates:[{session_id:'s0', text:'I found the message stream.', ts:'2099-01-01T00:00:00Z'}]}) });
    });
    await page.waitForFunction(() => document.querySelector('[data-id="s0"] .conv-outcome-step').textContent === 'I found the message stream.');
    const after = await page.evaluate(() => {
      const r = originalRow.getBoundingClientRect();
      const t = originalRow.querySelector('.conv-title').getBoundingClientRect();
      return {x:t.x,width:t.width,h:r.height,scroll:document.querySelector('#convList').scrollTop};
    });
    assert.deepEqual(after,before);
    assert.equal(await page.evaluate(() => originalRow === document.querySelector('[data-id="s0"]') && originalRow.classList.contains('active')),true);
    assert.equal(await page.$eval('[data-id="s0"] [data-role="archive"]', el => el.textContent), 'Archive');
    const dot = await page.$eval('[data-id="s0"] .conv-working-dot', el => ({
      display: getComputedStyle(el).display,
      animation: getComputedStyle(el, '::after').animationName,
    }));
    assert.notEqual(dot.display, 'none', 'dense rows must show the working dot');
    assert.equal(dot.animation, 'ccc-working-dot-pulse');
    // A live-state poll patches status even when hovering defers full-list
    // renders, including a Codex turn between tools.
    await page.hover('[data-id="s0"]');
    await page.evaluate(() => {
      uf.rows()[0].state = 'idle';
      uf.rows()[0].codex_state = 'idle';
      uf.sync();
    });
    assert.equal(await page.$$eval('[data-id="s0"] .conv-working-dot', els => els.length), 0);
    await page.evaluate(() => { uf.rows()[0].codex_state = 'working'; uf.sync(); });
    assert.equal(await page.$$eval('[data-id="s0"] .conv-working-dot', els => els.length), 1);
    assert.equal(await page.evaluate(() => originalRow === document.querySelector('[data-id="s0"]')), true);
    await page.evaluate(() => {
      uf.feed('s0', [{type:'assistant',ts:'2099-01-02T00:00:00Z',blocks:[{kind:'tool_use',name:'Bash',text:'tool noise'},{kind:'thinking',text:'private thinking'}]}]);
      uf.accept({session_id:'s0',text:'Old turn',ts:'2098-01-01T00:00:00Z'});
      uf.accept({session_id:'s7',text:'Out of view leak',ts:'2099-01-03T00:00:00Z'});
    });
    assert.equal(await page.$eval('[data-id="s0"] .conv-outcome-step',el => el.textContent),'I found the message stream.');
    assert.doesNotMatch(await page.$eval('#convList',el=>el.textContent), /tool noise|private thinking|Out of view leak|Old turn/);
    await page.evaluate(() => uf.accept({ session_id:'s0',
      text:'New message while hovered [Q1:example]', ts:'2099-01-01T12:00:00Z' }));
    await page.waitForFunction(() => document.querySelector('[data-id="s0"] .conv-outcome-step').textContent.includes('New message while hovered'));
    await page.evaluate(() => {
      const step = document.querySelector('[data-id="s0"] .conv-outcome-step');
      step.innerHTML = 'New message while hovered <button class="qtag-chip">[Q1:example]</button>';
      window.originalQuestionChip = step.querySelector('.qtag-chip');
      uf.sync();
    });
    assert.equal(await page.evaluate(() => originalQuestionChip === document.querySelector('[data-id="s0"] .conv-outcome-step .qtag-chip')), true,
      'status refresh must retain the existing question chip');
    assert.equal(await page.evaluate(() => originalRow === document.querySelector('[data-id="s0"]')), true);
    // An informational DID is an update, never a fabricated next action,
    // even when a malformed/missing assessment prevents a Close button.
    await page.evaluate(() => {
      uf.rows()[0].state = 'idle';
      uf.rows()[0].codex_state = 'idle';
      uf.accept({session_id:'s0', text:'The source and runtime folders are separate.', ts:'2099-01-01T13:00:00Z',
        session_state:{did:'Clarified the project and runtime paths.',next_step_user:'None.',completion:{valid:false,ready_to_close:false}}});
    });
    await page.waitForFunction(() => document.querySelector('[data-id="s0"] .wa-explanation-label').textContent === 'Update:');
    assert.equal(await page.$eval('[data-id="s0"] .conv-outcome-step',el=>el.dataset.label),'Update');
    assert.match(await page.$eval('[data-id="s0"] .wa-preview',el=>el.textContent),/Clarified the project and runtime paths\./);
    await page.evaluate(() => uf.accept({session_id:'s0', text:'Playback is fixed.', ts:'2099-01-01T14:00:00Z',
      session_state:{did:'Fixed playback.',next_step_user:'Reload the dashboard page.'}}));
    await page.waitForFunction(() => document.querySelector('[data-id="s0"] .wa-explanation-label').textContent === 'Next:');
    assert.equal(await page.$eval('[data-id="s0"] .conv-outcome-step',el=>el.textContent),'Reload the dashboard page.');
    // A final assessment arriving on an existing row must create the action,
    // without relying on a future full-list render. A new turn removes it.
    await page.evaluate(() => {
      uf.rows()[0].state = 'idle';
      uf.rows()[0].codex_state = 'idle';
      uf.accept({session_id:'s0', text:'Finished and checked.', ts:'2099-01-01T18:00:00Z',
        session_state:{did:'Finished and checked.', completion:{valid:true,ready_to_close:true}}});
    });
    await page.waitForFunction(() => document.querySelectorAll('[data-id="s0"] .conv-ready-close').length === 2);
    assert.equal(await page.$eval('[data-id="s0"] .conv-outcome .conv-ready-close',el=>el.textContent),'Close');
    assert.equal(await page.$eval('[data-id="s0"] .wa-line2 .conv-ready-close',el=>el.dataset.archived),'true');
    // A user starts a newer turn; even a subsequently delivered old reply
    // cannot resurrect its explanation or completion recommendation.
    await page.evaluate(() => {
      uf.accept({session_id:'s0',clear:true,user_ts:'2099-01-02T00:00:00Z'});
      uf.accept({session_id:'s0',text:'Stale final',ts:'2099-01-01T12:00:00Z',session_state:{completion:{valid:true,ready_to_close:true}}});
    });
    assert.doesNotMatch(await page.$eval('[data-id="s0"]',el=>el.textContent),/Stale final|I found the message stream/);
    assert.equal(await page.$$eval('[data-id="s0"] .conv-ready-close',els=>els.length),0);
    await page.evaluate(() => { document.querySelector('#convList').scrollTop = 400; });
    await page.waitForFunction(() => !uf.state.entries.has('s0') && uf.state.entries.size <= 4);
    assert.equal(await page.evaluate(() => sources[0].closed),true);
    await page.evaluate(() => sources[0].onmessage({data:JSON.stringify({updates:[{session_id:'s4',text:'Old subscription leak',ts:'2100-01-01T00:00:00Z'}]})}));
    assert.doesNotMatch(await page.$eval('#convList',el=>el.textContent),/Old subscription leak/);
    // Simulate filter/order replacing the list, as the real renderer does.
    await page.evaluate(() => {
      const list = document.querySelector('#convList');
      list.scrollTop = 0;
      list.replaceChildren(...[...list.children].reverse().slice(0,3));
    });
    await page.waitForFunction(() => uf.state.ids === 's7,s6,s5');
    await page.evaluate(() => uf.accept({session_id:'s7',text:'Fresh filtered message',ts:'2099-02-01T00:00:00Z'}));
    await page.waitForFunction(() => document.querySelector('[data-id="s7"] .wa-preview').textContent.includes('Fresh filtered message'));
    if(process.env.ULTRA_FRESH_SCREENSHOT) await page.screenshot({path:process.env.ULTRA_FRESH_SCREENSHOT});
    await page.setViewport({width:390,height:844});
    await page.evaluate(() => {document.querySelector('#convList').style.width='390px';});
    await page.waitForFunction(() => document.querySelector('[data-id="s7"] .wa-preview').textContent.includes('Fresh filtered message'));
    assert.equal(await page.$eval('[data-id="s7"] .wa-explanation-label',el=>el.textContent),'Working:');
    if(process.env.ULTRA_FRESH_PHONE_SCREENSHOT) await page.screenshot({path:process.env.ULTRA_FRESH_PHONE_SCREENSHOT});
    await page.evaluate(() => {
      uf.setSending(true, 'Original message 7');
      uf.current(uf.rows()[7]); // The real renderer invokes this on send.
      uf.accept({session_id:'s7',text:'Delayed timestamp-free old live message',live:true});
    });
    assert.equal(await page.evaluate(() => uf.state.entries.get('s7').text), '');
    assert.doesNotMatch(await page.$eval('[data-id="s7"]',el=>el.textContent),/Delayed timestamp-free old live message/);

  } finally { await browser.close(); }
});
