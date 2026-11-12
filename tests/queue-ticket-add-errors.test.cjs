const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
const helpers = app.slice(app.indexOf('  async function _enqueueQueueTicket('), app.indexOf('  // CCC-163: the panel'));
const handlers = app.slice(app.indexOf('  async function _addQueueTicket('), app.indexOf('  {\n    const $newTicketBtn'));
function fixture(fetch) {
  const toasts = [], pending = new Map();
  const composer = async () => 'Fix <preview>\nplease';
  composer.lastQueue = 'TEST';
  const ctx = vm.createContext({fetch, console,
    escapeHtml: s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    showOpToast: (...args) => toasts.push(args),
    _uxqLastResolvedProject: 'TEST', _uxqWorkerProject: () => 'TEST',
    _ticketQueueNames: async () => ['TEST'], openQueueTicketComposer: composer,
    _UXQ_FAMILY_DEFAULT: {}, _uxqProjectKey: s => s,
    _uxqPendingQueueAdds: pending, _renderQueuePanel: async () => {},
    _uxqItemsCache: {}, _uxqHealthCache: {},
    _ticketQueueUsageRecord: () => {}, _uxqPrepareNewTicketView: () => {},
    _uxqRevealNewTicket: () => {}, document: {querySelector: () => null},
  });
  vm.runInContext(helpers + handlers, ctx);
  return {ctx, toasts, pending};
}
const responses = {
  'non-JSON gateway failure': async () => ({ok: false, status: 502, statusText: 'Bad Gateway', json: async () => {throw Error('HTML');}}),
  'empty success response': async () => ({ok: true, status: 200, json: async () => ({})}),
  'backend rejection': async () => ({ok: false, status: 400, json: async () => ({ok: false, error: 'Queue <TEST> is unavailable'})}),
  'network interruption': async () => {throw Error('Failed to fetch');},
  'bad HTTP status with success body': async () => ({ok: false, status: 503, json: async () => ({ok: true, item: {ref: 'TEST-1'}})}),
};
for (const handler of ['_addQueueTicket', '_newGlobalTicket']) {
  for (const [name, response] of Object.entries(responses)) test(handler + ': ' + name, async () => {
    const {ctx, toasts, pending} = fixture(response);
    await vm.runInContext(handler + '()', ctx);
    assert.equal(toasts.length, 1);
    assert.equal(toasts[0][1], 'error');
    assert.match(toasts[0][0], /Could not add ticket to TEST/);
    assert.match(toasts[0][0], /Fix &lt;preview&gt; please/);
    assert.doesNotMatch(toasts[0][0], /unknown|<TEST>|<preview>/);
    if (name === 'backend rejection') assert.match(toasts[0][0], /Queue &lt;TEST&gt; is unavailable/);
    else assert.match(toasts[0][0], /Check the queue before retrying/);
    if (name.includes('gateway')) assert.match(toasts[0][0], /HTTP 502 Bad Gateway/);
    assert.equal(pending.size, 0);
  });
  test(handler + ': confirmed creation stays a success', async () => {
    let body;
    const {ctx, toasts, pending} = fixture(async (_url, opts) => {
      body = JSON.parse(opts.body);
      return {ok: true, json: async () => ({ok: true, item: {ref: 'TEST-1'}})};
    });
    await vm.runInContext(handler + '()', ctx);
    assert.deepEqual(body, {note: 'Fix <preview>\nplease', project: 'TEST'});
    assert.match(toasts[0][0], /Added TEST-1/);
    assert.notEqual(toasts[0][1], 'error');
    assert.equal(pending.size, 0);
  });
}
