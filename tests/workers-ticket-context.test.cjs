const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const QA = require('../static/queue-attention.js');
const app = fs.readFileSync(require.resolve('../static/conversation-text.js'), 'utf8') + '\n' + require('./legacy-dashboard-source.cjs');
const slice = (start,end) => app.slice(app.indexOf(start), app.indexOf(end,app.indexOf(start)));
function setup(extra = {}) {
  const ctx = {window:{QueueAttention:QA}, _workersTicketSessions:new Map(),
    _uxqHealthCache:{queues:[{queue:'TEST',auto_drain:true,configured:true}]},
    _uxqItemRef:item=>item.ref, _uxqTicketAttention:item=>QA.project(item,{explain:true}),
    _applyLiveOverlayToRow:c=>c, _ultraFreshCurrent:c=>c,
    _ultraFreshTime:value=>Number(value) > 1e12 ? Number(value) : Number(value || 0)*1000,
    workerStateBucket:c=>c.bucket, rowOutcomeView:c=>c.outcome,
    ...extra};
  vm.createContext(ctx);
  vm.runInContext(slice('  function escapeHtml(s)', '  function unescapeHtml(s)')
    + slice('  function sessionQuickAction(value)', '  // Model-authored chips')
    + slice('  function _workersMeaningfulUpdate(', '  // Compact history rows'), ctx);
  return ctx;
}
const block = {ref:'TEST-1',status:'awaiting_answer',needs_input:true,
  blocked_at:'2026-01-01T10:00:00Z',block_question:'[Q42:scope] Choose: keep narrow or expand scope.'};

test('terminal chatter is hidden, active work streams, and recorded block wins over session sign-off', () => {
  const ctx = setup();
  assert.equal(ctx._workersTicketContext({ref:'TEST-2',status:'closed'}, {outcome:{kind:'update',text:'No claimable tickets remain.'}}), null);
  assert.equal(ctx._workersTicketContext({ref:'TEST-2',status:'in_progress'}, {outcome:{kind:'working',text:'Running checks.'}}).text, 'Running checks.');
  const reason = ctx._workersTicketContext(block, {outcome:{kind:'update',text:'No claimable tickets remain.'}});
  assert.match(reason.text, /Choose: keep narrow/);
  assert.doesNotMatch(reason.text, /claimable/);
  assert.match(ctx._workersTicketContextHtml(block, null), /data-workers-ticket-row="TEST-1"/, 'owner context remains clickable without an attached session');
  assert.equal(ctx._workersTicketState({...block,wait:{on:'worker',block_at:block.blocked_at}}).label, 'Waiting for worker');
  ctx._workersTicketSessions.set('TEST-2', {cards:[{outcome:{kind:'next',text:'Old owner step.'},bucket:'needs-you'}]});
  assert.equal(ctx._workersTicketState({ref:'TEST-2',status:'in_progress'}).label, 'Idle');
});

test('literal choices preserve question tag and unsafe or stale session choices are excluded', () => {
  const ctx = setup({sessionQuickActions:state=>state.next_actions});
  const choices = ctx._workersTicketChoices(block, null);
  assert.equal(choices.length, 2);
  assert.match(choices[0].reply, /^\[Q42:scope\] Keep narrow/);
  assert.equal(ctx._workersTicketChoices({...block,wait:{on:'external',block_at:block.blocked_at}}, null).length,0);
  const free = {...block,block_question:'[Q42:scope] What should happen next?'};
  const card = {modified:Date.parse(block.blocked_at)/1000-1,session_state:{next_step_user:'Choose [Q42:scope]',next_actions:[{label:'Old choice',reply:'[Q42:scope] Old choice'}]}};
  assert.equal(ctx._workersTicketChoices(free,card).length,0);
  card.modified += 10;
  assert.equal(ctx._workersTicketChoices(free,card).length,1);
});

