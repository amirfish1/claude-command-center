// Slice 3 of the Codex single-renderer merge (see
// CCC-private-docs/plans/2026-09-12-codex-single-renderer-merge.md): new
// event-kind renderers, still inert (nothing feeds them live data until
// Slice 4). Covers:
//   - `kind:'diff'` turn-level diff block (_turnDiffBlockHtml)
//   - `kind:'image_generation'` block (_imageGenerationBlockHtml)
//   - the `codex_request` pending-event card (_codexRequestCardHtml) and its
//     result-shaping helper (_codexRequestResultForDecision)
//   - the "hide the strip when a card exists" mitigation for the plan's
//     documented "Two approval UIs" risk (_codexRequestCardShowing)
//
// These are string-builder/DOM helpers that call escapeHtml/escapeAttr
// (real `document.createElement`-based) and renderImageDescriptors, so this
// uses the puppeteer + eval-into-window.__fns pattern established by
// tests/codex-live-key-upsert.test.cjs, not the plain vm.runInContext
// pattern (which has no DOM).
const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function extract(start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `range not found: ${start}`);
  return source.slice(a, b);
}

const helpersCode = [
  extract('  function escapeHtml(', '  function unescapeHtml('),
  extract('  function renderImageDescriptors(', '  function tsSpan('),
  extract('  function _diffLinesHtml(', '  function renderConversationEvents('),
].join('\n');

let browser;
before(async () => { browser = await puppeteer.launch({ headless: true }); });
after(async () => { if (browser) await browser.close(); });

async function withPage(fn) {
  const page = await browser.newPage();
  try {
    return await fn(page);
  } finally {
    await page.close();
  }
}

async function loadHelpers(page) {
  await page.evaluate((code) => {
    (0, eval)('window.__fns = (() => {\nvar currentConversation = "conv-test-1";\n'
      + code
      + '\nreturn { _turnDiffBlockHtml, _imageGenerationBlockHtml, _codexRequestCardHtml, '
      + '_codexRequestResultForDecision, _codexRequestCardShowing };\n})();');
  }, helpersCode);
}

