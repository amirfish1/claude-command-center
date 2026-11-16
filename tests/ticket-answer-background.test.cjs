const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const source = require('./legacy-dashboard-source.cjs');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const helpers = source.slice(source.indexOf('  const _uxqAnswerPending ='), source.indexOf('  function _uxqOpenItemModal(item)'));
const toast = source.slice(source.indexOf('  function showOpToast('), source.indexOf('  // Prepare a report locally;', source.indexOf('  function showOpToast(')));
const fixture = `<meta name="viewport" content="width=device-width, initial-scale=1"><style>
:root{--surface:#f6f8fa;--text:#24292f;--text-muted:#59636e;--border:#8c959f;--accent:#0969da;--green:#116329;--yellow:#7d4e00;--button-text:#fff}
${css}
body{margin:20px;background:var(--surface);color:var(--text);font-family:system-ui}
</style><main><h1>Queue</h1><p>Other work stays available while your answer sends.</p><button id="other-work">Open another ticket</button></main>
<script>
const _workersAnswersPending=new Set();const _uxqItemsCache={ts:1},_uxqHealthCache={ts:1};
const isImeKey=e=>e.isComposing;const debugModeEnabled=()=>false;
const escapeHtml=s=>{const el=document.createElement('span');el.textContent=s;return el.innerHTML;};
const _uxqItemRef=item=>item.ref;const _uxqReplaceCachedItem=item=>window.savedItem=item;
const _renderQueuePanel=()=>{if(window.failRefresh)throw Error('render failed');window.refreshed=true;};
window.requests=[];
window.fetch=(url,options)=>{requests.push({url,...options});return new Promise((resolve,reject)=>{window.answerResolve=resolve;window.answerReject=reject;});};
${toast}
${helpers}
window.cccOpenTicket=()=>openTicket();
window.openTicket=()=>{
 document.getElementById('uxqItemModal')?.remove();
 const modal=document.createElement('div');modal.id='uxqItemModal';modal.className='settings-modal-overlay uxq-td-overlay open';
 modal.innerHTML='<div class="uxq-td-card"><div class="uxq-td-header"><strong>TEST-1</strong></div><div class="uxq-td-body"><div class="uxq-td-main"><h2>Review the proposed fix</h2><section class="uxq-td-sec uxq-td-answer-sec"><div class="uxq-td-sec-label">Your decision</div><div class="uxq-answer-row"><input class="uxq-answer-input" aria-label="Answer this blocked ticket"><button class="ann-btn ann-primary uxq-answer-send">Send</button></div><div class="uxq-td-answer-hint">Sends your decision to the worker.</div></section></div></div></div>';
 document.body.append(modal);_uxqWireTicketAnswer(modal,{ref:'TEST-1'},()=>modal.remove());
};
openTicket();
</script>`;

async function result(page, data, ok = true) {
  await page.evaluate(({data, ok}) => answerResolve({ok, status: ok ? 200 : 400, json:async()=>data}), {data, ok});
  await page.waitForFunction(() => !_uxqAnswerPending.size);
}

