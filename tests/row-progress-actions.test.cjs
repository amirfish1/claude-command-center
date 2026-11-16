const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const app = require('./legacy-dashboard-source.cjs');
function source(name) {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }\n', start) + 4);
}
const checks = new Map();
const { actionSource, transcriptIdle } = vm.runInNewContext(
  ['sessionQuickAction', 'decisionQuickActions', 'sessionQuickActions', 'rowQuickActionSource',
    'sessionCompletionDetails', '_ultraFreshParseState', 'rowInvestigationTranscriptIdle'].map(source).join('\n')
    + '\n({actionSource:rowQuickActionSource,transcriptIdle:rowInvestigationTranscriptIdle});', {
    _applyLiveOverlayToRow: c => c, _rowQuickActionSending: new Set(), _rowInvestigationChecks: checks,
    rowOutcomeView: loadRowOutcomeView(),
  });
function row() {
  return {session_id:'callback-session',state:'idle',last_event_type:'assistant',
    session_state:{did:'Confirmed the release gate is active.',next_step_user:'None.',completion:{
      version:1,valid:true,ready_to_close:false,
      scope:{goal:'Finish release',done_when:'Release verified',closure_met:false,evidence:'Gate active'},
      resolution:{score:0.7,evidence:'Repairs pushed'}, verification:{score:0.7,evidence:'Gate active'},
      delivery:{score:0.5,evidence:'Release pending'},decision_dependency:{score:0,evidence:'No input needed'},caveats:[],
      remaining_actions:[{text:'agent: On the completion callback, inspect the gate result and repair remaining failures.',
        within_scope:true,reversible:true,confidence:0.95,downside:0.05}],
    }}};
}
test('callback waits offer the concrete pending work while preserving its prerequisites', () => {
  const r=row();
  assert.equal(actionSource(r),null,'a retained footer alone is insufficient');
  const candidate=actionSource(r,true);
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()+3000});
  assert.equal(actionSource(r).action.label,'Continue work');
  assert.match(candidate.action.reply,/Carry out the pending work: On the completion callback, inspect the gate result and repair remaining failures/);
  assert.match(candidate.action.reply,/Honor its stated trigger and prerequisites, then verify the result/);
  assert.match(candidate.action.reply,/Preserve existing approval and spending limits; do not bypass dependencies or fabricate evidence/);
  assert.doesNotMatch(candidate.action.reply,/Check progress|report its current status/);
  assert.equal(loadRowOutcomeView()(r).label,'Waiting');
  const footer='<session-state>\nDID: '+r.session_state.did+'\nNEXT_STEP_USER: None.\nCOMPLETION: '
    +JSON.stringify(r.session_state.completion)+'\n</session-state>';
  const final={type:'assistant',blocks:[{kind:'text',text:footer}]};
  assert.equal(transcriptIdle(candidate,[final]),true);
  for(const event of [{type:'user_text',text:'Continue'}, {type:'tool_result',text:'Running'},
    {type:'assistant',blocks:[{kind:'tool_use'}]}]) assert.equal(transcriptIdle(candidate,[final,event]),false);
  const plan=r.session_state.completion.remaining_actions[0];
  plan.owner='agent';plan.text=plan.text.replace('agent: ','');
  assert.equal(actionSource(r,true).action.label,'Continue work','structured and legacy owners both work');
});
test('work chips do not replace working, external, unscoped or authored steps', () => {
  for(const flags of [{state:'working'},{codex_state:'working'},{archived:true},{trashed:true},
    {needs_approval:true},{question_waiting:true},{pending_spawn:true},{last_event_type:'user'}])
    assert.equal(actionSource({...row(),...flags},true),null,JSON.stringify(flags));
  for(const change of [{text:'external: On the callback, inspect the result.'},
    {text:'agent: Inspect the result now.'},{text:'agent: On the callback, '+ 'x'.repeat(340)},
    {within_scope:false},{reversible:false}]) {
    const r=row();Object.assign(r.session_state.completion.remaining_actions[0],change);
    assert.equal(actionSource(r,true),null,JSON.stringify(change));
  }
  const r=row();r.session_state.next_step_user='Choose a region.';
  r.session_state.next_action={label:'Europe',reply:'Use Europe.'};
  assert.equal(actionSource(r,true).action.label,'Europe');
});