test('kind:diff block renders a file-scoped unified diff with add/delete coloring', async () => {
  await withPage(async (page) => {
    await page.setContent('<div></div>');
    await loadHelpers(page);
    const html = await page.evaluate(() => window.__fns._turnDiffBlockHtml({
      kind: 'diff',
      files: [{
        path: 'src/foo.py',
        kind: 'modify',
        diff: '@@ -1,2 +1,2 @@\n-old line\n+new line\n context line',
      }],
    }));
    assert.match(html, /turn-diff-card/);
    assert.match(html, /src\/foo\.py/);
    assert.match(html, /diff-hunk/);
    assert.match(html, /diff-add"[^>]*>\+new line/);
    assert.match(html, /diff-delete"[^>]*>-old line/);
  });
});

test('kind:diff block renders a raw unified diff string when no files list is present', async () => {
  await withPage(async (page) => {
    await page.setContent('<div></div>');
    await loadHelpers(page);
    const html = await page.evaluate(() => window.__fns._turnDiffBlockHtml({
      kind: 'diff', diff: '+added\n-removed',
    }));
    assert.match(html, /diff-add"[^>]*>\+added/);
    assert.match(html, /diff-delete"[^>]*>-removed/);
  });
});

test('kind:diff block with no diff content renders nothing', async () => {
  await withPage(async (page) => {
    await page.setContent('<div></div>');
    await loadHelpers(page);
    const html = await page.evaluate(() => window.__fns._turnDiffBlockHtml({ kind: 'diff' }));
    assert.equal(html, '');
  });
});

test('kind:image_generation block shows the prompt and a lazy-loaded image', async () => {
  await withPage(async (page) => {
    await page.setContent('<div></div>');
    await loadHelpers(page);
    const html = await page.evaluate(() => window.__fns._imageGenerationBlockHtml(
      { kind: 'image_generation', id: 'ig_1', prompt: 'a red bicycle', status: 'completed', image_idx: 0 },
      { line: 42 },
    ));
    assert.match(html, /image-gen-card/);
    assert.match(html, /a red bicycle/);
    assert.match(html, /<img class="msg-image"/);
    assert.match(html, /\/api\/conv-image\?conversation_id=conv-test-1&amp;line=42&amp;idx=0/);
  });
});

test('kind:image_generation block with no result image omits the media section', async () => {
  await withPage(async (page) => {
    await page.setContent('<div></div>');
    await loadHelpers(page);
    const html = await page.evaluate(() => window.__fns._imageGenerationBlockHtml(
      { kind: 'image_generation', id: 'ig_2', prompt: 'still generating', status: 'generating' },
      { line: 43 },
    ));
    assert.match(html, /image-gen-card/);
    assert.match(html, /still generating/);
    assert.doesNotMatch(html, /<img/);
  });
});

test('codex_request approval card renders decision buttons', async () => {
  await withPage(async (page) => {
    await page.setContent('<div></div>');
    await loadHelpers(page);
    const html = await page.evaluate(() => window.__fns._codexRequestCardHtml({
      key: 'req-key-1',
      request_id: 'req-1',
      method: 'requestApproval',
      generation: 3,
      params: { command: ['rm', '-rf', 'build'], cwd: '/repo' },
    }, 'thread-9'));
    assert.match(html, /codex-request-card/);
    assert.match(html, /data-request-id="req-1"/);
    assert.match(html, /data-request-method="requestApproval"/);
    assert.match(html, /data-thread-id="thread-9"/);
    assert.match(html, /data-request-generation="3"/);
    assert.match(html, /rm -rf build/);
    assert.match(html, /data-decision="accept"/);
    assert.match(html, /data-decision="decline"/);
  });
});

test('codex_request question card renders options and a free-text fallback', async () => {
  await withPage(async (page) => {
    await page.setContent('<div></div>');
    await loadHelpers(page);
    const html = await page.evaluate(() => window.__fns._codexRequestCardHtml({
      key: 'req-key-2',
      request_id: 'req-2',
      method: 'requestUserInput',
      params: { questions: [{ id: 'q1', question: 'Which branch?', options: ['main', 'dev'] }] },
    }, 'thread-9'));
    assert.match(html, /Which branch\?/);
    assert.match(html, /data-question-id="q1" data-answer="main"/);
    assert.match(html, /codex-request-free-text/);
  });
});

test('_codexRequestResultForDecision shapes results per method', () => {
  return withPage(async (page) => {
    await page.setContent('<div></div>');
    await loadHelpers(page);
    const results = await page.evaluate(() => ({
      question: window.__fns._codexRequestResultForDecision('requestUserInput', 'send-answer', { answers: { q1: 'main' } }),
      questionCancel: window.__fns._codexRequestResultForDecision('requestUserInput', 'cancel', {}),
      permission: window.__fns._codexRequestResultForDecision('session/request_permission', 'allow-selected', { permissions: { write: true } }),
      elicitationAccept: window.__fns._codexRequestResultForDecision('elicitation/create', 'accept', {}),
      elicitationDecline: window.__fns._codexRequestResultForDecision('elicitation/create', 'decline', {}),
      approval: window.__fns._codexRequestResultForDecision('requestApproval', 'accept', {}),
    }));
    assert.deepEqual(results.question, { answers: { q1: 'main' } });
    assert.deepEqual(results.questionCancel, { answers: {} });
    assert.deepEqual(results.permission, { permissions: { write: true }, scope: 'turn' });
    assert.deepEqual(results.elicitationAccept, { action: 'accept' });
    assert.deepEqual(results.elicitationDecline, { action: 'decline' });
    assert.deepEqual(results.approval, { decision: 'accept' });
  });
});

test('_codexRequestCardShowing suppresses the live strip once a matching card is present', async () => {
  await withPage(async (page) => {
    await page.setContent('<div class="conversations-view"></div>');
    await loadHelpers(page);
    const before1 = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      return window.__fns._codexRequestCardShowing(view, 'req-1');
    });
    assert.equal(before1, false, 'no card yet, strip should not be suppressed');

    const afterCard = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      const html = window.__fns._codexRequestCardHtml({
        key: 'req-key-1', request_id: 'req-1', method: 'requestApproval', params: {},
      }, 'thread-1');
      const wrap = document.createElement('div');
      wrap.innerHTML = html;
      view.appendChild(wrap.firstElementChild);
      return window.__fns._codexRequestCardShowing(view, 'req-1');
    });
    assert.equal(afterCard, true, 'a rendered card for the same request id must suppress the strip');

    const afterResolved = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      view.querySelector('.codex-request-card').classList.add('is-resolved');
      return window.__fns._codexRequestCardShowing(view, 'req-1');
    });
    assert.equal(afterResolved, false, 'a resolved card no longer suppresses the strip');

    const differentRequest = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      return window.__fns._codexRequestCardShowing(view, 'req-other');
    });
    assert.equal(differentRequest, false, 'a card for a different request id must not suppress');
  });
});
