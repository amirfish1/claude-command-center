const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync('static/app.js', 'utf8');
const picker = app.slice(app.indexOf('  const MODEL_PICKER_ENGINE_GLYPHS ='), app.indexOf('  function enterNewSessionMode()'));
let browser;
test.before(async () => { browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] }); });
test.after(async () => { await browser?.close(); });

async function pickerPage(picks, fetched = []) {
  const page = await browser.newPage();
  await page.setContent('<div id="nsModelPickerPills"></div>');
  await page.evaluate(({ picker, picks, fetched }) => {
    window._defaultModelsByEngine = { codex: 'model-a' };
    window.spawnDefaultsState = { engine: 'codex', models: {} };
    window.spawnEffortChoiceDirty = true;
    window.nsAdvisor = { manual: false, applying: false };
    window.$convInputEffortSelect = { value: 'high' };
    window.$convInputModelSelect = { value: 'model-a', style: { display: 'none' } };
    window.$convInputEngineSelect = null;
    window.$kptToolbarEngineSelect = null;
    window.MODEL_OPTIONS_BY_ENGINE = {};
    // Catalog ownership is covered separately; this fixture exercises the
    // strip's ordering, model deduplication and independent effort choice.
    window.SPAWN_DEFAULT_ENGINES = ['codex'];
    window._modelAllowedForEngine = () => true;
    window._normalizeModelId = value => value;
    window._spawnEngineChosenByUser = false;
    window._spawnModelChosenByUserForEngine = new Set();
    window.currentConversation = picks ? 'fixture' : '__new__';
    window.REASONING_LEVEL_LABELS = { high: 'High', low: 'Low' };
    window.escapeAttr = window.escapeHtml = value => String(value || '');
    window.getSpawnEngine = () => spawnDefaultsState.engine;
    window.normalizeSpawnDefaultEngine = engine => engine;
    window.syncSpawnEngineDependentUi = () => syncNsModelPickerPillsSelection();
    window.getSpawnCwd = window.spawnCwdLabel = window.activePaneId = () => '';
    window.updatePaneHeader = window.requestClaudePrewarm = () => {};
    window.fetch = async () => ({ ok: true, json: async () => ({ ok: true, picks: fetched }) });
    (0, eval)(picker);
    window._cachedServerModelPicks = picks;
    renderNsModelPickerPills();
  }, { picker, picks, fetched });
  return page;
}
const modelKeys = page => page.$$eval('#nsModelPickerPills button', buttons => buttons.map(button => `${button.dataset.engine}/${button.dataset.model}`));
const pick = (model, effort = 'high') => ({ engine: 'codex', model, effort });

test('effort variants render one model chip with a model-only label', async () => {
  const page = await pickerPage([pick('model-a'), pick('model-a', 'low'), pick('model-b')]);
  try {
    assert.deepEqual(await modelKeys(page), ['codex/model-a', 'codex/model-b']);
    assert.equal(await page.$eval('#nsModelPickerPills', el => /\(High\)|\(Low\)/.test(el.textContent)), false);
  } finally { await page.close(); }
});

test('clicking a chip preserves positions, effort, and selected state after a history refresh', async () => {
  const page = await pickerPage([pick('model-a'), pick('model-b', 'low')], [pick('model-b', 'low'), pick('model-a')]);
  try {
    const before = await modelKeys(page);
    await page.click('[data-model="model-b"]');
    await page.evaluate(async () => { await fetchModelPickerPicksFromServer(true); renderNsModelPickerPills(); });
    assert.deepEqual(await modelKeys(page), before);
    assert.equal(await page.evaluate(() => $convInputEffortSelect.value), 'high');
    assert.equal(await page.$eval('[aria-checked="true"]', el => el.dataset.model), 'model-b');
    await page.evaluate(() => { $convInputEffortSelect.value = 'low'; syncNsModelPickerPillsSelection(); });
    assert.equal(await page.$eval('[aria-checked="true"]', el => el.dataset.model), 'model-b');
  } finally { await page.close(); }
});

test('initial server fetch deduplicates before applying the ten-model limit', async () => {
  const fetched = [pick('model-a'), pick('model-a', 'low'), ...Array.from({ length: 9 }, (_, i) => pick(`model-${i}`))];
  const page = await pickerPage(null, fetched);
  try {
    await page.waitForSelector('#nsModelPickerPills button');
    const keys = await modelKeys(page);
    assert.equal(keys.length, 10);
    assert.equal(new Set(keys).size, 10);
  } finally { await page.close(); }
});

test('unnamed picks become a named model or drop; labels never say default', async () => {
  const page = await pickerPage([pick(''), pick('auto'), pick('model-b')]);
  try {
    // The saved default (model-a) stands in for blank and "auto" history.
    assert.deepEqual(await modelKeys(page), ['codex/model-a', 'codex/model-b']);
    await page.evaluate(() => {
      _defaultModelsByEngine.codex = 'adaptive';
      MODEL_OPTIONS_BY_ENGINE.codex = [{ id: 'adaptive', label: 'Adaptive (default)' }, { id: 'model-c', label: 'Model C (free, default)' }];
      _cachedServerModelPicks = [{ engine: 'codex', model: 'adaptive' }, { engine: 'codex', model: '' }];
      renderNsModelPickerPills();
    });
    assert.deepEqual(await modelKeys(page), ['codex/model-c']);
    assert.equal(await page.$eval('#nsModelPickerPills', el => /default/i.test(el.textContent)), false);
    // No named model to stand in: the engine gets no chip at all.
    assert.deepEqual(await page.evaluate(() => {
      MODEL_OPTIONS_BY_ENGINE.codex = [{ id: 'auto', label: 'Auto (default)' }];
      return getTopSpawnPicks([{ engine: 'codex', model: 'auto' }, { engine: 'codex', model: '' }]);
    }), []);
  } finally { await page.close(); }
});
