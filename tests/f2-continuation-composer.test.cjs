const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync('static/app.js', 'utf8');
const section = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
const source = section('  const _CONTINUATION_ORIGIN_RE', '  function _continuationRowForSid')
  + section('  function f2ContinuedIntoSessionId', '  // ── composer plumbing')
  + section('  function f2RenderComposer', '  // The sheet opens upward');
let browser;
test.before(async () => { browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] }); });
test.after(async () => { await browser?.close(); });

test('continuation rail and route agree across archive refreshes and missing successors', async () => {
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="conv-input-bar"><div id="rail"></div><textarea id="input">keep my draft</textarea><div id="panel"></div></div>');
    await page.evaluate(source => {
      (0, eval)(`
        var currentSession = {id:'origin-session'}, currentConversation = 'origin-session';
        var conversationsData = [], archiveData = [], edges = {};
        var f2PaneState = new Map(), f2ManualPanes = new Map(), f2DismissedPanes = new Map();
        var f2RailEl = () => document.querySelector('#rail');
        var f2PanelEl = () => document.querySelector('#panel');
        var f2InputEl = () => document.querySelector('#input');
        var f2PaneKey = () => 'p1', _f2ContinuationEdges = () => edges;
        var f2ResumeGate = () => ({tokens:208000,model:'example',ageMin:71,cache:{railNote:'cache likely cold'}});
        var f2StateFor = () => ({}), f2ComposerText = () => f2InputEl().value;
        var f2FmtTokens = () => '208k', f2FmtAge = () => '71 min';
        var escapeHtml = s => String(s), escapeAttr = s => String(s);
        var shortSessionId = s => s.slice(0,8), f2FitSheet = () => {};
        var f2RoutesHtml = () => '<button>Continue new</button>';
      ` + source);
      window.paint = () => {
        f2RenderComposer('p1');
        return {rail: f2RailEl().textContent, panel: f2PanelEl().textContent,
          continued: document.querySelector('.conv-input-bar').classList.contains('is-f2-continued'),
          draft: f2InputEl().value, disabled: f2InputEl().disabled};
      };
    }, source);
    const cold = await page.evaluate(() => paint());
    assert.match(cold.rail, /Large and stale/);
    for (const successor of [
      {id:'child-session',continued_from_session_id:'origin-session'},
      {id:'child-session',first_message:'Origin session id: origin-session'},
      {id:'child-session',hermes_continued_from:'origin-session'},
    ]) {
      for (const pool of ['live', 'archive']) {
        const continued = await page.evaluate(({successor,pool}) => {
          conversationsData = pool === 'live' ? [successor] : [];
          archiveData = pool === 'archive' ? [successor] : [];
          return paint();
        }, {successor,pool});
        assert.match(continued.rail, /^Continued/);
        assert.match(continued.panel, /Go to newer session/);
        assert.equal(continued.continued, true);
        assert.equal(continued.draft, 'keep my draft');
        assert.equal(continued.disabled, false);
      }
    }
    const removed = await page.evaluate(() => { conversationsData=[]; archiveData=[]; return paint(); });
    assert.match(removed.rail, /Large and stale/);
    assert.equal(removed.panel, 'Continue new');
    assert.equal(removed.continued, false);
    const local = await page.evaluate(() => { edges = {'child-session':{continued_from:'origin-session',ts:1}}; return paint(); });
    assert.match(local.rail, /^Continued/);
    assert.match(local.panel, /Go to newer session/);
    const artifact = process.env.CCC_CONTINUATION_SCREENSHOT;
    if (artifact) await page.screenshot({path:artifact});
    assert.equal(await page.evaluate(() => {
      edges={}; conversationsData=[{id:'child-session',parent_session_id:'origin-session'}];
      return f2ContinuedIntoSessionId('origin-session');
    }), '', 'ordinary spawned children are not continuations');
  } finally { await page.close(); }
});
