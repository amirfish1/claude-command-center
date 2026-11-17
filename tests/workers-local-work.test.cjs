const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(require.resolve('../static/conversation-text.js'), 'utf8') + '\n' + require('./legacy-dashboard-source.cjs');
const source = app.slice(app.indexOf('  function _uxqWithMachine('), app.indexOf('  // Compact history rows', app.indexOf('  function _uxqWithMachine(')));
function fixture(items, extra = {}) {
  const ctx = { _uxqItemsCache: { items }, _uxqItemRef: item => item.ref,
    _uxqTicketAttention: () => null, _uxqHealthCache: {worker_session_map:{'local-worker':'local-session'}}, ...extra };
  vm.createContext(ctx); vm.runInContext(source, ctx);
  return ctx;
}
const item = (ref, extra = {}) => ({ref, project:'EXAMPLE', status:'closed', closed_at:'2026-01-01T10:00:00Z', ...extra});
const refs = ctx => Array.from(ctx._workersActivityRows(), row => row.item.ref);

test('synced foreign tickets never appear merely because they share a queue or need the owner', () => {
  const items = [
    item('EXAMPLE-1', {claimed_by:'local-worker'}),
    item('EXAMPLE-2', {claimed_by:'foreign-worker',claimed_machine:'another-host',needs_input:true,status:'in_progress',closed_at:null,claimed_at:'2026-01-02T10:00:00Z'}),
    item('EXAMPLE-3', {claimed_by:'EXAMPLE queue worker',needs_input:true}),
    item('EXAMPLE-4', {claimed_machine:'this-host'}),
  ];
  const ctx = fixture(items);
  assert.deepEqual(refs(ctx), ['EXAMPLE-1']);
  assert.equal(ctx._uxqRecentWorkItems(items, Infinity).length, 4, 'global queue history stays complete');
  assert.equal(ctx._uxqItemsCache.items.length, 4, 'owner decisions and queue data are not removed');
});

test('current and historical explicit local actors retain work across reassignment and queues', () => {
  const cases = [
    {claimed_by:'local-worker'}, {claimed_session_id:'local-session'},
    {closed_by:'local-worker'}, {closed_session_id:'local-session'},
    {history:[{event:'claim',by:{worker:'local-worker'}}]},
    {timeline:[{event:'close',session_id:'local-session'}]},
    {history:[{event:'block',by:'local-worker'}]},
    {history:[{event:'progress',worker:'local-worker'}]},
    {progress_notes:[{by:{session_id:'local-session'}}]},
  ];
  cases.forEach((extra, i) => {
    assert.deepEqual(refs(fixture([item('OTHER-' + (i + 1), {claimed_by:'replacement-worker',...extra})])), ['OTHER-' + (i + 1)]);
  });
  const excluded = [item('EXAMPLE-9', {history:[{event:'comment',by:{worker:'local-worker'}}]}),
    item('EXAMPLE-10', {history:[{event:'edit',by:{worker:'local-worker'}}]})];
  assert.deepEqual(refs(fixture(excluded)), [], 'comments and edits do not prove participation in work');
});

test('live records, pruned worker ledgers and known sessions all identify genuine local work', () => {
  for (const extra of [
    {_uxqHealthCache:{wt_workers:[{worker_id:'local-worker'}]}},
    {_uxqHealthCache:{past_workers:[{worker_id:'local-worker'}]}},
    {_uxqHealthCache:{worker_session_ids:['local-worker']}},
    {_uxqHealthCache:{worker_session_map:{'local-worker':'local-session'}}},
    {_uxqHealthCache:{}, conversationsData:[{id:'local-worker'}]},
  ]) assert.deepEqual(refs(fixture([item('EXAMPLE-1',{claimed_by:'local-worker'})], extra)), ['EXAMPLE-1']);
  assert.deepEqual(refs(fixture([item('EXAMPLE-1',{claimed_by:'local-worker'})], {_uxqHealthCache:{}})), [], 'do not show foreign history while local identities load');
});

test('local assigned session trees survive without claim metadata; manual queues stay excluded', () => {
  const ctx = fixture([item('EXAMPLE-1'),item('EXAMPLE-2'),item('IDEAS-1'),item('SMALL-BUGS-1')]);
  vm.runInContext("_workersAssignedTicketRefs = new Set(['EXAMPLE-1','IDEAS-1','SMALL-BUGS-1'])", ctx);
  assert.deepEqual(refs(ctx), ['EXAMPLE-1']);
});

test('foreign work cannot crowd out the recent ten genuine local tickets', () => {
  const items = Array.from({length:14}, (_,i) => item('LOCAL-'+i,{claimed_by:'local-worker',closed_at:new Date(1700000000000+i*1000).toISOString()}))
    .concat(Array.from({length:20}, (_,i) => item('FOREIGN-'+i,{claimed_by:'foreign-worker',closed_at:'2026-01-03T10:00:00Z'})));
  const ctx = fixture(items);
  vm.runInContext("_workersAssignedTicketRefs = new Set(['LOCAL-0'])", ctx);
  assert.deepEqual(refs(ctx), [...Array.from({length:10},(_,i)=>'LOCAL-'+(13-i)), 'LOCAL-0']);
});

