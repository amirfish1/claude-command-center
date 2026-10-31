// Behavioral checks for job classification, filtering, and cached-data honesty.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/jobs-tab.js'), 'utf8');
async function mount(jobs, {offline = false, fail = false} = {}) {
  const handlers = {};
  const host = {innerHTML: '', scrollTop: 0, firstChild: null, querySelectorAll: () => [],
    addEventListener: (name, cb) => { handlers[name] = cb; }};
  const document = {activeElement: null, getElementById: id => id === 'sidebarJobsHost' ? host : null, querySelector: () => null};
  const window = {};
  vm.runInNewContext(source, {document, window, localStorage: {getItem: () => null, setItem: () => {}},
    setTimeout: () => {}, setInterval: () => {}, Date, Intl,
    fetch: async () => { if (fail) throw Error('offline'); return {ok:true,json:async()=>({ok:true,local_host:'hermes',hosts:{hermes:{status:offline?'offline':'online'}},jobs})}; }});
  window.CCCJobsTab.mount();
  await new Promise(resolve => setImmediate(resolve));
  return {host, click(attr, value='') { handlers.click({target:{closest: sel => sel === '[' + attr + ']' ? {getAttribute:()=>value} : null}}); },
    input(value) {handlers.input({target:{matches:()=>true,value}});},
    change(attr,value) {handlers.change({target:{matches:sel=>sel==='['+attr+']',value}});} };
}
function job(id, extra={}) {return {id, name:id, host:'hermes', project:'Example', status:'ok', enabled:true, schedule:'Daily', next_run_at:new Date(Date.now()+3600000).toISOString(), ...extra};}
test('overview surfaces problems before scheduled work and keeps raw output in details',async()=>{
  const {host}=await mount([job('healthy'),job('broken',{status:'failed',outcome:'RAW_SECRET_SHAPED_OUTPUT',outcome_kind:'output'})]);
  assert.ok(host.innerHTML.indexOf('Needs attention') < host.innerHTML.indexOf('Next scheduled'));
  assert.match(host.innerHTML,/Failed/); assert.doesNotMatch(host.innerHTML,/RAW_SECRET_SHAPED_OUTPUT/);
});
test('upcoming separates frequent jobs and excludes disabled jobs',async()=>{
 const ui=await mount([job('frequent',{timeline:{kind:'interval',interval_s:60}}),job('daily'),job('disabled-task',{enabled:false})]);
 ui.click('data-jobs-sort','upcoming');
 assert.match(ui.host.innerHTML,/Repeats throughout the day/);
 assert.doesNotMatch(ui.host.innerHTML,/data-job-id="disabled-task"/);
 assert.ok(ui.host.innerHTML.indexOf('data-job-id="daily"') < ui.host.innerHTML.indexOf('data-job-id="frequent"'));
});
test('all jobs supports text, project, and status filtering with reset',async()=>{
 const ui=await mount([job('alpha'),job('beta',{status:'unknown',project:'Second'})]);
 ui.click('data-jobs-sort','all');ui.input('beta');assert.doesNotMatch(ui.host.innerHTML,/data-job-id="alpha"/);
 ui.change('data-jobs-project','Example');assert.match(ui.host.innerHTML,/No matching jobs/);
 ui.click('data-jobs-clear');ui.change('data-jobs-filter','unknown');assert.match(ui.host.innerHTML,/Unverified/);assert.doesNotMatch(ui.host.innerHTML,/data-job-id="alpha"/);
});
test('cached, estimated, and overdue timestamps never imply fresh certainty',async()=>{
 const ui=await mount([job('old',{next_run_at:'2000-01-01T00:00:00Z'}),job('estimate',{next_run_estimated:true})],{offline:true});
 assert.match(ui.host.innerHTML,/last known results/);assert.match(ui.host.innerHTML,/Awaiting schedule update/);assert.match(ui.host.innerHTML,/Est. next/);
});
test('feed failure offers recovery and untrusted names are escaped',async()=>{
 const fail=await mount([],{fail:true});assert.match(fail.host.innerHTML,/Try again/);
 const ui=await mount([job('<script>alert(1)</script>')]);assert.doesNotMatch(ui.host.innerHTML,/<script>/);assert.match(ui.host.innerHTML,/&lt;script&gt;/);
});