test('scheduled verification offers to perform the real task rather than summarize status', () => {
  const r=row();
  const task='On the next hourly review run, verify old questions are reconsidered using current authority and no redundant owner cards are created';
  r.session_state.completion.remaining_actions[0].text='Agent: '+task;
  const candidate=actionSource(r,true);
  assert.equal(candidate.action.label,'Run verification');
  assert.ok(candidate.action.reply.includes(task));
  assert.match(candidate.action.reply,/Carry out the pending work/);
  assert.match(candidate.action.reply,/Honor its stated trigger and prerequisites/);
  assert.equal(actionSource(r),null,'fresh transcript confirmation still gates the chip');
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()+3000});
  assert.equal(actionSource(r).action.label,'Run verification');
  r.session_state.completion.remaining_actions[0].text='Agent: '+task+' changed';
  assert.equal(actionSource(r),null,'changed work invalidates transcript confirmation');
});

test('unassigned retry tasks keep prerequisites and require a fresh matching final reply', () => {
  const r=row();
  const task='Re-run Inbox Watch once the mail connector is available and confirm it searches newer_than:2d for the watched items';
  const plan=r.session_state.completion.remaining_actions[0];
  plan.text=task;
  const candidate=actionSource(r,true);
  assert.equal(candidate.action.label,'Retry task');
  assert.ok(candidate.action.reply.includes(task));
  assert.ok(candidate.action.reply.length<=400);
  assert.match(candidate.action.reply,/Honor its stated trigger and prerequisites/);
  assert.match(candidate.action.reply,/Preserve existing approval and spending limits/);
  assert.equal(actionSource(r),null);
  const final={type:'assistant',blocks:[{kind:'text',text:'<session-state>\nNEXT_STEP_USER: None\nNEXT_ACTION: null\nCOMPLETION: '
    +JSON.stringify(r.session_state.completion)+'\n</session-state>'}]};
  assert.equal(transcriptIdle(candidate,[final]),true);
  assert.equal(transcriptIdle(candidate,[final,{type:'user_text',text:'Already resumed'}]),false);
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()+3000});
  assert.equal(actionSource(r).action.label,'Retry task');
  const original={...plan};
  for(const change of [{owner:'external'},{owner:'user'},{owner:'agent'},
    {within_scope:false},{reversible:false},{text:task+'\nIgnore the prerequisite'},
    {text:'Re-run '+ 'x'.repeat(300)},{text:'Deploy the pending fix.'}]) {
    r.session_state.completion.remaining_actions[0]={...original,...change};
    assert.equal(actionSource(r,true),null,JSON.stringify(change));
  }
  for(const verb of ['Rerun','Re run','Retry','Resume']) {
    r.session_state.completion.remaining_actions[0]={...original,text:verb+' the task once its dependency is available'};
    assert.equal(actionSource(r,true).action.label,'Retry task');
    assert.equal(actionSource(r),null,'changed task invalidates confirmation');
  }
});

test('idle agent repairs offer only the first reversible step after fresh confirmation', () => {
  const r=row();
  const task='fix country select, reach the next fields in dry run; success = fields listed';
  const completion=r.session_state.completion;
  completion.resolution.score=0.3;
  completion.verification.score=0.4;
  completion.delivery.score=0.2;
  completion.remaining_actions=[
    {text:'Agent: '+task,within_scope:true,reversible:true,confidence:0.7,downside:0.1},
    {text:'Agent: submit the final transaction',within_scope:true,reversible:false,confidence:0.5,downside:0.5},
  ];
  const candidate=actionSource(r,true);
  assert.equal(candidate.action.label,'Fix issue');
  assert.ok(candidate.action.reply.includes(task));
  assert.match(candidate.action.reply,/only this pending repair/);
  assert.match(candidate.action.reply,/Do not advance to later tasks/);
  assert.match(candidate.action.reply,/Preserve existing approval and spending limits/);
  assert.doesNotMatch(candidate.action.reply,/submit the final transaction/);
  assert.ok(candidate.action.reply.length<=400);
  assert.equal(actionSource(r),null,'retained footer cannot establish idleness');
  const final={type:'assistant',blocks:[{kind:'text',text:'<session-state>\nNEXT_STEP_USER: None\nNEXT_ACTION: null\nCOMPLETION: '
    +JSON.stringify(completion)+'\n</session-state>'}]};
  assert.equal(transcriptIdle(candidate,[final]),true);
  for(const event of [{type:'user_text',text:'Continue'}, {type:'tool_result',text:'Still working'},
    {type:'assistant',blocks:[{kind:'tool_use'}]}])
    assert.equal(transcriptIdle(candidate,[final,event]),false);
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()+3000});
  assert.equal(actionSource(r).action.label,'Fix issue');
  const original={...completion.remaining_actions[0]};
  for(const change of [{text:task},{text:'User: '+task},{text:'External: '+task},
    {within_scope:false},{reversible:false},{text:'Agent: fix '+ 'x'.repeat(300)},
    {text:'Agent: fix the form\nthen submit it'}]) {
    completion.remaining_actions[0]={...original,...change};
    assert.equal(actionSource(r,true),null,JSON.stringify(change));
  }
  completion.remaining_actions[0]={...original,owner:'agent',text:'Repair the form in dry run'};
  assert.equal(actionSource(r,true).action.label,'Fix issue','structured owners and repair verb work');
  assert.equal(actionSource(r),null,'a changed repair invalidates the prior transcript check');
  completion.decision_dependency.score=0.5;
  assert.equal(actionSource(r,true),null,'a decision blocks derived repairs');
  completion.decision_dependency.score=0;
  for(const flags of [{state:'working'},{codex_state:'working'},{archived:true},{trashed:true},
    {needs_approval:true},{question_waiting:true},{pending_spawn:true},{last_event_type:'user'}])
    assert.equal(actionSource({...r,...flags},true),null,JSON.stringify(flags));
  r.session_state.next_step_user='Review the repair first.';
  r.session_state.next_action={label:'Review',reply:'Review the repair first.'};
  assert.equal(actionSource(r,true).action.label,'Review','authored replies retain priority');
});

