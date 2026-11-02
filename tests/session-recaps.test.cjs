const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function load() {
  const window = {};
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/session-recaps.js'), 'utf8'), {
    window, document: { addEventListener() {} }, Date, Map, Set, JSON
  });
  return window.CCCSessionRecaps;
}

test('old owner waits get escaped context without changing their action', () => {
  const api = load();
  const row = { session_id: 'old', modified: Date.now() / 1000 - 3 * 86400,
    session_state: { did: 'Shipped <layout>', completion: { scope: { goal: 'Readable <rows>' } } } };
  const html = api.render(row, { kind: 'next', text: 'Check <rows>' });
  assert.match(html, /Recap · Readable &lt;rows&gt;/);
  assert.match(html, /Reported done:/);
  assert.match(html, /Check &lt;rows&gt;/);
  assert.doesNotMatch(html, /<layout>|<rows>/);
  assert.equal(api.render(row, { kind: 'working' }), '');
  assert.equal(api.render({ ...row, archived: true }, { kind: 'next' }), '');
  assert.equal(api.render({ ...row, modified: Date.now() / 1000 }, { kind: 'next' }), '');
});

test('recheck is an annotation that retains the recorded request and its uncertainty', () => {
  const html = load().contents({ status: 'checked', recap: { request: 'Reload CCC' },
    result: { goal: 'Sidebar cleanup', shipped: 'Reported shipped', needs_you: 'No specific decision recorded', finding: 'Generic visual check' } });
  assert.match(html, /Reload CCC/);
  assert.match(html, /No specific decision recorded/);
  assert.match(html, /saved conversation and linked-ticket evidence/);
  assert.match(load().contents({ status: 'unverified' }), /no automatic retry/);
});
