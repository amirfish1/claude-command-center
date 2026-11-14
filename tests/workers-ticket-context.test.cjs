const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const QA = require('../static/queue-attention.js');
const app = fs.readFileSync('static/app.js', 'utf8');
const slice = (start,end) => app.slice(app.indexOf(start), app.indexOf(end,app.indexOf(start)));
function setup(extra = {}) {
  const ctx = {window:{QueueAttention:QA}, _workersTicketSessions:new Map(),
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

test('choice click rechecks block generation, prepares a draft, and never submits it', async () => {
  const input = {value:'',focus(){}}; let modal = null; const calls = [];
  const ctx = setup({_uxqItemForRef:()=>block, _uxqOpenItemModal:item=>{modal=item;},
    document:{querySelector:()=>input}, showOpToast:()=>{},
    fetch:async (url,init)=>{calls.push({url,init});return {ok:true,json:async()=>({ok:true,item:{...block}})};}});
  ctx._workersTicketSessions.set(block.ref,{cards:[]});
  await ctx._workersReviewChoice(block.ref,0);
  assert.equal(modal.ref,block.ref);
  assert.match(input.value,/\[Q42:scope\] Keep narrow/);
  assert.equal(calls.length,1);
  assert.equal(calls[0].init.method,undefined);
  input.value=''; modal=null;
  ctx.fetch=async()=>({ok:true,json:async()=>({ok:true,item:{...block,blocked_at:'2026-01-02T10:00:00Z'}})});
  await ctx._workersReviewChoice(block.ref,0);
  assert.equal(input.value,'');
  assert.notEqual(modal.blocked_at,block.blocked_at);
  input.value='';
  ctx.fetch=async()=>({ok:true,json:async()=>({ok:true,item:{...block,wait:{on:'worker',block_at:block.blocked_at}}})});
  await ctx._workersReviewChoice(block.ref,0);
  assert.equal(input.value,'', 'a changed wait owner cannot receive an answer draft');
});
