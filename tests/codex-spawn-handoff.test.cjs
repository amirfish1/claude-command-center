const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
function fn(name) {
  const start = source.indexOf('  function ' + name + '(');
  const end = source.indexOf('\n  }\n', start) + 5;
  assert.ok(start >= 0 && end > start, name);
  return source.slice(start, end);
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness(rows, selected = true) {
  const card = {id:'spawning-temp', source:'codex', pending_spawn:true};
  const calls = [], timers = [];
  const ctx = vm.createContext({
    Date, Map, Array, Object, String,
    currentConversation:selected ? card.id : 'other-session',
    pendingSpawns:new Map([['spawn-1',card]]), conversationsData:[card],
    fetch:async () => { calls.push('probe'); return {ok:true, json:async () => rows}; },
    refreshConversationList:() => { calls.push('refresh'); return new Promise(() => {}); },
    refreshArchiveData:async () => { calls.push('archive'); },
    rebindCurrentSelectionToRealCard:real => {
      calls.push(['bind',real.id]); ctx.currentConversation = real.id;
    },
    resolvePlaceholderPanes:(ids, real) => calls.push(['panes',Array.from(ids),real.id]),
    renderArchiveList:() => calls.push('render'),
    $convSearch:{value:''},
    markPendingSpawnNotAcknowledged:() => calls.push('failed'),
    _watchPendingSpawnRegistration:() => calls.push('generic-watch'),
    setTimeout:(fn,ms) => timers.push({fn,ms}),
    isSpawnLogPlaceholderSource:() => true,
    renderSidebar() {}, filterConversations:() => [],
  });
  vm.runInContext(['_pendingSpawnStillWaiting','_watchCodexSpawnRegistration','adoptPendingSpawnPid']
    .map(fn).join('\n'), ctx);
  return {ctx,card,calls,timers};
}

test('binds a confirmed thread before a full refresh that never resolves', async () => {
  const h = harness([{session_id:'new-session',spawn_id:'spawn-1'}]);
  h.ctx._watchCodexSpawnRegistration('spawn-1',h.card.id,'new-session');
  await flush();
  assert.equal(h.ctx.currentConversation,'new-session');
  assert.equal(h.card.pending_spawn,false);
  assert.equal(h.card._landedResolved,true);
  assert.deepEqual(h.calls.slice(0,2),['probe',['bind','new-session']]);
  assert.ok(h.calls.indexOf('refresh') > h.calls.indexOf('render'));
  assert.equal(h.timers.length,0,'no initial five-second delay');
});

test('normal Codex adoption starts the same watcher and F2 cannot start it twice', async () => {
  const h = harness([{session_id:'new-session',spawn_id:'spawn-1'}]);
  h.ctx.pendingSpawns.delete('spawn-1');
  h.ctx.pendingSpawns.set('temp',h.card);
  h.ctx.adoptPendingSpawnPid('temp','spawn-1','capture.log','new-session');
  h.ctx._watchCodexSpawnRegistration('spawn-1',h.card.id,'new-session');
  await flush();
  assert.equal(h.ctx.currentConversation,'new-session');
  assert.equal(h.calls.filter(c => c === 'probe').length,1);
});

test('a confirmed background pane can resolve without stealing the selected conversation', async () => {
  const h = harness([{session_id:'new-session'}],false);
  h.ctx._watchCodexSpawnRegistration('spawn-1',h.card.id,'new-session');
  await flush();
  assert.equal(h.ctx.currentConversation,'other-session');
  assert.equal(h.calls.some(c => Array.isArray(c) && c[0] === 'bind'),false);
  assert.ok(h.calls.some(c => Array.isArray(c) && c[0] === 'panes' && c[2] === 'new-session'));
});

test('missing or mismatched durable threads remain pending and retry', async () => {
  for (const rows of [[],[{session_id:'old-session',spawn_id:'spawn-1'}]]) {
    const h = harness(rows);
    h.ctx._watchCodexSpawnRegistration('spawn-1',h.card.id,'new-session');
    await flush();
    assert.equal(h.ctx.currentConversation,h.card.id);
    assert.equal(h.card.pending_spawn,true);
    assert.equal(h.calls.includes('refresh'),false);
    assert.equal(h.timers[0].ms,5000);
  }
});
