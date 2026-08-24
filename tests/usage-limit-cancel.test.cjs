const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');

function extractFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} not found in app.js`);
  let cursor = app.indexOf('{', start);
  let depth = 0;
  for (; cursor < app.length; cursor++) {
    if (app[cursor] === '{') depth++;
    else if (app[cursor] === '}') {
      depth--;
      if (depth === 0) return app.slice(start, cursor + 1);
    }
  }
  assert.fail(`unbalanced braces extracting ${name}`);
}

function buildContext(fetchImpl) {
  const banners = [{
    dataset: { sid: 'sid-1' },
    removed: false,
    remove() { this.removed = true; },
  }];
  const ctx = {
    conversationsData: [{ session_id: 'sid-1', usage_limit_resume_at: 12345 }],
    archiveData: [{ id: 'sid-1', usage_limit_resume_at: 12345 }],
    document: {
      querySelectorAll(selector) {
        assert.equal(selector, '.usage-limit-resume-banner');
        return banners;
      },
    },
    fetch: fetchImpl,
    syncCalls: 0,
  };
  ctx.syncUsageLimitCountdowns = () => { ctx.syncCalls += 1; };
  vm.createContext(ctx);

  const start = app.indexOf('const _usageLimitSuppressedSids');
  assert.notEqual(start, -1, 'usage-limit suppression state is missing');
  const end = app.indexOf('function syncUsageLimitCountdowns()', start);
  assert.notEqual(end, -1, 'syncUsageLimitCountdowns boundary is missing');
  const source = app.slice(start, end);
  vm.runInContext(source + `
    this.suppress = _usageLimitSuppressSession;
    this.rollback = _usageLimitRollbackSuppression;
    this.cancel = _cancelUsageLimitAutoResume;
    this.suppressedSids = _usageLimitSuppressedSids;
    this.cancelErrors = _usageLimitCancelErrors;
  `, ctx);
  ctx.banners = banners;
  return ctx;
}

test('local suppression clears stale resume fields from every row cache', () => {
  const ctx = buildContext(async () => ({ ok: true, json: async () => ({ ok: true }) }));

  ctx.suppress('sid-1');

  assert.equal(ctx.suppressedSids.has('sid-1'), true);
  assert.equal(ctx.conversationsData[0].usage_limit_resume_at, undefined);
  assert.equal(ctx.archiveData[0].usage_limit_resume_at, undefined);
  assert.equal(ctx.banners[0].removed, true);
});

test('successful cancellation keeps the session tombstoned', async () => {
  const ctx = buildContext(async () => ({ ok: true, json: async () => ({ ok: true }) }));

  const ok = await ctx.cancel('sid-1', 12345);

  assert.equal(ok, true);
  assert.equal(ctx.suppressedSids.has('sid-1'), true);
  assert.equal(ctx.cancelErrors.has('sid-1'), false);
  assert.equal(ctx.syncCalls, 0);
});

test('failed cancellation restores the deadline and a retryable error', async () => {
  const ctx = buildContext(async () => ({
    ok: false,
    status: 500,
    json: async () => ({ ok: false, error: 'disk full' }),
  }));

  const ok = await ctx.cancel('sid-1', 12345);

  assert.equal(ok, false);
  assert.equal(ctx.suppressedSids.has('sid-1'), false);
  assert.equal(ctx.cancelErrors.has('sid-1'), true);
  assert.equal(ctx.conversationsData[0].usage_limit_resume_at, 12345);
  assert.equal(ctx.archiveData[0].usage_limit_resume_at, 12345);
  assert.equal(ctx.syncCalls, 1);
});

test('countdown rendering honors tombstones and exposes Retry on failure', () => {
  const start = app.indexOf('function syncUsageLimitCountdowns()');
  const end = app.indexOf('function _tickUsageLimitCountdowns()', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const source = app.slice(start, end);
  assert.match(source, /_usageLimitSuppressedSids\.has\(sid\)/);
  assert.match(source, /AUTO-RESUME STILL ENABLED/);
  assert.match(source, />Retry</);
});

test('repeated countdown sync cannot redraw a locally suppressed stale row', () => {
  let banner = null;
  const inputBar = {
    querySelector(selector) {
      assert.equal(selector, '.usage-limit-resume-banner');
      return banner && !banner.removed ? banner : null;
    },
    prepend(node) { banner = node; },
  };
  const pane = {
    dataset: { paneId: '' },
    querySelector(selector) {
      assert.equal(selector, '.conv-input-bar');
      return inputBar;
    },
  };
  const ctx = {
    conversationsData: [{ session_id: 'sid-1', usage_limit_resume_at: Date.now() / 1000 + 3600 }],
    archiveData: [],
    currentSession: { id: 'sid-1' },
    paneByPaneId: () => null,
    document: {
      querySelectorAll(selector) {
        if (selector === '.conv-pane[data-pane-id]') return [pane];
        if (selector === '.usage-limit-resume-banner') {
          return banner && !banner.removed ? [banner] : [];
        }
        throw new Error(`unexpected selector: ${selector}`);
      },
      createElement(tag) {
        assert.equal(tag, 'div');
        return {
          className: '',
          dataset: {},
          removed: false,
          classList: { toggle() {} },
          setAttribute() {},
          remove() { this.removed = true; },
        };
      },
    },
    fetch: async () => ({ ok: true, json: async () => ({ ok: true }) }),
  };
  vm.createContext(ctx);
  const stateStart = app.indexOf('const _usageLimitSuppressedSids');
  const syncStart = app.indexOf('function syncUsageLimitCountdowns()', stateStart);
  vm.runInContext(
    extractFunction('_usageLimitRowForSession')
      + app.slice(stateStart, syncStart)
      + extractFunction('syncUsageLimitCountdowns')
      + '; this.sync = syncUsageLimitCountdowns; this.suppress = _usageLimitSuppressSession;',
    ctx,
  );

  ctx.sync();
  assert.equal(banner.removed, false);
  ctx.suppress('sid-1');
  ctx.sync();
  ctx.sync();

  assert.equal(banner.removed, true);
  assert.equal(ctx.conversationsData[0].usage_limit_resume_at, undefined);
});

test('delegated X handler awaits the durable cancellation request', () => {
  const start = app.indexOf("document.addEventListener('click', async (ev) => {");
  const end = app.indexOf('setInterval(syncUsageLimitCountdowns, 5000);', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const source = app.slice(start, end);
  assert.match(source, /usage-limit-resume-cancel/);
  assert.match(source, /await _cancelUsageLimitAutoResume\(/);
});
