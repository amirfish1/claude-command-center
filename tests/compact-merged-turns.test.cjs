const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const compact = app.slice(app.indexOf('  function _ccUnitsOf('),
  app.indexOf('  // CCC-454: transcript view segmented control'));
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');

test('merged turns fold tools and reasoning across lifecycle markers, preserving controls', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<style>${css}</style><body class="conv-compact">
      <div class="conv-pane is-webui-session"><div class="conversations-view">
        <div class="event user_text"><div class="user-msg">Check the layout</div></div>
        <div class="kimi-turn">
          <div class="event assistant kimi-marker"></div>
          <div class="assistant-text" id="narration"><p>Checking the layout.</p></div>
          <div class="kimi-answer-meta"><span class="line-num">L1</span><button>Copy</button></div>
          <div class="kimi-thinking">Reasoning about the layout</div>
          <div class="kimi-tool" id="single"><div class="kimi-tool-head" onclick="this.parentElement.classList.toggle('open')">Bash</div><div class="kimi-tool-peek">Raw output</div><div class="kimi-tool-body">Full output</div></div>
        </div>
        <div class="conv-codex-state state-working">Working</div>
        <div class="conv-live-tool-inline">Tool activity</div>
        <div class="event system codex-subagent">Sub-agent finished</div>
        <div class="kimi-turn">
          <div class="kimi-tool-group collapsed"><div class="kimi-tool-group-head">2 tool calls</div><div class="kimi-tool-group-body"><div class="kimi-tool"></div><div class="kimi-tool"></div></div></div>
          <div class="assistant-text" id="final"><p>Layout checked.</p></div>
          <div class="kimi-answer-meta"><span class="msg-ts">Just now</span><button>Copy</button></div>
          <div class="kimi-tool acp-needs-approval" id="approval"><button>Allow</button></div>
        </div>
      </div></div></body>`);
    await page.evaluate(code => { (0, eval)(code + '\nwindow.markRuns = _ccMarkRuns;'); }, compact);
    const result = await page.evaluate(() => {
      const view = document.querySelector('.conversations-view');
      markRuns(view);
      markRuns(view); // repeated live updates must not duplicate chips
      const chip = view.querySelector('.cc-run-chip');
      return {
        chips: view.querySelectorAll('.cc-run-chip').length,
        label: chip.dataset.ccRunLabel,
        hiddenTools: [...view.querySelectorAll('.kimi-tool-group, #single, .codex-subagent')].every(el => getComputedStyle(el).display === 'none'),
        final: document.querySelector('#final').classList.contains('cc-final'),
        narrationFinal: document.querySelector('#narration').classList.contains('cc-final'),
        approvalVisible: getComputedStyle(document.querySelector('#approval')).display !== 'none',
      };
    });
    assert.deepEqual(result, { chips: 1, label: '3 tool calls · 1 thought', hiddenTools: true,
      final: true, narrationFinal: false, approvalVisible: true });
    await page.click('.cc-run-chip');
    await page.click('#single .kimi-tool-head');
    assert.equal(await page.$eval('#single', el => el.classList.contains('open')), true);
    await page.click('.cc-run-chip');
    assert.equal(await page.$eval('#single', el => getComputedStyle(el).display), 'none');
    await page.evaluate(() => {
      document.body.classList.remove('conv-compact');
      document.querySelector('#single').classList.remove('open');
    });
    assert.notEqual(await page.$eval('#single', el => getComputedStyle(el).display), 'none');
    assert.notEqual(await page.$eval('.kimi-tool-peek', el => getComputedStyle(el).display), 'none');
    // Tool activity before the first narration still has a compact head,
    // with a fold control after expansion. Grouped approvals stay exposed.
    const standalone = await page.evaluate(() => {
      document.body.classList.add('conv-compact');
      const view = document.querySelector('.conversations-view');
      document.querySelector('#narration').remove();
      markRuns(view);
      const head = view.querySelector('.cc-run-head');
      head.click();
      const open = head.classList.contains('cc-run-open');
      head.click();
      const closed = !head.classList.contains('cc-run-open');
      const approval = document.querySelector('#approval');
      view.querySelector('.kimi-tool-group-body').appendChild(approval);
      markRuns(view);
      return { open, closed, approvalUnfolded: getComputedStyle(approval.parentElement).display !== 'none' };
    });
    assert.deepEqual(standalone, { open: true, closed: true, approvalUnfolded: true });
  } finally {
    await browser.close();
  }
});

test('Codex wt / CCC API tool rows get purple orchestration chips (CCC-227)', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    const rowFn = (name) => { const s = app.indexOf('  function ' + name + '('); return app.slice(s, app.indexOf('\n  }', s) + 4); };
    await page.setContent(`<style>${css}</style><body class="conv-compact">
      <div class="conv-pane is-webui-session"><div class="conversations-view">
        <div class="event user_text"><div class="user-msg">Drain the queue</div></div>
        <div class="kimi-turn" id="turn"></div>
      </div></div></body>`);
    const result = await page.evaluate((code) => {
      window.escapeHtml = window.escapeAttr = s => String(s);
      window._kimiToolStatusOf = () => 'ok';
      window._webuiToolDisplayName = n => n;
      window._kimiToolSummary = (n, b) => b.detail || '';
      window._kimiDiffHtml = () => '';
      window._kimiNormToolName = n => n;
      window._kimiToolGlyph = () => '';
      window._kimiToolPeekHtml = () => '';
      window._KIMI_GLYPHS = {};
      (0, eval)(code + '\nwindow.markRuns = _ccMarkRuns; window.rowHtml = _kimiToolRowHtml;');
      const turn = document.querySelector('#turn');
      turn.innerHTML = rowHtml({ kind: 'tool_use', name: 'Bash', detail: "wt claim -q CCC --json" })
        + rowHtml({ kind: 'tool_use', name: 'Bash', detail: 'curl', command: 'curl -s http://127.0.0.1:8091/api/sessions' });
      const view = document.querySelector('.conversations-view');
      markRuns(view);
      return [...view.querySelectorAll('.cc-orch-chip')].map(c => ({ label: c.dataset.ccOrch, shown: getComputedStyle(c).display !== 'none' }));
    }, compact + rowFn('_kimiToolRowHtml'));
    assert.deepEqual(result, [
      { label: 'wt claim', shown: true },
      { label: 'CCC API /api/sessions', shown: true },
    ]);
  } finally {
    await browser.close();
  }
});
