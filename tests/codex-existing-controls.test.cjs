const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('  async function sendEscToTerminal()');
const end=source.indexOf('  if ($convEscBtn)',start);
const functionSource=source.slice(start,end);
for(const native of [true,false])test(`existing Escape uses ${native?'inline native':'legacy'} connection`,async()=>{
 let nativeCalls=0,legacyCalls=0;const pane={};
 const context={currentSession:{id:'task',source:'codex'},$convEscBtn:{textContent:'Esc',classList:{add(){},remove(){}}},activePaneId:()=> 'p1',convPaneElById:()=>pane,setTimeout:()=>0,showOpToast(){},window:{CCCCodexClient:{isInlineActive:()=>native,interruptInline:async p=>{assert.equal(p,pane);nativeCalls++;return{ok:true}}}},fetch:async()=>{legacyCalls++;return{ok:true,json:async()=>({ok:true})}}};
 await vm.runInNewContext(functionSource+';sendEscToTerminal()',context);
 assert.equal(nativeCalls,native?1:0);assert.equal(legacyCalls,native?0:1);
});
for(const native of [true,false])test(`existing pending-message Cancel uses ${native?'inline native':'legacy'} connection`,async()=>{
 const a=source.indexOf('  async function cancelOptimisticAgentTurn');const z=source.indexOf('  function showOptimisticAgentIndicator',a);
 let nativeCalls=0,legacyCalls=0,errors=0;const pane={};
 const context={currentSession:{id:'task'},view:{closest:()=>pane,querySelector:()=>null},button:{disabled:false,textContent:'Cancel'},showOpToast:()=>{errors++},window:{CCCCodexClient:{isInlineActive:()=>native,interruptInline:async()=>{nativeCalls++;return{ok:true}}}},fetch:async()=>{legacyCalls++;return{ok:true,json:async()=>({ok:true})}}};
 await vm.runInNewContext(source.slice(a,z)+';cancelOptimisticAgentTurn(view,button)',context);
 assert.equal(nativeCalls,native?1:0);assert.equal(legacyCalls,native?0:1);assert.equal(errors,0);
});
