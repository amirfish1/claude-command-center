const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  function browserNormalizeUrl(raw)');
const end = app.indexOf('  function _browserPaneShowing()');
assert.ok(start > 0 && end > start, 'browser sidekick URL helpers not found in app.js');
const { browserNormalizeUrl, browserIsLoopback } = new Function(
  app.slice(start, end) + '\nreturn { browserNormalizeUrl, browserIsLoopback };')();

test('address bar shorthand becomes a full URL', () => {
  assert.equal(browserNormalizeUrl('3000'), 'http://localhost:3000/');
  assert.equal(browserNormalizeUrl(' localhost:5173/app '), 'http://localhost:5173/app');
  assert.equal(browserNormalizeUrl('0.0.0.0:8000'), 'http://localhost:8000/');
  assert.equal(browserNormalizeUrl('127.0.0.1:4000/x?y=1'), 'http://127.0.0.1:4000/x?y=1');
  assert.equal(browserNormalizeUrl('example.com'), 'https://example.com/');
  assert.equal(browserNormalizeUrl('https://example.com/a'), 'https://example.com/a');
});

test('non-web schemes and junk are rejected', () => {
  assert.equal(browserNormalizeUrl('javascript:alert(1)'), '');
  assert.equal(browserNormalizeUrl('file:///etc/passwd'), '');
  assert.equal(browserNormalizeUrl(''), '');
  assert.equal(browserNormalizeUrl('http://'), '');
});

test('loopback detection', () => {
  assert.equal(browserIsLoopback('http://localhost:3000/'), true);
  assert.equal(browserIsLoopback('http://127.0.0.1:3000/'), true);
  assert.equal(browserIsLoopback('http://[::1]:3000/'), true);
  assert.equal(browserIsLoopback('https://example.com/'), false);
});