test('live claims remain visible while session details load, without inventing activity', () => {
  const worker = {worker_id:'worker-1',session_id:'session-1',queue:'TEST',alive:true,idle_seconds:2};
  const ctx = setup({_uxqHealthCache:{wt_workers:[worker]}});
  const item = {ref:'TEST-2',project:'TEST',status:'in_progress',claimed_by:'worker-1'};
  assert.equal(ctx._workersTicketState(item).label, 'Working');
  assert.equal(ctx._workersTicketContext(item, null), null, 'the Working state already says it');
  for (const patch of [{alive:false},{released_at:'2026-01-01'},{idle_seconds:61},{idle_seconds:null},{queue:'OTHER'},{worker_id:'another-worker'}]) {
    ctx._uxqHealthCache.wt_workers = [{...worker,...patch}];
    assert.equal(ctx._workersTicketState(item).label, 'Claimed', JSON.stringify(patch));
  }
  ctx._uxqHealthCache.wt_workers = [worker];
  assert.equal(ctx._workersTicketState({...block,claimed_by:'worker-1'}).label, 'Needs you');
  assert.equal(ctx._workersTicketState({...item,status:'closed'}).label, 'Resolved');
});

test('Starting expires after ten seconds and registered sessions never look like a launch', () => {
  const ctx = setup();
  const started = Date.parse('2026-01-01T10:00:00Z');
  const worker = {started_at:new Date(started).toISOString()};
  assert.equal(ctx._workersPendingState(worker, started + 1000).label, 'Starting');
  assert.equal(ctx._workersPendingState(worker, started + 1000).refreshIn, 9000);
  assert.equal(ctx._workersPendingState(worker, started + 10000).label, 'Startup delayed');
  assert.equal(ctx._workersPendingState(worker, started + 3600000).label, 'Startup delayed');
  assert.equal(ctx._workersPendingState({...worker,session_id:'session-1',idle_seconds:2}, started + 1000).label, 'Active');
  assert.equal(ctx._workersPendingState({...worker,session_id:'session-1',idle_seconds:80}, started + 1000).label, 'Standing by');
  assert.equal(ctx._workersPendingState({...worker,session_id:'session-1'}, started + 1000).label, 'Session not listed');
  assert.equal(ctx._workersPendingState({started_at:'invalid'}, started).label, 'Session not registered');
});

test('awaiting-worker chips include only currently runnable open tickets', () => {
  const ctx = setup();
  const ready = {ref:'TEST-1',status:'open',priority:'p2'};
  const items = [{...ready,ref:'ARCHIVED-1',project:'ARCHIVED'},ready,{...ready}, {...ready,ref:'TEST-2',priority:'p1'},
    {...ready,ref:'TEST-3',status:'in_progress'}, {...ready,ref:'TEST-4',needs_input:true},
    {...ready,ref:'TEST-5',waiting_on:['TEST-9']}, {...ready,ref:'TEST-6',claimable:false},
    {...ready,ref:'TEST-7',deferred_until:'2099-01-01T00:00:00Z'},
    {...ready,ref:'TEST-8',readiness:'needs-spec'}, {...ready,ref:'TEST-9',no_auto_drain:true},
    {...ready,ref:'TEST-10',stages:{current:'verify'}},
    {...ready,ref:'TEST-11',pending_answer:{state:'delivered'},waiting_on:['TEST-9']},
    {...ready,ref:'IDEAS-1'}, {...ready,ref:'TEST-12',pending_answer:{state:'delivered'}}];
  assert.deepEqual(Array.from(ctx._workersAwaitingTickets(items),it=>it.ref), ['TEST-2','TEST-1','TEST-12']);
  ctx._uxqTicketAttention = item => QA.project(item,{explain:true,queue:{auto_drain:false}});
  assert.equal(ctx._workersAwaitingTickets(items).length,0);
});