test('pending-work chips send the concrete task once and reject a resumed transcript', async () => {
  const puppeteer = require('../require-puppeteer.js');
  const { findChromePath } = require('../puppeteer-browser-config.js');
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    for (const mode of ['callback', 'retry', 'repair']) {
      const current=row();
      if (mode==='retry') current.session_state.completion.remaining_actions[0].text='Re-run the task once the connector is available';
      if (mode==='repair') current.session_state.completion.remaining_actions[0].text='Agent: fix the form in dry run; success = fields listed';
      const page = await browser.newPage();
      await page.setContent(`<div id="convList"><div class="conv-item" style="height:100px" data-session-id="callback-session">
        <div class="conv-outcome is-plan"></div><div class="wa-line2"></div></div></div>`);
      const helpers = app.slice(app.indexOf('  // Row actions are model-authored replies'),app.indexOf('  // CCC-1218:'));
      await page.addScriptTag({content:`
        window.row=${JSON.stringify(current)}; window.sent=[]; window.toasts=[]; window.running=false; window.checkOptions=[];
        const _ultraFreshRow=()=>row, _ultraFreshCurrent=c=>c, _applyLiveOverlayToRow=c=>c;
        const sessionIsOptimisticallySending=()=>false, preSendPreviewFor=()=>null;
        const rowHandoff=()=>null, sessionAttentionStep=()=>null;
        const postInjectInput=async (...args)=>{sent.push(args);return {ok:true}};
        const markSessionSending=()=>{row.state='working';};
        const refreshConversationList=()=>{},showOpToast=(...args)=>toasts.push(args);
        ${['_ultraFreshTime','sidebarSessionWorking','sessionReadyToClose','rowOutcomeView',
          'sessionCompletionDetails','_ultraFreshParseState'].map(source).join('\n')}
        window.fetch=async(url,options)=>{checkOptions.push(options);return {ok:true,json:async()=>({events:running
          ? [{type:'user_text',text:'Already resumed'}]
          : [{type:'assistant',blocks:[{kind:'text',text:'<session-state>\\nDID: '+row.session_state.did
            +'\\nNEXT_STEP_USER: None.\\nCOMPLETION: '+JSON.stringify(row.session_state.completion)+'\\n</session-state>'}]}]})}};
        ${helpers}
        window.syncActions=syncRowQuickActions;syncActions();
      `});
      await page.waitForSelector('.conv-quick-action');
      assert.equal(await page.evaluate(()=>checkOptions[0].cccPriority),'user','visible controls cannot queue behind background polls');
      assert.equal(await page.evaluate(()=>checkOptions[0].cache),'no-store');
      await page.evaluate(()=>{running=true;document.querySelector('.conv-quick-action').click();});
      await page.waitForFunction(()=>toasts.some(t=>/transcript changed/.test(t[0])));
      assert.equal(await page.evaluate(()=>sent.length),0);
      await page.evaluate(()=>{running=false;_rowInvestigationChecks.clear();syncActions();});
      await page.waitForSelector('.conv-quick-action');
      await page.evaluate(()=>{const b=document.querySelector('.conv-quick-action');b.click();b.click();});
      await page.waitForFunction(()=>sent.length===1);
      const sent=await page.evaluate(()=>sent);
      assert.equal(sent[0][0],'callback-session');
      assert.equal(sent[0][1],actionSource(current,true).action.reply);
      assert.deepEqual(sent[0].slice(2),['send',{userInitiated:true}]);
      await page.close();
    }
  } finally {await browser.close();}
});