test('desktop and phone render only locally attributed work and retain ticket/session links', async () => {
  const puppeteer = require('../require-puppeteer.js');
  const { findChromePath } = require('../puppeteer-browser-config.js');
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  const slice = (from,to) => app.slice(app.indexOf(from),app.indexOf(to,app.indexOf(from)));
  try {
    const page = await browser.newPage();
    await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><div id="convList" class="workers-view"><div data-role="workers-recent-work"><div data-role="workers-awaiting" hidden></div><div class="fq-recent-rows"></div></div></div>');
    await page.addStyleTag({content:fs.readFileSync('static/app.css','utf8')+'\nbody{display:block!important;margin:16px}#convList{width:100%;max-width:850px}'});
    await page.addScriptTag({content:`
      const _uxqItemsCache = {items:[
        {ref:'EXAMPLE-1',title:'Local fix',status:'closed',claimed_by:'local-worker',claimed_session_id:'local-session',closed_at:'2026-01-01T10:00:00Z'},
        {ref:'OTHER-2',title:'Local work in another queue',status:'closed',history:[{event:'close',at:'2026-01-01T11:00:00Z',by:{worker:'local-worker'}}]},
        {ref:'FOREIGN-3',title:'Remote owner decision',status:'in_progress',needs_input:true,claimed_at:'2026-01-02T10:00:00Z',claimed_by:'foreign-worker'}
      ]};
      const _uxqHealthCache = {worker_session_map:{'local-worker':'local-session'},
        wt_workers:[{worker_id:'starting-worker',queue:'BUILD',alive:true,session_id:'starting-session',idle_seconds:2}]};
      const conversationsData = [{id:'local-session'}];
      const _uxqItemRef = item => item.ref;
      const _uxqItemForRef = ref => _uxqItemsCache.items.find(item=>item.ref===ref);
      const _uxqTicketAttention = () => null;
      const _scheduleSidebarRender = () => {};
      const wipAgeChipHtml = () => '';
      const _uxqRelTime = () => '1m';
      const WORKERS_RECENT_DETAILED = 0;
      window.picked = []; window.opened = [];
      const selectConversation = id => picked.push(id);
      const _uxqOpenItemDetail = ref => opened.push(ref);
      const _uxqPickerPickTicket = ref => opened.push(ref);
    `+slice('  function workerSectionHeaderHtml(', '  // WORKER_ROW_FACTS_END')+slice('  function escapeHtml(s)','  function unescapeHtml(s)')+source
      +slice('  function _uxqRecentWorkHosts()','  function _uxqBindWorkingStrip(')+'\n_uxqRenderRecentWork();'});
    for (const width of [1100,390]) {
      await page.setViewport({width,height:760});
      const shown = await page.$$eval('[data-workers-ticket]',nodes=>nodes.map(n=>n.dataset.workersTicket));
      assert.deepEqual(shown,['OTHER-2','EXAMPLE-1']);
      assert.equal(await page.$eval('.fq-recent-rows',el=>el.firstElementChild.className),'workers-pending-list',
        'live workers without listed tickets appear before historical tickets');
      assert.ok(await page.$eval('[data-workers-pending-id=starting-worker]',el=>el.checkVisibility()));
      assert.ok(await page.$eval('[data-workers-ticket="EXAMPLE-1"]',el=>el.checkVisibility()));
      await page.click('[data-workers-ticket="EXAMPLE-1"] .workers-ticket-title');
      await page.click('[data-workers-ticket="OTHER-2"] .workers-ticket-link');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      if (process.env.WORKERS_LOCAL_SCREENSHOT) await page.screenshot({path:process.env.WORKERS_LOCAL_SCREENSHOT+'-'+width+'.png'});
    }
    assert.deepEqual(await page.evaluate(()=>picked),['local-session','local-session']);
    assert.deepEqual(await page.evaluate(()=>opened),['OTHER-2','OTHER-2']);
  } finally {await browser.close();}
});

test('every actively working ticket survives the recent-history cap', () => {
  const items = Array.from({length:14}, (_, i) => item('ACTIVE-'+i, {
    status:'in_progress', closed_at:null, claimed_by:'local-worker',
    claimed_at:new Date(1700000000000+i*1000).toISOString(),
  }));
  const ctx = fixture(items, {_uxqHealthCache:{wt_workers:[
    {worker_id:'local-worker',queue:'EXAMPLE',alive:true,idle_seconds:2},
  ]}});
  assert.equal(refs(ctx).length, 14, 'active claims are never history-limited');
});
