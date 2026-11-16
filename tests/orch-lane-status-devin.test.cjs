// CCC-1203: a Devin lane is is_live whenever CCC's shared `devin acp` conn
// could steer it, so a finished one-shot run must not read "working".
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const app=require('./legacy-dashboard-source.cjs');
function helpers(){
 const start=app.indexOf('  const ORCH_BOOT_GRACE_S =');
 assert.notEqual(start,-1,'orch lane status helpers exist');
 const end=app.indexOf('  // Lane label:',start);
 const ctx=vm.createContext({Date,_liveSessionsActivityFetchedAt:0,_liveSessionsActivityLast:{sessions:{}}});
 vm.runInContext(app.slice(start,end)+';this.orchLaneStatus=orchLaneStatus;',ctx);return ctx;
}
const old=Date.now()/1000-3600;
const row=(extra)=>Object.assign({session_id:'devincli-x',engine:'devin',state:'idle',is_live:true},extra);
test('idle ACP devin lane past boot grace is done, not working',()=>{
 assert.equal(helpers().orchLaneStatus(row({acp_status:'idle'}),null,old),'done');
});
test('running ACP devin lane is working',()=>{
 assert.equal(helpers().orchLaneStatus(row({acp_status:'running'}),null,old),'working');
});
test('headless devin run (no ACP status) keeps is_live as the working signal',()=>{
 assert.equal(helpers().orchLaneStatus(row({acp_status:null}),null,old),'working');
 assert.equal(helpers().orchLaneStatus(row({}),null,old),'working');
});
test('a just-spawned idle ACP devin lane stays working through boot grace',()=>{
 assert.equal(helpers().orchLaneStatus(row({acp_status:'idle'}),null,Date.now()/1000-5),'working');
});
