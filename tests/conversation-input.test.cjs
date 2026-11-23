const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../static/conversation-input.js'),'utf8');
function fixture(saved={}) {
 const storage=new Map(Object.entries(saved)), timers=new Map();let timerId=0;
 const localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
 const classes=()=>({toggle(){},add(){},remove(){}});
 const input={value:'',classList:classes(),dispatchEvent(){},setSelectionRange(){}};
 const send={classList:classes()},steer={classList:classes()},compact={classList:classes()};
 const controls={'textarea, input[type="text"]':input,'.send-btn':send,'.steer-btn':steer,'.compact-btn':compact,'#convTtyLabel, .tty-label, [data-role="tty-label"]':{}};
 const bar={dataset:{},classList:classes(),querySelector:s=>controls[s]||null};
 const pane={id:'p1',conversationId:'one'};
 const state={currentConversation:'one',currentSession:{id:'one',source:'codex'},conversationsData:[{id:'one'}],archiveData:[],liveStatus:{},activeTab:'sessions',$convInputBar:bar,$convInput:input,
  activePaneId:()=>pane.id,paneByPaneId:()=>pane,getConvInputBarForPane:()=>bar,convPaneElById:()=>({querySelector:()=>input})};
 const helpers=new Proxy({isImeKey:()=>false,rowRepoPath:r=>r.repo_path,popoutRepoPath:()=>'/fixture',liveStatusMatchesOpenConv:()=>false,antigravityCanSend:()=>true,newSessionPaneId:()=>null,getSpawnEngine:()=> 'claude'}, {get:(o,k)=>o[k]||(()=>{})});
 const ctx={localStorage,Event:class {},document:{querySelector:()=>null,getElementById:()=>null},setTimeout:f=>{timers.set(++timerId,f);return timerId;},clearTimeout:id=>timers.delete(id)};
 vm.runInNewContext(source,ctx);
 return {storage,localStorage,state,pane,input,send,steer,compact,timers,controller:ctx.CCCConversationInput.createController({state,helpers})};
}
test('draft storage survives controller reload, pane changes and failed delivery',()=>{
 const h=fixture({'ccc-input-drafts-v1':JSON.stringify({'conv:one':'legacy saved draft'})});
 h.controller.restoreComposerDraftForPane('p1','one');assert.equal(h.input.value,'legacy saved draft');
 h.input.value='next edit';h.controller.rememberComposerDraftForPane('p1');h.controller.flushSaveInputDrafts();
 const reload=fixture(Object.fromEntries(h.storage));reload.controller.restoreComposerDraftForPane('p1','one');assert.equal(reload.input.value,'next edit');
 reload.input.value='';reload.controller.restoreInputAfterSendFailure(reload.input,'failed send');assert.equal(reload.input.value,'failed send');
 reload.input.value='new human edit';reload.controller.restoreInputAfterSendFailure(reload.input,'failed send');assert.equal(reload.input.value,'new human edit');
 reload.pane.conversationId='two';reload.input.value='second pane draft';reload.controller.rememberComposerDraftForPane('p1');
 assert.equal(reload.controller.inputDraftForConversation('one'),'next edit');assert.equal(reload.controller.inputDraftForConversation('two'),'second pane draft');
});
test('backlog draft identity follows repo and command recall uses the same storage',()=>{
 const h=fixture();h.state.conversationsData=[{id:'backlog-issue-1',source:'backlog',repo_path:'/repo-one'}];
 h.controller.rememberInputDraft({value:'issue edit'},'backlog-issue-1');
 h.state.conversationsData=[{id:'backlog-issue-1',source:'backlog',repo_path:'/repo-two'}];h.controller.clearInputDraftKeyCache();
 assert.equal(h.controller.inputDraftForConversation('backlog-issue-1'),'');
 h.controller.rememberComposerCommand('first');h.controller.rememberComposerCommand('second');h.controller.rememberComposerCommand('first');
 assert.deepEqual(Array.from(h.controller.loadComposerHistory()),['second','first']);
 let prevented=false;assert.equal(h.controller.recallLastComposerCommand(h.input,{key:'ArrowUp',preventDefault(){prevented=true;}}),true);
 assert.equal(h.input.value,'first');assert.equal(prevented,true);
});
test('composer availability uses current live session rather than the prior selection',()=>{
 const h=fixture();h.controller.updateInputBar();assert.equal(h.send.disabled,false);assert.equal(h.input.readOnly,false);
 assert.equal(h.steer.disabled,false);
 h.state.currentSession={id:'cloud',source:'codex',codex_cloud:true};h.controller.updateInputBar();assert.equal(h.send.disabled,true);assert.equal(h.input.readOnly,true);
 h.state.currentSession={id:'local',source:'codex'};h.controller.updateInputBar();assert.equal(h.send.disabled,false);assert.equal(h.input.readOnly,false);
});
