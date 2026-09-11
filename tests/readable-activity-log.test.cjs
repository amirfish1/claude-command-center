const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const app=fs.readFileSync('static/app.js','utf8');
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
