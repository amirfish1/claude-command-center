const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const app = fs.readFileSync('static/app.js', 'utf8');
const slice = (from, to) => {
  const start = app.indexOf(from), end = app.indexOf(to, start);
  assert.ok(start >= 0 && end > start);
  return app.slice(start, end);
};

test('one session and at most one lane opens directly; larger groups still disclose', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewport({width:1000, height:700});
    await page.setContent('<html data-theme="light"><body><div id="convList" class="workers-view">'
      + '<div class="conv-workers-activity"><div data-role="workers-recent-work">'
      + '<div class="fq-recent-rows"></div></div></div></div></body></html>');
    await page.addStyleTag({content:fs.readFileSync('static/app.css','utf8')
      + '\nbody{display:block!important;height:auto!important;margin:24px}#convList{width:100%;max-width:720px}'});
    await page.addScriptTag({content:`
      const picked = [], tickets = [];
      const WORKERS_RECENT_DETAILED = 0;
      const items = [
        {ref:'EXAMPLE-1',title:'Repair pending request recovery'},
        {ref:'EXAMPLE-2',title:'Coordinate several worker lanes'},
        {ref:'EXAMPLE-3',title:'Unassigned report'},
        {ref:'EXAMPLE-4',title:'Another standalone session'},
      ];
      const _workersTicketSessions = new Map([
        ['EXAMPLE-1',{count:1,lanes:1,cards:[{id:'owner'},{id:'child'}],html:'<div>Owner and child</div>'}],
        ['EXAMPLE-2',{count:1,lanes:2,cards:[{id:'many'},{id:'lane-a'},{id:'lane-b'}],html:'<div>Several lanes</div>'}],
        ['EXAMPLE-4',{count:1,lanes:0,cards:[{id:'solo'}],html:'<div>Solo</div>'}],
      ]);
      const _workersTicketExpanded = new Set(['EXAMPLE-1']);
      let _workersTicketRecentSignature = '';
      const _workersFallbackTickets = [];
      const _uxqItemRef = item => item.ref;
      const _workersActivityRows = () => items.map(item => ({item,at:new Date().toISOString(),workerState:{label:'Working'},worker:'Example worker'}));
      const _workersPendingWorkers = () => [];
      const _workersPendingHtml = () => '';
      const _scheduleSidebarRender = () => {};
      const _workersLatestTicketCard = s => s?.cards[0];
      const _workersTicketContextHtml = item => '<div class="workers-ticket-context" data-workers-ticket-row="'+item.ref+'">Checking request recovery.</div>';
      const wipAgeChipHtml = () => '';
      const _uxqRelTime = () => '1m';
      const selectConversation = id => picked.push(id);
      const _uxqItemForRef = ref => items.find(item => item.ref === ref);
      const _uxqPickerPickTicket = ref => tickets.push(ref);
      const _uxqOpenItemDetail = ref => tickets.push(ref);
      const _workersRememberTicketExpansion = (ref, expanded) => expanded ? _workersTicketExpanded.add(ref) : _workersTicketExpanded.delete(ref);
    ` + slice('  function escapeHtml(s)', '  function unescapeHtml(s)')
      + slice('  function _workersSingleTicketSession(', '  function _workersActiveClaim(')
      + slice('  function _uxqRecentWorkHosts()', '  function _uxqBindWorkingStrip(')
      + '\n_uxqRenderRecentWork();'});
    assert.deepEqual(errors, []);
    const single = '[data-workers-ticket="EXAMPLE-1"]';
    assert.equal(await page.$$eval(single+' .workers-ticket-toggle', es=>es.length),0);
    assert.equal(await page.$$eval(single+' .workers-ticket-session-body', es=>es.length),0);
    await page.click(single+' .workers-ticket-title');
    await page.click(single+' .fq-recent-state');
    await page.click(single+' .workers-ticket-context');
    await page.focus(single+' .workers-ticket-title');
    await page.keyboard.press('Enter');
    await page.keyboard.press('Space');
    assert.deepEqual(await page.evaluate(()=>picked),Array(5).fill('owner'));
    await page.click(single+' .workers-ticket-link');
    assert.deepEqual(await page.evaluate(()=>tickets),['EXAMPLE-1']);
    assert.equal(await page.$eval(single+' .workers-ticket-title',el=>el.hasAttribute('aria-expanded')),false);
    const multi = '[data-workers-ticket="EXAMPLE-2"]';
    await page.click(multi+' .workers-ticket-title');
    assert.equal(await page.$eval(multi+' .workers-ticket-session-body',el=>el.hidden),false);
    await page.focus(multi+' .workers-ticket-toggle');
    await page.keyboard.press('Enter');
    assert.equal(await page.$eval(multi+' .workers-ticket-session-body',el=>el.hidden),true);
    await page.click('[data-workers-ticket="EXAMPLE-4"] .workers-ticket-title');
    assert.equal(await page.evaluate(()=>picked.at(-1)),'solo');
    await page.click('[data-workers-ticket="EXAMPLE-3"] .workers-ticket-link');
    assert.equal(await page.evaluate(()=>tickets.at(-1)),'EXAMPLE-3');
    await page.evaluate(()=>_uxqRenderRecentWork());
    assert.equal(await page.$$eval(single+' .workers-ticket-toggle',es=>es.length),0,'poll cannot restore old disclosure');
    await page.setViewport({width:390,height:760});
    await page.click(single+' .workers-ticket-title');
    assert.equal(await page.evaluate(()=>picked.at(-1)),'owner');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),true);
    if (process.env.CCC_VERIFICATION_SCREENSHOT) await page.screenshot({path:process.env.CCC_VERIFICATION_SCREENSHOT});
  } finally { await browser.close(); }
});
