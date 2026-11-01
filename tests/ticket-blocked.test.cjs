const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const ctx = { window: {} };
vm.runInNewContext(fs.readFileSync('static/ticket-prose.js', 'utf8'), ctx);
const render = ctx.window.CCCTicketProse.renderBlocked;
test('decision comes first, with recommendation and original preserved', () => {
  const source = '[Q12:cache] Slow after 9 attempts. Recommended: (A+C) cache the payload. Alternatives: (B) wait; (D) reduce refresh. Approve A+C (details load on open)?';
  const html = render(source);
  assert.ok(html.indexOf('Approve A+C') < html.indexOf('Recommended approach'));
  assert.ok(html.indexOf('Recommended approach') < html.indexOf('Why it paused'));
  assert.match(html, /\[Q12:cache\]/);
  assert.match(html, /<details class="tp-blocked-original">/);
  assert.match(html, /Slow after 9 attempts/);
});
test('does not invent a decision for a dependency block', () => {
  const html = render('Waiting for another task to complete.');
  assert.match(html, /What’s blocking progress/);
  assert.doesNotMatch(html, /The decision|Approve|tp-reading-reply/);
});
test('untrusted markup is escaped in all extracted sections', () => {
  const html = render('Failed. Recommended: <img src=x onerror=alert(1)>. Approve <script>alert(1)<\/script>?');
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;img/);
});
test('empty and multiline input remain usable', () => {
  assert.equal(render(null), '');
  assert.match(render('Blocked\n\nChoose option B?'), /Choose option B/);
});

test('plain-English view escapes generated content and never renders decision buttons', () => {
  const html = ctx.window.CCCTicketProse.renderReading({question:'<img src=x>',proposal:'Try the cache',consequence:'Unknown',context:'Paused',alternatives:[{label:'<script>',explanation:'No estimate'}]});
  assert.doesNotMatch(html, /<img|<script|<button/);
  assert.match(html, /&lt;img/);
  assert.match(html, /What changes for you/);
});
test('sentence punctuation before extracted question is preserved', () => {
  const html = render('Host work belongs elsewhere. Approve the change?');
  assert.match(html, /Host work belongs elsewhere\.<\/p>/);
});

test('recommended option label stays tied to the source', () => {
  const data = {question:'Use short rows?',proposal:'Load details on open.',consequence:'Less detail on the board.',context:'Work paused.',alternatives:[]};
  assert.match(ctx.window.CCCTicketProse.renderReading(data, 'Recommended: (A+C) use short rows.'), /The proposal \(A\+C\)/);
});