test('unlisted worker rows exclude dead, released, and already represented processes', () => {
  const worker = {worker_id:'worker-1',session_id:'session-1',queue:'TEST',alive:true};
  const ctx = setup({_uxqHealthCache:{wt_workers:[worker,{...worker,worker_id:'dead',alive:false},
    {...worker,worker_id:'released',released_at:'2026-01-01'},
    {...worker,worker_id:'unknown',alive:null}]}});
  const item = {ref:'TEST-1',status:'in_progress',claimed_by:'worker-1'};
  assert.equal(ctx._workersPendingWorkers([]).length,1);
  assert.equal(ctx._workersPendingWorkers([{item}]).length,0);
  const roster = ctx._workersPendingWorkers([{item}], true);
  assert.equal(roster.length,1, 'claimed workers retain their own roster row');
  assert.deepEqual(Array.from(roster[0].ticket_refs),['TEST-1']);
  assert.equal(ctx._workersPendingWorkers([{item:{...item,status:'closed'}}]).length,1);
  ctx._uxqHealthCache.worker_session_map = {'worker-1':'canonical-session'};
  ctx._uxqHealthCache.wt_workers = [{...worker,session_id:''}];
  assert.equal(ctx._workersPendingWorkers([])[0].session_id,'canonical-session');
  ctx._workersTicketSessions.set('TEST-2',{cards:[{session_id:'canonical-session'}]});
  assert.equal(ctx._workersPendingWorkers([{item:{ref:'TEST-2',status:'in_progress'}}]).length,0);
});


test('a live claim names the worker row and overrides a stale open ticket row', () => {
  const worker = {worker_id:'worker-1',session_id:'session-1',queue:'TEST',alive:true,
    claimed_tickets:[{ref:'TEST-7',title:'Fix the thing',claimed_at:'2026-01-01T10:00:00Z'}]};
  const ctx = setup({_uxqHealthCache:{wt_workers:[worker]}});
  const listed = ctx._workersPendingWorkers([], true);
  assert.deepEqual(Array.from(listed[0].ticket_refs), ['TEST-7'], 'claim shown before the list catches up');
  assert.equal(listed[0].ticket_items[0].title, 'Fix the thing');
  const items = [{ref:'TEST-7',status:'open'}, {ref:'TEST-8',status:'closed',closed_at:'x'}];
  ctx._workersOverlayLiveClaims(items);
  assert.equal(items[0].status, 'in_progress');
  assert.equal(items[0].claimed_by, 'worker-1');
  assert.equal(ctx._workersPendingWorkers([{item:items[0]}]).length, 0, 'worker merges into its ticket row');
  ctx._uxqHealthCache.wt_workers = [{...worker, claimed_tickets:[{ref:'TEST-8'}]}];
  ctx._workersOverlayLiveClaims(items);
  assert.equal(items[1].status, 'closed', 'a newer close in the list is never reopened');
});

test('approval questions get scoped binary answers; ambiguous and offline questions do not', () => {
  const ctx = setup();
  const item = {...block, block_question:'[Q42:scope] Approve the queued 48-request focused validation?'};
  const choices = ctx._workersTicketChoices(item, null);
  assert.deepEqual(Array.from(choices, c=>c.label), ['Approve','Decline']);
  assert.match(choices[0].reply, /48-request focused validation/);
  assert.equal(ctx._workersTicketChoices({...item,block_question:'Approve four scenarios, capped at 40 requests, no writes or sends?'},null).length,2);
  assert.match(choices[1].reply, /^\[Q42:scope\] No/);
  for (const question of ['Approve A or B?', 'Review the report.', 'Approve A? Also approve B?']) {
    assert.equal(ctx._workersTicketChoices({...item,block_question:question},null).length,0);
  }
});

