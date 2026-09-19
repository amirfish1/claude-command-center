const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('puppeteer');

const app = fs.readFileSync('static/app.js', 'utf8');
const sourceStart = app.indexOf('  function f2ModelsForEngine(');
const sourceEnd = app.indexOf('\n  // CCC-823:', sourceStart);
const pickerSource = app.slice(sourceStart, sourceEnd);
const handlerStart = app.indexOf("  document.addEventListener('click', (ev) => {", app.indexOf('  function f2InterceptEnterSend('));
const handlerEnd = app.indexOf('\n\n  // Launch-spec overrides.', handlerStart);
const quickPickHandlerSource = app.slice(handlerStart, handlerEnd);
let browser;

test.before(async () => { browser = await puppeteer.launch({ headless: true }); });
test.after(async () => { await browser?.close(); });

test('continuation launch picker offers usage-ranked engine, model, and effort chips', async () => {
  const page = await browser.newPage();
  try {
    await page.setContent('<div id="target"></div>');
    const html = await page.evaluate(({ source, handlerSource }) => {
      const setup = `
        var F2_LAUNCH_ENGINES = [
          { id: 'claude', label: 'Claude', fallback: [{ id: 'sonnet-5', label: 'Sonnet 5' }] },
          { id: 'codex', label: 'Codex', fallback: [{ id: 'gpt-5.3-codex', label: 'GPT-5.3 Codex' }] }
        ];
        var F2_LAUNCH_EFFORTS = [{ id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }];
        var f2PaneState = new Map();
        var _cachedServerModelPicks = [
          { engine: 'claude', model: 'sonnet-5', effort: 'high' },
          { engine: 'codex', model: 'gpt-5.3-codex', effort: 'medium' }
        ];
        var MODEL_OPTIONS_BY_ENGINE = {};
        var escapeAttr = value => String(value || '');
        var escapeHtml = value => String(value || '');
        var renderCalls = 0;
        function f2PaneKey(paneId) { return paneId || 'p1'; }
        function f2RenderComposer() { renderCalls += 1; }
        function effortLevelsForEngine() { return F2_LAUNCH_EFFORTS; }
      `;
      (0, eval)(setup + source + handlerSource);
      f2PaneState.set('p1', { launch: { engine: 'claude', model: 'sonnet-5', effort: 'high' } });
      return f2ConfigHtml({ engine: 'claude', model: 'sonnet-5', effort: 'high' });
    }, { source: pickerSource, handlerSource: quickPickHandlerSource });
    await page.$eval('#target', (el, value) => {
      el.innerHTML = '<div class="conv-pane" data-pane-id="p1"><div class="f2c-panel">' + value + '</div></div>';
    }, html);

    const picks = await page.$$eval('[data-f2-launch-pick]', chips => chips.map(chip => ({
      engine: chip.dataset.engine,
      model: chip.dataset.model,
      effort: chip.dataset.effort,
      label: chip.textContent,
    })));
    assert.deepEqual(picks, [
      { engine: 'claude', model: 'sonnet-5', effort: 'high', label: 'Claude · Sonnet 5 · High' },
      { engine: 'codex', model: 'gpt-5.3-codex', effort: 'medium', label: 'Codex · GPT-5.3 Codex · Medium' },
    ]);
    await page.click('[data-engine="codex"][data-f2-launch-pick]');
    assert.deepEqual(await page.evaluate(() => ({ launch: f2PaneState.get('p1').launch, renderCalls })), {
      launch: { engine: 'codex', model: 'gpt-5.3-codex', effort: 'medium' },
      renderCalls: 1,
    });
  } finally {
    await page.close();
  }
});

test('launch picker hides engines disabled in Setup > Engines', async () => {
  const page = await browser.newPage();
  try {
    const result = await page.evaluate(({ source, handlerSource }) => {
      const setup = `
        var F2_LAUNCH_ENGINES = [
          { id: 'claude', label: 'Claude', fallback: [{ id: 'sonnet-5', label: 'Sonnet 5' }] },
          { id: 'codex', label: 'Codex', fallback: [{ id: 'gpt-5.3-codex', label: 'GPT-5.3 Codex' }] },
          { id: 'kimi', label: 'Kimi', fallback: [{ id: 'kimi-code/k3', label: 'K3' }] }
        ];
        var F2_LAUNCH_EFFORTS = [{ id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }];
        var f2PaneState = new Map();
        var _cachedServerModelPicks = [
          { engine: 'claude', model: 'sonnet-5', effort: 'high' },
          { engine: 'kimi', model: 'kimi-code/k3', effort: 'medium' }
        ];
        var MODEL_OPTIONS_BY_ENGINE = {};
        var spawnDefaultsState = { disabled_engines: ['kimi'], models: {}, engine: 'claude' };
        var escapeAttr = value => String(value || '');
        var escapeHtml = value => String(value || '');
        function f2PaneKey(paneId) { return paneId || 'p1'; }
        function f2RenderComposer() {}
        function getSpawnEngine() { return 'claude'; }
        function effortLevelsForEngine() { return F2_LAUNCH_EFFORTS; }
      `;
      (0, eval)(setup + source + handlerSource);
      const engineOptions = (launch) => Array.from(
        new DOMParser().parseFromString(f2ConfigHtml(launch), 'text/html')
          .querySelectorAll('select[data-f2-launch="engine"] option')
      ).map(o => o.value);
      const options = engineOptions({ engine: 'claude', model: 'sonnet-5', effort: 'high' });
      // A disabled engine that is nonetheless the current pick stays listed.
      const disabledCurrent = engineOptions({ engine: 'kimi', model: 'kimi-code/k3', effort: 'medium' });
      // Disabled gate engine falls back to an enabled default.
      const st = f2StateFor('p1', 'sid-1', { engine: 'kimi' });
      // Quick picks for a disabled engine are filtered out.
      const pickEngines = f2TopLaunchPicks().map(p => p.engine);
      return { options, disabledCurrent, stateEngine: st.launch.engine, pickEngines };
    }, { source: pickerSource, handlerSource: quickPickHandlerSource });
    assert.deepEqual(result.options, ['claude', 'codex']);
    assert.deepEqual(result.disabledCurrent, ['claude', 'codex', 'kimi']);
    assert.equal(result.stateEngine, 'claude');
    assert.deepEqual(result.pickEngines, ['claude']);
  } finally {
    await page.close();
  }
});
