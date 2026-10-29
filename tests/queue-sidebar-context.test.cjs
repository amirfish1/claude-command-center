const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const QA=require('../static/queue-attention.js');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('  function _uxqTicketAttention(it) {');
const end=source.indexOf('  function _uxqWaitingOn(it)',start);
function project(item, health) {
 const ctx={window:{QueueAttention:QA},_uxqHealthCache:health,_uxqProjectKey:v=>String(v||'').toUpperCase()};
 vm.createContext(ctx);vm.runInContext(source.slice(start,end)+';this.project=_uxqTicketAttention',ctx);return ctx.project(item);
}
test('sidebar adapter distinguishes verified live work, remote claims, and queue pause',()=>{
 const health={wt_workers:[{worker_id:'worker-1',session_id:'session-1',queue:'TASK',alive:true}],queues:[{queue:'TASK',auto_drain:false}]};
 assert.equal(project({project:'TASK',status:'in_progress',claimed_by:'worker-1'},health).label,'working');
 const remote=project({project:'TASK',status:'in_progress',claimed_by:'remote-worker',pending_answer:{state:'delivered'}},health);
 assert.notEqual(remote.key,'answer-working');assert.match(remote.label,/awaiting worker/);
 assert.equal(project({project:'TASK',status:'open'},health).key,'paused');
 assert.notEqual(project({project:'TASK',status:'in_progress',claimed_by:'worker-1'}, {...health,wt_workers:[{...health.wt_workers[0],released_at:'2026-01-01'}]}).key,'working');
});
test('modal and sidebar rows share the contextual adapter',()=>{
 assert.match(source,/const attention = _uxqTicketAttention\(item\)/);
 assert.match(source,/const _uxqAttention = _uxqTicketAttention/);
});
