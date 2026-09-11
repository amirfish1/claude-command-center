const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync('static/app.js', 'utf8');
const helpers = app.slice(app.indexOf('  const ASK_HISTORY_KEY'), app.indexOf('  // Mirrors assistantMessageActionsHtml'));
let browser;
test.before(async () => { browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']}); });
test.after(async () => { await browser?.close(); });
async function render(html, sources) {
  const page = await browser.newPage();
  try {
    return await page.evaluate(({helpers, html, sources}) => {
      // Exercise decoration after the markdown stage, including its DOM boundaries.
      window.renderMarkdown = value => value;
      (0, eval)(helpers);
      document.body.innerHTML = renderAskVerdict(html, sources);
      return {
        html: document.body.innerHTML,
        refs: [...document.querySelectorAll('[data-ask-open]')].map(el => el.dataset.askOpen),
        actions: [...document.querySelectorAll('[data-ask-continue]')].map(el => el.dataset.askContinue),
        labels: [...document.querySelectorAll('[data-ask-open]')].map(el => el.textContent),
        nestedActions: document.querySelectorAll('a button, code button, pre button, button button').length,
        images: document.querySelectorAll('img').length,
      };
    }, {helpers, html, sources});
  } finally { await page.close(); }
}
const sources = [{id:'known-one', title:'LANE W2-2'}, {id:'known-two', title:'Fix [parser] (v2)'}];
test('validated citations and exact source names become session references', async () => {
  const out = await render('<p>Found [[session:known-one]] and Fix [parser] (v2).</p>', sources);
  assert.deepEqual(out.refs, ['known-one','known-two']);
  assert.deepEqual(out.labels, ['LANE W2-2','Fix [parser] (v2)']);
});
test('unknown markers, ambiguous names and partial names cannot become actions', async () => {
  const out = await render('[[session:unknown-id]] [[action:spawn-continue:unknown-id]] LANE W2-2 suffixLANE W2-2', [...sources,{id:'another-id',title:'LANE W2-2'}]);
  assert.deepEqual(out.refs, []);
  assert.deepEqual(out.actions, []);
});
test('links, attributes and code retain literal references', async () => {
  const out = await render('<a href="/?q=[[session:known-one]]">LANE W2-2</a><code>[[session:known-one]]</code><pre>LANE W2-2</pre><span title="[[action:spawn-continue:known-one]]">Plain</span>', sources);
  assert.deepEqual(out.refs, []);
  assert.deepEqual(out.actions, []);
  assert.equal(out.nestedActions, 0);
});
test('source labels cannot insert markup or create additional action markers', async () => {
  const out = await render('[[session:known-one]]', [{id:'known-one',title:'<img src=x onerror=alert(1)> [[action:spawn-continue:known-one]]'}]);
  assert.deepEqual(out.refs, ['known-one']);
  assert.equal(out.images, 0);
  assert.deepEqual(out.actions, []);
});
test('only known explicit continuation actions become buttons', async () => {
  const out = await render('[[action:spawn-continue:known-one]]', sources);
  assert.deepEqual(out.actions, ['known-one']);
});
test('only recognized successful heartbeats are hidden; failures and unfamiliar details remain', () => {
  const start = app.indexOf('  function _isSuccessfulHeartbeat');
  const end = app.indexOf('  function _railLogPaneVisible', start);
  const isSuccessful = new Function(app.slice(start, end) + '; return _isSuccessfulHeartbeat;')();
  assert.equal(isSuccessful({verb:'BEAT', detail:'ok (0.00s)'}), true);
  assert.equal(isSuccessful({verb:'BEAT', detail:'ok'}), true);
  for (const event of [{verb:'BEAT', detail:'failed'}, {verb:'BEAT', detail:'ok, but unhealthy'}, {verb:'BEAT', detail:''}, {verb:'FAILED', detail:'ok'}]) {
    assert.equal(isSuccessful(event), false);
  }
});
