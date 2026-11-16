// A session-backed ticket can precede the queue snapshot. Its live preview
// and full refresh must agree, or the context line repeatedly collapses.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/conversation-text.js'), 'utf8') + '\n' + fs.readFileSync(process.env.CCC_TEST_APP_SOURCE || 'static/app.js', 'utf8');
const slice = (from, to) => {
  const start = app.indexOf(from), end = app.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return app.slice(start, end);
};

test('live and full worker refreshes retain session-backed ticket previews', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.setViewport({width:1176,height:600});
    await page.setContent('<html data-theme="light"><body><div class="sidebar"><div id="convList" class="workers-view">'
      + '<div class="conv-workers-activity"><div data-role="workers-recent-work"><div class="fq-recent-rows"></div>'
      + '</div></div></div></div></body></html>');
    await page.addStyleTag({content:fs.readFileSync('static/app.css','utf8')
      + '\nbody{display:block!important;height:auto!important;margin:0}.sidebar{width:min(1070px,100vw);height:auto}'});
    await page.addScriptTag({content:`
      const fallback = {ref:'EXAMPLE-1',title:'Implement reconciliation rules',status:'in_progress'};
      const _workersFallbackTickets = [fallback];
      const _uxqItemsCache = {items:[]};
      const card = {id:'session-a',session_id:'session-a',bucket:'working',outcome:{kind:'working',text:'Checking the event against the current source.'}};
      const _workersTicketSessions = new Map([['EXAMPLE-1',{count:1,lanes:0,cards:[card],html:''}]]);
      const _workersTicketExpanded = new Set();
      let _workersTicketRecentSignature = '';
      const WORKERS_RECENT_DETAILED = 0;
      const _uxqItemRef = item => item.ref;
      const _uxqItemForRef = ref => _uxqItemsCache.items.find(item => item.ref === ref);
      const _workersActivityRows = () => (_uxqItemsCache.items.length ? _uxqItemsCache.items : _workersFallbackTickets).map(item => ({item,at:'2026-01-01T12:00:00Z',workerState:_workersTicketState(item),worker:'Example worker'}));
      const _uxqHealthCache = {wt_workers:[]};
      const _scheduleSidebarRender = () => {};
      const _ultraFreshCurrent = c => c, _applyLiveOverlayToRow = c => c;
      const workerStateBucket = c => c.bucket;
      const rowOutcomeView = c => c.outcome;
      const _uxqTicketAttention = () => null;
      const wipAgeChipHtml = () => '<span class="conv-working-age">25m 8s</span>';
      const _uxqRelTime = () => '1m';
    ` + slice('  function escapeHtml(s)', '  function unescapeHtml(s)')
      + slice('  function _workersMeaningfulUpdate(', '  // Compact history rows')
      + slice('  function _uxqRecentWorkHosts()', '  function _uxqBindWorkingStrip(')
      + '\n_uxqRenderRecentWork();'});
    assert.deepEqual(errors,[]);
    const measure = () => page.evaluate(() => {
      const entry=document.querySelector('[data-workers-ticket="EXAMPLE-1"]');
      return {height:entry.getBoundingClientRect().height,
        text:entry.querySelector('.workers-ticket-update')?.textContent || '',
        context:!!entry.querySelector('.workers-ticket-context')};
    });
    const first=await measure();
    assert.equal(first.context,true);
    assert.match(first.text,/current source/);
    for (let i=0;i<4;i++) {
      await page.evaluate(()=>_workersPaintTicketUpdate('session-a'));
      assert.deepEqual(await measure(),first,'live patch must preserve preview and row height before queue snapshot arrives');
      await page.evaluate(()=>_uxqRenderRecentWork());
      assert.deepEqual(await measure(),first,'periodic refresh must agree with live patch');
    }
    await page.setViewport({width:390,height:760});
    await page.evaluate(()=>_uxqRenderRecentWork());
    const phone=await measure();
    await page.evaluate(()=>_workersPaintTicketUpdate('session-a'));
    assert.deepEqual(await measure(),phone,'phone refresh also preserves the context line');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),true);
    await page.setViewport({width:1176,height:600});
    await page.evaluate(()=>{card.outcome.text='Verifying the focused regression.';_workersPaintTicketUpdate('session-a');});
    assert.equal((await measure()).text,'Verifying the focused regression.');
    assert.equal((await measure()).height,first.height);
    // New canonical state wins over the recovered copy, including removal of
    // a working preview when the ticket resolves.
    await page.evaluate(()=>{
      _uxqItemsCache.items=[{...fallback,status:'closed',closed_at:'2026-01-01T13:00:00Z'}];
      card.bucket='done';card.outcome={kind:'update',text:'The work is complete.'};
      _workersPaintTicketUpdate('session-a');
    });
    assert.equal((await measure()).context,false);
    assert.equal(await page.$eval('.fq-recent-state',e=>e.textContent),'Resolved');
    await page.evaluate(()=>_uxqRenderRecentWork());
    assert.equal((await measure()).context,false);
    assert.deepEqual(errors,[]);
    if (process.env.CCC_VERIFICATION_SCREENSHOT) {
      await page.evaluate(()=>{
        _uxqItemsCache.items=[];card.bucket='working';card.outcome={kind:'working',text:'Verifying the focused regression.'};_uxqRenderRecentWork();_workersPaintTicketUpdate('session-a');
      });
      await page.screenshot({path:process.env.CCC_VERIFICATION_SCREENSHOT});
    }
  } finally {await browser.close();}
});
