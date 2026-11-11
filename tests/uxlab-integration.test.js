'use strict';
// Opt-in real Chromium checks: nice -n 15 node --test tests/uxlab-integration.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {BrowserSession} = require('../uxlab/browser');
const listen = (server, host) => new Promise(resolve => server.listen(0, host, resolve));
const close = server => new Promise(resolve => server.close(resolve));

test('real browser enforces host, click, form and touch boundaries', {timeout: 60000}, async () => {
  let escaped = 0, submitted = '', paid = 0, touched = false;
  const outside = http.createServer((req, res) => {escaped++; res.end('outside');});
  await listen(outside, '127.0.0.2');
  const external = `http://127.0.0.2:${outside.address().port}`;
  const app = http.createServer((req, res) => {
    if (req.url === '/redirect') {res.writeHead(302, {Location: external}); res.end(); return;}
    if (req.url === '/pay') paid++;
    if (req.url === '/touch') touched = true;
    if (req.url === '/form') {req.on('data', chunk => submitted += chunk); req.on('end', () => res.end('Saved fake form')); return;}
    res.setHeader('Content-Type', 'text/html');
    res.end(`<meta name="viewport" content="width=device-width"><style>button,input{display:block;margin:12px;font-size:20px}</style>
      <p>Visible words</p><span hidden>HIDDEN SENTINEL</span>
      <a href="${external}">Outside</a><a href="/redirect">Redirect</a>
      <button onclick="location='/pay'">Purchase</button>
      <button ontouchstart="fetch('/touch')">Touch me</button>
      <form action="/form" method="post"><label>Email<input name="email" type="email"></label><label>City<input name="city"></label><button>Save</button></form>
      <iframe src="${external}"></iframe><script>window.open('${external}')</script>`);
  });
  await listen(app, '127.0.0.1');
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'uxlab-integration-'));
  const session = new BrowserSession({url: `http://127.0.0.1:${app.address().port}`, person: {id: 'tester-1', device: 'phone'},
    output, browser_sandbox: process.env.UXLAB_TEST_NO_SANDBOX !== '1', timeout: 45, steps: 40, safety: {hosts: ['127.0.0.1'], allow_risky: false}});
  const number = label => session.controls.findIndex(c => c.info.label === label) + 1;
  try {
    await session.start();
    const seen = await session.call('look', {});
    assert.match(seen[0].text, /Visible words/);
    assert(!seen[0].text.includes('HIDDEN SENTINEL'));
    await assert.rejects(session.call('click', {control: number('Outside'), expected: 'outside'}), /Unsafe/);
    await assert.rejects(session.call('click', {control: number('Purchase'), expected: 'purchase'}), /Risky/);
    await session.call('click', {control: number('Touch me'), expected: 'touch event'});
    assert.equal(touched, true);
    await session.call('type', {control: number('Email'), value: 'email', expected: 'fake email'});
    await session.call('type', {control: number('City'), value: 'city', expected: 'fake city'});
    await session.call('click', {control: number('Save'), expected: 'safe form saved'});
    assert.equal(submitted, 'email=tester%40example.com&city=Example+City');
    await session.call('restart', {expected: 'start over'});
    await session.call('click', {control: number('Redirect'), expected: 'redirect checked'}).catch(() => {});
    assert.equal(escaped, 0);
    assert.equal(paid, 0);
  } finally {
    await session.close();
    await close(app); await close(outside);
    fs.rmSync(output, {recursive: true, force: true});
  }
});
