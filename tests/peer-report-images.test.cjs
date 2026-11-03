const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const helper = app.slice(app.indexOf('  function peerReportImagesHtml('), app.indexOf('  function renderImageDescriptors('));
const context = vm.createContext({ URLSearchParams });
vm.runInContext(`
  const PEER_LABEL_RE = /^([A-Z][A-Z0-9 _/-]{1,30}):[ \\t]*(.*)$/;
  const currentConversation = 'report-session';
  const currentSession = { id: 'report-session' };
  const sessionIdByConv = {};
  function activeConvRepoPath() { return '/tmp/project'; }
  function escapeAttr(s) { return String(s).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'); }
  const escapeHtml = escapeAttr;
  ${helper}
`, context);
const render = text => context.peerReportImagesHtml(text);
const sources = html => Array.from(html.matchAll(/data-full-src="([^"]+)"/g), m =>
  new URL(m[1].replaceAll('&amp;', '&'), 'http://ccc.test'));

test('peer report resolves semicolon-separated images beside an explicit report file', () => {
  const html = render('STATUS: SUCCEEDED\nFILES: /tmp/report/verification.md appended; evidence.json; desktop.png; diagram.png; phone.png (same directory).');
  assert.deepEqual(sources(html).map(u => u.searchParams.get('path')),
    ['/tmp/report/desktop.png', '/tmp/report/diagram.png', '/tmp/report/phone.png']);
  for (const url of sources(html)) {
    assert.equal(url.searchParams.get('session_id'), 'report-session');
    assert.equal(url.searchParams.get('repo_path'), '/tmp/project');
    assert.equal(url.searchParams.has('preview'), false, 'full-size source remains available');
  }
  assert.equal((html.match(/preview=1/g) || []).length, 3);
});

test('peer report handles explicit directories, continuation lines and duplicate images', () => {
  const html = render('FILES: /tmp/screens/\nfirst.png, second.webp; /tmp/screens/first.png\nSUMMARY: unrelated.png');
  assert.deepEqual(sources(html).map(u => u.searchParams.get('path')),
    ['/tmp/screens/first.png', '/tmp/screens/second.webp']);
});

test('peer gallery skips unresolved names, URLs and uploads already embedded in prose', () => {
  assert.equal(render('FILES: missing.png; https://example.com/picture.png'), '');
  assert.equal(render('SUMMARY: /tmp/report/screen.png'), '');
  assert.equal(render('FILES: /home/test/.claude/command-center/pasted-images/paste-test.png'), '');
});

test('peer filenames are escaped in attributes and captions', () => {
  const html = render('FILES: /tmp/a&b/screen.png');
  assert.equal(sources(html)[0].searchParams.get('path'), '/tmp/a&b/screen.png');
  assert.match(html, /a&amp;b/);
  assert.ok(!html.includes('/tmp/a&b/'));
});
