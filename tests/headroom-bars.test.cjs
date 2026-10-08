const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const api = require('../static/headroom-bars.js');
const source = fs.readFileSync(require.resolve('../static/headroom-bars.js'), 'utf8');
const NOW = Date.parse('2026-10-06T12:00:00Z');
const RESET = (NOW + 9 * 3600000) / 1000;
const row = (engine = 'claude', extra = {}) => ({
  id: engine + ':default', engine, account: 'default', available: true,
  stale: false, unlimited: false, percent_left: 64, resets_at: RESET,
  hours_to_reset: 9, burn_pct_per_hour: 2.1, projected_expiring_pct: 20,
  expiring_usd_estimate: 12.5, expiring_tokens_estimate: 120000, source: 'quota', ...extra,
});
const payload = (...rows) => ({ ok: true, rows, generated_at: new Date(NOW).toISOString() });
const normalized = (extra = {}) => api.normalize(payload(row('claude', extra)), NOW)[0];

function browser({ mount = true } = {}) {
  let now = NOW;
  const timers = new Map();
  const intervals = [];
  const listeners = Object.create(null);
  const calls = [];
  let timerId = 0;
  let handler = () => Promise.resolve({ ok: true, status: 200, json: async () => payload(row()) });
  const doc = { hidden: false, readyState: 'loading', activeElement: null };
  class Element {
    constructor() { this.children = []; this.parentNode = null; this.attributes = {}; this.dataset = {}; this.style = {}; this.className = ''; this.value = null; }
    get textContent() { return this.value == null ? this.children.map(child => child.textContent).join('') : this.value; }
    set textContent(value) { this.value = String(value); }
    appendChild(el) { return this.insertBefore(el, null); }
    insertBefore(el, target) {
      if (el.parentNode) el.parentNode.children.splice(el.parentNode.children.indexOf(el), 1);
      const index = target ? this.children.indexOf(target) : this.children.length;
      this.children.splice(index, 0, el);
      el.parentNode = this;
      return el;
    }
    remove() {
      if (this.contains(doc.activeElement)) doc.activeElement = null;
      if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
      this.parentNode = null;
    }
    replaceChildren(...children) { this.children.slice().forEach(child => child.remove()); children.forEach(child => this.appendChild(child)); }
    contains(el) { return this === el || this.children.some(child => child.contains(el)); }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    removeAttribute(key) { delete this.attributes[key]; }
    querySelector(selector) {
      for (const child of this.children) {
        if (child.className.split(' ').includes(selector.slice(1))) return child;
        const found = child.querySelector(selector);
        if (found) return found;
      }
      return null;
    }
    focus() { doc.activeElement = this; }
  }
  const host = mount ? new Element() : null;
  doc.body = new Element();
  if (host) { host.hidden = true; doc.body.appendChild(host); }
  doc.createElement = () => new Element();
  doc.getElementById = id => id === 'headroomBars' ? host : null;
  doc.addEventListener = (name, fn) => { (listeners[name] ||= []).push(fn); };
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const context = vm.createContext({
    document: doc, Date: Clock, AbortController, Promise,
    fetch: (...args) => { calls.push(args); return handler(...args); },
    setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout: id => timers.delete(id),
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
  });
  vm.runInContext(source, context);
  return {
    api: context.cccHeadroom, host, doc, timers, intervals, calls,
    advance: ms => { now += ms; },
    setFetch: fn => { handler = fn; },
    emit: name => (listeners[name] || []).forEach(fn => fn()),
    chips: () => host?.querySelector('.hb-grid')?.children || [],
  };
}

const response = (data, status = 200) => Promise.resolve({ ok: status < 400, status, json: async () => data });

test('canonical backend rows sort by engine with stable per-account identity', () => {
  const accounts = [row('free_router', { unlimited: true, percent_left: null }), row('devin', { available: false }), row('codex'), row('kimi'), row('claude', { id: 'claude:b', account: 'b' }), row('claude', { id: 'claude:a', account: 'a' })];
  const sorted = api.normalize(payload(...accounts), NOW);
  assert.deepEqual(sorted.map(item => item.id), ['claude:a', 'claude:b', 'codex:default', 'kimi:default', 'free_router:default']);
  assert.deepEqual(api.normalize(payload(...accounts.reverse()), NOW).map(item => item.id), sorted.map(item => item.id));
  assert.equal(sorted[0].showAccount, true);
  assert.equal(sorted[2].showAccount, false);
});

test('unknown engines, malformed envelopes, and duplicate row ids do not fabricate gauges', () => {
  for (const data of [null, [], {}, { ok: false, rows: [row()] }, { ok: true, accounts: [row()] }, { claude: { percent: 64 } }]) assert.deepEqual(api.normalize(data, NOW), []);
  assert.equal(api.normalize(payload(null, row('other'), { engine: 'claude' }, row(), row()), NOW).length, 1);
});

