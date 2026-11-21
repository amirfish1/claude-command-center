const respondConversationAsset = require('./conversation-assets.cjs');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const unitSource = require('./legacy-dashboard-source.cjs');
const scan = unitSource.slice(unitSource.indexOf('  const _CC_ORCH_RES = ['), unitSource.indexOf('  // Shared conversation-content.js: _ccOrchLabels.'));
const context = vm.createContext({});
vm.runInContext(scan, context);
const labels = command => Array.from(context._ccOrchScan(command));
const resultLabels = (command, output) => Array.from(context._ccOrchResultLabels(labels(command), output));

test('WatchTower commands retain distinct ticket targets and queue context', () => {
  assert.deepEqual(labels('wt find APP-12 --json; wt close APP-13 --summary "Mentions APP-99" --no-code'),
    ['wt find · APP-12', 'wt close · APP-13']);
  assert.deepEqual(labels('"/opt/bin/wt" duplicate APP-12 --of APP-13'), ['wt duplicate · APP-12, APP-13']);
  assert.deepEqual(labels('wt plan show "APP-12"'), ['wt plan · APP-12']);
  assert.deepEqual(labels('wt defer --reason APP-99 APP-12 --until +24h'), ['wt defer · APP-12']);
  assert.deepEqual(labels('wt close APP-12 --summary=APP-99 --no-code'), ['wt close · APP-12']);
  assert.deepEqual(labels('wt status --queue=APP'), ['wt status · APP']);
  assert.deepEqual(labels(['wt claim \\', '    -q APP --worker test-worker --json'].join('\n')), ['wt claim · APP']);
  assert.deepEqual(labels('wt add -q APP --note "Mention APP-99; quoted text"; wt find OTHER-2'),
    ['wt add · APP', 'wt find · OTHER-2']);
  assert.deepEqual(labels('wt status\necho APP-99'), ['wt status']);
  assert.deepEqual(labels('wt find APP-12; wt find APP-13; wt find APP-12'), ['wt find · APP-12', 'wt find · APP-13']);
});

test('filing and claim results identify their ticket without guessing on empty or ambiguous results', () => {
  assert.deepEqual(resultLabels('wt add -q APP --title "Repair the panel"', 'FILED: APP-42\nDispatching worker'), ['wt add · APP-42']);
  assert.deepEqual(resultLabels('wt claim -q APP --json', '{"ref":"APP-42","note":"See OTHER-1"}'), ['wt claim · APP-42']);
  assert.deepEqual(resultLabels('wt take -q APP --json', '{\\"ref\\":\\"APP-42\\"}'), ['wt take · APP-42']);
  assert.deepEqual(resultLabels('wt claim -q APP', 'CLAIMED: APP-42 -> test-worker'), ['wt claim · APP-42']);
  assert.deepEqual(resultLabels('wt claim -q APP', 'HANDED BACK: APP-42 -> test-worker'), ['wt claim · APP-42']);
  for (const output of ['', '{"stop":true}', 'Something mentions APP-42', '{"ref":"OTHER-1"}',
    '[{"ref":"APP-42"},{"ref":"APP-43"}]']) {
    assert.deepEqual(resultLabels('wt claim -q APP --json', output), ['wt claim · APP']);
  }
  assert.deepEqual(resultLabels('wt ls -q APP --json', '{"ref":"APP-42"}'), ['wt ls · APP']);
  assert.deepEqual(resultLabels('wt find APP-12 --json', '{"ref":"APP-42"}'), ['wt find · APP-12']);
});

