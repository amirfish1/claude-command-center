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

async function pickerPage(picks, fetched = picks || []) {
  const page = await browser.newPage();
  await page.setContent('<div id="nsModelPickerPills"></div>');
  await page.evaluate(({ picker, picks, fetched }) => {
    window._defaultModelsByEngine = { codex: 'model-a' };
    window.spawnDefaultsState = { engine: 'codex', models: {}, blockedModels: new Set(), disabled_engines: [] };
    window._emnInstalled = { codex: true, claude: true };
    window.spawnEffortChoiceDirty = true;
    window.nsAdvisor = { manual: false, applying: false, data: {config: {enabled: false}} };
    window.$convInputEffortSelect = { value: 'high' };
    window.$convInputModelSelect = { value: 'model-a', style: { display: 'none' } };
    window.$convInputEngineSelect = null;
    window.$kptToolbarEngineSelect = null;
    window.MODEL_OPTIONS_BY_ENGINE = {};
    // Catalog ownership is covered separately; this fixture exercises the
    // strip's ordering, model deduplication and independent effort choice.
    window.SPAWN_DEFAULT_ENGINES = ['codex', 'claude'];
    window._modelAllowedForEngine = () => true;
    window._normalizeModelId = value => String(value || '').replace(/^claude-/, '');
    window.scheduleClaudePrewarm = () => {};
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

async function enableRankedPicker(page) {
  await page.evaluate(() => {
    currentConversation = '__new__';
    nsAdvisor = {depth: 'routine', manual: false, applying: false, data: {config: {enabled: true}}};
    window.nsAdvisorChoices = () => nsAdvisor.depth === 'deep'
      ? [{engine: 'codex', model: 'deep-model', effort: 'medium', unavailable: ''}]
      : [{engine: 'codex', model: 'routine-first', effort: 'medium', unavailable: ''},
         {engine: 'codex', model: 'routine-next', effort: 'low', unavailable: ''},
         {engine: 'codex', model: 'unavailable-model', effort: 'low', unavailable: 'Engine unavailable'}];
    window.currentSpawnChoice = () => ({engine: getSpawnEngine(), model: _defaultModelsByEngine[getSpawnEngine()], effort: $convInputEffortSelect.value});
    window.getConvView = () => null;
    window.updateInputBar = () => {};
    renderNsModelPickerPills();
  });
}

test('advisor keeps depth rankings and explicit thinking separate from manual history', async () => {
  const page = await pickerPage([pick('unrelated-expensive-model')]);
  try {
    await enableRankedPicker(page);
    assert.deepEqual(await page.$$eval('.orch-tier-chip', buttons => buttons.map(b => b.dataset.model)), ['routine-first', 'routine-next']);
    assert.deepEqual(await page.$$eval('[data-rank]', buttons => buttons.map(b => [b.dataset.rank, b.dataset.model, b.dataset.effort])),
      [['1', 'routine-first', 'medium'], ['2', 'routine-next', 'low']]);
    assert.match(await page.$eval('[data-rank="1"]', el => el.textContent), /Medium/);
    await page.evaluate(() => { nsAdvisor.depth = 'deep'; renderNsModelPickerPills(); });
    assert.deepEqual(await page.$$eval('[data-rank]', buttons => buttons.map(b => b.dataset.model)), ['deep-model']);
    assert.match(await page.$eval('#nsModelPickerPills', el => el.getAttribute('aria-label')), /Deep/);
    await page.evaluate(() => { nsAdvisor.depth = 'routine'; renderNsModelPickerPills(); });
    assert.deepEqual(await page.$$eval('[data-rank]', buttons => buttons.map(b => b.dataset.model)), ['routine-first', 'routine-next']);
  } finally { await page.close(); }
});

test('ranked click applies that model and thinking instead of inheriting the previous thinking', async () => {
  const page = await pickerPage([pick('unrelated-expensive-model')]);
  try {
    await enableRankedPicker(page);
    await page.click('[data-rank="2"]');
    assert.deepEqual(await page.evaluate(() => currentSpawnChoice()), {engine:'codex', model:'routine-next', effort:'low'});
    assert.equal(await page.evaluate(() => nsAdvisor.manual), true);
    await page.evaluate(() => renderNsModelPickerPills());
    assert.equal(await page.$eval('[aria-checked="true"]', el => el.dataset.rank), '2');
    await page.evaluate(() => { $convInputEffortSelect.value = 'high'; syncNsModelPickerPillsSelection(); });
    assert.equal(await page.$$eval('[data-rank][aria-checked="true"]', buttons => buttons.length), 0);
  } finally { await page.close(); }
});

test('a late history response cannot overwrite the active advisor ranking', async () => {
  const page = await pickerPage([pick('historical-model')]);
  try {
    await page.evaluate(() => {
      currentConversation = '__new__';
      window.fetch = () => new Promise(resolve => { window.resolveHistory = resolve; });
      renderNsModelPickerPills();
    });
    await enableRankedPicker(page);
    await page.evaluate(async () => {
      resolveHistory({ok:true,json:async()=>({ok:true,picks:[{engine:'codex',model:'late-history-model'}]})});
      await _fetchServerModelPicksPromise;
    });
    assert.deepEqual(await page.$$eval('[data-rank]', buttons => buttons.map(b => b.dataset.model)), ['routine-first', 'routine-next']);
  } finally { await page.close(); }
});


test('ranked composer refreshes recent models and offers Fable as an explicit manual override', async () => {
  const history = [{engine: 'claude', model: 'fable-5-2', count: 26, last_used: 100}];
  const page = await pickerPage([], history);
  try {
    await page.evaluate(() => {
      $convInputEngineSelect = {options: [{value:'claude'}, {value:'codex'}], value:'codex'};
      MODEL_OPTIONS_BY_ENGINE.claude = [{id:'fable-5-2',label:'Fable 5.2'}];
    });
    await enableRankedPicker(page);
    await page.waitForSelector('[data-recent][data-model="fable-5-2"]');
    assert.match(await page.$eval('[data-recent]', el => el.textContent), /Recent.*Fable 5.2/);
    assert.equal(await page.$eval('[data-recent]', el => el.hasAttribute('data-rank')), false);
    assert.deepEqual(await page.$$eval('[data-rank]', buttons => buttons.map(b => b.dataset.model)), ['routine-first', 'routine-next']);
    await page.click('[data-recent]');
    assert.deepEqual(await page.evaluate(() => currentSpawnChoice()), {engine:'claude',model:'fable-5-2',effort:'high'});
    assert.equal(await page.evaluate(() => nsAdvisor.manual), true);
    assert.equal(await page.$eval('[data-recent]', el => el.getAttribute('aria-checked')), 'true');
    await page.evaluate(() => { nsAdvisor.depth = 'deep'; renderNsModelPickerPills(); });
    assert.deepEqual(await page.$$eval('[data-rank]', buttons => buttons.map(b => b.dataset.model)), ['deep-model']);
    assert.ok(await page.$('[data-recent][data-model="fable-5-2"]'));
  } finally { await page.close(); }
});

test('recent shortcuts deduplicate aliases and efforts, exclude ranked or blocked models, and cap at three', async () => {
  const history = [
    {engine:'claude',model:'claude-fable-5-2',count:20,last_used:100},
    {engine:'claude',model:'fable-5-2',effort:'low',count:10,last_used:99},
    ...['routine-first','blocked','unavailable','hidden-engine','extra-1','extra-2','extra-3'].map(model => ({engine: model === 'hidden-engine' ? 'claude' : 'codex',model,count:1,last_used:90})),
  ];
  const page = await pickerPage(history);
  try {
    await page.evaluate(() => {
      $convInputEngineSelect = {options:[{value:'claude'}, {value:'codex'}],value:'codex'};
      spawnDefaultsState.blockedModels.add('blocked');
      MODEL_OPTIONS_BY_ENGINE.codex = [{id:'unavailable',available:false}];
      MODEL_OPTIONS_BY_ENGINE.claude = [{id:'fable-5-2',label:'Fable 5.2'},{id:'hidden-engine',disabled:true}];
    });
    await enableRankedPicker(page);
    assert.deepEqual(await page.$$eval('[data-recent]', buttons => buttons.map(b => b.dataset.model)), ['fable-5-2','extra-1','extra-2']);
    await page.evaluate(() => { spawnDefaultsState.disabled_engines = ['claude']; renderNsModelPickerPills(); });
    assert.equal(await page.$$eval('[data-recent][data-engine="claude"]', buttons => buttons.length), 0);
  } finally { await page.close(); }
});