test('percent left uses only finite numeric readings in the contract', () => {
  for (const percent_left of [null, undefined, true, false, '', '64', NaN, Infinity, -1, 101]) {
    assert.equal(normalized({ percent_left }).pctLeft, null);
  }
  for (const percent_left of [0, 0.3, 10, 30, 99.7, 100]) assert.equal(normalized({ percent_left }).pctLeft, percent_left);
  assert.equal(normalized({ available: false, percent_left: 64 }), undefined);
});

test('unavailable accounts disappear and an all-unavailable panel stays hidden', () => {
  const b = browser();
  const items = b.api.normalize(payload(row('devin', { available: false }), row('free_router', { available: false, unlimited: true })), NOW);
  assert.equal(items.length, 0);
  b.api.render(items, NOW);
  assert.equal(b.host.hidden, true);
  assert.equal(b.chips().length, 0);
});

test('free routing stays neutral without a made-up full bar', () => {
  for (const percent_left of [null, 100]) {
    const item = normalized({ engine: 'free_router', id: 'free_router:default', unlimited: true, percent_left, resets_at: null, hours_to_reset: null });
    assert.equal(item.pctLeft, null);
    assert.equal(api.riskOf(item, NOW), 'off');
    assert.equal(api.subText(item, NOW), 'Limits vary by provider');
    assert.match(api.buildTooltip(item, NOW), /Free routing\. Limits vary by provider\./);
    assert.doesNotMatch(api.buildTooltip(item, NOW), /unlimited|100%|refund/);
  }
});

test('epoch-second reset takes precedence over hours fallback; invalid values remain unknown', () => {
  assert.equal(normalized({ hours_to_reset: 2 }).resetAtMs, NOW + 9 * 3600000);
  assert.equal(normalized({ resets_at: null, hours_to_reset: 2 }).resetAtMs, NOW + 2 * 3600000);
  for (const resets_at of ['not a date', new Date(RESET * 1000).toISOString(), String(RESET), Infinity, true, -1, 1e15]) {
    assert.equal(normalized({ resets_at, hours_to_reset: null }).resetAtMs, null);
  }
  for (const hours_to_reset of [null, Infinity, -1, '2', true, 1e15]) {
    assert.equal(normalized({ resets_at: null, hours_to_reset }).resetAtMs, null);
  }
  assert.equal(api.subText(normalized({ resets_at: null, hours_to_reset: 0 }), NOW), 'Reset pending');
});

test('quota colors have explicit thresholds and stale readings are neutral', () => {
  for (const [percent_left, risk] of [[0, 'low'], [10, 'low'], [10.1, 'warn'], [30, 'warn'], [30.1, 'ok'], [100, 'ok']]) assert.equal(api.riskOf(normalized({ percent_left }), NOW), risk);
  assert.equal(api.riskOf(normalized({ stale: true }), NOW), 'off');
  assert.equal(api.riskOf(normalized({ resets_at: NOW / 1000 }), NOW), 'off');
  assert.equal(api.riskOf(normalized({ percent_left: null }), NOW), 'off');
});

test('countdown keeps hours and minutes without rounding a future reset to now', () => {
  for (const [ms, text] of [[0, 'now'], [-1, 'now'], [1, '<1m'], [59000, '<1m'], [60000, '1m'], [59 * 60000, '59m'], [3600000, '1h'], [80 * 60000, '1h 20m'], [23 * 3600000, '23h'], [24 * 3600000, '1d'], [49 * 3600000, '2d 1h']]) assert.equal(api.fmtCountdown(ms), text);
  assert.equal(api.fmtCountdown(null), '');
  assert.equal(api.subText(normalized({ resets_at: (NOW - 1) / 1000 }), NOW), 'Reset pending');
});

test('tooltip carries exact numbers and distinguishes work estimates from money', () => {
  const text = api.buildTooltip(normalized(), NOW);
  for (const part of ['64% left', '20% could go unused', '2.1% of quota used per hour', 'in 9h', '$12.50', 'API-priced work', 'not money or a refund', '120,000 tokens']) assert.ok(text.includes(part), part);
  assert.doesNotMatch(text, /\u2014/);
  for (const extra of [{ stale: true }, { projected_expiring_pct: null }, { unlimited: true }, { percent_left: null }]) assert.doesNotMatch(api.buildTooltip(normalized(extra), NOW), /\$12\.50|120,000|could go unused/);
  const legacy = normalized({ projected_expiring_pct: null, expiring_usd_estimate: null, expiring_tokens_estimate: null, percent_used: 36, forecast_available: true, expires_unused_pct: 20, expires_unused_usd: 12.5, expires_unused_tokens: 120000 });
  assert.doesNotMatch(api.buildTooltip(legacy, NOW), /36% used|\$12\.50|120,000/);
});

