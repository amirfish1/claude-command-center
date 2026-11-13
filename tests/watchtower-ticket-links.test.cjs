const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('static/app.js', 'utf8');

function renderInline(text) {
  const start = app.indexOf('  function renderInline(s) {');
  const end = app.indexOf('\n  function linkifyPath(', start);
  assert.ok(start >= 0 && end > start, 'renderInline helper found');
  const helper = new Function(
    'escapeHtml', 'escapeAttr', 'linkifyPastedImages',
    '_shouldLinkifyInlineCodePath', 'normalizeMarkdownLinkTarget',
    'isUnavailableMarkdownImageTarget', 'linkifyCodexInlineVisuals',
    app.slice(start, end) + '; return renderInline;'
  )(
    value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;'),
    value => value,
    () => false,
    value => value,
    () => false,
    value => value,
  );
  return helper(text);
}

test('WatchTower ticket references open their queue from inline code and prose', () => {
  for (const text of ['`OPS-1148`', 'See OPS-1148 for details.']) {
    const html = renderInline(text);
    assert.match(html, /class="watchtower-ticket-link"/);
    assert.match(html, /data-watchtower-ticket="OPS-1148"/);
  }
});

test('ordinary hyphenated text does not become a WatchTower ticket link', () => {
  assert.doesNotMatch(renderInline('A non-ticket ABC-def remains plain text.'), /watchtower-ticket-link/);
});

test('WatchTower ticket links resolve to the corresponding queue page', () => {
  const start = app.indexOf('  function watchtowerTicketUrl(');
  const end = app.indexOf('\n  document.addEventListener(', start);
  assert.ok(start >= 0 && end > start, 'WatchTower ticket URL helper found');
  const ticketUrl = new Function(app.slice(start, end) + '; return watchtowerTicketUrl;')();
  assert.equal(ticketUrl('OPS-1148', 'http://127.0.0.1:8787/'), 'http://127.0.0.1:8787/q/OPS#OPS-1148');
  assert.equal(ticketUrl('HARBOR-GH-TERN-402', 'http://localhost:8787'), 'http://localhost:8787/q/HARBOR-GH-TERN#HARBOR-GH-TERN-402');
});


test('ticket refs in image URLs and alt text preserve the image tag', () => {
  const html = renderInline('![Calendar for OPS-1148](https://example.com/OPS-1148/calendar.png) See OPS-1148.');
  assert.ok(html.startsWith('<img src="https://example.com/OPS-1148/calendar.png" alt="Calendar for OPS-1148" class="msg-image" loading="lazy">'));
  assert.equal((html.match(/class="watchtower-ticket-link"/g) || []).length, 1);
  assert.ok(html.indexOf('class="watchtower-ticket-link"') > html.indexOf('loading="lazy">'));
});

test('ticket refs in local image query paths do not become nested markup', () => {
  const html = renderInline('![Calendar](/api/file-image?path=%2Ftmp%2FOPS-1148%2Fcalendar.png)');
  assert.equal(html, '<img src="/api/file-image?path=%2Ftmp%2FOPS-1148%2Fcalendar.png" alt="Calendar" class="msg-image" loading="lazy">');
});
