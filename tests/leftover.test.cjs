const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/leftover.js'), 'utf8');
const popupSource = fs.readFileSync(path.join(__dirname, '../static/popups.js'), 'utf8');

function load() {
  const storage = {};
  const notices = [];
  let delivers = true;
  const ctx = {
    window: { cccNotify: { enabled: () => true, show: item => { if (!delivers) return false; notices.push(item); return true; } } },
    localStorage: { getItem: key => storage[key] ?? null, setItem: (key, value) => { storage[key] = String(value); } },
    document: { readyState: 'loading', addEventListener: () => {}, getElementById: () => null },
    navigator: {}, console, Date, Number, setTimeout, clearTimeout, URLSearchParams,
  };
  vm.runInNewContext(popupSource, ctx);
  vm.runInNewContext(source, ctx);
  return { api: ctx.window.cccLeftover, popups: ctx.window.cccPopups, storage, notices,
    allow: id => { storage['ccc-popups-preview'] = id || 'leftover-notification'; },
    delivery: on => { delivers = on; } };
}

function row(changes = {}) {
  return { id: 'claude:default', engine: 'claude', account: 'default', label: 'Claude Max', available: true, stale: false,
    unlimited: false, percent_left: 60, projected_expiring_pct: 20, expiring_usd_estimate: 12.5,
    expiring_tokens_estimate: null, burn_pct_per_hour: 2, source: 'quota', hours_to_reset: 24,
    resets_at: Date.now() / 1000 + 86400, ...changes };
}

test('uses fixed headroom contract and exact thresholds without obsolete fields', () => {
  const { api } = load();
  assert.equal(api.isCandidate(row()), true);
  assert.equal(api.isCandidate(row({ account: 'team', id: 'claude:team' })), true);
  assert.equal(api.isCandidate(row({ hours_to_reset: 0 })), true);
  for (const changes of [
    { projected_expiring_pct: 19.9 }, { hours_to_reset: 24.1 }, { hours_to_reset: -1 },
    { stale: true }, { stale: null }, { available: false }, { unlimited: true },
    { projected_expiring_pct: null }, { projected_expiring_pct: NaN },
    { projected_expiring_pct: 101 }, { hours_to_reset: null }, { engine: 'free_router' }, { engine: 'unknown' },
  ]) assert.equal(api.isCandidate(row(changes)), false, JSON.stringify(changes));
});

test('approval requires a future reset in epoch seconds', () => {
  const { api } = load();
  assert.equal(api.resetIsCurrent(row()), true);
  for (const resets_at of [null, 0, NaN, Infinity, Date.now() / 1000 - 1, new Date(Date.now() + 86400000).toISOString()]) {
    assert.equal(api.resetIsCurrent(row({ resets_at })), false, String(resets_at));
  }
});

test('unknown dollars never become a zero estimate', () => {
  const { api } = load();
  assert.match(api.summary(row({ hours_to_reset: 9 })), /^You have about \$12.50 of Claude left that resets in 9h\. Put it to work\?$/);
  const text = api.summary(row({ expiring_usd_estimate: null, hours_to_reset: 0.5 }));
  assert.match(text, /20%.*30m/);
  assert.doesNotMatch(text, /\$/);
  assert.match(api.summary(row({ expiring_usd_estimate: -5 })), /20%/);
});

test('explicit subscription engine and stable dedupe, never free runtime', () => {
  const { api } = load();
  const proposal = { id: 'abc', title: 'Fix search', prompt: 'Task data', repo_path: '/repo' };
  const body = api.spawnBody(proposal, row({ engine: 'codex' }));
  assert.equal(body.engine, 'codex');
  assert.equal(body.repo_path, '/repo');
  assert.equal(body.cwd, '/repo');
  assert.equal(body.task_key, 'leftover:abc:codex');
  assert.equal(body.worktree, true);
  assert.equal(body.dedupe, true);
  assert.equal(body.runtime, undefined);
  assert.equal(body.key_profile, undefined);
});

test('proposals match the canonical folder the server answers with', () => {
  const { api } = load();
  const item = { id: 'a', title: 'Fix search', prompt: 'Task data', repo_path: '/home/u/Apps/foo' };
  // Typed "~/Apps/foo/" resolves server-side; the client must not drop the tasks.
  assert.equal(api.validTasks({ repo_path: '/home/u/Apps/foo', proposals: [item] }).length, 1);
  assert.equal(api.validTasks({ repo_path: '/home/u/Apps/bar', proposals: [item] }).length, 0);
  assert.equal(api.validTasks({ proposals: [item] }).length, 0);
});

test('toggle defaults on and persists off', () => {
  const { api, storage } = load();
  assert.equal(api.enabled(), true);
  api.setEnabled(false);
  assert.equal(api.enabled(), false);
  assert.equal(storage['ccc-leftover-enabled'], '0');
});

test('notification has its own approval gate and one-day cap', () => {
  const fixture = load();
  fixture.api.notifyCandidate(row());
  assert.equal(fixture.notices.length, 0);
  fixture.allow('notify-other');
  fixture.api.notifyCandidate(row());
  assert.equal(fixture.notices.length, 0);
  fixture.allow();
  assert.equal(fixture.popups.notifyAllowed('leftover'), true);
  assert.equal(fixture.popups.notifyAllowed('info'), false);
  fixture.api.notifyCandidate(row());
  fixture.api.notifyCandidate(row({ engine: 'codex' }));
  assert.equal(fixture.notices.length, 1);
  assert.equal(fixture.notices[0].kind, 'leftover');
  fixture.storage['ccc-leftover-notified-at'] = String(Date.now() - 86400001);
  fixture.api.notifyCandidate(row());
  assert.equal(fixture.notices.length, 2);
  fixture.api.setEnabled(false);
  fixture.storage['ccc-leftover-notified-at'] = '0';
  fixture.api.notifyCandidate(row());
  assert.equal(fixture.notices.length, 2);
});

test('unsuccessful notification delivery does not consume daily cap', () => {
  const fixture = load();
  fixture.allow();
  fixture.delivery(false);
  fixture.api.notifyCandidate(row());
  assert.equal(fixture.storage['ccc-leftover-notified-at'], undefined);
  fixture.delivery(true);
  fixture.api.notifyCandidate(row());
  assert.equal(fixture.notices.length, 1);
});

test('no unattended spawns and popup registry starts unapproved', () => {
  const fixture = load();
  assert.equal(fixture.notices.length, 0);
  assert.equal((source.match(/fetch\('\/api\/sessions\/spawn'/g) || []).length, 1);
  assert.match(source, /cccPopups\.allowed\('leftover-offer'\)/);
  assert.match(source, /cccPopups\.allowed\('leftover-notification'\)/);
  assert.match(source, /data-lo-start/);
});
