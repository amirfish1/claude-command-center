const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function fixture() {
 const panes=[{id:'p1',conversationId:'one',currentSession:{id:'one',source:'claude'}},{id:'p2',conversationId:'two',currentSession:{id:'two',source:'codex'}}];
 const calls=[],classes=[false,false];
 const state={splitState:{panes,activeIndex:0,orientation:'vertical'},_sttRecording:true,_sttActivePaneId:'p1',_sttActiveConvId:'one',conversationsData:[],archiveData:[],
  _convPaneHost:()=>({querySelectorAll:()=>panes.map((p,i)=>({getAttribute:()=>p.id,classList:{contains:()=>classes[i],toggle:(name,on)=>{classes[i]=on;}}}))})};
 Object.defineProperty(state,'currentConversation',{get:()=>panes[state.splitState.activeIndex].conversationId});
 const helpers={};
 for(const name of ['stopSpeechRecognition','syncConversationNavigationFocus','updateSubagentsPanel','updateOrchestrationPane','mountStatusRailForActivePane','saveSplitState','updatePaneHeader','setCopyableSessionId','fetchCodexDiagnostics','_codexDiagHidePanel','updateInputBar','updateResumeButton','updateAnnounceButton','updateJumpButton','updateSplitToolbar','scheduleTtsPrefetch','pollVercelDeploy','resetLocalhostPill','pollLocalhost','_applyStatusRailLayout'])helpers[name]=(...args)=>calls.push([name,...args]);
 const ctx={window:{}};vm.runInNewContext(fs.readFileSync(require.resolve('../static/conversation-panes.js'),'utf8'),ctx);
 return {panes,state,calls,classes,controller:ctx.window.CCCConversationPanes.createController({state,helpers})};
}
test('focus shares the current workspace and moves rail before composer chrome',()=>{
 const h=fixture();assert.equal(h.controller.setActivePaneById('p2'),true);
 assert.equal(h.state.splitState.activeIndex,1);assert.deepEqual(h.classes,[false,true]);
 const focus=h.calls.find(c=>c[0]==='syncConversationNavigationFocus');assert.equal(focus[1],'two');assert.equal(focus[2].has('one'),true);
 assert.ok(h.calls.find(c=>c[0]==='stopSpeechRecognition'));
 assert.ok(h.calls.findIndex(c=>c[0]==='mountStatusRailForActivePane')<h.calls.findIndex(c=>c[0]==='updateInputBar'));
 assert.ok(h.calls.some(c=>c[0]==='fetchCodexDiagnostics'&&c[1]==='two'&&c[2]==='p2'));
 h.state.splitState.panes=[{id:'replaced',conversationId:'fresh'}];h.state.splitState.activeIndex=0;
 assert.equal(h.controller.activePaneId(),'replaced');assert.equal(h.controller.paneByPaneId('p2'),null);
});
test('missing pane cannot shift focus; omitted and explicit conversation IDs remain distinct',()=>{
 const h=fixture();assert.equal(h.controller.setActivePaneById('missing'),false);assert.equal(h.calls.length,0);
 h.controller.setActivePaneById('p1',null);assert.equal(h.calls.find(c=>c[0]==='syncConversationNavigationFocus')[1],null);
 h.calls.length=0;h.controller.setActivePaneById('p1');assert.equal(h.calls.find(c=>c[0]==='syncConversationNavigationFocus')[1],'one');
 assert.equal(h.calls.some(c=>c[0]==='saveSplitState'),false);
});
