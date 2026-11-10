'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the public open/render path without boot timers or a live server.
// Browser layout verification is separate from these copy/branch regressions.
async function render(items, notice = { pending: false, items: [] }) {
  const nodes = new Map();
  function element() {
    return {
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener() {}, setAttribute() {}, focus() {},
      querySelector(selector) {
        if (!nodes.has(selector)) nodes.set(selector, element());
        return nodes.get(selector);
      },
    };
  }
  const state = { ok: true, items, notice, engines_present: { claude: true, codex: true } };
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/config-consent.js'), 'utf8'), {
    window,
    document: {
      readyState: 'loading', addEventListener() {}, getElementById() { return null; },
      createElement: element, body: { appendChild() {} },
    },
    fetch: async () => ({ ok: true, json: async () => state }),
  });
  await window.cccConfigConsent.open({ mode: 'manage' });
  return {
    summary: nodes.get('[data-role="cfgc-brief"]').innerHTML,
    intro: nodes.get('[data-role="cfgc-intro"]').innerHTML,
  };
}

function instruction(engine, status = 'pending') {
  return { id: `${engine}-question-guidance`, kind: 'instructions', status,
    needs_review: true, engines: [engine], title: 'Question guidance' };
}

test('instruction-only consent counts both blocks and uses accurate config wording', async () => {
  const { summary, intro } = await render([instruction('claude'), instruction('codex')]);
  assert.match(summary, /CCC will add 2 instruction blocks to your Claude Code and Codex setup/);
  assert.match(summary, /preserves your other config/);
  assert.match(summary, /CCC guidance/);
  assert.match(intro, /Review the changes CCC proposes for your agent config/);
  assert.doesNotMatch(summary + intro, /undefined|only adds its own files|few hooks and skills/);
});

test('single changed instruction uses singular and update wording', async () => {
  const { summary } = await render([instruction('codex', 'changed')]);
  assert.match(summary, /CCC has updated 1 instruction block in your Codex setup/);
  assert.doesNotMatch(summary, /1 instruction blocks|undefined/);
});

test('mixed consent retains skills, hooks, WatchTower, and instructions', async () => {
  const { summary } = await render([
    instruction('claude'),
    { id: 'skill:example', status: 'pending', needs_review: true },
    { id: 'claude-hooks', hook_count: 8, status: 'pending', needs_review: true },
    { id: 'watchtower-skills', status: 'pending', needs_review: true },
  ]);
  assert.match(summary, /1 skill, 8 hooks, the WatchTower skills and 1 instruction block/);
  assert.doesNotMatch(summary, /undefined/);
});

test('unrecognized config kind still has a meaningful summary', async () => {
  const { summary } = await render([{ id: 'future-config', kind: 'other', status: 'pending', needs_review: true }]);
  assert.match(summary, /CCC will add 1 config change/);
  assert.doesNotMatch(summary, /undefined/);
});

test('existing-install notice remains readable without pending review', async () => {
  const { summary } = await render([{ id: 'old', title: 'Existing config', status: 'enabled', needs_review: false }],
    { pending: true, items: [{ id: 'old' }] });
  assert.match(summary, /Earlier versions of CCC added 1 item/);
  assert.doesNotMatch(summary, /undefined/);
});

test('first load defers pending config consent without acknowledging it', async () => {
  const requests = [];
  let created = 0;
  const state = { ok: true, items: [instruction('claude')], notice: { pending: true } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/config-consent.js'), 'utf8'), {
    window: { cccFirstRunLoad: true },
    document: {
      readyState: 'complete', addEventListener() {}, getElementById() { return null; },
      createElement() { created++; throw new Error('Unexpected first-load dialog'); },
    },
    setInterval() {},
    fetch: async (url, options) => {
      requests.push([url, options?.method || 'GET']);
      return { ok: true, json: async () => state };
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(created, 0);
  assert.deepEqual(requests, [['/api/config-consent', 'GET']]);
});
