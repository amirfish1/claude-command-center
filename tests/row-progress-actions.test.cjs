const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
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
test('callback waits offer Check progress without authorizing their pending release action', () => {
  const r=row();
  assert.equal(actionSource(r),null,'a retained footer alone is insufficient');
  const candidate=actionSource(r,true);
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()+3000});
  assert.equal(actionSource(r).action.label,'Check progress');
  assert.match(candidate.action.reply,/report its current status/);
  assert.match(candidate.action.reply,/Do not restart work, rerun jobs, publish, deploy, spend money, or execute the waiting action/);
  assert.equal(loadRowOutcomeView()(r).label,'Waiting');
  const footer='<session-state>\nDID: '+r.session_state.did+'\nNEXT_STEP_USER: None.\nCOMPLETION: '
    +JSON.stringify(r.session_state.completion)+'\n</session-state>';
  const final={type:'assistant',blocks:[{kind:'text',text:footer}]};
  assert.equal(transcriptIdle(candidate,[final]),true);
  for(const event of [{type:'user_text',text:'Continue'}, {type:'tool_result',text:'Running'},
    {type:'assistant',blocks:[{kind:'tool_use'}]}]) assert.equal(transcriptIdle(candidate,[final,event]),false);
  const plan=r.session_state.completion.remaining_actions[0];
  plan.owner='agent';plan.text=plan.text.replace('agent: ','');
  assert.equal(actionSource(r,true).action.label,'Check progress','structured and legacy owners both work');
});
test('status chips do not replace working, external, unscoped or authored steps', () => {
  for(const flags of [{state:'working'},{codex_state:'working'},{archived:true},{trashed:true},
    {needs_approval:true},{question_waiting:true},{pending_spawn:true},{last_event_type:'user'}])
    assert.equal(actionSource({...row(),...flags},true),null,JSON.stringify(flags));
  for(const change of [{text:'external: On the callback, inspect the result.'},
    {text:'agent: Inspect the result now.'},{within_scope:false},{reversible:false}]) {
    const r=row();Object.assign(r.session_state.completion.remaining_actions[0],change);
    assert.equal(actionSource(r,true),null,JSON.stringify(change));
  }
  const r=row();r.session_state.next_step_user='Choose a region.';
  r.session_state.next_action={label:'Europe',reply:'Use Europe.'};
  assert.equal(actionSource(r,true).action.label,'Europe');
});
