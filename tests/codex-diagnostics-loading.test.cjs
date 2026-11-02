const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  function _codexDiagAge(');
const end = source.indexOf('  function _codexDiagMetadataTabActive(', start);
assert.ok(start >= 0 && end > start);
const panel = {hidden:true, innerHTML:''};
const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;');
const render = new Function('codexDiagPanelEl', 'escapeHtml', 'escapeAttr',
  source.slice(start, end) + '\nreturn renderCodexDiagnostics;')(() => panel, escape, escape);

test('pending diagnostics shows collection without inventing process or storage facts', () => {
  render({ok:false, pending:true, error:'Diagnostics collection in progress'});
  assert.equal(panel.hidden, false);
  assert.match(panel.innerHTML, /role="status">Collecting diagnostics/);
  assert.doesNotMatch(panel.innerHTML, /State DB row|not live|No app-server state/);
});

test('a complete snapshot replaces loading and marks a refresh without hiding its verdict', () => {
  const data = {ok:true, verdict:{headline:'Idle'}, refreshing:true};
  render(data);
  assert.match(panel.innerHTML, /Idle/);
  assert.match(panel.innerHTML, /refreshing/);
  assert.doesNotMatch(panel.innerHTML, /Collecting diagnostics/);
  render({...data, refreshing:false});
  assert.doesNotMatch(panel.innerHTML, /refreshing/);
  render({ok:false, error:'not a known Codex session'});
  assert.equal(panel.hidden, true);
});
