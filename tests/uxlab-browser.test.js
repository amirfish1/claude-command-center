'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {allowedURL, risky, fakeValue, requestAllowed} = require('../uxlab/safety');
const {BrowserSession, TOOLS, validate} = require('../uxlab/browser');
const fs = require('fs');
const os = require('os');
const path = require('path');
const safe = {hosts: ['localhost', '127.0.0.1', '::1'], allow_risky: false};
const allow = request => requestAllowed(request, safe);

test('exact host, protocol, credentials and frame/redirect destinations', () => {
  assert(allowedURL('http://[::1]:9000/', safe.hosts));
  assert(allowedURL('https://localhost/', safe.hosts));
  for (const url of ['https://localhost.evil/', 'http://evil.test/', 'file:///tmp/a', 'data:text/html,hi', 'javascript:1', 'http://u:p@localhost/']) {
    assert(!allowedURL(url, safe.hosts), url);
    assert(!allow({url, resource: 'document'}), url);
  }
});
test('write methods, websockets, risky labels, card digits and real emails', () => {
  assert(allow({url: 'http://localhost/details'}));
  for (const url of ['http://localhost/pay', 'http://localhost/delete-account', 'http://localhost/invite',
    'http://localhost/search?q=0000-1111-1111-1111', 'http://localhost/?email=person%40real.test']) assert(!allow({url}), url);
  assert(allow({url: 'http://localhost/api', method: 'POST', body: '{"name":"Test User"}'}));
  assert(!allow({url: 'http://localhost/api', method: 'DELETE'}));
  assert(!allow({url: 'http://localhost/socket', resource: 'websocket'}));
  assert(!allow({url: 'ws://localhost/socket'}));
  assert(risky('Send message')); assert(risky('Buy now')); assert(risky('delete_account'));
});
test('allow risky still enforces hosts, fake bodies and card prohibition', () => {
  const safety = {...safe, allow_risky: true};
  const request = {url: 'http://localhost/pay', method: 'POST', body: '{"name":"Test User","email":"tester@example.com"}'};
  assert(requestAllowed(request, safety));
  assert(!requestAllowed({...request, body: '{"card":"0000111111111111"}'}, safety));
  assert(!requestAllowed({...request, body: '{"email":"person@real.test"}'}, safety));
  assert(!requestAllowed({...request, body: '{"name":"Actual person"}'}, safety));
  assert(!requestAllowed({...request, body: 'Actual person'}, safety));
  assert(!requestAllowed({...request, body: 'Actual%20person='}, safety));
  assert(!requestAllowed({...request, url: 'http://external.test/pay'}, safety));
});
test('typing accepts only enum fake text and safe field types', () => {
  assert.equal(fakeValue('email', {type: 'email'}), 'tester@example.com');
  for (const kind of ['arbitrary text', '0000111111111111', 'constructor', '__proto__']) assert.throws(() => fakeValue(kind));
  for (const field of [{type: 'file'}, {type: 'hidden'}, {name: 'credit-card'}, {autocomplete: 'cc-number'}, {label: 'Bank account'}]) assert.throws(() => fakeValue('name', field));
  assert.throws(() => fakeValue('text', {type: 'email'}));
});
test('tool schemas exclude selectors, JS, arbitrary navigation and keyboard', () => {
  assert.deepEqual(TOOLS.map(t => t.name), ['look', 'click', 'type', 'select', 'scroll', 'wait', 'back', 'restart', 'record_finding', 'replay', 'finish']);
  const click = TOOLS.find(t => t.name === 'click').inputSchema;
  assert.throws(() => validate(click, {control: 1, expected: 'Open', selector: 'a'}));
  assert.throws(() => validate(click, {control: -1, expected: 'Open'}));
  assert.throws(() => validate(click, {control: 1, expected: ''}));
  const typing = TOOLS.find(t => t.name === 'type').inputSchema;
  assert.throws(() => validate(typing, {control: 1, expected: 'Fill', value: 'real data'}));
});
test('MCP budgets, evidence and finish persistence without a browser', async () => {
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'uxlab-test-'));
  try {
    const session = new BrowserSession({person: {id: 'tester-1'}, output, timeout: 30, steps: 2, earlier: [{key: 'old'}]});
    session.page = {url: () => 'http://localhost/'};
    session.viewport = {width: 390, height: 844};
    session.shot = async () => 'shot-1.png';
    const args = {control: 'Favorite', symptom: 'No effect', severity: 'blocked', steps: ['Tap favorite'], expected: 'Confirmation', actual: 'Nothing'};
    await session.call('record_finding', args);
    await session.call('replay', {key: 'old', verdict: 'present', steps: ['Tap favorite'], expected: 'Confirmation', actual: 'Nothing'});
    await assert.rejects(session.call('look', {}), /Budget/);
    await session.call('finish', {success: false, worked: 'Labels were readable', actual: 'Budget ended'});
    const result = JSON.parse(fs.readFileSync(path.join(output, 'result.json')));
    assert.equal(result.status, 'finished'); assert.equal(result.findings.length, 1);
    assert.equal(result.findings[0].viewport, '390×844'); assert.equal(result.replays[0].verdict, 'present');
    await assert.rejects(session.call('look', {}), /finished/);
  } finally { fs.rmSync(output, {recursive: true, force: true}); }
});
test('MCP initialize and tools/list do not launch browser; errors are tool results', async () => {
  const {protocolReply} = require('../uxlab/browser');
  let starts = 0;
  const session = {call: async () => {throw Error('Blocked action');}, trace: () => {}};
  const start = async () => {++starts;};
  const init = await protocolReply({id: 1, method: 'initialize'}, session, start);
  assert.equal(init.result.serverInfo.name, 'uxlab');
  assert.equal((await protocolReply({id: 2, method: 'tools/list'}, session, start)).result.tools.length, TOOLS.length);
  assert.equal(await protocolReply({method: 'notifications/initialized'}, session, start), undefined);
  assert.equal(starts, 0);
  const error = await protocolReply({id: 3, method: 'tools/call', params: {name: 'click', arguments: {}}}, session, start);
  assert.equal(starts, 1); assert.equal(error.result.isError, true);
  assert.equal(error.result.content[0].text, 'Blocked action');
});

test('phone actions tap while desktop actions click', async () => {
  const calls = [];
  const session = Object.create(BrowserSession.prototype);
  session.page = {touchscreen: {tap: async (...args) => calls.push(['tap', ...args])}, mouse: {click: async (...args) => calls.push(['click', ...args])}};
  session.viewport = {hasTouch: true}; await session.tap(20, 30);
  session.viewport = {hasTouch: false}; await session.tap(40, 50);
  assert.deepEqual(calls, [['tap', 20, 30], ['click', 40, 50]]);
});
