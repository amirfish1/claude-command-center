const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const code = extract('  function trackSpawnView(', '  function spawnStatsEnabled(')
  + extract('  function spawnStatsMark(', '  // Ordered for reading');

test('startup marks require visible assistant text in the selected connected pane', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setContent('<div id="view" style="height:300px;overflow:auto"></div>');
    await page.evaluate(code => {
      const view = document.getElementById('view');
      const spawnStatsT0 = new Map([['task', Date.now() - 1000]]);
      const spawnStatsSent = new Map(), spawnStatsLocalMarks = new Map();
      const codexSpawnPreviews = new Map(), pendingSpawns = new Map();
      const pane = {conversationId:'task'};
      let active = 'p1';
      const paneByPaneId = () => pane, activePaneId = () => active;
      const getConvViewForPane = () => view;
      const spawnStatsEnabled = () => true, fmtStatMs = ms => ms + 'ms';
      const pendingSpawnIsSelected = () => pane.conversationId === 'task';
      window.__posted = [];
      window.fetch = async (_, init) => { __posted.push(JSON.parse(init.body)); return {}; };
      eval(code + '\nwindow.__mark = markSpawnAssistantPaint; window.__track = trackSpawnView; window.__stats = spawnStatsMark;');
      window.__select = id => { pane.conversationId = id; };
      window.__activate = id => { active = id; };
      window.__card = {id:'task',expected_session_id:'task',spawn_requested_at:Date.now()-1000};
      codexSpawnPreviews.set('task', __card);
    }, code);
    const paint = async () => page.evaluate(() => new Promise(resolve => {
      __mark('task', document.getElementById('view'), 'p1');
      requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }));
    await page.evaluate(() => { view.innerHTML = '<details class="spawn-stats"><summary class="spawn-stats-summary"><strong>12ms</strong> elapsed</summary><div class="spawn-stats-note">Waiting for CCC to create the thread.</div></details><div class="user-msg">Prompt only</div><div class="assistant-text"></div>'; });
    await paint();
    await page.evaluate(() => { view.querySelector('.assistant-text').textContent = 'Answer'; __select('other-session'); });
    await paint();
    await page.evaluate(() => { __select('task'); __activate('p2'); });
    await paint();
    await page.evaluate(() => { __activate('p1'); view.style.display = 'none'; });
    await paint();
    await page.evaluate(() => { view.style.display = ''; Object.defineProperty(document, 'hidden', {configurable:true,get:()=>true}); });
    await paint();
    assert.deepEqual(await page.evaluate(() => __posted), [], 'no false first-output mark');
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable:true,get:()=>false}); });
    await paint();
    const marks = await page.evaluate(() => __posted);
    assert.equal(marks.length, 1);
    assert.match(await page.$eval('.spawn-stats-summary', el=>el.textContent), /to first visible output/);
    assert.doesNotMatch(await page.$eval('.spawn-stats-note', el=>el.textContent), /Waiting for CCC/);
    assert.ok(marks[0].marks.first_visible_output >= 1000);
    await paint();
    assert.equal(await page.evaluate(() => __posted.length), 1, 'first paint is recorded once');
    await page.evaluate(() => {
      __track(__card);
      clearTimeout(__card._spawnViewTiming.timer);
      const timing = __card._spawnViewTiming;
      timing.hidden = true; timing.at = Date.now() - 300;
      timing.sample(); clearTimeout(timing.timer);
      timing.hidden = false; timing.selected = false; timing.at = Date.now() - 400;
      timing.sample(); clearTimeout(timing.timer);
      __stats('task', 'first_paint');
      clearTimeout(timing.timer);
    });
    const timing = await page.evaluate(() => __posted.at(-1).marks);
    assert.ok(timing.first_paint_hidden_ms >= 300);
    assert.ok(timing.first_paint_unselected_ms >= 400);
  } finally { await browser.close(); }
});
