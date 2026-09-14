const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

test('a committed mobile row swipe cancels opening and gives row-level feedback', () => {
  const start = app.indexOf('  function wireConvListRowSwipe($list) {');
  const end = app.indexOf('\n  // WhatsApp-style drag-to-dismiss', start);
  assert.ok(start >= 0 && end > start, 'conversation-list swipe handler found');
  const swipeHandler = app.slice(start, end);

  assert.match(swipeHandler, /cancelMobileConversationRowTap\(\);/);
  assert.match(swipeHandler, /currentRow\.classList\.add\('is-swipe-committed'\)/);
  assert.match(swipeHandler, /currentRow\.dataset\.swipeActionLabel/);
  assert.match(css, /\.conv-item\.is-swipe-committed/);
  assert.match(css, /\.conv-item\.is-swipe-committed::before/);
  assert.match(css, /content: attr\(data-swipe-action-label\)/);
});
