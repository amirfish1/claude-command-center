// Slice 6 of the Codex single-renderer merge deleted the native inline
// connection (static/codex-client.js) entirely, so sendEscToTerminal() and
// cancelOptimisticAgentTurn() no longer branch on window.CCCCodexClient --
// every Codex pane (and every other engine) now goes through the same
// /api/inject-esc fetch path. These tests just confirm that path still works.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('  async function sendEscToTerminal()');
const end=source.indexOf('  if ($convEscBtn)',start);
const functionSource=source.slice(start,end);

test('existing Escape control calls /api/inject-esc', async () => {
  let fetchCalls=0;const pane={};
  const context={currentSession:{id:'task',source:'codex'},$convEscBtn:{textContent:'Esc',classList:{add(){},remove(){}}},activePaneId:()=> 'p1',convPaneElById:()=>pane,setTimeout:()=>0,showOpToast(){},fetch:async()=>{fetchCalls++;return{ok:true,json:async()=>({ok:true})}}};
  await vm.runInNewContext(functionSource+';sendEscToTerminal()',context);
  assert.equal(fetchCalls,1);
});

test('existing pending-message Cancel control calls /api/inject-esc', async () => {
  const a=source.indexOf('  async function cancelOptimisticAgentTurn');const z=source.indexOf('  function showOptimisticAgentIndicator',a);
  let fetchCalls=0,errors=0;const pane={};
  const context={currentSession:{id:'task'},view:{closest:()=>pane,querySelector:()=>null},button:{disabled:false,textContent:'Cancel'},showOpToast:()=>{errors++},fetch:async()=>{fetchCalls++;return{ok:true,json:async()=>({ok:true})}}};
  await vm.runInNewContext(source.slice(a,z)+';cancelOptimisticAgentTurn(view,button)',context);
  assert.equal(fetchCalls,1);assert.equal(errors,0);
});
