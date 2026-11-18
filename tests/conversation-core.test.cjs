const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function fixture(extra={}) {
 const storage=new Map();
 const context={AbortController,URLSearchParams,localStorage:{getItem:k=>storage.get(k)},location:{search:'?ccc_popout=conversation&embed=app&conv=session-one'},...extra};
 context.window=context;
 vm.createContext(context);
 for(const name of ['state','core','defaults','preferences'])vm.runInContext(fs.readFileSync(require.resolve('../static/conversation-'+name+'.js'),'utf8'),context);
 const state={},helpers={};const core=context.CCCConversationCore.createController({state,helpers});Object.assign(state,core);
 const initializers=context.CCCConversationDefaults.createInitializers({state,helpers});
 return {context,state,core,initializers,storage};
}
test('live status follows the selected pane and replacement workspace panes',()=>{
 const h=fixture();const workspace=h.context.CCCConversationState.createWorkspace();
 h.context.CCCConversationState.bindLegacyProperty(h.state,workspace,'currentSession');
 h.state.currentSession={id:'one'};h.state.liveStatus={forSessionId:'one',live:true};assert.equal(h.core.liveStatusMatchesOpenConv(),true);
 workspace.panes=[h.context.CCCConversationState.createPane('replacement')];
 h.state.currentSession={id:'two'};assert.equal(h.core.liveStatusMatchesOpenConv(),false);
 h.state.liveStatus={forSessionId:'two'};assert.equal(h.core.liveStatusMatchesOpenConv(),true);
});
test('scoped requests abort together and the next selection receives a fresh signal',()=>{
 const h=fixture();const first=h.core.convScopeSignal();assert.equal(first.aborted,false);assert.equal(h.core.convScopeSignal(),first);
 h.core.abortConvScopedRequests();assert.equal(first.aborted,true);
 const next=h.core.convScopeSignal();assert.notEqual(next,first);assert.equal(next.aborted,false);
});
test('process chrome is safe before the legacy current-session property is bound',()=>{
 const pill={innerHTML:'old'};const document={getElementById:()=>null,querySelector:()=>pill};
 const h=fixture({document});Object.defineProperty(h.state,'currentSession',{get:()=>h.context.currentSession});
 h.core.updateConvProcessIndicator();assert.equal(pill.innerHTML,'');
});
test('transient proxy failures retry the same durable request and retire old bodies',async()=>{
 const calls=[],canceled=[];let attempt=0;const h=fixture({fetch:async(url,options)=>{calls.push({url,options});const n=attempt++;return {status:n<2?502:200,body:{cancel:async()=>canceled.push(n)}};},setTimeout:fn=>queueMicrotask(fn)});
 const options={method:'POST',body:JSON.stringify({delivery_id:'test-delivery',text:'Submitted message'})};
 const response=await h.core.fetchInjectWithRetry('/api/inject-input',options);
 assert.equal(response.status,200);assert.equal(calls.length,3);assert.deepEqual(canceled,[0,1]);assert.ok(calls.every(call=>call.options===options));
});
test('canonical mode initializers and workspace factories retain independent mutable state',()=>{
 const h=fixture();h.state._bootUrlParams=h.initializers._bootUrlParams();
 assert.equal(h.initializers.CONV_POPOUT_MODE(),true);assert.equal(h.initializers.CONV_POPOUT_TARGET(),'session-one');
 const one=h.initializers.splitState(),two=h.initializers.splitState();one.panes[0].pendingSends.push('draft');assert.equal(two.panes[0].pendingSends.length,0);
 h.state._bootUrlParams=new URLSearchParams('?conv=other');assert.equal(h.initializers.CONV_POPOUT_MODE(),false);assert.equal(h.initializers.CONV_POPOUT_TARGET(),'other');
});
test('persisted voice preferences use the shared limits and fallback',()=>{
 const h=fixture();Object.assign(h.state,{_TTS_RATE_MIN:0.8,_TTS_RATE_MAX:1.25,_TTS_RATE_DEFAULT:1.25});
 const preferences=h.context.CCCConversationPreferences.createController({state:h.state,helpers:{}});
 h.storage.set('ccc-tts-rate','1.1');assert.equal(preferences._readPersistedTtsRate(),1.1);
 h.storage.set('ccc-tts-rate','99');assert.equal(preferences._readPersistedTtsRate(),1.25);
});

test('bulk row controls preserve first-match precedence and reread changing rows',()=>{
 const removed=[];
 const row=id=>({dataset:{sessionId:id},querySelector:()=>null,
  querySelectorAll:selector=>selector.includes('popout-row-session') ? [{remove:()=>removed.push(id)}] : []});
 const mounted=['duplicate','archive-only','missing'].map(row);
 const h=fixture({document:{querySelectorAll:()=>mounted},innerWidth:1440});
 const first={session_id:'duplicate',archived:true,pending_spawn:true};
 const fallback={session_id:'archive-only',archived:true};
 Object.assign(h.state,{conversationsData:[first,{...first,pending_spawn:false}],
  archiveData:[{...first,pending_spawn:false},fallback],
  _rowQuickActionSending:new Set(),_rowQuickActionFeedback:new Map(),
  _rowInvestigationChecksInFlight:4,_ultraFresh:{entries:new Map()}});
 h.core.syncRowQuickActions();
 assert.deepEqual(removed,['duplicate','missing'],'first live duplicate wins over later live and archive rows');
 removed.length=0;
 first.pending_spawn=false;
 fallback.pending_spawn=true;
 h.core.syncRowQuickActions();
 assert.deepEqual(removed,['archive-only','missing'],'in-place edits are visible on the next update');
 removed.length=0;
 h.state.conversationsData=[{...first,pending_spawn:true}];
 h.state.archiveData=[];
 h.core.syncRowQuickActions();
 assert.deepEqual(removed,['duplicate','archive-only','missing'],'replaced arrays cannot leave retained controls');
});
