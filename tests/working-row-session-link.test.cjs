const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

function sliceFn(name, endMarker) {
  const start = app.indexOf('  function ' + name);
  assert.ok(start >= 0, name + ' found');
  const end = app.indexOf('\n  function ' + endMarker, start);
  assert.ok(end > start, 'end of ' + name + ' found');
  return app.slice(start, end);
}

test('working-now rows carry the worker session id into the row builder', () => {
  const body = sliceFn('_uxqRenderWorkingNow()', '_wtWarmActivityForWorkersLane');
  assert.match(body, /return \{ ref, title, queue: qKey,[\s\S]*sid: sid \}/);
});

test('both row variants render the session-open button only with a sid', () => {
  const body = sliceFn('_uxqRenderWorkingNow()', '_wtWarmActivityForWorkersLane');
  assert.match(body, /class="fq-worker-session" data-uxq-open-session="/);
  // Guard the conditional: no sid, no button.
  assert.match(body, /const sessBtn = r\.sid/);
  assert.equal((body.match(/data-uxq-open-session=/g) || []).length, 1,
    'button markup built once and shared by desktop + mobile rows');
  assert.equal((body.match(/\+ sessBtn/g) || []).length, 2, 'button placed in both row variants');
});

test('strip click handler opens the CCC session without opening the ticket', () => {
  const start = app.indexOf('  function _uxqBindWorkingStrip($working) {');
  assert.ok(start >= 0, '_uxqBindWorkingStrip found');
  const end = app.indexOf('\n  // ── WORKING NOW strip', start);
  assert.ok(end > start, 'end of _uxqBindWorkingStrip found');
  const fn = app.slice(start, end);
  assert.ok(fn.indexOf('[data-uxq-open-session]') !== -1, 'session button intercepted');
  assert.ok(fn.indexOf('ev.stopPropagation()') !== -1, 'row ticket-open is suppressed');
  assert.ok(fn.indexOf('window.cccOpenSession(sid)') !== -1, 'opens via the cccOpenSession bridge');
  // The session handler must run BEFORE the generic row-open delegation.
  assert.ok(fn.indexOf('[data-uxq-open-session]') < fn.indexOf('.fq-working-row[data-uxq-working-ref]'),
    'session handler precedes the row-open handler');
});

test('ticket detail session fallback consults the live worker roster', () => {
  const start = app.indexOf('  function _uxqOpenItemModal(item) {');
  assert.ok(start >= 0, '_uxqOpenItemModal found');
  const end = app.indexOf('\n  function _uxqOpenItemDetail(ref)', start);
  const fn = app.slice(start, end);
  assert.ok(fn.indexOf('(_uxqHealthCache || {}).wt_workers') !== -1,
    'roster lookup present in the session fallback');
  assert.ok(fn.indexOf('_uxFixesIdentityKey(w.worker_id || \'\') === key') !== -1,
    'roster matched by claimed_by identity key');
  // conversations stay the preferred source
  assert.ok(fn.indexOf('conversationsData') < fn.indexOf('(_uxqHealthCache || {}).wt_workers'),
    'conversation match is tried before the roster');
});

test('session button styled like the kill button with a neutral accent', () => {
  const m = css.match(/\.fq-worker-session \{[^}]*\}/);
  assert.ok(m, '.fq-worker-session rule exists');
  assert.match(m[0], /cursor: pointer/);
  assert.match(css, /\.fq-working-row:hover \.fq-worker-session \{ opacity: 1; \}/);
});
