const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
function fn(name) {
  const start = app.indexOf('  function ' + name + '(');
  assert.ok(start >= 0, name + ' is present');
  return app.slice(start, app.indexOf('\n  }', start) + 4);
}
const taps = app.slice(app.indexOf('  const CONVERSATION_ROW_ACTION_SELECTOR ='),
  app.indexOf('  function wireConversationListScrollPause('));
const origin = app.slice(app.indexOf('    if (!$convList._originChipWired) {'),
  app.indexOf('    // "See log" button'));

for (const width of [1440, 390]) {
  test(`native lanes expand, survive repaint, and open read-only transcripts at ${width}px`, async () => {
    const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
    try {
      const page = await browser.newPage();
      await page.setViewport({ width, height: 900, isMobile: width < 600, hasTouch: width < 600 });
      await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1"><style>
        :root{--bg:#fff;--surface-2:#f3f5f8;--text:#17202a;--text-muted:#59636e;--border:#c9d1d9}
        ${css}
        #convList{position:fixed;top:40px;left:0;width:min(100vw,500px);height:800px;overflow:auto}
        </style><div id="convList"></div>`);
      await page.addScriptTag({ content: `
        const $convList = document.querySelector('#convList');
        const expanded = new Set(), collapsed = new Set();
        const _subagentClustersExpandedSet = () => new Set(expanded);
        const _subagentClustersCollapsedSet = () => new Set(collapsed);
        const _subagentClustersSaveExpandedSet = s => {expanded.clear();s.forEach(id=>expanded.add(id));};
        const _subagentClustersSaveCollapsedSet = s => {collapsed.clear();s.forEach(id=>collapsed.add(id));};
        const escapeHtml = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
        const escapeAttr = escapeHtml;
        window.opened=[]; window.selected=[]; window.open=(...args)=>opened.push(args);
        const selectConversation=id=>selected.push(id);
        const selectedListIds=new Set(); const isMobile=()=>${width < 600};
        const shouldUseTouchRowMode=()=>${width < 600}; const trackedTableOpen=()=>false;
        const paneByPaneId=()=>null; const activePaneId=()=>'';
        const clearSelectedConversationRows=()=>{};
        const details=[{id:'agent-one',convId:'parent:agent-one',name:'Inspect <layout>',isTaskSubagent:true},
          {id:'agent-two',convId:'parent:agent-two',name:'Check behavior',isTaskSubagent:true}];
        let _orchFamily={root:'parent',lanes:details.map(l=>l.convId),details};
        ${fn('orchSyncFamilyDisclosure')}
        ${fn('orchApplyFamilyRows')}
        ${taps}
        ${origin}
        window.render=()=>{
          $convList.innerHTML='<div class="conv-item" data-id="parent"><div class="wa-row"><div class="wa-body"><div class="wa-line1"><span class="wa-title">Review pending work</span></div></div></div><div class="conv-title-row"><div class="conv-main-row"><div class="conv-title">Review pending work</div><span class="conv-meta-col"></span></div></div></div>';
          $convList.querySelector('.conv-item').addEventListener('click',ev=>activateConversationRowFromTap(ev.currentTarget,ev));
          orchApplyFamilyRows();
        };
        render();
      ` });
      const host = width < 600 ? '.wa-body' : '.conv-main-row';
      const disclosure = host + ' .conv-orch-family-details';
      const summary = disclosure + ' > summary';
      assert.equal(await page.$eval(disclosure, el => el.open), false);
      if (width < 600) await page.tap(summary); else await page.click(summary);
      await page.waitForFunction(selector => document.querySelector(selector).open, {}, disclosure);
      await page.waitForFunction(() => expanded.has('parent'));
      assert.deepEqual(await page.$$eval(disclosure + ' .conv-family-link', els => els.map(el => el.textContent)), ['Inspect <layout>', 'Check behavior']);
      assert.equal(await page.$eval(disclosure + ' .conv-family-link', el => el.checkVisibility()), true);
      assert.deepEqual(await page.evaluate(() => selected), [], 'disclosure must not select its parent');
      const stable = await page.evaluate(() => {
        const first = document.querySelector('.conv-orch-family-details summary');
        orchApplyFamilyRows();
        return first === document.querySelector('.conv-orch-family-details summary');
      });
      assert.equal(stable, true, 'unchanged paints leave disclosure DOM untouched');
      await page.evaluate(() => render());
      assert.equal(await page.$eval(disclosure, el => el.open), true, 'polling rebuild keeps expansion');
      if (width < 600) await page.tap(disclosure + ' .conv-family-link'); else await page.click(disclosure + ' .conv-family-link');
      assert.deepEqual(await page.evaluate(() => opened.map(args => args[0])), ['/?ccc_popout=conversation&conv=parent%3Aagent-one']);
      assert.deepEqual(await page.evaluate(() => selected), [], 'native transcript must not go through normal session selection');
      if (process.env.NATIVE_LANE_SCREENSHOTS) await page.screenshot({ path: process.env.NATIVE_LANE_SCREENSHOTS + '/native-' + width + '.png' });
      if (width < 600) await page.tap(summary); else { await page.focus(summary); await page.keyboard.press('Enter'); }
      await page.waitForFunction(() => collapsed.has('parent'));
      await page.evaluate(() => render());
      assert.equal(await page.$eval(disclosure, el => el.open), false, 'collapse survives rebuild');
      const cleanup = await page.evaluate(() => {
        _orchFamily={root:'',lanes:[],details:[]};orchApplyFamilyRows();
        return document.querySelectorAll('.conv-orch-family-details').length;
      });
      assert.equal(cleanup, 0, 'switching families removes old controls');
      const existing = await page.evaluate(() => {
        render();
        document.querySelector('.conv-main-row').insertAdjacentHTML('beforeend','<button class="conv-subagent-cluster-toggle" data-role="subagent-cluster-toggle">2 lanes</button>');
        _orchFamily={root:'parent',lanes:details.map(l=>l.convId),details};orchApplyFamilyRows();
        return [document.querySelectorAll('.conv-orch-family-details').length, document.querySelectorAll('.conv-subagent-cluster-toggle').length];
      });
      assert.deepEqual(existing, [0, 1], 'ordinary clusters keep their own toggle');
    } finally { await browser.close(); }
  });
}
