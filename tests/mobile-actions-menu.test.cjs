const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');

const html = fs.readFileSync(require.resolve('../static/index.html'), 'utf8');
const js = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const menuStart = html.indexOf('id="convSimpleActionsMenu"');
const menu = html.slice(menuStart, html.indexOf('<div class="conv-overflow-wrap">', menuStart));

test('text-size buttons share one row in the ⋮ menu', () => {
  const row = menu.match(/<div class="mobile-actions-row"[^>]*>([\s\S]*?)<\/div>/);
  assert.ok(row, 'mobile-actions-row exists');
  assert.match(row[1], /data-simple-action="font-minus"/);
  assert.match(row[1], /data-simple-action="font-plus"/);
});

test('⋮ menu shows copyable session id and repo path', () => {
  assert.match(menu, /data-simple-action="copy-session-id"[\s\S]*data-role="menu-session-id"/);
  assert.match(menu, /data-simple-action="copy-repo-path"[\s\S]*data-role="menu-repo-path"/);
  const sync = js.slice(js.indexOf('function _syncSessionMenuItems('), js.indexOf('function _wireSimpleActionsMenu('));
  assert.match(sync, /menu-session-id/);
  assert.match(sync, /rowRepoPath\(row\)/);
});

test('proxied font clicks do not close the ⋮ menu', () => {
  const wire = js.slice(js.indexOf('function _wireSimpleActionsMenu('), js.indexOf('_wireSimpleActionsMenu();'));
  assert.match(wire, /#fontMinus, #fontPlus/);
  assert.match(wire, /action !== 'font-minus' && action !== 'font-plus'/);
});
