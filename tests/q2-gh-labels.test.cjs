const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const q2 = fs.readFileSync('static/q2.js', 'utf8');
const css = fs.readFileSync('static/q2.css', 'utf8');

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function loadHelpers() {
  const start = q2.indexOf('  function ghLabels(it) {');
  const end = q2.indexOf('\n  function ticketRow(', start);
  assert.ok(start >= 0 && end > start, 'ghLabels/ghLabelChip helpers found');
  return new Function('esc', q2.slice(start, end) + '; return { ghLabels, ghLabelChip };')(esc);
}

const { ghLabels, ghLabelChip } = loadHelpers();

test('github_labels drive both surfaces; watchtower:* plumbing labels are hidden', () => {
  assert.deepEqual(
    ghLabels({ github_labels: ['watchtower:TODO', 'work', 'BUG', '[BE]'] }),
    ['work', 'BUG', '[BE]']);
});

test('local tickets and missing labels render no chips', () => {
  assert.deepEqual(ghLabels({}), []);
  assert.deepEqual(ghLabels({ github_labels: 'not-an-array' }), []);
  assert.deepEqual(ghLabels({ github_labels: ['watchtower:CCC'] }), []);
});

test('label chips escape their text', () => {
  const html = ghLabelChip('<img src=x>');
  assert.match(html, /class="q2-gh-label"/);
  assert.doesNotMatch(html, /<img/);
});

test('ticketRow renders the GitHub label chips after the title', () => {
  const start = q2.indexOf('  function ticketRow(it) {');
  const end = q2.indexOf('\n  function renderTickets(', start);
  assert.ok(start >= 0 && end > start, 'ticketRow found');
  const body = q2.slice(start, end);
  assert.match(body, /class="q2-tgh"/);
  assert.match(body, /gh\.map\(ghLabelChip\)/);
});

test('renderDetail puts read-only GitHub labels on the chip row', () => {
  const start = q2.indexOf('  function renderDetail() {');
  const end = q2.indexOf('\n  function openDetailModal(', start);
  assert.ok(start >= 0 && end > start, 'renderDetail found');
  const body = q2.slice(start, end);
  assert.match(body, /gh\.map\(ghLabelChip\)/);
});

test('row label container yields instead of pushing signals off the row', () => {
  const m = css.match(/\.q2-tgh \{[^}]*\}/);
  assert.ok(m, '.q2-tgh rule exists');
  assert.match(m[0], /overflow: hidden/);
  assert.match(m[0], /min-width: 0/);
  assert.match(css, /\.q2-gh-label \{/);
});