test('rendered folded chips show returned and explicit refs on desktop and phone', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    const compact = unitSource.slice(unitSource.indexOf('  // Shared conversation-annotations.js: _ccUnitsOf.'), unitSource.indexOf('  // CCC-454: transcript view segmented control'));
    const rows = unitSource.slice(unitSource.indexOf('  // Shared conversation-core.js: _kimiToolRowHtml.'), unitSource.indexOf('  // Thinking block (kimi-web'));
    const regroup = unitSource.slice(unitSource.indexOf('  // Shared conversation-core.js: _kimiFlushRegroups.'), unitSource.indexOf('  // kimi-web parity (messagesToTurns flushGroup)'));
    const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
    for (const width of [1100, 390]) {
      await page.setViewport({ width, height: 700 });
      await page.setContent(`<style>${css}</style><body><div class="conv-pane is-webui-session"><div class="conversations-view"><div class="kimi-turn" id="turn"></div></div></div></body>`);
      await page.evaluate(({ compact, rows, regroup }) => {
        window.escapeHtml = window.escapeAttr = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
        window._kimiToolStatusOf = () => 'ok';
        window._webuiToolDisplayName = n => n;
        window._kimiToolSummary = (n, b) => b.detail || '';
        window._kimiDiffHtml = window._kimiToolGlyph = window._kimiToolPeekHtml = () => '';
        window._kimiNormToolName = n => n;
        window._KIMI_GLYPHS = {};
        (0, eval)(compact + rows + regroup + '\nwindow.markRuns=_ccMarkRuns; window.regroup=_kimiRegroupTools; window.resultLabels=_ccOrchResultLabels;');
        document.querySelector('#turn').innerHTML = _kimiToolRowHtml({ name: 'Bash', detail: 'wt add -q APP', output_preview: 'FILED: APP-42' })
          + _kimiToolRowHtml({ name: 'Bash', detail: 'wt close APP-41 --no-code', output_preview: 'Closed APP-41' });
        window.regroup(document.querySelector('#turn'));
      }, { compact, rows, regroup });
      assert.deepEqual(await page.$$eval('.kimi-tg-orch-chip', chips => chips.map(c => c.dataset.ccOrch)),
        ['wt add · APP-42', 'wt close · APP-41']);
      await page.evaluate(() => {
        document.body.classList.add('conv-compact');
        document.querySelector('#turn').insertAdjacentHTML('afterbegin', '<div class="assistant-text"><p>Checking the tickets.</p></div>');
        markRuns(document.querySelector('.conversations-view'));
      });
      assert.deepEqual(await page.$$eval('.cc-orch-chip', chips => chips.map(c => c.dataset.ccOrch).sort()),
        ['wt add · APP-42', 'wt close · APP-41']);
      assert.equal(await page.$$eval('.cc-orch-chip', chips => chips.every(c => {
        const r = c.getBoundingClientRect();
        return c.checkVisibility() && r.left >= 0 && r.right <= innerWidth && getComputedStyle(c, '::before').content.includes('APP-4');
      })), true);
      if (process.env.CCC_WT_SCREENSHOT_DIR) await page.screenshot({ path: process.env.CCC_WT_SCREENSHOT_DIR + '/chips-' + width + '.png' });
    }
  } finally { await browser.close(); }
});