test('ticket answers release the dialog before the response, with recoverable outcomes', async t => {
  const server = http.createServer((_req, res) => { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(fixture); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  const url = `http://127.0.0.1:${server.address().port}`;
  const open = async (width = 1280) => {
    const page = await browser.newPage();
    await page.setViewport({width, height:900});
    await page.goto(url, {waitUntil:'load'});
    return page;
  };
  try {
    await t.test('slow response: immediate close, visible pending status, one POST across keyboard and reopen', async () => {
      const page = await open();
      try {
        const before = await page.evaluate(() => {
          document.querySelector('input').value='Use the reviewed fix.';
          const input=document.querySelector('input'), button=document.querySelector('.uxq-answer-send');
          const start=performance.now();button.click();input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'}));
          return {ms:performance.now()-start,closed:!document.getElementById('uxqItemModal'),notice:document.querySelector('.op-toast').innerText,posts:requests.length};
        });
        assert.ok(before.ms < 200, `click-to-close ${before.ms}ms`);
        assert.equal(before.closed, true);assert.equal(before.posts,1);assert.match(before.notice,/Sending answer in the background/);
        assert.doesNotMatch(before.notice,/Answer recorded/);
        await page.click('#other-work');
        if (process.env.TICKET_ANSWER_SCREENSHOT) await page.screenshot({path:process.env.TICKET_ANSWER_SCREENSHOT});
        await page.evaluate(() => openTicket());
        assert.equal(await page.$eval('input', el=>el.value),'Use the reviewed fix.');
        assert.equal(await page.$eval('.uxq-answer-send', el=>el.disabled),true);
        await page.evaluate(() => document.querySelector('input').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'})));
        assert.equal(await page.evaluate(()=>requests.length),1);
        await result(page,{ok:true,item:{ref:'TEST-1',needs_input:false}});
        assert.equal(await page.$eval('.uxq-answer-send', el=>el.textContent),'Answered');
        assert.equal(await page.evaluate(()=>sessionStorage.getItem(UXQ_ANSWER_DRAFT_PREFIX+'TEST-1')),null);
        assert.equal(await page.evaluate(()=>savedItem.needs_input),false);
        assert.equal(await page.evaluate(()=>requests[0].cccRetryHandled),true);
        console.log(`Click-to-close: ${before.ms.toFixed(1)}ms with response held pending`);
      } finally { await page.close(); }
    });
    await t.test('server refusal: exact draft restored for review on a phone', async () => {
      const page=await open(390);
      try {
        await page.type('input','Keep the existing behavior.');await page.click('.uxq-answer-send');
        await result(page,{ok:false,error:'Ticket changed'},false);
        await page.click('.op-toast button');
        assert.equal(await page.$eval('input',el=>el.value),'Keep the existing behavior.');
        assert.equal(await page.$eval('.uxq-answer-send',el=>el.disabled),false);
        assert.match(await page.$eval('.uxq-td-answer-hint',el=>el.innerText),/Check Activity before retrying/);
        assert.equal(await page.evaluate(()=>requests.length),1);
        if (process.env.TICKET_ANSWER_PHONE_SCREENSHOT) await page.screenshot({path:process.env.TICKET_ANSWER_PHONE_SCREENSHOT});
      } finally { await page.close(); }
    });
    await t.test('lost response and reload: retain text and warn without replaying the decision', async () => {
      const page=await open();
      try {
        await page.type('input','Please keep the saved draft.');await page.click('.uxq-answer-send');
        await page.evaluate(()=>answerReject(new TypeError('Failed to fetch')));
        await page.waitForFunction(()=>!_uxqAnswerPending.size);
        assert.match(await page.$eval('.op-toast',el=>el.innerText),/not confirmed/);
        await page.reload({waitUntil:'load'});
        await page.waitForSelector('.op-toast');
        assert.equal(await page.$eval('input',el=>el.value),'Please keep the saved draft.');
        assert.equal(await page.evaluate(()=>requests.length),0,'reload must not resend an uncertain answer');
        assert.match(await page.$eval('.op-toast',el=>el.innerText),/earlier answer is unconfirmed/);
      } finally { await page.close(); }
    });
    await t.test('recorded answer with failed worker delivery is distinguished from a failed save', async () => {
      const page=await open();
      try {
        await page.type('input','Use the proposed scope.');await page.click('.uxq-answer-send');
        await result(page,{ok:true,delivery:{ok:false,error:'worker unavailable'}});
        assert.match(await page.$eval('.op-toast',el=>el.innerText),/Answer recorded; worker notification could not be confirmed/);
        assert.equal(await page.evaluate(()=>_uxqAnswerDraft('TEST-1')),'');
      } finally { await page.close(); }
    });
    await t.test('a queue repaint failure cannot mislabel a confirmed answer', async () => {
      const page=await open();
      try {
        await page.evaluate(()=>window.failRefresh=true);
        await page.type('input','Proceed with the fix.');await page.click('.uxq-answer-send');
        await result(page,{ok:true});
        assert.match(await page.$eval('.op-toast',el=>el.innerText),/Answer recorded/);
        assert.doesNotMatch(await page.$eval('.op-toast',el=>el.innerText),/not confirmed/);
      } finally { await page.close(); }
    });
  } finally { await browser.close();await new Promise(resolve=>server.close(resolve)); }
});
