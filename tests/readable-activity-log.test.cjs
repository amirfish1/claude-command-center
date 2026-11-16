const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const app=require('./legacy-dashboard-source.cjs');
const index=fs.readFileSync('static/index.html','utf8');
function helpers(){
 const start=app.indexOf('  // Readable activity log:');
 assert.notEqual(start,-1,'readable log helpers exist');
 const end=app.indexOf('  // ── Rail Log pane',start);
 const ctx=vm.createContext({Date,escapeHtml:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),escapeAttr:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),_activityLogTimestampLocal:s=>s});
 vm.runInContext(app.slice(start,end),ctx);return ctx;
}
const event=(verb,detail='',category='app-server',ts='2026-09-05 19:00:00 UTC')=>({verb,detail,category,ts});
test('severity follows outcomes, not words in message previews or successful-looking requests',()=>{
 const h=helpers();
 for(const [e,level] of [[event('TIMEOUT'),'warning'],[event('LATE'),'warning'],[event('CCC-PEER-AUTH-FAIL'),'error'],[event('FAILED'),'error'],[event('TITLED','title=Fix ERROR handling'),'info'],[event('INJECT','text="ok"','inject'),'info'],[event('UDS','receipt=delivered text="error"','inject'),'success'],[event('UDS','receipt=unknown text="receipt=delivered"','inject'),'warning'],[event('CCC-PEER-'),'warning']]){
  assert.equal(h._readableLogPresentation(e).level,level,JSON.stringify(e));
 }
});
test('known noisy records receive useful summaries with raw records intact',()=>{
 const h=helpers();const e=event('TIMEOUT','method=thread/list id=9076 no reply within 3s (real); watching for late arrival');
 assert.match(h._readableLogPresentation(e).headline,/Session list.*3s/);
 const groups=h._readableLogGroups([e]);const html=h._readableLogGroupHtml(groups[0],false);
 assert.ok(html.includes('id=9076'));assert.ok(html.includes('TIMEOUT'));
 assert.ok(html.includes('<details'));assert.ok(html.includes('Warning'));
});
test('only adjacent matching bursts combine, with every raw occurrence retained',()=>{
 const h=helpers();const events=[event('TIMEOUT','method=thread/list id=1 no reply within 3s','app-server','2026-09-05 19:00:00 UTC'),event('TIMEOUT','method=thread/list id=2 no reply within 3s','app-server','2026-09-05 19:00:03 UTC'),event('TITLED','title=Work','autotitle','2026-09-05 19:00:04 UTC'),event('TIMEOUT','method=thread/list id=3 no reply within 3s','app-server','2026-09-05 19:00:05 UTC')];
 const groups=h._readableLogGroups(events);
 assert.deepEqual(Array.from(groups,g=>g.events.length),[1,1,2]);
 assert.deepEqual(Array.from(groups[2].events,e=>e.detail.match(/id=(\d+)/)[1]),['2','1']);
});
test('a successful spawn absorbs its immediately preceding request',()=>{
 const h=helpers();const events=[
  event('REQUEST',"engine='antigravity' prompt=\"Build a thing\"",'spawn','2026-09-05 19:00:00 UTC'),
  event('SPAWN','engine=antigravity session=abc123','spawn','2026-09-05 19:00:01 UTC'),
 ];
 const groups=h._readableLogGroups(events);
 assert.equal(groups.length,1);
 assert.equal(groups[0].presentation.level,'success');
 assert.equal(groups[0].presentation.headline,'Agent started');
 assert.deepEqual(Array.from(groups[0].events,e=>e.verb),['SPAWN','REQUEST']);
});
test('unrelated failures and bursts separated by time stay separate',()=>{
 const h=helpers();
 assert.equal(h._readableLogGroups([event('FAILED','error=One'),event('FAILED','error=Two')]).length,2);
 assert.equal(h._readableLogGroups([event('TIMEOUT','method=thread/list id=1','app-server','2026-09-05 18:00:00 UTC'),event('TIMEOUT','method=thread/list id=2','app-server','2026-09-05 19:00:00 UTC')]).length,2);
});
test('summaries and expanded details escape untrusted log text',()=>{
 const h=helpers();const groups=h._readableLogGroups([event('TITLED','title=<img src=x onerror=alert(1)>','autotitle')]);
 const html=h._readableLogGroupHtml(groups[0],true);
 assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'));
});
test('rail log exposes one control that toggles every visible entry',()=>{
 assert.match(index,/id="railLogToggleAll"/);
 assert.match(app,/function _setRailLogEntriesOpen\(open\)/);
 assert.match(app,/button\.textContent = open \? 'Collapse all' : 'Expand all'/);
 const render=app.slice(app.indexOf('  function _renderRailLogPane()'),app.indexOf('  async function refreshRailLogPane()'));
 assert.match(render,/if \(!events\.length\) \{[^}]*_syncRailLogToggleAll\(\);/);
});
test('CCC-1189: inject rows lead with the text, then from -> to', ()=>{
 const h=helpers();
 const p=h._readableLogPresentation(event('INJECT','session=3084fe5f-e761-4691-9cb4-e3a26626a8fc mode=send source=composer idem=inject:x wt_origin=False via=acp-prompt queued=True text="this is me sshing"','inject'));
 assert.equal(p.headline,'→ “this is me sshing”');
 assert.equal(p.origin,'From Dashboard composer → to 6626a8fc · queued');
 const html=h._readableLogGroupHtml(h._readableLogGroups([event('INJECT','session=abc source=api text="hi <b>"','inject')])[0],false);
 assert.ok(html.includes('From API → to abc'));
 assert.ok(html.includes('“hi &lt;b&gt;”'));
 const rej=h._readableLogPresentation(event('INJECT_REJECT','session=s1 source=wt code=busy error="nope" text="x"','inject'));
 assert.equal(rej.level,'error');
 assert.match(rej.origin,/From WatchTower → to s1 · rejected/);
});
test('app-server timeouts, held messages and recoveries read in plain words', ()=>{
 const h=helpers();
 const t=h._readableLogPresentation(event('TIMEOUT','method=initialize id=1 no reply within 10s (real); watching for late arrival'));
 assert.equal(t.headline,'Codex app-server did not start within 10s');
 assert.match(t.origin,/falls back to the slower CLI/);
 const q=h._readableLogPresentation(event('Q_HELD','session=35fa97b3-67dd-43fd-8a37-01acdd16402b reason=orphaned_spawn — queued terminal input held, will keep retrying every 5s','inject'));
 assert.equal(q.headline,'Message waiting: session is from before the last CCC restart');
 assert.equal(q.origin,'To dd16402b · retrying every 5s');
 assert.equal(q.level,'warning');
 const r=h._readableLogPresentation(event('RECOVER','session=35fa97b3-67dd-43fd-8a37-01acdd16402b pid=9287 held=396s log_silent=122s reason=orphaned_spawn — retiring','inject'));
 assert.match(r.headline,/Restarted an unresponsive session/);
 assert.equal(r.origin,'To dd16402b · message waited 396s');
});
test('an app-server that dies at startup reads as a crash with its stderr',()=>{
 const h=helpers();
 const p=h._readableLogPresentation(event('EXITED','method=initialize id=7 app-server exited after 0.04s with no reply (exit=1) stderr=Error: spawn failed errno -88'));
 assert.equal(p.headline,'Codex app-server crashed at startup');
 assert.equal(p.level,'error');
 assert.match(p.origin,/errno -88/);
});
test('worker stale check is hidden when current and explained when stale', ()=>{
 const h=helpers();
 const cur=event('stale?','WORKER_STALE_CHECK old_stale=False new_stale=False active=0 old_hash=a new_hash=b disk_new_hash=b','worker');
 assert.equal(h._readableLogPresentation(cur).hidden,true);
 assert.equal(h._readableLogGroups([cur]).length,0);
 const stale=h._readableLogPresentation(event('stale?','WORKER_STALE_CHECK old_stale=True new_stale=False active=0','worker'));
 assert.equal(stale.headline,'Worker is running older code than what is on disk');
 assert.equal(stale.level,'warning');
 const shadow=h._readableLogPresentation(event('stale?','WORKER_STALE_CHECK old_stale=False new_stale=True active=0','worker'));
 assert.match(shadow.headline,/Experimental check/);
});
test('every expanded occurrence has a copy button carrying the raw event', ()=>{
 const h=helpers();
 const html=h._readableLogGroupHtml(h._readableLogGroups([event('TIMEOUT','method=initialize id=1 no reply within 10s')])[0],true);
 assert.ok(html.includes('class="activity-log-copy"'));
 assert.ok(html.includes('data-copy-text="2026-09-05 19:00:00 UTC  app-server  TIMEOUT  method=initialize id=1 no reply within 10s"'));
});
test('rows name the session instead of a bare id, and interrupt asks read plainly', ()=>{
 const h=helpers();
 h._logSessionName=sid=>sid==='32faca19-397a-4328-a30d-613c972a1e33'?'this devin session is stuck.':'';
 const d=h._readableLogPresentation(event('DISMISS','sid=32faca19-397a-4328-a30d-613c972a1e33 source=model-switch','interrupt-ask'));
 assert.equal(d.headline,'Restart request dismissed; the model switch applies when the session next resumes');
 assert.equal(d.origin,'this devin session is stuck.');
 const a=h._readableLogPresentation(event('ASK','sid=32faca19-397a-4328-a30d-613c972a1e33 source=model-switch — reason','interrupt-ask'));
 assert.equal(a.headline,'CCC asked to restart a session to apply a model switch');
 const g=h._readableLogPresentation(event('RETIRE','sid=ffff0000-0000-0000-0000-00001234abcd reason=x','retire'));
 assert.equal(g.origin,'1234abcd · retire · RETIRE');
});
