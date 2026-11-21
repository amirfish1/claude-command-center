const { test } = require('node:test');
const assert = require('node:assert/strict');
require('../static/conversation-text.js');
const { createRenderer } = globalThis.CCCConversationText;

test('session links read current live state across replacements and corrections', () => {
  const sid = '00000000-0000-4000-8000-000000000001';
  let rows = [];
  const renderer = createRenderer({
    findSession: id => rows.find(row => row.id === id),
    sidebarRowDisplayTitle: value => value,
    shortSessionId: value => value.slice(0, 8),
    cleanIssuePrompt: value => value,
  });
  assert.equal(renderer.renderInline(sid), sid);
  rows = [{ id: sid, display_name: 'First title' }];
  assert.match(renderer.renderInline(sid), /First title/);
  rows[0].display_name = 'Corrected title';
  assert.match(renderer.renderInline(sid), /Corrected title/);
  rows = [];
  assert.equal(renderer.renderInline(sid), sid);
});

test('image attribution reads the currently active session on every render', () => {
  let sid = 'first-session';
  const renderer = createRenderer({ activeSessionId: () => sid });
  assert.match(renderer.pastedImageTag('/tmp/example.png'), /session_id=first-session/);
  sid = 'second-session';
  assert.match(renderer.pastedImageTag('/tmp/example.png'), /session_id=second-session/);
  sid = '';
  assert.doesNotMatch(renderer.pastedImageTag('/tmp/example.png'), /session_id=/);
});

test('renderers remain independent while sharing the same implementation', () => {
  const first = createRenderer({ activeSessionId: () => 'first' });
  const second = createRenderer({ activeSessionId: () => 'second' });
  assert.match(first.pastedImageTag('image.png'), /session_id=first/);
  assert.match(second.pastedImageTag('image.png'), /session_id=second/);
  assert.equal(first.renderMarkdown('**hello** world'), second.renderMarkdown('**hello** world'));
});

test('bare URLs after a bold list label keep their full destination and visible gap', () => {
  const renderer = createRenderer({});
  const url = 'https://example.com/presentation/d/demo/edit?mode=view&slide=1';
  const html = renderer.renderMarkdown('- **Link:** ' + url);

  assert.match(html, /<strong>Link:<\/strong><span class="md-bold-gap">&nbsp;<\/span>/);
  assert.ok(html.includes('<a href="' + url.replace('&', '&amp;') + '" target="_blank" rel="noopener">'));
});

test('a URL in an explicit link label does not create a second anchor', () => {
  const renderer = createRenderer({});
  const html = renderer.renderInline('[**Link:** https://example.com/label](https://example.com/target)');

  assert.equal((html.match(/<a\b/g) || []).length, 1);
  assert.match(html, /<a href="https:\/\/example\.com\/target"/);
});
