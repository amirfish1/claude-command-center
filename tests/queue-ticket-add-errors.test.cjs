const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
const helpers = app.slice(app.indexOf('  async function _enqueueQueueTicket('), app.indexOf('  // CCC-163: the panel'));
const handlers = app.slice(app.indexOf('  async function _addQueueTicket('), app.indexOf('  {\n    const $newTicketBtn'));
function fixture(fetch) {
  const toasts = [], pending = new Map(), removed = [];
  const composer = async () => 'Fix <preview>\nplease';
  composer.lastQueue = 'TEST';
  const storage = new Map();
  const ctx = vm.createContext({fetch, console, AbortController,
    crypto: require('node:crypto').webcrypto,
    sessionStorage: {getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v)},
    setTimeout: (fn, ms) => { if (ms < 10000) queueMicrotask(fn); return 1; }, clearTimeout: () => {},
    escapeHtml: s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    showOpToast: (...args) => {toasts.push(args); return {remove: () => removed.push(args)};},
    _uxqLastResolvedProject: 'TEST', _uxqWorkerProject: () => 'TEST',
    _ticketQueueNames: async () => ['TEST'], openQueueTicketComposer: composer,
    _UXQ_FAMILY_DEFAULT: {}, _uxqProjectKey: s => s,
    _uxqPendingQueueAdds: pending, _renderQueuePanel: async () => {},
    _uxqItemsCache: {}, _uxqHealthCache: {},
    _ticketQueueUsageRecord: () => {}, _uxqPrepareNewTicketView: () => {},
    _uxqRevealNewTicket: () => {}, document: {querySelector: () => null},
  });
  ctx.window = ctx;
  vm.runInContext(fs.readFileSync('static/queue-ticket-submit.js', 'utf8') + helpers + handlers, ctx);
  return {ctx, toasts, pending, removed};
}
test('a confirmed save stays successful when the queue refresh fails', async () => {
  const {ctx, toasts, pending} = fixture(async () => ({ok:true,
    json: async () => ({ok:true, item:{ref:'TEST-1'}})}));
  ctx._renderQueuePanel = async opts => {if (!opts) throw Error('offline');};
  await vm.runInContext('_addQueueTicket()', ctx);
  assert.match(toasts.at(-1)[0], /Added TEST-1; queue refresh is unavailable/);
  assert.ok(toasts.every(([message]) => !message.includes('Could not add')));
  assert.equal(pending.size, 0);
});
const responses = {
  'non-JSON gateway failure': async () => ({ok: false, status: 502, statusText: 'Bad Gateway', json: async () => {throw Error('HTML');}}),
  'empty success response': async () => ({ok: true, status: 200, json: async () => ({})}),
  'backend rejection': async () => ({ok: false, status: 400, json: async () => ({ok: false, error: 'Queue <TEST> is unavailable'})}),
  'network interruption': async () => {throw Error('Failed to fetch');},
  'bad HTTP status with success body': async () => ({ok: false, status: 503, json: async () => ({ok: true, item: {ref: 'TEST-1'}})}),
};
for (const handler of ['_addQueueTicket', '_newGlobalTicket']) {
  for (const [name, response] of Object.entries(responses)) test(handler + ': ' + name, async () => {
    const {ctx, toasts, pending, removed} = fixture(response);
    await vm.runInContext(handler + '()', ctx);
    const last = toasts.at(-1);
    assert.equal(last[1], name === 'backend rejection' ? 'error' : 'warning');
    assert.match(last[0], name === 'backend rejection' ? /Could not add ticket to TEST/ : /Ticket confirmation pending in TEST/);
    assert.match(toasts.at(-1)[0], /Fix &lt;preview&gt; please/);
    assert.doesNotMatch(toasts.at(-1)[0], /unknown|<TEST>|<preview>/);
    if (name === 'backend rejection') assert.match(toasts.at(-1)[0], /Queue &lt;TEST&gt; is unavailable/);
    else assert.match(toasts.at(-1)[0], /Check the queue or retry/);
    if (name.includes('gateway')) assert.match(toasts.at(-1)[0], /HTTP 502/);
    assert.equal(pending.size, 0);
    if (name !== 'backend rejection') assert.equal(new Set(removed).size, 3, 'progress notices removed on settlement');
  });
  test(handler + ': confirmed creation stays a success', async () => {
    let body;
    const {ctx, toasts, pending} = fixture(async (_url, opts) => {
      body = JSON.parse(opts.body);
      return {ok: true, json: async () => ({ok: true, item: {ref: 'TEST-1'}})};
    });
    await vm.runInContext(handler + '()', ctx);
    assert.ok(body.idempotency_key);
    delete body.idempotency_key;
    assert.deepEqual(body, {note: 'Fix <preview>\nplease', project: 'TEST'});
    assert.match(toasts.at(-1)[0], /Added TEST-1/);
    assert.notEqual(toasts[0][1], 'error');
    assert.equal(pending.size, 0);
  });
}