test('DOM uses safe text, exact meter values, and honest unknown labels', () => {
  const b = browser();
  const items = b.api.normalize(payload(row('claude', { label: '<img src=x onerror=bad()>', percent_left: 0.3 }), row('codex', { percent_left: null, resets_at: null, hours_to_reset: null }), row('free_router', { unlimited: true, percent_left: null, resets_at: null, hours_to_reset: null })), NOW);
  b.api.render(items, NOW);
  const [known, unknown, free] = b.chips();
  assert.equal(known.querySelector('.hb-name').textContent, '<img src=x onerror=bad()>');
  assert.ok(known.title.includes('<img src=x onerror=bad()>'));
  assert.equal(known.querySelector('.hb-val').textContent, '<1% left');
  assert.equal(known.getAttribute('aria-valuenow'), '0.3');
  assert.equal(known.querySelector('.hb-fill').style.width, '0.3%');
  assert.equal(known.getAttribute('role'), 'meter');
  assert.equal(unknown.getAttribute('role'), 'group');
  assert.equal(unknown.getAttribute('aria-valuenow'), null);
  assert.equal(unknown.querySelector('.hb-val').textContent, 'Not available');
  assert.equal(unknown.querySelector('.hb-fill').style.width, '0%');
  assert.equal(free.getAttribute('role'), 'group');
  assert.equal(free.getAttribute('aria-valuenow'), null);
  assert.equal(free.querySelector('.hb-val').textContent, 'Free routing');
  assert.equal(free.querySelector('.hb-fill').style.width, '0%');
});

test('unchanged countdown and reordered API preserve keyed nodes and focus', () => {
  const b = browser();
  const accounts = [row('claude', { id: 'claude:b', account: 'b' }), row('claude', { id: 'claude:a', account: 'a' })];
  b.api.render(b.api.normalize(payload(...accounts), NOW), NOW);
  const [a, second] = b.chips();
  a.focus();
  b.api.render(b.api.normalize(payload(...accounts.reverse()), NOW), NOW + 60000);
  assert.equal(b.chips()[0], a);
  assert.equal(b.chips()[1], second);
  assert.equal(b.doc.activeElement, a);
  assert.equal(a.querySelector('.hb-sub').textContent, 'Resets in 8h 59m');
  b.api.render([], NOW);
  assert.equal(b.host.hidden, true);
  assert.equal(b.chips().length, 0);
});

test('missing mount is quiet and boot is idempotent', async () => {
  const missing = browser({ mount: false });
  missing.doc.readyState = 'complete';
  missing.api.boot();
  missing.api.render([normalized()], NOW);
  assert.equal(missing.calls.length, 0);
  const b = browser();
  b.doc.readyState = 'complete';
  b.api.boot();
  b.api.boot();
  await b.api.poll();
  assert.equal(b.calls.length, 1);
  assert.deepEqual(b.intervals.map(timer => timer.ms), [60000, 30000]);
});

test('overlapping forced polls share one fetch; hidden tabs do not fetch or tick', async () => {
  const b = browser();
  let resolve;
  b.setFetch(() => new Promise(done => { resolve = done; }));
  const first = b.api.poll(true);
  assert.equal(b.api.poll(true), first);
  await Promise.resolve();
  assert.equal(b.calls.length, 1);
  resolve(await response(payload(row())));
  await first;
  b.doc.hidden = true;
  await b.api.poll(true);
  assert.equal(b.calls.length, 1);
  b.doc.readyState = 'complete';
  b.api.boot();
  b.advance(60000);
  b.intervals.find(timer => timer.ms === 30000).fn();
  assert.equal(b.chips()[0].querySelector('.hb-sub').textContent, 'Resets in 9h');
});

test('404 after a valid response clears cached data and permanently stops polling', async () => {
  const b = browser();
  await b.api.poll();
  assert.equal(b.host.hidden, false);
  b.setFetch(() => response(null, 404));
  await b.api.poll();
  assert.equal(b.host.hidden, true);
  assert.equal(b.chips().length, 0);
  await b.api.poll(true);
  assert.equal(b.calls.length, 2);
});

test('server errors and invalid payloads back off, mute cached data, and recover', async () => {
  for (const fail of [() => response(null, 500), () => response({ ok: false }), () => Promise.reject(new Error('network'))]) {
    const b = browser();
    await b.api.poll();
    b.setFetch(fail);
    await b.api.poll();
    assert.equal(b.host.hidden, false);
    assert.match(b.chips()[0].className, /hb-off hb-stale/);
    assert.match(b.chips()[0].title, /Older reading/);
    await b.api.poll(true);
    assert.equal(b.calls.length, 2);
    b.advance(90000);
    b.setFetch(() => response(payload(row())));
    await b.api.poll();
    assert.match(b.chips()[0].className, /hb-ok/);
    assert.doesNotMatch(b.chips()[0].className, /hb-stale/);
  }
});

test('ten-second deadline aborts a hung fetch and frees the single-flight guard', async () => {
  const b = browser();
  b.setFetch((url, opts) => new Promise((resolve, reject) => opts.signal.addEventListener('abort', () => reject(new Error('aborted')))));
  const request = b.api.poll();
  await Promise.resolve();
  const deadline = Array.from(b.timers.values()).find(timer => timer.ms === 10000);
  assert.ok(deadline);
  deadline.fn();
  await request;
  assert.equal(b.calls[0][1].signal.aborted, true);
  assert.equal(b.timers.size, 0);
  b.advance(90000);
  b.setFetch(() => response(payload(row())));
  await b.api.poll();
  assert.equal(b.host.hidden, false);
});
