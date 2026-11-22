const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
require('../static/session-origin.js');
const {describe, sidebarHtml} = globalThis.CCCSessionOrigin;

test('helper prompt content does not become an invented parent', () => {
  const o = describe({id:'child', session_cwd:'/tmp/te-side-example',
    first_message:'You monitor an AI coding agent that is partway through ONE task in a software repo. Recent process digest: unrelated task title'});
  assert.equal(o.launcher, 'TokenEconomizer');
  assert.equal(o.status, 'Inferred');
  assert.equal(o.purpose, 'Check agent progress');
  assert.equal(o.parentId, '');
  assert.match(o.note, /does not establish a parent/);
});

test('missing parent is not treated as missing launcher', () => {
  const o = describe({id:'direct', spawned_via:'ui'});
  assert.equal(o.launcher, 'CCC dashboard');
  assert.equal(o.status, 'Recorded');
  assert.equal(o.parentId, '');
  assert.match(o.note, /Direct launches may have no parent/);
});

test('native creating client is recorded without guessing the launch interface', () => {
  const ccc = describe({id:'one', launch_originator:'claude-command-center'});
  assert.equal(ccc.launcher, 'CCC');
  assert.equal(ccc.status, 'Recorded');
  assert.doesNotMatch(ccc.launcher, /dashboard|Terminal/);
  const worker = describe({id:'two', spawned_via:'watchtower', launch_originator:'watchtower_app_worker'});
  assert.equal(worker.status, 'Recorded');
  assert.match(worker.evidence, /Transcript records/);
  const exec = describe({id:'three', launch_originator:'codex_exec'});
  assert.equal(exec.launcher, 'Codex exec');
  assert.match(exec.evidence, /calling tool is not identified/);
  assert.equal(exec.parentId, '');
});

test('unknown, explicit caller, parent and continuation evidence stay distinct', () => {
  assert.equal(describe({id:'one', thread_source:'user', source:'codex'}).status, 'Unknown');
  assert.equal(describe({id:'one', spawn_caller:'helper.py'}).launcher, 'helper.py');
  assert.equal(describe({id:'child', parent_session_id:'parent'}).parentId, 'parent');
  const continuation = describe({id:'new', continued_from_session_id:'old'});
  assert.equal(continuation.continuedId, 'old');
  assert.equal(continuation.parentId, '');
  assert.equal(describe({id:'self', parent_session_id:'self'}).parentId, '');
});

test('helper classification uses directory evidence, not titles or prompt mentions', () => {
  assert.equal(describe({id:'one', display_name:'te-side-example', session_cwd:'/projects/app'}).tool, '');
  assert.equal(describe({id:'one', session_cwd:'/projects/project-te-side-example'}).tool, '');
  assert.equal(describe({id:'two', slug:'-tmp-te-side-example'}).tool, 'tokeneconomizer');
  assert.equal(describe({id:'three', session_cwd:'/tmp/wt-model-example'}).tool, 'watchtower-model');
});

test('sidebar labels disclose helper purpose without duplicating task titles', () => {
  const html = sidebarHtml({id:'helper', spawned_via:'tokeneconomizer', spawned_kind:'loop-check', display_name:'Unrelated title'});
  assert.match(html, /TokenEconomizer · loop check/);
  assert.doesNotMatch(html, /Unrelated title/);
  assert.equal(sidebarHtml({id:'ordinary', spawned_via:'ui'}), '');
  assert.equal(sidebarHtml({source:'backlog', spawned_via:'tokeneconomizer'}), '');
  assert.doesNotMatch(sidebarHtml({id:'helper',spawned_via:'tokeneconomizer',spawned_kind:'<script>alert(1)</script>'}), /<script>/);
});

test('opened-session metadata loads once, ignores stale replies and preserves disclosure state', async () => {
  const requests = [];
  const details = {dataset: {}, hidden: true, open: false};
  const summary = {};
  const button = {};
  const body = {replaceChildren() {}, querySelector() {return this.innerHTML.includes('session-origin-parent') ? button : null;}};
  const doc = {getElementById: id => ({railSessionOrigin: details, railSpawnedVia: summary, railSessionOriginBody: body})[id]};
  const context = vm.createContext({fetch(url) {return new Promise(resolve => requests.push({url, resolve}));}});
  vm.runInContext(fs.readFileSync(require.resolve('../static/session-origin.js'), 'utf8'), context);
  const api = context.CCCSessionOrigin;
  let opened;
  const options = {document: doc, openSession: id => {opened = id;}, findSession: () => ({display_name: 'Parent title'})};
  api.render({id: 'first'}, options);
  api.render({id: 'first'}, options);
  assert.equal(requests.length, 1);
  assert.match(summary.innerHTML, /Checking origin/);
  details.open = true;
  api.render({id: 'second', spawned_via: 'ui'}, options);
  assert.equal(details.open, false);
  requests[0].resolve({ok: true, json: async () => ({session_id: 'first', spawned_via: 'cli'})});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(summary.innerHTML, /CCC dashboard/);
  details.open = true;
  requests[1].resolve({ok: true, json: async () => ({
    session_id: 'second', spawned_via: 'watchtower', parent_session_id: 'parent',
    worker_origin: {role: 'docs-writer', ref: 'EXAMPLE-42'},
  })});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(summary.innerHTML, /WatchTower/);
  assert.equal(details.open, true);
  assert.match(body.innerHTML, /Parent title/);
  assert.match(body.innerHTML, /docs writer · EXAMPLE-42/);
  assert.equal(body.innerHTML.match(/WatchTower session-origin record\./g)?.length, 1);
  api.render({id: 'second', spawned_kind: 'review'}, options);
  assert.match(body.innerHTML, /WatchTower session-origin record\./);
  assert.match(body.innerHTML, /Helper kind saved in session metadata\./);
  button.onclick();
  assert.equal(opened, 'parent');
  api.render(null, options);
  assert.equal(details.hidden, true);
  assert.equal(details.open, false);
});
