const {test} = require('node:test');
const assert = require('node:assert/strict');
const audit = require('../static/row-audit.js');
function completion(overrides = {}) {
  return {version: 1, scope: {goal:'Reliable session rows',done_when:'Evidence remains accurate',closure_met:true,evidence:'All checks passed'},
    resolution:{score:1,evidence:'Handled'},verification:{score:.9,evidence:'Browser passed'},delivery:{score:1,evidence:'Pushed'},
    decision_dependency:{score:0,evidence:'No decision'},caveats:[],remaining_actions:[],...overrides};
}
function events({did='Shipped the row fix.', next='None.', action=null, assessment=completion(), prose='The fix is live.\n\nLast paragraph with evidence.'}={}) {
  return [{type:'assistant',blocks:[{kind:'text',text:prose+'\n<session-state>\nDID: '+did+'\nINSIGHT: Root cause fixed.\nNEXT_STEP_USER: '+next+'\nNEXT_ACTION: '+JSON.stringify(action)+'\nCOMPLETION: '+JSON.stringify(assessment)+'\n</session-state>'}]}];
}
const row = (did='Shipped the row fix.', next='None.') => ({session_state:{did,next_step_user:next}});
const messages = result => result.findings.map(x => x.text).join('\n');
test('matching row is consistent while retaining original evidence', () => {
  const result = audit.inspect(row(), events(), {text:'Shipped the row fix.',close:true});
  assert.equal(result.status,'consistent');
  assert.equal(result.source.paragraph,'Last paragraph with evidence.');
  assert.match(messages(result), /self-reported/);
  assert.equal(result.findings.filter(x => x.kind === 'warning').length,0);
});
test('stale footer and visible preview are identified independently', () => {
  const result = audit.inspect(row('Old update'), events(), {text:'Unrelated old paragraph'});
  assert.match(messages(result),/row summary differs/);
  assert.match(messages(result),/visible row text differs/);
});
test('a model action must actually appear with the exact reply', () => {
  const action={label:'Run sample',reply:'Yes, run one sample only.'};
  const data=events({next:'Approve one sample only.',action});
  const source=row(undefined,'Approve one sample only.');
  assert.match(messages(audit.inspect(source,data)),/action button is missing/);
  assert.match(messages(audit.inspect(source,data,{action:{...action,reply:'Run all samples'}})),/differs from the model-authored reply/);
  assert.equal(audit.inspect(source,data,{action}).status,'consistent');
});
test('questions require a details chip rather than an invented approval', () => {
  const next='Answer [Q9:post-as] about publication.';
  const data=events({next});
  const result=audit.inspect(row(undefined,next),data);
  assert.match(messages(result),/without a visible details chip/);
  assert.deepEqual(result.questions,[{n:9,tag:'[Q9:post-as]'}]);
  assert.equal(result.action,null);
  assert.equal(audit.inspect(row(undefined,next),data,{questions:true}).status,'consistent');
});
test('missing controls, contradictory None, and missing plan are reported', () => {
  assert.match(messages(audit.inspect(row(undefined,'Choose a format.'),events({next:'Choose a format.'}))),/no model-authored button or question/);
  const assessment=completion({scope:{goal:'Experiment',done_when:'Real use verified',closure_met:false,evidence:'One milestone shipped'},decision_dependency:{score:.5,evidence:'Approval still needed'}});
  const result=audit.inspect(row(),events({assessment}),{close:true});
  assert.match(messages(result),/Needs you is empty/);
  assert.match(messages(result),/no concrete remaining plan/);
  assert.match(messages(result),/Close is shown despite/);
});
test('each independent Close gate blocks an incorrect recommendation', () => {
  const variants=[{resolution:{score:.8,evidence:'Partial'}},{verification:{score:.7,evidence:'Unverified'}},
    {delivery:{score:.5,evidence:'Local'}},{decision_dependency:{score:.5,evidence:'Choice'}},
    {caveats:[{text:'Blocking',blocking:true}]},{remaining_actions:[{text:'More work'}]},
    {scope:undefined},{verification:{score:1}},{version:2},{caveats:[{text:'Bad caveat'}]}];
  for(const variant of variants) assert.match(messages(audit.inspect(row(),events({assessment:completion(variant)}),{close:true})),/Close is shown despite/);
  assert.deepEqual(audit.closeReasons(completion({delivery:{score:0,applicable:false,evidence:'Explanation only'}})),[]);
});
test('missing, malformed, tool-only, and in-progress answers stay unassessed', () => {
  assert.equal(audit.inspect(row(),[]).status,'unknown');
  assert.equal(audit.inspect(row(),[{type:'assistant',blocks:[{kind:'tool_use',text:'not an answer'}]}]).status,'unknown');
  assert.equal(audit.inspect(row(),[{type:'assistant',blocks:[{kind:'text',text:'No footer'}]}]).status,'unknown');
  const active=[...events(),{type:'user',blocks:[]}];
  assert.equal(audit.inspect(row('Old'),active,{close:true}).status,'warning');
  assert.match(messages(audit.inspect(row(),active)),/reply is in progress/);
  const invalid=events();invalid[0].blocks[0].text=invalid[0].blocks[0].text.replace(/COMPLETION:.*\n/,'COMPLETION: {broken\n');
  assert.match(messages(audit.inspect(row(),invalid,{close:true})),/No completion assessment/);
});
test('uses latest text answer and latest footer, never an older successful assessment', () => {
  const data=[...events(),{type:'assistant',blocks:[{kind:'tool_use'}]},...events({did:'Follow-up incomplete',assessment:completion({resolution:{score:.5,evidence:'Unfinished'}})})];
  const result=audit.inspect(row('Follow-up incomplete'),data,{close:true});
  assert.equal(result.source.fields.DID,'Follow-up incomplete');
  assert.match(messages(result),/resolution gate is not met/);
  assert(!result.source.paragraph.includes('session-state'));
});
test('hourly audit detects a missing Close, accepts explanatory None, and skips real requests', () => {
  const next='None — deployed and checked.';
  const data=events({next});
  const result=audit.inspect(row(undefined,next),data,{checked:true});
  assert(result.findings.some(x=>x.code==='missing_close'));
  assert(!result.findings.some(x=>x.code==='missing_action_spec'));
  const bookkeeping=[...data,{type:'user_text',bookkeeping:true,text:'Compaction context'}];
  assert(audit.inspect(row(undefined,next),bookkeeping,{checked:true}).findings.some(x=>x.code==='missing_close'));
  const request=[...data,{type:'user_text',text:'Please change it again'}];
  assert(!audit.inspect(row(undefined,next),request,{checked:true}).findings.some(x=>x.code));
  assert(!audit.inspect({...row(),state:'working'},data,{checked:true}).findings.some(x=>x.code));
});
test('a user step without a button is a review hint, never an incident', () => {
  const {incident}=require('../scripts/audit-session-rows.cjs');
  const next='Check the release log after 07:00 UTC tomorrow.';
  const result=audit.inspect(row(undefined,next),events({next}),{checked:true});
  assert.match(messages(result),/no model-authored button or question/);
  assert.equal(incident(result,'example-session'),null);
});
test('incident identity stays stable across viewports and excludes unfinished turns', () => {
  const {incident}=require('../scripts/audit-session-rows.cjs');
  const result=audit.inspect(row(),events(),{checked:true});
  assert.equal(incident(result,'example-session').turn,incident(result,'example-session').turn);
  assert.equal(incident(audit.inspect(row(),events(),{working:true,checked:true}),'example-session'),null);
});
test('footerless questions require explicit end-turn evidence before filing', () => {
  const question={type:'assistant',blocks:[{kind:'text',text:'Draft ready.\n\nWant me to send it privately first, or to the group?'}]};
  const ended={type:'result',subtype:'end_turn'};
  assert(audit.inspect({},[question,ended],{checked:true}).findings.some(f=>f.code==='missing_question'));
  assert(!audit.inspect({},[question],{checked:true}).findings.some(f=>f.code));
  assert(!audit.inspect({state:'working'},[question,ended],{checked:true}).findings.some(f=>f.code));
  assert(!audit.inspect({},[question,ended,{type:'user_text',text:'Change the draft'}],{checked:true}).findings.some(f=>f.code));
  assert(!audit.inspect({},[{type:'assistant',blocks:[{kind:'text',text:'Waiting for the external deployment.'}]},ended],{checked:true}).findings.some(f=>f.code));
  assert(!audit.inspect({},[question,ended],{checked:true,questions:true}).findings.some(f=>f.code));
});
