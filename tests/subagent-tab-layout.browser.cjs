// Render the real pane stylesheet and tab controller: task tabs stay above
// the transcript in flex, right-rail, collapsed, narrow and split layouts.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const css = fs.readFileSync(process.env.CCC_TAB_TEST_CSS || require.resolve('../static/app.css'), 'utf8');

test('task tabs precede the transcript and keep lane controls usable', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const cases = [
      {width:1440, body:''},
      {width:1440, body:'status-pos-right'},
      {width:1440, body:'status-pos-right status-rail-collapsed'},
      {width:1000, body:'status-pos-right'},
      {width:390, body:'status-pos-right'},
      {width:1000, body:'status-pos-right popout-rail-forced'},
      {width:1440, body:'status-pos-right', split:true},
      {width:390, body:'status-pos-right', fresh:true},
    ];
    for (const scenario of cases) {
      await page.setViewport({width:scenario.width, height:900});
      const pane = `<section class="conv-pane has-status-rail has-last-user-message ${scenario.fresh ? 'has-new-session-context' : ''}">
        <header class="conv-pane-header">Example session</header>
        <div class="conv-presentation-toolbar">View: Normal</div>
        <div class="conversations-view"><div class="event assistant" data-tab="master"><div class="assistant-text">Master transcript</div></div>
          <div class="event assistant" data-tab="task-example" style="display:none"><div class="assistant-text">Background command transcript</div></div></div>
        <div class="conv-input-bar visible">Send to session…</div>
        <div class="conv-input-context visible ${scenario.fresh ? 'is-new-session' : ''}">Example model · Context</div>
        <aside class="status-rail">Session details</aside>
      </section>`;
      await page.setContent(`<html data-theme="dark"><head><meta name="viewport" content="width=device-width"></head><body class="${scenario.body}"><div class="conv-split" data-orientation="vertical">${pane.repeat(scenario.split ? 2 : 1)}</div></body></html>`);
      await page.addStyleTag({content:css});
      // Only bound the outer host; all pane tracks and tab styles are shipped CSS.
      await page.addStyleTag({content:'body{margin:0;display:flex;height:900px}.conv-split{height:100%}'});
      await page.addScriptTag({path:require.resolve('../static/conversation-core.js')});
      await page.evaluate(() => {
        const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        const controller = window.CCCConversationCore.createController({state:{
          escapeHtml:escape, escapeAttr:escape, isConversationAtBottom:() => false,
        }, helpers:{}});
        window.tabController = controller;
        for (const view of document.querySelectorAll('.conversations-view')) {
          // End-of-transcript navigation is outside this tab placement fixture.
          view._convEndAffordanceAttached = true;
          const state = controller._convPaneTabState(view);
          state.tasks.example = {label:'Background command', completed:false};
          controller._convPaneRenderTabStrip(view);
        }
      });
      const measure = () => page.evaluate(() => [...document.querySelectorAll('.conv-pane')].map(pane => {
        const rect = selector => pane.querySelector(selector).getBoundingClientRect().toJSON();
        return {tabs:rect('.conv-tab-strip'), view:rect('.conversations-view'), input:rect('.conv-input-bar'), pane:pane.getBoundingClientRect().toJSON()};
      }));
      const geometry = await measure();
      for (const g of geometry) {
        assert.ok(g.tabs.height > 0 && g.tabs.bottom <= g.view.top + 1, JSON.stringify({scenario, geometry}));
        assert.ok(g.view.height > 200 && g.view.bottom <= g.input.top + 1, JSON.stringify({scenario, geometry}));
        assert.ok(g.tabs.left >= g.pane.left && g.tabs.right <= g.pane.right + 1, 'tabs fit pane');
      }
      const taskTab = await page.$('.conv-tab[data-tab="task-example"]');
      await taskTab.click();
      assert.equal(await page.$eval('.conversations-view', view => view.dataset.activeTab), 'task-example');
      assert.equal(await page.$eval('.event[data-tab="master"]', el => getComputedStyle(el).display), 'none');
      await page.evaluate(() => document.querySelector('.conv-tab[data-tab="master"]').click());
      assert.equal(await page.$eval('.conversations-view', view => view.dataset.activeTab), 'master');
      if (process.env.CCC_TAB_TEST_SHOTS) await page.screenshot({path:`${process.env.CCC_TAB_TEST_SHOTS}/tabs-${scenario.width}-${scenario.body.replaceAll(' ', '-') || 'top'}${scenario.split ? '-split' : ''}${scenario.fresh ? '-fresh' : ''}.png`});
      await page.evaluate(() => document.querySelector('.conv-tab-close').click());
      assert.equal(await page.$eval('.conv-tab-strip', strip => strip.getBoundingClientRect().height), 0, 'last closed tab leaves no empty row');
      console.log(JSON.stringify({scenario, geometry}));
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