test('real conversation renderers update existing chips when a filing result arrives later', async () => {
  const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
  assert.ok(app.includes(marker));
  const exposed = app.replace(marker, '\nwindow.__wtContextTest={renderConversationEvents,activePaneId,getConvViewForPane};' + marker);
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (request.isNavigationRequest()) return request.respond({ status: 200, contentType: 'text/html',
        body: fs.readFileSync(require.resolve('../static/index.html'), 'utf8') });
      if (respondConversationAsset(request)) return;
      if (url.pathname === '/static/app.js') return request.respond({ status: 200, contentType: 'application/javascript', body: exposed });
      return request.respond({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.goto('http://ccc-context.test/', { waitUntil: 'load' });
    await page.waitForFunction(() => !!window.__wtContextTest);
    const outcomes = await page.evaluate(() => {
      const h = window.__wtContextTest;
      const paneId = h.activePaneId(), view = h.getConvViewForPane(paneId);
      const outcomes = [];
      let line = 910000;
      document.body.classList.add('conv-compact');
      for (const webui of [false, true]) {
        view.replaceChildren();
        view.closest('.conv-pane').classList.toggle('is-webui-session', webui);
        h.renderConversationEvents([{ type: 'user_text', text: 'Check these tickets', line: ++line },
          { type: 'assistant', line: ++line, blocks: [{ kind: 'text', text: 'Checking the ticket.' },
            { kind: 'tool_use', name: 'Bash', id: 'filing-' + webui, detail: 'wt add -q APP', command: 'wt add -q APP' }] }],
        paneId, { initialLoad: true });
        const row = view.querySelector('[data-tool-use-id="filing-' + webui + '"]');
        const before = row?.dataset.ccOrch;
        h.renderConversationEvents([{ type: 'tool_result', tool_use_id: 'filing-' + webui, line: ++line,
          text: 'FILED: APP-42\nWorker dispatched' }], paneId, { initialLoad: true });
        outcomes.push({ before, after: row?.dataset.ccOrch,
          chips: [...view.querySelectorAll('.activity-run .cc-orch-chip')].map(c => c.dataset.ccOrch) });
      }
      return outcomes;
    });
    assert.deepEqual(outcomes, [false, true].map(() => ({ before: 'wt add · APP', after: 'wt add · APP-42', chips: ['wt add · APP-42'] })));
  } finally { await browser.close(); }
});

test('standalone Claude filing groups keep chips and bounded previews until expanded', async () => {
  const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
  const exposed = app.replace(marker, '\nwindow.__wtContextTest={renderConversationEvents,activePaneId,getConvViewForPane};' + marker);
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (request.isNavigationRequest()) return request.respond({ status: 200, contentType: 'text/html',
        body: fs.readFileSync(require.resolve('../static/index.html'), 'utf8') });
      if (respondConversationAsset(request)) return;
      if (url.pathname === '/static/app.js') return request.respond({ status: 200, contentType: 'application/javascript', body: exposed });
      return request.respond({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.goto('http://ccc-context.test/', { waitUntil: 'load' });
    await page.waitForFunction(() => !!window.__wtContextTest);
    await page.addStyleTag({ content: fs.readFileSync(require.resolve('../static/app.css'), 'utf8') });
    // Keep the real renderer/view but remove unrelated dashboard layout.
    await page.evaluate(() => {
      const view = window.__wtContextTest.getConvViewForPane(window.__wtContextTest.activePaneId());
      const host = view.closest('.conv-pane');
      host.style.cssText = 'position:fixed;inset:0;display:block;overflow:auto;background:var(--bg);z-index:999999';
      const split=document.getElementById('convSplit');split.hidden=false;split.style.display='block';document.body.append(split);
      view.style.cssText = 'display:block;width:100%;height:100%;box-sizing:border-box';
    });
    for (const width of [1100, 390]) {
      await page.setViewport({ width, height: 700 });
      for (const mode of ['normal', 'compact']) {
        const outcome = await page.evaluate(mode => {
          const h = window.__wtContextTest, paneId = h.activePaneId(), view = h.getConvViewForPane(paneId);
          document.body.classList.toggle('conv-compact', mode === 'compact');
          view.replaceChildren();
          const command = 'wt add -q APP --title "Repair the reply label" --text "' + 'Long acceptance details. '.repeat(100) + 'END OF COMMAND"';
          const output = 'FILED: APP-42 Repair the reply label ' + 'Long result details. '.repeat(100) + 'END OF RESULT';
          h.renderConversationEvents([{ type: 'user_text', line: 920000, text: 'Check this ticket' },
            { type: 'assistant', line: 920001, blocks: [{ kind: 'tool_use', name: 'Bash', id: 'standalone-filing', detail: command, command }] }],
          paneId, { initialLoad: true });
          const pending = view.querySelector('.tcg-orch .kimi-tg-orch-chip')?.dataset.ccOrch;
          h.renderConversationEvents([{ type: 'tool_result', line: 920002, tool_use_id: 'standalone-filing', text: output }], paneId, { initialLoad: true });
          // The current renderer folds tools into an activity run. Reveal that
          // native outer disclosure before inspecting the inner command group.
          const run=view.querySelector('.activity-run');
          if(run&&!run.classList.contains('is-expanded'))run.querySelector('button').click();
          const group = view.querySelector('.tool-call-group'), header = group.querySelector('.tool-call-group-header');
          const chip = header.querySelector('.tcg-orch .kimi-tg-orch-chip');
          const fields = [...header.querySelectorAll('.tcg-label, .tcg-result')];
          return { pending, chip: chip?.dataset.ccOrch, visible: chip?.checkVisibility(), closed: group.classList.contains('collapsed'), height: header.getBoundingClientRect().height,
            bounded: fields.every(el => {
              const style = getComputedStyle(el);
              const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.5;
              return style.webkitLineClamp === '2' && el.getBoundingClientRect().height <= lineHeight * 2 + 1;
            }),
            overflow: header.scrollWidth > view.clientWidth,
            fullCommand: group.querySelector('.tool-detail')?.textContent.includes('END OF COMMAND'),
            fullResult: group.querySelector('.tool-result-output')?.textContent.includes('END OF RESULT') };
        }, mode);
        assert.equal(outcome.pending, 'wt add · APP');
        assert.equal(outcome.chip, 'wt add · APP-42');
        assert.equal(outcome.visible, true, JSON.stringify(outcome));
        assert.equal(outcome.closed, true);
        assert.equal(outcome.bounded, true, JSON.stringify(outcome));
        assert.ok(outcome.height < 150, JSON.stringify(outcome));
        assert.equal(outcome.overflow, false);
        assert.equal(outcome.fullCommand, true);
        assert.equal(outcome.fullResult, true);
        await page.$eval('.tool-call-group', el => el.scrollIntoView({ block: 'start' }));
        if (process.env.CCC_WT_SCREENSHOT_DIR) await (await page.$('.tool-call-group')).screenshot({ path: process.env.CCC_WT_SCREENSHOT_DIR + '/native-' + mode + '-' + width + '.png' });
        await page.click('.tool-call-group-header');
        assert.equal(await page.$eval('.tool-call-group-body', el => el.checkVisibility()), true);
        assert.equal(await page.$eval('.tcg-label', el => getComputedStyle(el).webkitLineClamp), 'none');
        assert.equal(await page.$eval('.tcg-result', el => getComputedStyle(el).webkitLineClamp), 'none');
      }
    }
  } finally { await browser.close(); }
});