test('one tap submits once; stale generations and changed owners do not submit', async () => {
  let modal = null; const calls = []; const item = {...block};
  const ctx = setup({_uxqItemForRef:()=>item, _uxqOpenItemModal:value=>{modal=value;},
    _uxqItemsCache:{}, _uxqHealthCache:{}, _uxqRenderRecentWork(){}, _renderQueuePanel(){},
    showOpToast:()=>{},
    fetch:async (url,init)=>{
      calls.push({url,init});
      return {ok:true,json:async()=>({ok:true,item:{...block,needs_input:!init.method}})};
    }});
  ctx._workersTicketSessions.set(block.ref,{cards:[]});
  await Promise.all([ctx._workersReviewChoice(block.ref,0),ctx._workersReviewChoice(block.ref,0)]);
  assert.equal(modal,null);
  assert.equal(calls.length,2);
  assert.equal(calls[1].init.method,'POST');
  assert.match(JSON.parse(calls[1].init.body).text,/\[Q42:scope\] Keep narrow/);
  await ctx._workersReviewChoice(block.ref,0);
  assert.equal(calls.length,2);
  item.needs_input=true;
  ctx.fetch=async()=>({ok:true,json:async()=>({ok:true,item:{...block,blocked_at:'2026-01-02T10:00:00Z'}})});
  await ctx._workersReviewChoice(block.ref,0);
  assert.notEqual(modal.blocked_at,block.blocked_at);
  modal=null;
  ctx.fetch=async()=>({ok:true,json:async()=>({ok:true,item:{...block,wait:{on:'worker',block_at:block.blocked_at}}})});
  await ctx._workersReviewChoice(block.ref,0);
  assert.equal(modal.wait.on,'worker');
});

test('failed send reports an error and releases the pending guard for retry', async () => {
  let sends=0; const errors=[];
  const ctx=setup({_uxqItemForRef:()=>({...block}), showOpToast:text=>errors.push(text),
    fetch:async(url,init)=>init.method
      ? (sends++, {ok:false,json:async()=>({error:'Try again'})})
      : {ok:true,json:async()=>({ok:true,item:{...block}})}});
  await ctx._workersReviewChoice(block.ref,0);
  await ctx._workersReviewChoice(block.ref,0);
  assert.equal(sends,2);
  assert.deepEqual(errors,['Try again','Try again']);
});

test('pending Devin workers retire when their prefixed session card appears', () => {
  const ctx = setup({_uxqHealthCache:{wt_workers:[{worker_id:'devin-1',session_id:'slug',alive:true}]}});
  const item = {ref:'TEST-3',status:'in_progress'};
  assert.equal(ctx._workersPendingWorkers([{item}]).length,1);
  ctx._workersTicketSessions.set('TEST-3',{cards:[{session_id:'devincli-slug'}]});
  assert.equal(ctx._workersPendingWorkers([{item}]).length,0);
  assert.equal(ctx._workersPendingWorkers([{item:{...item,status:'closed'}}]).length,1);
});


test('live worker task title follows its newest active claim, including note-only reports', () => {
  const worker = {worker_id:'worker-1',session_id:'session-1',queue:'TEST',alive:true,idle_seconds:2};
  const ctx = setup({_uxqHealthCache:{wt_workers:[worker]},workerSectionHeaderHtml:()=>'',_uxqRelTime:()=> '1m'});
  const older = {ref:'TEST-1',status:'in_progress',claimed_by:'worker-1',title:'Earlier task',claimed_at:'2026-01-01T09:00:00Z'};
  const newer = {...older,ref:'TEST-2',title:'',note:'Repair the latest report',claimed_at:'2026-01-01T10:00:00Z'};
  const roster = ctx._workersPendingWorkers([{item:older},{item:newer}], true);
  assert.equal(roster[0].ticket_items[0].ref, 'TEST-2');
  const html = ctx._workersPendingHtml(roster);
  assert.match(html, /Repair the latest report/);
  assert.doesNotMatch(html, />Earlier task</);
  assert.match(html, /data-workers-pending-session="session-1"/);
});
