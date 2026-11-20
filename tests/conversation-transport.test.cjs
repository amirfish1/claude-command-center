const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture(options = {}) {
  const panes = [{id:'left',conversationId:'one',lastLine:0}, {id:'right',conversationId:'two',lastLine:5}];
  const timers = new Map(); let timerId = 0;
  const streams = [], rendered = [], ready = [], views = panes.map(() => ({innerHTML:'',querySelector:()=>null}));
  class EventSource {
    constructor(url) { this.url=url; this.listeners={}; streams.push(this); }
    addEventListener(name, callback) { this.listeners[name]=callback; }
    close() { this.closed=true; }
  }
  const context = {EventSource, JSON, performance, AbortSignal,
    setTimeout(fn,ms) { timers.set(++timerId,{fn,ms}); return timerId; },
    clearTimeout(id) { timers.delete(id); }, window:{}, fetch:async()=>{throw new Error('unexpected network');}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/conversation-transport.js'),'utf8'),context);
  const state = {splitState:{panes,activeIndex:0},conversationsData:[],archiveData:[],sessionSourceByConv:{},
    codexSpawnPreviews:new Set(),claudeSpawnAwaitingFirstPaint:new Map(),_perfConvOpen:null,
    CONV_CACHE_PAINT_HOLD_MS:0,CONV_CACHE_READ_BUDGET_MS:0,
    escapeHtml:text=>text,renderConversationEvents(events,pid,options) { rendered.push({events,pid,options}); return true; }};
  Object.defineProperties(state,{
    currentConversation:{get:()=>panes[state.splitState.activeIndex].conversationId},
    currentSession:{get:()=>({id:state.currentConversation})},
    convLastLine:{get:()=>panes[state.splitState.activeIndex].lastLine,set:value=>panes[state.splitState.activeIndex].lastLine=value},
    convEventSource:{get:()=>panes[state.splitState.activeIndex].eventSource,set:value=>panes[state.splitState.activeIndex].eventSource=value},
  });
  const network = {request:options.load || (async()=>({events:[{type:'assistant',text:'Fresh',line:8}],last_line:8,first_line:1}))};
  const helpers = {activePaneId:()=>panes[state.splitState.activeIndex].id,paneByPaneId:pid=>panes.find(p=>p.id===pid),
    paneIndexByPaneId:pid=>panes.findIndex(p=>p.id===pid), getConvViewForPane:pid=>views[panes.findIndex(p=>p.id===pid)],
    _refreshTokenEconomizerAnnotation(){},ffcInvalidate(){},fetchSessionUsage(){},
    _contEnsureSegments:()=>[],_contNextParentForPane:()=>'',convTailLines:()=>240,convFirstOpenLines:()=>32,
    _takePrefetchedConversationTail:()=>null,_convCacheEligible:()=>false,
    _fetchConvJsonTimed:(...args)=>network.request(...args),
    noteAppEmbedDrawn:(...args)=>ready.push(args),_flushAfterConvTailAfterPaint(){},
    convPaneElById:()=>({classList:{toggle(){}}}),restorePendingSendEchoes(){},
    spawnStatsMark(){},renderSpawnStatsPanel(){},_convCachePutSoon(){},
    ...options.pending,
  };
  // The constructor reads stable service functions; live values remain getters.
  const controller = context.window.CCCConversationTransport.createController({state,helpers});
  return {panes,state,helpers,controller,streams,timers,views,rendered,ready,network};
}

test('stream renders into its owning pane and restores focus even when rendering fails',()=>{
  const h=fixture();h.controller.startConvStream('left');h.state.splitState.activeIndex=1;
  const source=h.streams[0];source.onmessage({data:JSON.stringify({events:[{type:'assistant',line:6}],last_line:6})});
  h.state.renderConversationEvents=()=>{throw new Error('renderer failed');};
  source.onmessage({data:JSON.stringify({events:[{type:'assistant',line:7}],last_line:7})});
  assert.equal(h.state.splitState.activeIndex,1);
  assert.equal(h.panes[0].lastLine,6);
  assert.equal(h.rendered[0].pid,'left');assert.equal(h.rendered[0].options.queueSnapshot,false);
  assert.equal(h.panes[0].lastLine,6);assert.equal(h.state.splitState.activeIndex,1);
  h.state.renderConversationEvents=()=>{throw new Error('render failed');};
  source.onmessage({data:JSON.stringify({events:[{type:'assistant',line:7}],last_line:7})});
  assert.equal(h.state.splitState.activeIndex,1);assert.equal(h.panes[0].lastLine,6);
  h.controller.stopConvStream('left');assert.equal(source.closed,true);assert.equal(h.timers.size,0);
});

test('stale stream cannot paint or reset a newly selected conversation',()=>{
  const h=fixture();h.controller.startConvStream('left');const source=h.streams[0];h.panes[0].conversationId='new';
  h.state.selectConversation=()=>assert.fail('stale source reset');
  source.onmessage({data:JSON.stringify({events:[{type:'assistant',line:6}],last_line:6})});
  source.listeners.source_reset();assert.equal(h.rendered.length,0);assert.equal(h.panes[0].lastLine,0);
  h.controller.stopConvStream('left');
});

test('provisional Antigravity cursor requests a reset when the native source takes over', async () => {
  const h = fixture({load: async () => ({
    events: [{type: 'user_text', line: 1}], last_line: 5, antigravity_provisional: true,
  })});
  await h.controller.fetchConversationEvents('left');
  h.controller.startConvStream('left');
  const source = h.streams[0];
  assert.equal(source.url, '/api/conversations/one/stream?after=5&antigravity_provisional=1');
  const reopened = [];
  h.state.selectConversation = (...args) => reopened.push(args);
  source.listeners.source_reset();
  assert.equal(source.closed, true);
  assert.deepEqual(reopened, [['one', 'left']]);
});

test('selected Antigravity spawn retries its string PID until the native ID lands', async () => {
  const card = {id: 'spawning-temp', source: 'antigravity', spawn_pid: '123'};
  const reads = [];
  const pending = fixture({pending: {
    resolvedSpawnConversationId: id => id,
    isSpawnLogPlaceholderSource: source => source === 'antigravity',
    loadCodexLog: async row => {
      reads.push(row.spawn_pid);
      if (reads.length === 2) row._landedResolved = true;
    },
    stopCodexLogPoller() {}, _pollerOff: () => false, _pollerTick() {},
  }});
  pending.panes[0].conversationId = card.id;
  pending.state.conversationsData.push(card);
  await pending.controller.fetchConversationEvents('left');
  assert.deepEqual(reads, ['123']);
  const timer = pending.state.codexLogPoller;
  await pending.timers.get(timer).fn();
  assert.deepEqual(reads, ['123', '123']);
  assert.equal(pending.state.codexLogPoller, timer);
});

test('fresh fetch reports ready only after the shared renderer succeeds',async()=>{
  const h=fixture();await h.controller.fetchConversationEvents('left');
  assert.equal(h.rendered.length,1);assert.equal(h.rendered[0].options.initialLoad,true);
  assert.deepEqual(h.ready,[['one',false]]);assert.equal(h.panes[0].lastLine,8);
  h.panes[0].lastLine=0;h.state.renderConversationEvents=()=>false;h.ready.length=0;
  await h.controller.fetchConversationEvents('left');assert.deepEqual(h.ready,[]);assert.equal(h.panes[0].lastLine,0);
});

 test('failed fetch cannot report a fresh ready pane',async()=>{
  const failed=fixture();failed.state.renderConversationEvents=()=>{throw new Error('render failed');};
  await failed.controller.fetchConversationEvents('left');
  assert.deepEqual(failed.ready,[]);assert.match(failed.views[0].innerHTML,/Failed to load conversation/);
  const stale=fixture();const pending=stale.controller.fetchConversationEvents('left');
  stale.panes[0].conversationId='replacement';await pending;
  assert.deepEqual(stale.ready,[]);assert.equal(stale.rendered.length,0);
});

test('late success or failure cannot replace a newly selected pane',async()=>{
  for (const fail of [false,true]) {
    let resolve,reject;
    const pending=new Promise((yes,no)=>{resolve=yes;reject=no;});
    const h=fixture({load:()=>pending});const read=h.controller.fetchConversationEvents('left');
    h.panes[0].conversationId='new-selection';h.views[0].innerHTML='New conversation';
    if(fail)reject(new Error('Old request failed'));
    else resolve({events:[{type:'assistant',text:'Old response'}],last_line:9});
    await read;assert.equal(h.views[0].innerHTML,'New conversation');
    assert.equal(h.rendered.length,0);assert.deepEqual(h.ready,[]);
  }
});
